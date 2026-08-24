"""A small, safe arithmetic expression evaluator.

Formula and rule models come from user input, so expressions are parsed into an
AST and walked with an explicit allow-list of node types and functions. There is
no eval(), no attribute access, no imports and no name resolution beyond the
variables handed in.
"""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable
from typing import Any

from app.shared.errors import ValidationError

MAX_EXPRESSION_LENGTH = 2000

_BIN_OPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS: dict[type, Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
    ast.Not: operator.not_,
}

_COMPARE_OPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}

_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "int": int,
    "float": float,
    "len": len,
    "sqrt": math.sqrt,
    "exp": math.exp,
    "log": math.log,
    "log10": math.log10,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "floor": math.floor,
    "ceil": math.ceil,
    "pow": pow,
    "sum": sum,
}

_CONSTANTS: dict[str, Any] = {"pi": math.pi, "e": math.e, "true": True, "false": False}

_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.IfExp,
    ast.List,
    ast.Tuple,
    ast.And,
    ast.Or,
) + tuple(_BIN_OPS) + tuple(_UNARY_OPS) + tuple(_COMPARE_OPS)


def compile_expression(source: str) -> ast.Expression:
    """Parse and statically validate an expression, raising on anything unsafe."""
    if not isinstance(source, str) or not source.strip():
        raise ValidationError("expression must be a non-empty string")
    if len(source) > MAX_EXPRESSION_LENGTH:
        raise ValidationError(
            f"expression exceeds {MAX_EXPRESSION_LENGTH} characters"
        )
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise ValidationError(f"invalid expression: {exc.msg}") from exc

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValidationError(
                f"expression uses an unsupported construct: {type(node).__name__}"
            )
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
                raise ValidationError(
                    "only these functions are allowed: "
                    + ", ".join(sorted(_FUNCTIONS))
                )
            if node.keywords:
                raise ValidationError("keyword arguments are not supported")
    return tree


def expression_variables(source: str) -> set[str]:
    """Names the expression reads, excluding functions and constants."""
    tree = compile_expression(source)
    names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    }
    return names - set(_FUNCTIONS) - set(_CONSTANTS)


def evaluate(tree: ast.Expression, variables: dict[str, Any]) -> Any:
    """Evaluate a pre-compiled expression against a variable mapping."""
    return _eval(tree.body, variables)


def evaluate_source(source: str, variables: dict[str, Any]) -> Any:
    return evaluate(compile_expression(source), variables)


def _eval(node: ast.AST, env: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        raise ValidationError(f"unknown variable '{node.id}'")
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ValidationError("unsupported binary operator")
        return op(_eval(node.left, env), _eval(node.right, env))
    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValidationError("unsupported unary operator")
        return op(_eval(node.operand, env))
    if isinstance(node, ast.BoolOp):
        values = [_eval(v, env) for v in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, env)
        for op_node, comparator in zip(node.ops, node.comparators, strict=True):
            op = _COMPARE_OPS.get(type(op_node))
            if op is None:
                raise ValidationError("unsupported comparison operator")
            right = _eval(comparator, env)
            if not op(left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return _eval(node.body, env) if _eval(node.test, env) else _eval(node.orelse, env)
    if isinstance(node, ast.Call):
        func = _FUNCTIONS[node.func.id]
        return func(*[_eval(arg, env) for arg in node.args])
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(item, env) for item in node.elts]
    raise ValidationError(f"unsupported expression node: {type(node).__name__}")


def allowed_functions() -> list[str]:
    return sorted(_FUNCTIONS)
