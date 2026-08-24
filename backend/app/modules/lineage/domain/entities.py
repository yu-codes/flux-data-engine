"""What a lineage graph is made of.

Deliberately small: a node is a kind and an id with enough label to render,
and an edge says one thing produced or fed another. Everything here is derived
from rows that already exist - no lineage is written anywhere by this module,
because a second copy of a fact is a second thing that can be wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeKind(str, Enum):
    SOURCE = "source"
    DATASET = "dataset"
    DATASET_VERSION = "dataset_version"
    MODEL = "model"
    PIPELINE = "pipeline"
    EXECUTION = "execution"
    RESULT = "result"
    VISUALIZATION = "visualization"
    DASHBOARD = "dashboard"


@dataclass(frozen=True)
class NodeRef:
    """A node's identity: what kind of thing, and which one."""

    kind: NodeKind
    id: str

    def key(self) -> str:
        return f"{self.kind.value}:{self.id}"


@dataclass
class LineageNode:
    ref: NodeRef
    label: str
    #  Whatever the page needs to say more than the name - a version number, a
    #  status, a row count. Kept open because each kind has different answers.
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.ref.key(),
            "kind": self.ref.kind.value,
            "id": self.ref.id,
            "label": self.label,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class LineageEdge:
    """`source` produced or fed `target`.

    Direction is always "the way the data flowed", whichever way the question
    was asked. A reader tracing upstream still wants arrows pointing at the
    thing they started from.
    """

    source: NodeRef
    target: NodeRef
    relation: str

    def to_dict(self) -> dict[str, str]:
        return {
            "from": self.source.key(),
            "to": self.target.key(),
            "relation": self.relation,
        }


@dataclass
class LineageGraph:
    root: NodeRef
    direction: str
    nodes: list[LineageNode] = field(default_factory=list)
    edges: list[LineageEdge] = field(default_factory=list)
    #  True when the walk stopped at the depth limit rather than at the end of
    #  the graph, so a reader knows there is more rather than assuming there
    #  is not.
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root.key(),
            "direction": self.direction,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "truncated": self.truncated,
        }
