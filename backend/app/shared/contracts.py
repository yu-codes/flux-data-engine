"""Schema-first contracts.

Every Model declares three contracts - input, parameter and output - and every
Dataset declares a schema. Both are expressed with the same primitives so a
dataset schema can be checked against a model input contract directly.

This module is deliberately free of any framework or ML dependency: it is part
of the shared kernel used by all domain layers.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any


class FieldType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    TIMESTAMP = "timestamp"
    JSON = "json"
    ARRAY = "array"
    ANY = "any"


class ContractShape(str, Enum):
    """How the payload described by a contract is laid out."""

    OBJECT = "object"      # a single record, e.g. {"price": 10, "quantity": 3}
    TABLE = "table"        # many records sharing the field set
    SCALAR = "scalar"      # a single unnamed value
    FREE = "free"          # opaque payload, validated by the plugin itself


_TRUE = {"true", "1", "yes", "y", "t"}
_FALSE = {"false", "0", "no", "n", "f"}


@dataclass(frozen=True)
class VisibleWhen:
    """A field that only applies when another field has a particular value.

    `degree` means nothing unless the family is polynomial; a constraint's
    upper bound means nothing unless the constraint is bounded above. Without
    this a form shows every field at once and leaves the reader to work out
    which of them the provider will actually read.
    """

    field: str
    equals: Any = None
    #  `equals` covers one value; `in_` covers a set of them, which is what a
    #  field shared by two of five families needs.
    in_: tuple[Any, ...] | None = None

    def satisfied_by(self, record: dict) -> bool:
        """Whether the condition holds for this record.

        `record` is expected to carry effective values - what was supplied, or
        the field's default where nothing was. A field's default is its value
        until somebody changes it, so a condition on "kind == range" must hold
        for a record that never mentions `kind` and whose `kind` defaults to
        range. Reading the raw record instead made every dependent field
        silently inapplicable, which is a validation that checks nothing.
        """
        value = record.get(self.field)
        if self.in_ is not None:
            return value in self.in_
        return value == self.equals

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "equals": self.equals,
            "in": list(self.in_) if self.in_ is not None else None,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> VisibleWhen:
        options = raw.get("in")
        return cls(
            field=raw["field"],
            equals=raw.get("equals"),
            in_=tuple(options) if options is not None else None,
        )


@dataclass(frozen=True)
class FieldSpec:
    """One named field of a contract or of a dataset schema.

    A field is usually a scalar. Three cases are not, and they are the ones
    that used to force a provider's parameters into a JSON textbox:

    * `fields` - this field is an object with a known shape;
    * `item`   - this field is a list, and every element looks like this;
    * `visible_when` - this field only applies in some configurations.

    A dataset schema uses none of them, which is intentional: a column is a
    column. They cost nothing when unused.
    """

    name: str
    type: FieldType = FieldType.ANY
    required: bool = True
    nullable: bool = False
    description: str = ""
    default: Any = None
    enum: tuple[Any, ...] | None = None
    unit: str | None = None
    fields: tuple[FieldSpec, ...] | None = None
    item: FieldSpec | None = None
    #  A mapping whose keys are names the user chooses and whose values all
    #  look the same: an optimiser's variables, a simulation's distributions,
    #  a formula's expressions. Distinct from `fields`, where the keys are
    #  fixed and known, and from `item`, where the container is a list.
    values: FieldSpec | None = None
    visible_when: VisibleWhen | None = None

    def applies_to(self, record: dict) -> bool:
        """Whether this field is relevant given the rest of the record."""
        return self.visible_when is None or self.visible_when.satisfied_by(record)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type.value,
            "required": self.required,
            "nullable": self.nullable,
            "description": self.description,
            "default": self.default,
            "enum": list(self.enum) if self.enum else None,
            "unit": self.unit,
            "fields": [f.to_dict() for f in self.fields] if self.fields else None,
            "item": self.item.to_dict() if self.item else None,
            "values": self.values.to_dict() if self.values else None,
            "visible_when": self.visible_when.to_dict() if self.visible_when else None,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> FieldSpec:
        enum = raw.get("enum")
        nested = raw.get("fields")
        item = raw.get("item")
        visible = raw.get("visible_when")
        return cls(
            name=raw["name"],
            type=FieldType(raw.get("type", "any")),
            required=bool(raw.get("required", True)),
            nullable=bool(raw.get("nullable", False)),
            description=raw.get("description", "") or "",
            default=raw.get("default"),
            enum=tuple(enum) if enum else None,
            unit=raw.get("unit"),
            fields=tuple(cls.from_dict(f) for f in nested) if nested else None,
            item=cls.from_dict(item) if item else None,
            values=cls.from_dict(raw["values"]) if raw.get("values") else None,
            visible_when=VisibleWhen.from_dict(visible) if visible else None,
        )


@dataclass
class ValidationResult:
    """Outcome of checking a payload (or a schema) against a contract."""

    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> ValidationResult:
        self.valid = False
        self.errors.append(message)
        return self

    def add_warning(self, message: str) -> ValidationResult:
        self.warnings.append(message)
        return self

    def merge(self, other: ValidationResult) -> ValidationResult:
        self.valid = self.valid and other.valid
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        return self

    def to_dict(self) -> dict:
        return {"valid": self.valid, "errors": self.errors, "warnings": self.warnings}


@dataclass
class Contract:
    """An ordered set of FieldSpec plus the shape of the payload."""

    fields: list[FieldSpec] = field(default_factory=list)
    shape: ContractShape = ContractShape.OBJECT
    description: str = ""

    # -- serialisation -----------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "shape": self.shape.value,
            "description": self.description,
            "fields": [f.to_dict() for f in self.fields],
        }

    @classmethod
    def from_dict(cls, raw: dict | None) -> Contract:
        raw = raw or {}
        return cls(
            fields=[FieldSpec.from_dict(f) for f in raw.get("fields", [])],
            shape=ContractShape(raw.get("shape", "object")),
            description=raw.get("description", "") or "",
        )

    # -- lookups -----------------------------------------------------------
    @property
    def names(self) -> list[str]:
        return [f.name for f in self.fields]

    def get(self, name: str) -> FieldSpec | None:
        for spec in self.fields:
            if spec.name == name:
                return spec
        return None

    # -- validation --------------------------------------------------------
    def validate_record(self, record: dict, *, path: str = "") -> ValidationResult:
        result = ValidationResult()
        if self.shape is ContractShape.FREE:
            return result
        if not isinstance(record, dict):
            return result.add_error(f"{path or 'payload'} must be an object")

        effective = self._with_defaults(record)
        for spec in self.fields:
            #  A field the configuration has made irrelevant is neither
            #  required nor checked: asking for a polynomial's degree when the
            #  family is linear would be an error about nothing.
            if not spec.applies_to(effective):
                continue
            present = spec.name in record and record[spec.name] is not None
            if not present:
                if spec.required and spec.default is None and not spec.nullable:
                    result.add_error(f"missing required field '{path}{spec.name}'")
                continue
            value = record[spec.name]
            if spec.enum and value not in spec.enum:
                result.add_error(
                    f"'{path}{spec.name}' must be one of {list(spec.enum)}, got {value!r}"
                )
                continue
            result.merge(_validate_value(value, spec, f"{path}{spec.name}"))

        unknown = set(record) - set(self.names)
        if unknown and self.fields:
            result.add_warning(f"ignored unknown field(s): {sorted(unknown)}")
        return result

    def validate_rows(
        self, rows: Iterable[dict], *, sample: int = 200
    ) -> ValidationResult:
        result = ValidationResult()
        for index, row in enumerate(rows):
            if index >= sample:
                break
            result.merge(self.validate_record(row, path=f"row[{index}]."))
        return result

    def coerce_record(self, record: dict) -> dict:
        """Apply defaults and cast values; unknown keys are dropped."""
        if self.shape is ContractShape.FREE or not self.fields:
            return dict(record)
        out: dict[str, Any] = {}
        effective = self._with_defaults(record)
        for spec in self.fields:
            if not spec.applies_to(effective):
                continue
            if spec.name in record and record[spec.name] is not None:
                out[spec.name] = _coerce_field(record[spec.name], spec)
            elif spec.default is not None:
                out[spec.name] = _coerce_field(spec.default, spec)
            elif spec.nullable or not spec.required:
                out[spec.name] = None
        return out

    def _with_defaults(self, record: dict) -> dict:
        """The record as it will actually be read: supplied values, then defaults."""
        effective = dict(record)
        for spec in self.fields:
            if effective.get(spec.name) is None and spec.default is not None:
                effective[spec.name] = spec.default
        return effective

    def validate_schema(self, schema_fields: Iterable[FieldSpec]) -> ValidationResult:
        """Check that a dataset schema can satisfy this (input) contract."""
        result = ValidationResult()
        available = {f.name: f for f in schema_fields}
        for spec in self.fields:
            if spec.name not in available:
                if spec.required and spec.default is None:
                    result.add_error(f"dataset has no column '{spec.name}'")
                continue
            actual = available[spec.name]
            if not _type_compatible(actual.type, spec.type):
                result.add_warning(
                    f"column '{spec.name}' is {actual.type.value}, "
                    f"contract expects {spec.type.value}"
                )
        return result


_NUMERIC = {FieldType.INTEGER, FieldType.FLOAT}


def _type_compatible(actual: FieldType, expected: FieldType) -> bool:
    if FieldType.ANY in (actual, expected) or actual == expected:
        return True
    return actual in _NUMERIC and expected in _NUMERIC


def coerce_value(value: Any, field_type: FieldType) -> Any:
    """Cast value to field_type, raising on genuinely wrong input."""
    if value is None:
        return None
    if field_type in (FieldType.ANY, FieldType.JSON):
        return value
    if field_type is FieldType.STRING:
        return value if isinstance(value, str) else str(value)
    if field_type is FieldType.INTEGER:
        if isinstance(value, bool):
            raise TypeError("boolean is not an integer")
        return int(value)
    if field_type is FieldType.FLOAT:
        if isinstance(value, bool):
            raise TypeError("boolean is not a float")
        return float(value)
    if field_type is FieldType.BOOLEAN:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise ValueError(f"cannot read {value!r} as boolean")
    if field_type is FieldType.TIMESTAMP:
        if isinstance(value, (datetime, date)):
            return value
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if field_type is FieldType.ARRAY:
        if isinstance(value, (list, tuple)):
            return list(value)
        raise TypeError("expected an array")
    return value


def _validate_value(value: Any, spec: FieldSpec, path: str) -> ValidationResult:
    """Check one value against one field, descending where the field does.

    Nested objects and list elements are checked with the same rules as the
    top level, which is the property that makes a contract composable: a
    provider describes a constraint once and it means the same thing whether
    it stands alone or appears fifty times inside a list.
    """
    result = ValidationResult()

    if spec.fields is not None:
        if not isinstance(value, dict):
            return result.add_error(f"'{path}' must be an object")
        nested = Contract(shape=ContractShape.OBJECT, fields=list(spec.fields))
        return result.merge(nested.validate_record(value, path=f"{path}."))

    if spec.item is not None:
        if not isinstance(value, (list, tuple)):
            return result.add_error(f"'{path}' must be a list")
        for index, element in enumerate(value):
            result.merge(_validate_value(element, spec.item, f"{path}[{index}]"))
        return result

    if spec.values is not None:
        if not isinstance(value, dict):
            return result.add_error(f"'{path}' must be an object")
        for key, element in value.items():
            result.merge(_validate_value(element, spec.values, f"{path}.{key}"))
        return result

    try:
        coerce_value(value, spec.type)
    except (TypeError, ValueError):
        result.add_error(
            f"'{path}' expects {spec.type.value}, got {type(value).__name__}"
        )
    return result


def _coerce_field(value: Any, spec: FieldSpec) -> Any:
    """Apply defaults and casts, descending into objects and lists."""
    if spec.fields is not None:
        nested = Contract(shape=ContractShape.OBJECT, fields=list(spec.fields))
        return nested.coerce_record(value if isinstance(value, dict) else {})
    if spec.item is not None:
        if not isinstance(value, (list, tuple)):
            return []
        return [_coerce_field(element, spec.item) for element in value]
    if spec.values is not None:
        if not isinstance(value, dict):
            return {}
        return {k: _coerce_field(v, spec.values) for k, v in value.items()}
    return coerce_value(value, spec.type)
