"""Composite scoring as a Model.

A weighted index over declared components: statistical, not learned. It exists
as a provider rather than as a formula because the three things that make a
score trustworthy — what an absent input means, how much of the evidence was
usable, and which component moved the number — are exactly the three a formula
cannot express.
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
from app.shared.contracts import (
    Contract,
    ContractShape,
    FieldSpec,
    FieldType,
    ValidationResult,
    VisibleWhen,
)
from app.shared.errors import ValidationError
from app.shared.payloads import ResultKind, ResultPayload
from app.shared.tabular import Table

from .scoring import MISSING_POLICIES, SCALE_KINDS, scorecard_from_config

PLUGIN_KEY = "scorecard"


def _band_contract(name: str, description: str) -> FieldSpec:
    return FieldSpec(
        name,
        FieldType.ARRAY,
        required=False,
        description=description,
        item=FieldSpec(
            name="band",
            type=FieldType.JSON,
            fields=(
                FieldSpec("upto", FieldType.FLOAT, required=False, nullable=True,
                          description="upper bound; leave empty for the open band"),
                FieldSpec("score", FieldType.FLOAT, required=False,
                          description="0-100 awarded here; a band that only "
                                      "labels the total leaves this empty"),
                FieldSpec("label", FieldType.STRING, required=False,
                          description="what this band is called"),
            ),
        ),
    )


class ScorecardPlugin:
    """Turn several measurements into one defensible number."""

    def describe(self) -> PluginDescriptor:
        return PluginDescriptor(
            key=PLUGIN_KEY,
            name="Scorecard",
            model_type=ModelType.STATISTICAL,
            runtime=RuntimeKind.PYTHON,
            version="1",
            description=(
                "A weighted 0-100 index over declared components, with each "
                "component's sub-score, its share of the total, and the share "
                "of the evidence that was actually available."
            ),
            trainable=False,
            supported_kinds=(ExecutionKind.CALCULATION, ExecutionKind.EVALUATION),
            configuration_contract=Contract(
                shape=ContractShape.OBJECT,
                fields=[
                    FieldSpec(
                        "components",
                        FieldType.ARRAY,
                        description="the measurements the score is built from",
                        item=FieldSpec(
                            name="component",
                            type=FieldType.JSON,
                            fields=(
                                FieldSpec("name", FieldType.STRING, required=False,
                                          description="what this component is called"),
                                FieldSpec("column", FieldType.STRING,
                                          description="the column it reads"),
                                FieldSpec("weight", FieldType.FLOAT, required=False,
                                          default=1.0),
                                FieldSpec("kind", FieldType.STRING, required=False,
                                          default="linear", enum=SCALE_KINDS,
                                          description="how the value becomes 0-100"),
                                FieldSpec("good", FieldType.FLOAT, required=False,
                                          description="the value that scores 100",
                                          visible_when=VisibleWhen("kind", "linear")),
                                FieldSpec("bad", FieldType.FLOAT, required=False,
                                          description="the value that scores 0",
                                          visible_when=VisibleWhen("kind", "linear")),
                                _band_contract(
                                    "bands", "ordered thresholds; the first that fits wins"
                                ),
                                FieldSpec("true_score", FieldType.FLOAT, required=False,
                                          default=0.0,
                                          visible_when=VisibleWhen("kind", "boolean")),
                                FieldSpec("false_score", FieldType.FLOAT, required=False,
                                          default=100.0,
                                          visible_when=VisibleWhen("kind", "boolean")),
                                FieldSpec("missing", FieldType.STRING, required=False,
                                          default="skip", enum=MISSING_POLICIES,
                                          description="what an absent reading means"),
                                FieldSpec("neutral_score", FieldType.FLOAT,
                                          required=False, default=60.0,
                                          visible_when=VisibleWhen("missing", "neutral")),
                                FieldSpec("description", FieldType.STRING,
                                          required=False),
                            ),
                        ),
                    ),
                    _band_contract("bands", "how the total score is labelled"),
                    FieldSpec("min_coverage", FieldType.FLOAT, required=False,
                              default=0.0,
                              description=(
                                  "below this share of usable weight the score is "
                                  "withheld rather than reported"
                              )),
                    FieldSpec("output", FieldType.STRING, required=False,
                              default="score",
                              description="name of the score column"),
                    FieldSpec("include_components", FieldType.BOOLEAN, required=False,
                              default=True,
                              description="write each component's sub-score as a column"),
                ],
            ),
            input_contract=Contract(
                shape=ContractShape.TABLE,
                description="one row per subject, holding the component columns",
            ),
            output_contract=Contract(
                shape=ContractShape.TABLE,
                description="the input plus the score, its band and its coverage",
            ),
            examples=[
                {
                    "name": "Supplier quality index",
                    "configuration": {
                        "components": [
                            {"name": "On-time delivery", "column": "on_time_pct",
                             "kind": "linear", "good": 100, "bad": 70, "weight": 2},
                            {"name": "Defect rate", "column": "defect_ppm",
                             "kind": "linear", "good": 0, "bad": 5000, "weight": 3},
                        ],
                        "bands": [
                            {"upto": 50, "label": "at risk"},
                            {"upto": 80, "label": "acceptable"},
                            {"upto": None, "label": "preferred"},
                        ],
                    },
                }
            ],
        )

    # -- validation --------------------------------------------------------
    def validate(self, definition: ModelDefinition) -> ValidationResult:
        result = ValidationResult()
        try:
            card = scorecard_from_config(definition.configuration or {})
        except ValidationError as exc:
            return result.add_error(exc.message)
        weights = sum(c.weight for c in card.components)
        if weights <= 0:
            result.add_error("the components' weights sum to zero")
        duplicated = {c.name for c in card.components}
        if len(duplicated) != len(card.components):
            result.add_warning(
                "two components share a name; their columns will collide"
            )
        return result

    def check_dataset(self, definition: ModelDefinition, schema_fields) -> ValidationResult:
        """Whether this dataset carries the columns the components read."""
        result = ValidationResult()
        try:
            card = scorecard_from_config(definition.configuration or {})
        except ValidationError as exc:
            return result.add_error(exc.message)
        available = {f.name for f in schema_fields}
        for component in card.components:
            if component.source not in available:
                #  A warning rather than an error: a component may state what
                #  its absence means, and one that does is legitimately run
                #  against a dataset without the column.
                result.add_warning(
                    f"no column '{component.source}' for component "
                    f"'{component.name}'; it will be treated as '{component.missing}'"
                )
        return result

    # -- execution ---------------------------------------------------------
    def execute(self, context: ExecutionContext) -> ExecutionOutcome:
        config = {**(context.definition.configuration or {}), **context.parameters}
        card = scorecard_from_config(config)
        output = str(config.get("output") or "score")
        detail = bool(config.get("include_components", True))

        records = (
            context.input.table.to_rows()
            if context.input.has_table
            else [context.input.record]
        )
        if not records or records == [{}]:
            raise ValidationError(
                "a scorecard needs either a dataset or an inline input record"
            )

        scored: list[dict[str, Any]] = []
        withheld = 0
        for record in records:
            answer = card.score(record)
            row = dict(record)
            row[output] = answer["score"]
            row[f"{output}_band"] = answer["band"]
            row[f"{output}_coverage"] = answer["coverage"]
            row[f"{output}_explanation"] = answer["explanation"]
            if answer["score"] is None:
                withheld += 1
            if detail:
                for component in answer["components"]:
                    key = component["name"].replace(" ", "_").lower()
                    row[f"{output}_{key}"] = component["score"]
            row[f"{output}_components"] = answer["components"]
            scored.append(row)

        single = len(scored) == 1 and not context.input.has_table
        if single:
            payload = ResultPayload(
                kind=ResultKind.OBJECT,
                value=scored[0],
                summary={"components": [c.name for c in card.components]},
            )
        else:
            #  The per-component breakdown is a list of objects; Arrow would
            #  have to guess at a struct type for it and a chart cannot read
            #  one anyway. It stays in the summary, where the detail view finds
            #  it, and out of the table every downstream step reads.
            table = Table.from_rows(
                [{k: v for k, v in row.items() if k != f"{output}_components"}
                 for row in scored]
            )
            payload = ResultPayload.of_table(
                table,
                kind=ResultKind.TABLE,
                summary={
                    "components": [c.name for c in card.components],
                    "withheld": withheld,
                },
                materialise_as_dataset=True,
                dataset_name=f"{context.definition.name} result",
            )

        usable = [row[output] for row in scored if row[output] is not None]
        return ExecutionOutcome(
            payload=payload,
            metrics={
                "scored": len(usable),
                "withheld": withheld,
                "mean_score": round(sum(usable) / len(usable), 4) if usable else None,
                "min_score": min(usable) if usable else None,
                "mean_coverage": (
                    round(
                        sum(row[f"{output}_coverage"] for row in scored) / len(scored), 4
                    )
                    if scored
                    else None
                ),
            },
            logs=context.logs,
        )
