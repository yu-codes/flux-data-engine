"""Turning rows into evidence, and evidence into an argument.

A language model reasoning about equipment, credit or claims must not be shown
raw measurements and asked what it thinks — that is the arrangement in which it
invents readings, because a plausible reading is exactly what its training
rewards. What it is good at is the step *after* the analysis: taking findings
that already exist, ordering them, resolving the ones that disagree and saying
what follows.

So this module does two things and neither of them is generation:

* **`bundle`** turns the rows of an evidence table into numbered items, each
  carrying its own source columns. The numbering is the contract: everything
  the model says must point at one, which makes an unsupported claim
  detectable rather than merely discouraged.
* **`compose`** writes the argument from the evidence directly — no model
  involved. It exists because a platform that can only explain its conclusions
  while a network service is reachable cannot be relied on to explain them,
  and because it is the ground truth the model's answer is checked against.

`compose` is deliberately plain. It restates the evidence in severity order
and stops. It cannot be wrong about the facts because it contains none of its
own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

CITATION = re.compile(r"\[E(\d+)\]")


@dataclass
class EvidenceItem:
    """One finding, numbered so it can be cited."""

    ref: str
    statement: str
    severity: float = 0.0
    category: str = ""
    fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "statement": self.statement,
            "severity": self.severity,
            "category": self.category,
            "fields": self.fields,
        }


def _text(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def bundle(
    rows: list[dict[str, Any]],
    *,
    statement_column: str | None,
    severity_column: str | None = None,
    category_column: str | None = None,
    columns: list[str] | None = None,
    limit: int = 40,
) -> list[EvidenceItem]:
    """Number the evidence, worst first, and cap it.

    The cap matters: a model handed four hundred findings summarises them, and
    a summary of evidence is a new claim. Forty is enough to reason over and
    few enough that every one can be checked by a person.
    """
    items: list[EvidenceItem] = []
    for row in rows:
        severity = row.get(severity_column) if severity_column else None
        try:
            weight = float(severity) if severity is not None else 0.0
        except (TypeError, ValueError):
            weight = 0.0
        if statement_column and row.get(statement_column):
            statement = str(row[statement_column])
        else:
            #  No prose column: state the row itself, which is still evidence
            #  and is still checkable.
            names = columns or [k for k in row if k != severity_column]
            statement = "; ".join(f"{name} = {_text(row.get(name))}" for name in names)
        items.append(
            EvidenceItem(
                ref="",
                statement=statement,
                severity=weight,
                category=str(row.get(category_column) or "") if category_column else "",
                fields={
                    name: row.get(name)
                    for name in (columns or list(row))
                    if name in row
                },
            )
        )

    items.sort(key=lambda item: -item.severity)
    kept = items[:limit]
    for index, item in enumerate(kept, start=1):
        item.ref = f"E{index}"
    return kept


def render(items: list[EvidenceItem]) -> str:
    """The evidence as the model will see it."""
    lines = []
    for item in items:
        prefix = f"[{item.ref}]"
        category = f" ({item.category})" if item.category else ""
        lines.append(f"{prefix}{category} {item.statement}")
    return "\n".join(lines)


def compose(
    items: list[EvidenceItem],
    *,
    subject: str,
    conclusion: str | None = None,
    language: str = "zh-TW",
) -> str:
    """The argument, written from the evidence and nothing else.

    Used when no language model is configured, and used as the check when one
    is: an answer that says less than this is not adding reasoning, and one
    that says more than the evidence supports is inventing.
    """
    if not items:
        return _phrase(language, "no_evidence", subject=subject)

    ordered = sorted(items, key=lambda item: -item.severity)
    strongest = ordered[: min(4, len(ordered))]
    body = "\n".join(f"- {item.statement} [{item.ref}]" for item in strongest)
    remaining = len(ordered) - len(strongest)

    parts = [_phrase(language, "opening", subject=subject, count=len(ordered)), body]
    if remaining > 0:
        parts.append(_phrase(language, "remaining", count=remaining))
    if conclusion:
        parts.append(
            _phrase(
                language,
                "conclusion",
                conclusion=conclusion,
                refs=", ".join(item.ref for item in strongest),
            )
        )
    return "\n\n".join(parts)


_PHRASES = {
    "zh-TW": {
        "no_evidence": "{subject}：目前沒有任何足以支持判斷的證據，維持觀察。",
        "opening": "{subject}：本次分析共取得 {count} 項證據，其中影響最大的是：",
        "remaining": "另有 {count} 項次要證據，未列於上方但已納入評分。",
        "conclusion": "綜合以上，判斷為「{conclusion}」，依據 {refs}。",
    },
    "en": {
        "no_evidence": "{subject}: no evidence supports a judgement; keep watching.",
        "opening": "{subject}: {count} findings, of which the strongest are:",
        "remaining": "A further {count} weaker findings were scored but are not listed.",
        "conclusion": "Taken together this reads as \"{conclusion}\", on {refs}.",
    },
}


def _phrase(language: str, key: str, **values: Any) -> str:
    table = _PHRASES.get(language) or _PHRASES["en"]
    return table[key].format(**values)


def citations(text: str) -> set[str]:
    """Every evidence reference the text points at."""
    return {f"E{number}" for number in CITATION.findall(text or "")}


def unsupported(text: str, items: list[EvidenceItem]) -> list[str]:
    """References the answer makes that no evidence backs.

    The one check worth running on a generated explanation: a citation to
    evidence that does not exist is invention with a footnote.
    """
    known = {item.ref for item in items}
    return sorted(citations(text) - known)
