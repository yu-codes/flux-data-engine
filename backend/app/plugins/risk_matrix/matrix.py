"""A risk matrix, as a pure function over one record.

Likelihood times consequence is the oldest decision aid in engineering, and
its whole value is that the grid is *written down*: two people reading the same
cell reach the same answer, and an auditor can see which cell was read. The
moment it becomes an expression with weights in it, it stops being a matrix and
starts being an opinion.

Three things this keeps honest:

* **The grid is data.** Rows are likelihood levels, columns are consequence
  levels, and the cell is the answer. Nothing is computed from the level
  *names*, so a five-by-four matrix in somebody else's vocabulary works
  without code.
* **A level comes from a band or from a column, never from a guess.** A number
  becomes a level through declared thresholds; a level already in the data is
  taken as it is, and one that is not in the declared set is refused rather
  than mapped to the nearest thing.
* **The cell that was read is reported.** The output carries the two levels and
  the cell, so "why is this HIGH" is answered by pointing at the grid.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.shared.errors import ValidationError


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Axis:
    """One side of the matrix: its levels, and how a record lands on one."""

    name: str
    levels: tuple[str, ...]
    column: str
    #  Ascending boundaries mapping a number onto the levels. `len(bands)` is
    #  `len(levels) - 1`: a value at or below bands[i] is levels[i].
    bands: tuple[float, ...] = ()
    default: str | None = None

    def level_of(self, record: dict[str, Any]) -> tuple[str | None, str]:
        """The level this record sits at, and how that was decided."""
        value = record.get(self.column)
        if self.bands:
            number = _number(value)
            if number is None:
                return self.default, f"{self.column} has no reading"
            for index, edge in enumerate(self.bands):
                if number <= edge:
                    return self.levels[index], f"{self.column} = {number:g} ≤ {edge:g}"
            return self.levels[-1], f"{self.column} = {number:g} above every band"
        if value is None or str(value).strip() == "":
            return self.default, f"{self.column} is empty"
        label = str(value).strip()
        if label not in self.levels:
            #  Refused rather than coerced: a level nobody declared is a
            #  configuration fault, and rounding it to the nearest declared one
            #  would hide the fault behind a plausible answer.
            raise ValidationError(
                f"'{label}' is not a {self.name} level",
                details={"allowed": list(self.levels), "column": self.column},
            )
        return label, f"{self.column} = {label}"


@dataclass
class RiskMatrix:
    """Two axes and the grid that reads them."""

    likelihood: Axis
    consequence: Axis
    #  grid[likelihood index][consequence index]
    grid: tuple[tuple[str, ...], ...]
    #  Ordered worst-last, so a caller can compare two outcomes without
    #  knowing the vocabulary.
    severity_order: tuple[str, ...] = ()

    def assess(self, record: dict[str, Any]) -> dict[str, Any]:
        likelihood, why_l = self.likelihood.level_of(record)
        consequence, why_c = self.consequence.level_of(record)
        if likelihood is None or consequence is None:
            return {
                "risk_level": None,
                "likelihood": likelihood,
                "consequence": consequence,
                "rank": None,
                "basis": "; ".join(part for part in (why_l, why_c) if part),
                "explanation": "one axis could not be read, so no cell applies",
            }
        row = self.likelihood.levels.index(likelihood)
        column = self.consequence.levels.index(consequence)
        level = self.grid[row][column]
        order = self.severity_order or _distinct(self.grid)
        return {
            "risk_level": level,
            "likelihood": likelihood,
            "consequence": consequence,
            "rank": order.index(level) + 1 if level in order else None,
            "basis": f"{why_l}; {why_c}",
            "explanation": (
                f"{likelihood} likelihood against {consequence} consequence "
                f"reads {level}"
            ),
        }


def _distinct(grid: tuple[tuple[str, ...], ...]) -> list[str]:
    """Every level the grid can produce, in first-appearance order."""
    seen: dict[str, None] = {}
    for row in grid:
        for cell in row:
            seen.setdefault(cell, None)
    return list(seen)


def _axis_from(config: dict[str, Any], name: str) -> Axis:
    raw = config.get(name) or {}
    if not isinstance(raw, dict):
        raise ValidationError(f"'{name}' must be an object")
    column = raw.get("column")
    if not column:
        raise ValidationError(f"'{name}' must name the column it reads")
    levels = tuple(str(level) for level in (raw.get("levels") or []))
    if len(levels) < 2:
        raise ValidationError(f"'{name}' needs at least two levels")
    bands_raw = raw.get("bands") or []
    bands = tuple(
        float(b) for b in bands_raw if _number(b) is not None
    ) if bands_raw else ()
    if bands and len(bands) != len(levels) - 1:
        raise ValidationError(
            f"'{name}' has {len(levels)} levels and so needs "
            f"{len(levels) - 1} band boundaries, not {len(bands)}"
        )
    if bands and list(bands) != sorted(bands):
        raise ValidationError(f"'{name}' bands must ascend")
    default = raw.get("default")
    if default is not None and str(default) not in levels:
        raise ValidationError(
            f"'{name}' default '{default}' is not one of its levels"
        )
    return Axis(
        name=name,
        levels=levels,
        column=str(column),
        bands=bands,
        default=None if default is None else str(default),
    )


def matrix_from_config(config: dict[str, Any]) -> RiskMatrix:
    """Build a RiskMatrix from a plain configuration mapping."""
    likelihood = _axis_from(config, "likelihood")
    consequence = _axis_from(config, "consequence")
    raw_grid = config.get("grid")
    if not isinstance(raw_grid, list) or not raw_grid:
        raise ValidationError("a risk matrix needs a 'grid'")
    grid = tuple(
        tuple(str(cell) for cell in row) for row in raw_grid if isinstance(row, list)
    )
    if len(grid) != len(likelihood.levels):
        raise ValidationError(
            f"the grid has {len(grid)} rows but there are "
            f"{len(likelihood.levels)} likelihood levels"
        )
    widths = {len(row) for row in grid}
    if widths != {len(consequence.levels)}:
        raise ValidationError(
            f"every grid row must have {len(consequence.levels)} cells, "
            f"one per consequence level"
        )
    order = tuple(str(level) for level in (config.get("severity_order") or ()))
    if order:
        unknown = sorted(set(_distinct(grid)) - set(order))
        if unknown:
            raise ValidationError(
                f"severity_order does not mention {unknown}",
                details={"declared": list(order)},
            )
    return RiskMatrix(
        likelihood=likelihood, consequence=consequence, grid=grid, severity_order=order
    )
