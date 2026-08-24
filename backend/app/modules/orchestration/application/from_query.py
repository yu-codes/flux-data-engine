"""Turning an Explore query into pipeline steps.

Explore is where people work out what they want: narrow to the rows that
matter, sort them, look. Then the answer had nowhere to go - the only way to
keep it was to open the pipeline builder and set the same conditions again from
memory, which is both tedious and the point at which the two drift apart.

The translation lives here rather than in the frontend because it is knowledge
about transforms: which one implements a condition, what it calls its options,
which operators it has. That belongs next to the transforms, where it can be
tested without a browser.
"""

from __future__ import annotations

from typing import Any

from app.shared.errors import ValidationError

#  Explore's operator vocabulary, mapped onto `filter_rows`. Explore speaks in
#  comparison terms ("ne"), the transform speaks in words ("not_equals"); both
#  are reasonable and neither should have to change to suit the other.
_OPERATORS = {
    "eq": "equals",
    "ne": "not_equals",
    "gt": "gt",
    "gte": "gte",
    "lt": "lt",
    "lte": "lte",
    "in": "in",
    "is_null": "is_empty",
    "not_null": "not_empty",
}

#  What a condition needs no value for.
_VALUELESS = {"is_empty", "not_empty"}


def steps_from_query(
    *,
    columns: list[str] | None = None,
    filters: list[dict[str, Any]] | None = None,
    sort_by: str | None = None,
    sort_desc: bool = False,
) -> list[dict[str, Any]]:
    """The steps that reproduce what Explore is currently showing.

    Ordered as a person would read it: keep the columns, keep the rows, then
    order what is left. `contains` has no `filter_rows` equivalent, so it is
    refused by name rather than silently dropped - a pipeline that quietly
    filters less than the screen did is worse than one that will not be built.

    Each step reads the one before it. Leaving `input_from` unset means "the
    pipeline's input dataset", which would make every step a branch off the
    source rather than a link in a chain - three steps that each ignore the
    other two, and three output datasets where one was wanted.
    """
    steps: list[dict[str, Any]] = []

    if columns:
        steps.append(
            _step(
                "keep the chosen columns",
                "select_columns",
                {"columns": list(columns)},
            )
        )

    for condition in filters or []:
        column = (condition or {}).get("column")
        if not column:
            continue
        raw = str(condition.get("op") or "eq")
        operator = _OPERATORS.get(raw)
        if operator is None:
            raise ValidationError(
                f"'{raw}' is not something a pipeline step can express",
                details={"supported": sorted(_OPERATORS)},
            )
        options: dict[str, Any] = {"column": column, "op": operator}
        if operator not in _VALUELESS:
            value = condition.get("value")
            if operator == "in" and isinstance(value, str):
                value = [part.strip() for part in value.split(",") if part.strip()]
            options["value"] = value
        steps.append(_step(f"keep where {column} {raw}", "filter_rows", options))

    if sort_by:
        steps.append(
            _step(
                f"order by {sort_by}",
                "sort_rows",
                {"column": sort_by, "descending": bool(sort_desc)},
            )
        )

    if not steps:
        raise ValidationError(
            "there is nothing to save: add a filter, a sort or a column "
            "selection first"
        )

    #  Chained here rather than at each append, so the rule is stated once and
    #  cannot be forgotten by whoever adds the next kind of step.
    for earlier, step in zip(steps, steps[1:], strict=False):
        step["input_from"] = earlier["name"]
    return steps


def _step(name: str, transform: str, options: dict[str, Any]) -> dict[str, Any]:
    """One inline step. No library model is created - a step is its own thing."""
    return {
        "name": name,
        "provider": "python-transform",
        "configuration": {"transform": transform, "options": options},
    }
