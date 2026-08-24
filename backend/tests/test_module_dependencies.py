"""Modules may only depend on modules below them.

The architecture the project documents is a stack: data is ingested, models are
defined over it, executions run them, results record what happened, and
orchestration wires executions together. Dependencies point one way down that
stack.

They had stopped. `Pipeline` lived in `data` while depending on
ExecutionService and ResultService, so `data -> execution` was a real edge
pointing back up - invisible in review because both ends were files somebody
had a reason to touch. This test makes that edge fail rather than accumulate.

Adding a module means adding it to LAYERS, deliberately, at the level where it
belongs. If a new dependency does not fit, the fix is almost never to move the
module up a level.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

#  Lowest first. A module may import from its own level and anything below it.
#
#  `applications` sits at the bottom despite being the end of the product
#  narrative, because structurally it only holds lists of ids - it names things
#  without needing to know how any of them work. That is worth keeping: the
#  moment it reaches upward, publishing has become entangled with running.
LAYERS: list[tuple[str, ...]] = [
    ("platform", "data", "applications", "jobs"),
                                            # users and audit; sources and
                                            # datasets; packaged bundles;
                                            # background work whose kinds are
                                            # injected, never imported
    ("model", "results"),                   # definitions over data; what a
                                            # run produced
    ("execution", "analysis"),              # running one model; charts over
                                            # data and results
    ("orchestration", "reporting", "evaluation", "lineage"),
                                            # pipelines and schedules;
                                            # documents across everything;
                                            # experiments and their scores;
                                            # and the graph that reads every
                                            # layer below to answer where a
                                            # number came from
]

MODULES = Path(__file__).resolve().parents[1] / "app" / "modules"


def _level(module: str) -> int | None:
    for index, names in enumerate(LAYERS):
        if module in names:
            return index
    return None


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if parts[:2] == ["app", "modules"] and len(parts) > 2:
                found.add(parts[2])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[:2] == ["app", "modules"] and len(parts) > 2:
                    found.add(parts[2])
    return found


def test_every_module_is_placed_in_the_stack():
    """A module nobody has placed is a module nobody has thought about."""
    present = {
        d.name
        for d in MODULES.iterdir()
        if d.is_dir() and not d.name.startswith("_")
    }
    placed = {name for level in LAYERS for name in level}
    assert present == placed, (
        f"modules missing from the dependency stack: {sorted(present - placed)}; "
        f"listed but absent: {sorted(placed - present)}"
    )


@pytest.mark.parametrize("module", sorted({n for level in LAYERS for n in level}))
def test_a_module_never_imports_from_above_itself(module):
    directory = MODULES / module
    if not directory.exists():
        pytest.skip(f"{module} is not present")

    own = _level(module)
    offenders = []
    for path in sorted(directory.rglob("*.py")):
        for imported in _imported_modules(path):
            if imported == module:
                continue
            other = _level(imported)
            if other is None or other > own:
                offenders.append(
                    f"{path.relative_to(MODULES).as_posix()} -> {imported}"
                )
    assert not offenders, (
        f"'{module}' imports from a module above it in the stack: {offenders}"
    )


def test_data_does_not_know_how_to_run_a_model():
    """The specific edge that had gone wrong, named so a regression is obvious."""
    offenders = []
    for path in sorted((MODULES / "data").rglob("*.py")):
        imported = _imported_modules(path)
        for upward in ("execution", "results", "orchestration", "analysis"):
            if upward in imported:
                offenders.append(f"{path.name} -> {upward}")
    assert not offenders, f"data reaches upward: {offenders}"


def test_pipelines_live_in_orchestration():
    """Where the pipeline code is, is the whole point of the move."""
    assert (MODULES / "orchestration" / "application" / "services.py").exists()
    assert not (MODULES / "data" / "application" / "pipelines.py").exists()
    assert not (MODULES / "data" / "domain" / "pipelines.py").exists()
