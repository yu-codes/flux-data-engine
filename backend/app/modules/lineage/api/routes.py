"""Lineage as a graph a page can draw.

Two questions, one endpoint: `direction=up` answers "where did this come
from", `direction=down` answers "what depends on this". Both were unanswerable
before, despite every fact needed being on rows the platform already had.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import LineageServiceDep

router = APIRouter(tags=["lineage"])


@router.get(
    "/lineage/{kind}/{node_id}",
    summary="Where something came from, or what depends on it",
)
def lineage(
    kind: str,
    node_id: str,
    service: LineageServiceDep,
    direction: str = Query("up", pattern="^(up|down)$"),
    depth: int = Query(4, ge=1, le=8),
) -> dict[str, Any]:
    return service.graph(kind, node_id, direction=direction, depth=depth).to_dict()


@router.get("/lineage-kinds", summary="What lineage can be traced from")
def kinds() -> dict[str, list[str]]:
    from ..domain.entities import NodeKind

    return {"kinds": [kind.value for kind in NodeKind]}
