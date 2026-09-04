"""Composite scoring, as a pure function over one record.

A single number standing for many measurements is one of the oldest things a
model does — a credit score, a quality index, a condition score — and it is
also one of the easiest to build badly. Three failures are the reason this is
a component with rules rather than an expression somebody writes once:

* **A missing input silently becomes a good one.** Treating an absent
  measurement as zero, or as the middle of the range, produces a confident
  score computed from nothing. Here every component states what its absence
  means, and the score reports the share of weight it was actually able to
  use, so "72 out of 100" and "72, from two-thirds of the evidence" are
  different answers.
* **The contributions are not recoverable.** A weighted sum tells nobody which
  term moved it. Each component's sub-score and its share of the total come
  back beside the score, because the question after every score is "why".
* **The direction is assumed.** Some measurements are better high and some
  better low, and the same is true of the score itself. Both are declared.

Nothing here knows what is being scored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.shared.errors import ValidationError

#  What an absent measurement means for the component that needed it.
MISSING_POLICIES = ("skip", "neutral", "best", "worst")
SCALE_KINDS = ("linear", "bands", "boolean", "passthrough")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class Band:
    """A sub-score awarded when a value falls at or below `upto`.

    Bands are read in order and the first that fits wins, so an ordered list
    reads exactly like the threshold table an engineer already has on a wall:
    below 70 normal, 70-80 warning, 80-90 critical, above that emergency.
    """

    upto: float | None
    score: float
    label: str = ""

    def fits(self, value: float) -> bool:
        return self.upto is None or value <= self.upto


@dataclass(frozen=True)
class Component:
    """One measurement's contribution to a composite score."""

    name: str
    source: str
    weight: float = 1.0
    kind: str = "linear"
    #  linear: the value that scores 100, and the value that scores 0. Which
    #  of the two is larger is how "lower is better" is expressed - there is
    #  no separate direction flag to disagree with them.
    good: float | None = None
    bad: float | None = None
    bands: tuple[Band, ...] = ()
    true_score: float = 0.0
    false_score: float = 100.0
    missing: str = "skip"
    neutral_score: float = 60.0
    description: str = ""

    def sub_score(self, value: Any) -> tuple[float | None, str]:
        """This component's 0-100 reading, and the reason it is that.

        `None` means the component did not apply and its weight is not counted
        — which is different from scoring zero, and is the distinction that
        keeps a fleet of half-instrumented assets from all looking broken.
        """
        if value is None or (isinstance(value, str) and not value.strip()):
            return self._absent()
        if self.kind == "boolean":
            truthy = value if isinstance(value, bool) else str(value).strip().lower() in (
                "true", "1", "yes", "y", "t"
            )
            return (
                (self.true_score, "flag set")
                if truthy
                else (self.false_score, "flag clear")
            )
        number = _number(value)
        if number is None:
            return self._absent()
        if self.kind == "passthrough":
            return _clamp(number), f"{self.source} = {number:g}"
        if self.kind == "bands":
            for band in self.bands:
                if band.fits(number):
                    reason = band.label or f"{self.source} ≤ {band.upto:g}"
                    return _clamp(band.score), reason
            last = self.bands[-1] if self.bands else None
            return (_clamp(last.score) if last else 0.0), f"{self.source} beyond every band"
        #  linear
        if self.good is None or self.bad is None or self.good == self.bad:
            raise ValidationError(
                f"component '{self.name}' is linear and needs distinct 'good' and 'bad'"
            )
        span = self.bad - self.good
        fraction = (number - self.good) / span
        return _clamp(100.0 * (1.0 - fraction)), f"{self.source} = {number:g}"

    def _absent(self) -> tuple[float | None, str]:
        if self.missing == "skip":
            return None, "no reading"
        if self.missing == "best":
            return 100.0, "no reading, assumed sound"
        if self.missing == "worst":
            return 0.0, "no reading, assumed unsound"
        return self.neutral_score, "no reading, scored neutral"


@dataclass
class Scorecard:
    """Components, the scale they add up to, and how the total is labelled."""

    components: list[Component] = field(default_factory=list)
    bands: tuple[Band, ...] = ()
    #  A score computed from less evidence than this is not reported as a
    #  score. Silence is a usable answer; a confident number from one reading
    #  out of eight is not.
    min_coverage: float = 0.0

    def score(self, record: dict[str, Any]) -> dict[str, Any]:
        """The composite, its components, and how much of it was measured."""
        applied: list[dict[str, Any]] = []
        total_weight = 0.0
        used_weight = 0.0
        weighted = 0.0

        for component in self.components:
            total_weight += component.weight
            value = record.get(component.source)
            sub, reason = component.sub_score(value)
            entry = {
                "name": component.name,
                "source": component.source,
                "value": value,
                "weight": component.weight,
                "score": None if sub is None else round(sub, 2),
                "reason": reason,
                "description": component.description,
            }
            if sub is not None:
                used_weight += component.weight
                weighted += component.weight * sub
                entry["contribution"] = round(component.weight * sub, 4)
            applied.append(entry)

        coverage = 0.0 if total_weight <= 0 else used_weight / total_weight
        if used_weight <= 0 or coverage < self.min_coverage:
            return {
                "score": None,
                "band": None,
                "coverage": round(coverage, 4),
                "components": applied,
                "explanation": "not enough evidence to score",
            }

        total = weighted / used_weight
        #  Contributions are reported as shares of the score actually produced,
        #  so they sum to it rather than to a hypothetical full-coverage total.
        #  A score of exactly zero has no shares to report and is not an error:
        #  every component scored nothing, which is a real answer.
        for entry in applied:
            if entry.get("contribution") is not None:
                entry["share"] = (
                    round(entry["contribution"] / weighted, 4) if weighted else 0.0
                )
        return {
            "score": round(total, 2),
            "band": self._band(total),
            "coverage": round(coverage, 4),
            "components": applied,
            "explanation": self._explain(applied, total),
        }

    def _band(self, total: float) -> str | None:
        for band in self.bands:
            if band.fits(total):
                return band.label or str(band.score)
        return self.bands[-1].label if self.bands else None

    def _explain(self, applied: list[dict], total: float) -> str:
        """What actually moved the score, worst first.

        Naming the two weakest components is not a summary of the score; it is
        the part of it somebody can act on.
        """
        scored = [e for e in applied if e["score"] is not None]
        weakest = sorted(scored, key=lambda e: (e["score"], -e["weight"]))[:2]
        if not weakest:
            return "no component could be scored"
        parts = [f"{e['name']} {e['score']:.0f}/100 ({e['reason']})" for e in weakest]
        return f"score {total:.0f}; weakest: " + "; ".join(parts)


# --------------------------------------------------------------------------
# building one from configuration
# --------------------------------------------------------------------------
def _bands_from(raw: Any, *, what: str, scored: bool = True) -> tuple[Band, ...]:
    """Read a band list. `scored` says whether the band awards a value.

    A component's bands award a sub-score; the total's bands only name the
    range the score landed in. Requiring a score of both made a perfectly
    ordinary "50 / 80 / above" label list unreadable, which is how the
    provider came to reject its own published example.
    """
    if not raw:
        return ()
    if not isinstance(raw, list):
        raise ValidationError(f"{what} must be a list of bands")
    bands: list[Band] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValidationError(f"{what}: every band must be an object")
        upto = entry.get("upto")
        score = _number(entry.get("score"))
        if score is None:
            if scored:
                raise ValidationError(f"{what}: every band needs a numeric 'score'")
            #  A labelling band's "score" is only ever read back as a fallback
            #  label, so the boundary is the sensible stand-in.
            score = float(upto) if upto is not None else 0.0
        if not scored and not entry.get("label"):
            raise ValidationError(f"{what}: every band needs a 'label'")
        bands.append(
            Band(
                upto=None if upto is None else float(upto),
                score=float(score),
                label=str(entry.get("label") or ""),
            )
        )
    #  Ordered by boundary so the configuration may be written in any order and
    #  still mean "the first band that fits". An open band always sorts last.
    return tuple(
        sorted(bands, key=lambda b: (b.upto is None, b.upto if b.upto is not None else 0.0))
    )


def scorecard_from_config(config: dict[str, Any]) -> Scorecard:
    """Build a Scorecard from a plain configuration mapping."""
    raw_components = config.get("components") or []
    if not isinstance(raw_components, list) or not raw_components:
        raise ValidationError("a scorecard needs at least one component")

    components: list[Component] = []
    for entry in raw_components:
        if not isinstance(entry, dict):
            raise ValidationError("every component must be an object")
        source = entry.get("column") or entry.get("source")
        if not source:
            raise ValidationError("every component names the column it reads")
        kind = str(entry.get("kind", "linear"))
        if kind not in SCALE_KINDS:
            raise ValidationError(
                f"unknown component kind '{kind}'", details={"allowed": list(SCALE_KINDS)}
            )
        missing = str(entry.get("missing", "skip"))
        if missing not in MISSING_POLICIES:
            raise ValidationError(
                f"unknown missing policy '{missing}'",
                details={"allowed": list(MISSING_POLICIES)},
            )
        weight = _number(entry.get("weight", 1.0))
        if weight is None or weight <= 0:
            raise ValidationError(
                f"component '{source}' needs a positive weight"
            )
        components.append(
            Component(
                name=str(entry.get("name") or source),
                source=str(source),
                weight=float(weight),
                kind=kind,
                good=_number(entry.get("good")),
                bad=_number(entry.get("bad")),
                bands=_bands_from(entry.get("bands"), what=f"component '{source}'"),
                true_score=float(_number(entry.get("true_score", 0.0)) or 0.0),
                false_score=float(_number(entry.get("false_score", 100.0)) or 0.0),
                missing=missing,
                neutral_score=float(_number(entry.get("neutral_score", 60.0)) or 0.0),
                description=str(entry.get("description") or ""),
            )
        )
        if kind == "linear" and (components[-1].good is None or components[-1].bad is None):
            raise ValidationError(
                f"component '{source}' is linear and needs 'good' and 'bad' values"
            )
        if kind == "bands" and not components[-1].bands:
            raise ValidationError(f"component '{source}' is banded and declares no bands")

    coverage = _number(config.get("min_coverage", 0.0)) or 0.0
    return Scorecard(
        components=components,
        bands=_bands_from(config.get("bands"), what="score bands", scored=False),
        min_coverage=float(coverage),
    )
