"""Rule / logic model provider.

    IF temperature > 30 AND humidity > 80 THEN risk = HIGH

A rule model has no dataset training, no ML framework and no weights - and it
is still a Model: input in, decision out, versioned and executed like the rest.
"""

from __future__ import annotations

from typing import Any

from app.modules.model.domain.entities import ModelDefinition, ModelType, RuntimeKind
from app.modules.model.domain.plugin import (
    ExecutionContext,
    ExecutionKind,
    ExecutionOutcome,
    PluginDescriptor,
)
from app.plugins.formula.expression import (
    compile_expression,
    evaluate,
    expression_variables,
)
from app.shared.contracts import (
    Contract,
    ContractShape,
    FieldSpec,
    FieldType,
    ValidationResult,
)
from app.shared.errors import ValidationError
from app.shared.payloads import ResultKind, ResultPayload
from app.shared.tabular import Table

PLUGIN_KEY = "rule"

MODE_FIRST_MATCH = "first_match"
MODE_ALL_MATCHES = "all_matches"


class RuleModelPlugin:
    """Executable-only provider backed by a small rule engine."""

    def describe(self) -> PluginDescriptor:
        return PluginDescriptor(
            key=PLUGIN_KEY,
            name="Rule / Logic",
            model_type=ModelType.RULE,
            runtime=RuntimeKind.RULE_ENGINE,
            description=(
                "Ordered IF/THEN rules evaluated per input row, with an optional "
                "default outcome. No training, no weights."
            ),
            trainable=False,
            supported_kinds=(ExecutionKind.CALCULATION, ExecutionKind.TRANSFORMATION),
            configuration_contract=Contract(
                shape=ContractShape.OBJECT,
                fields=[
                    FieldSpec(
                        name="rules",
                        type=FieldType.ARRAY,
                        description="checked in order; the first match wins",
                        item=FieldSpec(
                            name="rule",
                            type=FieldType.JSON,
                            fields=(
                                FieldSpec(
                                    "name",
                                    FieldType.STRING,
                                    required=False,
                                    description="what this rule is for",
                                ),
                                FieldSpec(
                                    "when",
                                    FieldType.STRING,
                                    description="expression, e.g. wind_ms >= 51",
                                ),
                                FieldSpec(
                                    "then",
                                    FieldType.JSON,
                                    description="field → value to assign when it matches",
                                ),
                            ),
                        ),
                    ),
                    FieldSpec(
                        name="default",
                        type=FieldType.JSON,
                        required=False,
                        description="assignments applied when no rule matches",
                    ),
                    FieldSpec(
                        name="mode",
                        type=FieldType.STRING,
                        required=False,
                        default=MODE_FIRST_MATCH,
                        enum=(MODE_FIRST_MATCH, MODE_ALL_MATCHES),
                        description="stop at the first match, or apply every match",
                    ),
                ],
            ),
            output_contract=Contract(
                shape=ContractShape.TABLE,
                description="input rows plus the assignments and the rules that fired",
            ),
            examples=[
                {
                    "name": "Heat risk",
                    "configuration": {
                        "rules": [
                            {
                                "name": "hot and humid",
                                "when": "temperature > 30 and humidity > 80",
                                "then": {"risk": "HIGH"},
                            }
                        ],
                        "default": {"risk": "LOW"},
                    },
                }
            ],
        )

    # -- validation --------------------------------------------------------
    def validate(self, definition: ModelDefinition) -> ValidationResult:
        result = ValidationResult()
        config = definition.configuration or {}
        rules = config.get("rules")
        if not isinstance(rules, list) or not rules:
            return result.add_error("configuration.rules must be a non-empty list")

        mode = config.get("mode", MODE_FIRST_MATCH)
        if mode not in (MODE_FIRST_MATCH, MODE_ALL_MATCHES):
            result.add_error(f"unknown mode '{mode}'")

        declared = set(definition.input_contract.names)
        for index, rule in enumerate(rules):
            label = rule.get("name") or f"rules[{index}]"
            if not isinstance(rule, dict):
                result.add_error(f"{label}: each rule must be an object")
                continue
            if "when" not in rule:
                result.add_error(f"{label}: missing 'when' condition")
            else:
                try:
                    variables = expression_variables(str(rule["when"]))
                    unknown = variables - declared
                    if declared and unknown:
                        result.add_warning(
                            f"{label}: reads {sorted(unknown)}, not in the input contract"
                        )
                except ValidationError as exc:
                    result.add_error(f"{label}: {exc.message}")
            then = rule.get("then")
            if not isinstance(then, dict) or not then:
                result.add_error(f"{label}: 'then' must be a non-empty object")
        return result

    # -- execution ---------------------------------------------------------
    def execute(self, context: ExecutionContext) -> ExecutionOutcome:
        config = {**(context.definition.configuration or {}), **context.parameters}
        rules = config.get("rules") or []
        if not rules:
            raise ValidationError("this rule model defines no rules")
        default = config.get("default") or {}
        mode = config.get("mode", MODE_FIRST_MATCH)

        compiled = [
            (
                rule.get("name") or f"rule_{index}",
                compile_expression(str(rule["when"])),
                rule.get("then") or {},
            )
            for index, rule in enumerate(rules)
        ]

        rows = context.input.rows() if context.input.has_table else [context.input.record]
        if not rows or rows == [{}]:
            raise ValidationError(
                "rule execution needs either a dataset or an inline input record"
            )

        outputs: list[dict[str, Any]] = []
        fired_counts: dict[str, int] = {name: 0 for name, _, _ in compiled}
        unmatched = 0

        for row in rows:
            env = {k: v for k, v in row.items() if str(k).isidentifier()}
            assignments: dict[str, Any] = {}
            fired: list[str] = []
            for name, tree, then in compiled:
                try:
                    matched = bool(evaluate(tree, env))
                except Exception as exc:
                    context.log(f"rule '{name}' skipped a row: {exc}")
                    continue
                if not matched:
                    continue
                fired.append(name)
                fired_counts[name] += 1
                assignments.update(then)
                if mode == MODE_FIRST_MATCH:
                    break
            if not fired:
                unmatched += 1
                assignments.update(default)
            outputs.append({**row, **assignments, "matched_rules": fired})

        table = Table.from_rows(outputs)
        single_row = len(outputs) == 1 and not context.input.has_table
        if single_row:
            payload = ResultPayload(
                kind=ResultKind.CLASSIFICATION,
                value=outputs[0],
                summary={"rules_fired": outputs[0].get("matched_rules", [])},
            )
        else:
            payload = ResultPayload.of_table(
                table,
                kind=ResultKind.CLASSIFICATION,
                summary={"rule_hits": fired_counts, "unmatched_rows": unmatched},
                materialise_as_dataset=True,
                dataset_name=f"{context.definition.name} result",
            )

        return ExecutionOutcome(
            payload=payload,
            metrics={
                "rows_processed": len(outputs),
                "unmatched_rows": unmatched,
                "match_rate": round(1 - unmatched / len(outputs), 4) if outputs else 0.0,
                "rule_hits": fired_counts,
            },
            logs=context.logs,
        )
