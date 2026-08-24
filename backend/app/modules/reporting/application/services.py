"""Report service: compose sections, resolve them live, export a snapshot."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.modules.data.application.services import DatasetService
from app.modules.execution.application.services import ExecutionService
from app.modules.model.application.services import ModelService
from app.shared.errors import ConflictError, NotFoundError, ValidationError
from app.shared.ids import utcnow
from app.shared.storage import ObjectStore

from ..domain.entities import (
    ExportFormat,
    Report,
    ReportSection,
    ReportStatus,
    SectionKind,
)
from ..domain.ports import ReportRepository

logger = logging.getLogger(__name__)

DEFAULT_TABLE_ROWS = 20
MAX_TABLE_ROWS = 500


class ReportService:
    def __init__(
        self,
        *,
        repository: ReportRepository,
        results,
        executions: ExecutionService,
        models: ModelService,
        datasets: DatasetService,
        store: ObjectStore,
    ):
        self.repository = repository
        self.results = results
        self.executions = executions
        self.models = models
        self.datasets = datasets
        self.store = store

    # -- reads -------------------------------------------------------------
    def get(self, report_id: str) -> Report:
        report = self.repository.get(report_id)
        if not report:
            raise NotFoundError(f"report '{report_id}' not found")
        return report

    def list(self) -> list[Report]:
        return self.repository.list()

    # -- writes ------------------------------------------------------------
    def create(
        self,
        *,
        name: str,
        sections: list[dict] | None = None,
        description: str = "",
        tags: list[str] | None = None,
    ) -> Report:
        if self.repository.get_by_name(name):
            raise ConflictError(f"a report named '{name}' already exists")
        parsed = [ReportSection.from_dict(s) for s in (sections or [])]
        self._validate(parsed)
        return self.repository.add(
            Report(name=name, description=description, sections=parsed, tags=tags or [])
        )

    def update(self, report_id: str, changes: dict[str, Any]) -> Report:
        report = self.get(report_id)
        if changes.get("name"):
            report.name = changes["name"]
        if changes.get("description") is not None:
            report.description = changes["description"]
        if changes.get("tags") is not None:
            report.tags = list(changes["tags"])
        if changes.get("sections") is not None:
            parsed = [ReportSection.from_dict(s) for s in changes["sections"]]
            self._validate(parsed)
            report.sections = parsed
        if changes.get("status"):
            report.status = ReportStatus(changes["status"])
        report.updated_at = utcnow()
        return self.repository.update(report)

    def delete(self, report_id: str) -> None:
        report = self.get(report_id)
        if report.last_export_uri:
            self.store.delete(report.last_export_uri)
        self.repository.delete(report.id)

    # -- rendering ---------------------------------------------------------
    def render(self, report_id: str) -> dict[str, Any]:
        """Resolve every section against live data."""
        report = self.get(report_id)
        return {
            "id": report.id,
            "name": report.name,
            "description": report.description,
            "status": report.status.value,
            "generated_at": utcnow().isoformat(),
            "sections": [self._render_section(section) for section in report.sections],
        }

    def _render_section(self, section: ReportSection) -> dict[str, Any]:
        rendered: dict[str, Any] = {
            "kind": section.kind.value,
            "title": section.title,
            "options": section.options,
        }
        try:
            rendered.update(self._resolve(section))
        except Exception as exc:
            #  One broken reference must not blank the whole report.
            logger.warning("report section '%s' could not be resolved: %s",
                           section.title or section.kind.value, exc)
            rendered["error"] = f"{type(exc).__name__}: {exc}"
        return rendered

    def _resolve(self, section: ReportSection) -> dict[str, Any]:
        if section.kind is SectionKind.TEXT:
            return {"body": section.body}

        if section.kind is SectionKind.METRICS:
            execution_id = self._require(section.execution_id, "execution_id")
            execution = self.executions.get(execution_id)
            return {
                "execution_id": execution.id,
                "model": self._model_name(execution.model_id),
                "execution_kind": execution.kind.value,
                "status": execution.status.value,
                "metrics": execution.metrics,
                "duration_seconds": execution.duration_seconds,
            }

        if section.kind is SectionKind.EXECUTION:
            execution_id = self._require(section.execution_id, "execution_id")
            execution = self.executions.get(execution_id)
            return {
                "execution_id": execution.id,
                "model": self._model_name(execution.model_id),
                "model_version_id": execution.model_version_id,
                "execution_kind": execution.kind.value,
                "status": execution.status.value,
                "parameters": execution.parameters,
                "lineage": execution.lineage,
                "created_at": execution.created_at.isoformat(),
                "duration_seconds": execution.duration_seconds,
            }

        if section.kind is SectionKind.RESULT:
            result_id = self._require(section.result_id, "result_id")
            result = self.results.get(result_id)
            limit = self._row_limit(section)
            return {
                "result_id": result.id,
                "result_kind": result.kind.value,
                "summary": result.summary,
                "metrics": result.metrics,
                "payload": self.results.read_payload(result.id, limit=limit),
            }

        if section.kind is SectionKind.TABLE:
            limit = self._row_limit(section)
            if section.dataset_version_id:
                table = self.datasets.read_table(section.dataset_version_id)
                return {
                    "columns": [f.to_dict() for f in table.schema_fields()],
                    "rows": table.to_rows(limit=limit),
                    "row_count": table.num_rows,
                }
            result = self.results.get(self._require(section.result_id, "result_id"))
            payload = self.results.read_payload(result.id, limit=limit)
            if isinstance(payload, dict) and "rows" in payload:
                return {
                    "columns": payload.get("columns", []),
                    "rows": payload["rows"],
                    "row_count": payload.get("row_count", len(payload["rows"])),
                }
            raise ValidationError("this result has no tabular payload to tabulate")

        if section.kind is SectionKind.CHART:
            #  Charts are resolved by id; the UI renders them with the same
            #  component it uses everywhere else.
            return {"visualization_id": self._require(
                section.visualization_id, "visualization_id"
            )}

        if section.kind is SectionKind.MODEL:
            model = self.models.get(self._require(section.model_id, "model_id"))
            versions = self.models.list_versions(model.id)
            return {
                "model_id": model.id,
                "model_name": model.name,
                "model_type": model.type.value,
                "provider": model.provider,
                "trainable": model.is_trainable,
                "configuration": model.configuration,
                "input_contract": model.input_contract.to_dict(),
                "parameter_contract": model.parameter_contract.to_dict(),
                "output_contract": model.output_contract.to_dict(),
                "versions": [
                    {"version": v.version, "metrics": v.metrics, "notes": v.notes}
                    for v in versions
                ],
            }

        raise ValidationError(f"unsupported section kind '{section.kind}'")

    # -- export ------------------------------------------------------------
    def export(
        self, report_id: str, fmt: str = ExportFormat.MARKDOWN.value
    ) -> dict[str, Any]:
        """Freeze the current rendering into a file in the object store."""
        export_format = ExportFormat(fmt)
        report = self.get(report_id)
        rendered = self.render(report.id)

        if export_format is ExportFormat.MARKDOWN:
            content = render_markdown(rendered)
            suffix, media_type = "md", "text/markdown"
        elif export_format is ExportFormat.HTML:
            content = render_html(rendered)
            suffix, media_type = "html", "text/html"
        else:
            content = json.dumps(rendered, ensure_ascii=False, indent=2, default=str)
            suffix, media_type = "json", "application/json"

        uri = self.store.put_bytes(
            f"reports/{report.id}/report.{suffix}", content.encode("utf-8")
        )
        report.last_export_uri = uri
        report.last_export_format = export_format.value
        report.last_exported_at = utcnow()
        self.repository.update(report)

        return {
            "report_id": report.id,
            "format": export_format.value,
            "media_type": media_type,
            "uri": uri,
            "content": content,
        }

    # -- internals ---------------------------------------------------------
    def _validate(self, sections: list[ReportSection]) -> None:
        for index, section in enumerate(sections):
            if section.kind is SectionKind.TEXT:
                continue
            if section.kind is SectionKind.TABLE:
                if not (section.dataset_version_id or section.result_id):
                    raise ValidationError(
                        f"section {index + 1} ({section.kind.value}) needs a "
                        f"dataset_version_id or a result_id"
                    )
                continue
            if not section.reference():
                raise ValidationError(
                    f"section {index + 1} ({section.kind.value}) needs a reference id"
                )

    @staticmethod
    def _require(value: str | None, field_name: str) -> str:
        if not value:
            raise ValidationError(f"this section needs a {field_name}")
        return value

    @staticmethod
    def _row_limit(section: ReportSection) -> int:
        raw = section.options.get("limit", DEFAULT_TABLE_ROWS)
        try:
            return max(1, min(int(raw), MAX_TABLE_ROWS))
        except (TypeError, ValueError):
            return DEFAULT_TABLE_ROWS

    def _model_name(self, model_id: str) -> str:
        try:
            return self.models.get(model_id).name
        except NotFoundError:
            return model_id


# --------------------------------------------------------------------------
# renderers
# --------------------------------------------------------------------------
def render_markdown(rendered: dict[str, Any]) -> str:
    lines = [f"# {rendered['name']}", ""]
    if rendered.get("description"):
        lines += [rendered["description"], ""]
    lines += [f"_Generated {rendered['generated_at']}_", ""]

    for section in rendered["sections"]:
        if section.get("title"):
            lines += [f"## {section['title']}", ""]
        if section.get("error"):
            lines += [f"> Could not render this section: {section['error']}", ""]
            continue

        kind = section["kind"]
        if kind == "text":
            lines += [section.get("body", ""), ""]
        elif kind in ("metrics", "execution"):
            lines += _markdown_pairs(
                {k: v for k, v in section.items()
                 if k not in ("kind", "title", "options", "metrics")}
            )
            if section.get("metrics"):
                lines += ["", *_markdown_pairs(section["metrics"])]
            lines.append("")
        elif kind == "table":
            lines += _markdown_table(section.get("columns", []), section.get("rows", []))
            lines += ["", f"_{section.get('row_count', 0)} rows in total_", ""]
        elif kind == "result":
            lines += _markdown_pairs(section.get("summary", {}))
            if section.get("metrics"):
                lines += ["", *_markdown_pairs(section["metrics"])]
            payload = section.get("payload")
            if isinstance(payload, dict) and payload.get("rows"):
                lines += ["", *_markdown_table(payload.get("columns", []), payload["rows"])]
            lines.append("")
        elif kind == "chart":
            lines += [f"_Chart {section.get('visualization_id')} — "
                      f"open the report in the app to see it rendered._", ""]
        elif kind == "model":
            lines += _markdown_pairs(
                {
                    "name": section.get("model_name"),
                    "type": section.get("model_type"),
                    "provider": section.get("provider"),
                    "trainable": section.get("trainable"),
                }
            )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _markdown_pairs(payload: dict[str, Any]) -> list[str]:
    if not payload:
        return []
    rows = ["| Field | Value |", "| --- | --- |"]
    for key, value in payload.items():
        rows.append(f"| {key} | {_cell(value)} |")
    return rows


def _markdown_table(columns: list[dict], rows: list[dict]) -> list[str]:
    names = [c["name"] for c in columns] if columns else list(rows[0]) if rows else []
    if not names:
        return ["_no rows_"]
    out = ["| " + " | ".join(names) + " |", "| " + " | ".join("---" for _ in names) + " |"]
    for row in rows:
        out.append("| " + " | ".join(_cell(row.get(name)) for name in names) + " |")
    return out


def _cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (dict, list)):
        return f"`{json.dumps(value, ensure_ascii=False)}`"
    return str(value).replace("|", "\\|")


def render_html(rendered: dict[str, Any]) -> str:
    """Self-contained HTML, so an exported report opens anywhere."""
    from html import escape

    body = [f"<h1>{escape(rendered['name'])}</h1>"]
    if rendered.get("description"):
        body.append(f"<p class='lead'>{escape(rendered['description'])}</p>")
    body.append(f"<p class='meta'>Generated {escape(rendered['generated_at'])}</p>")

    for section in rendered["sections"]:
        if section.get("title"):
            body.append(f"<h2>{escape(section['title'])}</h2>")
        if section.get("error"):
            body.append(f"<p class='error'>{escape(section['error'])}</p>")
            continue
        kind = section["kind"]
        if kind == "text":
            for paragraph in (section.get("body") or "").split("\n\n"):
                body.append(f"<p>{escape(paragraph)}</p>")
        elif kind == "table":
            body.append(_html_table(section.get("columns", []), section.get("rows", [])))
        elif kind in ("metrics", "execution", "result", "model"):
            payload = {
                k: v for k, v in section.items() if k not in ("kind", "title", "options")
            }
            body.append(_html_pairs(payload))
        elif kind == "chart":
            body.append(
                f"<p class='meta'>Chart {escape(str(section.get('visualization_id')))}</p>"
            )

    return _HTML_TEMPLATE.format(title=escape(rendered["name"]), body="\n".join(body))


def _html_pairs(payload: dict[str, Any]) -> str:
    from html import escape

    rows = "".join(
        f"<tr><th>{escape(str(k))}</th><td>{escape(_cell(v))}</td></tr>"
        for k, v in payload.items()
    )
    return f"<table>{rows}</table>"


def _html_table(columns: list[dict], rows: list[dict]) -> str:
    from html import escape

    names = [c["name"] for c in columns] if columns else list(rows[0]) if rows else []
    if not names:
        return "<p class='meta'>no rows</p>"
    head = "".join(f"<th>{escape(n)}</th>" for n in names)
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(_cell(row.get(n)))}</td>" for n in names) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
          max-width: 62rem; margin: 2rem auto; padding: 0 1.5rem; line-height: 1.55;
          color: #1c2530; }}
  h1 {{ font-size: 1.6rem; }}
  h2 {{ font-size: 1.15rem; margin-top: 2rem; border-bottom: 1px solid #dde3ea;
        padding-bottom: .3rem; }}
  table {{ border-collapse: collapse; width: 100%; margin: .6rem 0 1.2rem;
           font-size: .88rem; }}
  th, td {{ border: 1px solid #dde3ea; padding: .35rem .6rem; text-align: left; }}
  th {{ background: #f4f7fa; font-weight: 600; }}
  .meta {{ color: #6b7885; font-size: .85rem; }}
  .lead {{ color: #46525f; }}
  .error {{ color: #b3453b; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""
