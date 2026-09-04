"""Evidence-grounded reasoning as a Model.

The platform's own analysis produces findings; this turns findings into an
argument a person can read and audit. It is a model like any other — versioned,
executed, traced, comparable — which is the point: an explanation that lives
outside the execution record is an explanation nobody can go back to.

Two rules make it usable rather than decorative.

**It reasons over evidence, never over raw data.** The input is a table of
findings that the statistical, threshold and rule layers already produced. A
language model asked to read raw measurements will report measurements that
were not there, and no amount of prompting removes the incentive.

**It answers without a network.** With no endpoint configured it composes the
argument from the evidence directly and says so (`mode: composed`). This is not
a fallback that degrades quality on a bad day — it is the floor the configured
model is measured against, and it means the platform can always explain itself.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.config import get_settings
from app.modules.model.domain.entities import ModelDefinition, ModelType, RuntimeKind
from app.modules.model.domain.plugin import (
    ExecutionContext,
    ExecutionKind,
    ExecutionOutcome,
    PluginDescriptor,
)
from app.plugins.python_function.columnar import as_list
from app.shared.contracts import (
    Contract,
    ContractShape,
    FieldSpec,
    FieldType,
    ValidationResult,
)
from app.shared.errors import ExecutionError, ValidationError
from app.shared.payloads import ResultKind, ResultPayload
from app.shared.tabular import Table

from . import evidence as ev

PLUGIN_KEY = "llm-reasoning"

LANGUAGES = ("zh-TW", "en")

MODE_COMPOSED = "composed"
MODE_MODEL = "model"

_GUARDRAILS = (
    "You are given findings that another system has already computed. "
    "Reason only over them.\n"
    "Rules you must follow exactly:\n"
    "1. Never state a measurement, date, part or event that is not in the "
    "findings.\n"
    "2. Cite the finding behind every claim as [E1], [E2], and so on.\n"
    "3. If the findings disagree, say so and say which you weigh more, and why.\n"
    "4. If the findings do not support a conclusion, say that instead of "
    "producing one.\n"
    "5. Be brief. You are writing for somebody who will act on this today."
)


class LlmReasoningPlugin:
    """Synthesise structured findings into an explained conclusion."""

    def describe(self) -> PluginDescriptor:
        settings = get_settings()
        return PluginDescriptor(
            key=PLUGIN_KEY,
            name="Evidence reasoning",
            model_type=ModelType.LLM,
            runtime=RuntimeKind.EXTERNAL_API,
            version="1",
            timeout_seconds=max(120, settings.llm_timeout_seconds * 2),
            description=(
                "Reads a table of findings and writes the argument they support "
                "— every claim cited back to a finding. Uses a configured "
                "chat-completions endpoint when there is one, and composes the "
                "same argument from the evidence when there is not."
            ),
            trainable=False,
            supported_kinds=(ExecutionKind.CALCULATION, ExecutionKind.EVALUATION),
            configuration_contract=Contract(
                shape=ContractShape.OBJECT,
                fields=[
                    FieldSpec("question", FieldType.STRING,
                              description="what the reasoning is being asked to settle"),
                    FieldSpec("subject_column", FieldType.STRING, required=False,
                              description="column naming what each argument is about; "
                                          "one answer per distinct value"),
                    FieldSpec("statement_column", FieldType.STRING, required=False,
                              description="column holding each finding in prose"),
                    FieldSpec("severity_column", FieldType.STRING, required=False,
                              description="numeric weight; the strongest are shown first"),
                    FieldSpec("category_column", FieldType.STRING, required=False,
                              description="what kind of finding this is"),
                    FieldSpec("conclusion_column", FieldType.STRING, required=False,
                              description="the conclusion already reached, which the "
                                          "reasoning explains rather than replaces"),
                    FieldSpec("evidence_columns", FieldType.ARRAY, required=False,
                              description="columns carried with each finding",
                              item=FieldSpec(name="column", type=FieldType.STRING)),
                    FieldSpec("max_evidence", FieldType.INTEGER, required=False,
                              default=40,
                              description="findings per subject; beyond this a model "
                                          "summarises rather than reasons"),
                    FieldSpec("language", FieldType.STRING, required=False,
                              default="zh-TW", enum=LANGUAGES),
                    FieldSpec("system", FieldType.STRING, required=False,
                              description="extra domain context for the model; the "
                                          "grounding rules are always applied"),
                    FieldSpec("temperature", FieldType.FLOAT, required=False,
                              default=0.0,
                              description="0 keeps the same evidence giving the same "
                                          "answer"),
                    FieldSpec("require_endpoint", FieldType.BOOLEAN, required=False,
                              default=False,
                              description="fail rather than compose when no endpoint "
                                          "is configured"),
                ],
            ),
            input_contract=Contract(
                shape=ContractShape.TABLE,
                description="findings: one row per piece of evidence",
            ),
            output_contract=Contract(
                shape=ContractShape.TABLE,
                description=(
                    "one row per subject: the reasoning, how it was produced, and "
                    "the evidence it cites"
                ),
            ),
            examples=[
                {
                    "name": "Explain a condition assessment",
                    "configuration": {
                        "question": (
                            "Does this equipment need attention, and what is the "
                            "evidence?"
                        ),
                        "subject_column": "asset_id",
                        "statement_column": "finding",
                        "severity_column": "weight",
                        "category_column": "analyzer",
                        "conclusion_column": "recommended_action",
                    },
                }
            ],
        )

    # -- validation --------------------------------------------------------
    def validate(self, definition: ModelDefinition) -> ValidationResult:
        result = ValidationResult()
        config = definition.configuration or {}
        if not config.get("question"):
            result.add_error("configuration.question must say what is being settled")
        language = str(config.get("language", "zh-TW"))
        if language not in LANGUAGES:
            result.add_error(f"unsupported language '{language}'")
        settings = get_settings()
        if config.get("require_endpoint") and not settings.llm_endpoint:
            result.add_error(
                "require_endpoint is set but FLUX_LLM_ENDPOINT is not configured"
            )
        if not settings.llm_endpoint:
            result.add_warning(
                "no FLUX_LLM_ENDPOINT is configured; reasoning will be composed "
                "from the evidence rather than generated"
            )
        return result

    def check_dataset(self, definition: ModelDefinition, schema_fields) -> ValidationResult:
        result = ValidationResult()
        config = definition.configuration or {}
        available = {f.name for f in schema_fields}
        for key in ("subject_column", "statement_column", "severity_column",
                    "category_column", "conclusion_column"):
            column = config.get(key)
            if column and column not in available:
                result.add_warning(f"dataset has no column '{column}' for '{key}'")
        return result

    # -- execution ---------------------------------------------------------
    def execute(self, context: ExecutionContext) -> ExecutionOutcome:
        config = {**(context.definition.configuration or {}), **context.parameters}
        settings = get_settings()

        if not context.input.has_table:
            raise ValidationError("evidence reasoning needs a table of findings")
        rows = context.input.table.to_rows()
        if not rows:
            raise ValidationError("the findings table is empty")

        subject_column = config.get("subject_column")
        language = str(config.get("language", "zh-TW"))
        limit = int(config.get("max_evidence", 40) or 40)
        columns = as_list(config.get("evidence_columns"))

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            key = str(row.get(subject_column, "")) if subject_column else "all"
            grouped.setdefault(key, []).append(row)

        endpoint = settings.llm_endpoint.strip()
        if not endpoint and config.get("require_endpoint"):
            raise ExecutionError(
                "this model requires a language-model endpoint and none is configured"
            )

        answers: list[dict[str, Any]] = []
        modes: dict[str, int] = {}
        for subject, findings in grouped.items():
            items = ev.bundle(
                findings,
                statement_column=config.get("statement_column"),
                severity_column=config.get("severity_column"),
                category_column=config.get("category_column"),
                columns=columns or None,
                limit=limit,
            )
            conclusion = _first(findings, config.get("conclusion_column"))
            grounded = ev.compose(
                items, subject=subject, conclusion=conclusion, language=language
            )

            narrative, mode, note = grounded, MODE_COMPOSED, ""
            if endpoint:
                generated, note = self._ask(
                    endpoint=endpoint,
                    settings=settings,
                    config=config,
                    subject=subject,
                    items=items,
                    conclusion=conclusion,
                    context=context,
                )
                if generated:
                    invented = ev.unsupported(generated, items)
                    if invented:
                        #  A citation to evidence that does not exist is
                        #  invention with a footnote. The composed answer is
                        #  kept instead, and the reason is recorded.
                        note = f"answer cited unknown evidence {invented}; composed instead"
                        context.log(f"{subject}: {note}")
                    else:
                        narrative, mode = generated, MODE_MODEL

            modes[mode] = modes.get(mode, 0) + 1
            answers.append(
                {
                    "subject": subject,
                    "question": str(config.get("question") or ""),
                    "conclusion": conclusion,
                    "reasoning": narrative,
                    "mode": mode,
                    "evidence_count": len(items),
                    "cited": ", ".join(sorted(ev.citations(narrative))),
                    "evidence": "\n".join(
                        f"[{item.ref}] {item.statement}" for item in items
                    ),
                    "note": note,
                }
            )

        return ExecutionOutcome(
            payload=ResultPayload.of_table(
                Table.from_rows(answers),
                kind=ResultKind.TABLE,
                summary={
                    "question": config.get("question"),
                    "subjects": len(answers),
                    "modes": modes,
                    "endpoint_configured": bool(endpoint),
                },
                materialise_as_dataset=True,
                dataset_name=f"{context.definition.name} result",
            ),
            metrics={
                "subjects": len(answers),
                "generated": modes.get(MODE_MODEL, 0),
                "composed": modes.get(MODE_COMPOSED, 0),
                "mean_evidence": (
                    round(sum(a["evidence_count"] for a in answers) / len(answers), 2)
                    if answers
                    else 0
                ),
            },
            logs=context.logs,
        )

    # -- the network half --------------------------------------------------
    def _ask(
        self,
        *,
        endpoint: str,
        settings,
        config: dict[str, Any],
        subject: str,
        items: list[ev.EvidenceItem],
        conclusion: str | None,
        context: ExecutionContext,
    ) -> tuple[str | None, str]:
        """Ask the configured endpoint, and never let it take the run down.

        A reasoning step that fails because a network service was slow should
        cost the *prose*, not the assessment: the composed answer is already
        computed, so the caller still gets a complete, grounded explanation.
        """
        import requests

        from app.shared.outbound import check_url

        try:
            check_url(endpoint, settings.network_policy)
        except Exception as exc:
            context.log(f"endpoint refused by the outbound policy: {exc}")
            return None, f"endpoint not permitted: {exc}"

        instruction = str(config.get("system") or "")
        language = str(config.get("language", "zh-TW"))
        prompt = (
            f"Question: {config.get('question')}\n"
            f"Subject: {subject}\n"
            + (f"Conclusion already reached: {conclusion}\n" if conclusion else "")
            + f"\nFindings:\n{ev.render(items)}\n\n"
            f"Answer in {language}. Keep to six sentences or fewer."
        )
        body = {
            "model": settings.llm_model,
            "temperature": float(config.get("temperature", 0.0) or 0.0),
            "max_tokens": settings.llm_max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": "\n\n".join(
                        part for part in (_GUARDRAILS, instruction) if part
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
        headers = {"Content-Type": "application/json"}
        if settings.llm_api_key:
            headers["Authorization"] = f"Bearer {settings.llm_api_key}"

        try:
            response = requests.post(
                endpoint,
                headers=headers,
                data=json.dumps(body),
                timeout=settings.llm_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            text = payload["choices"][0]["message"]["content"]
        except Exception as exc:  # a slow endpoint must not fail the assessment
            context.log(f"reasoning endpoint failed for {subject}: {exc}")
            return None, f"endpoint unavailable: {type(exc).__name__}"
        return str(text).strip(), ""


def _first(rows: list[dict[str, Any]], column: str | None) -> str | None:
    if not column:
        return None
    for row in rows:
        value = row.get(column)
        if value not in (None, ""):
            return str(value)
    return None
