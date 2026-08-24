"""Answering "where did this come from" and "what depends on this".

Every fact this needs is already stored: a dataset version records the source
or the execution that produced it, an execution records the model and the
version it read, a result records its execution, a chart records what it was
built from. What was missing was the walk - the rows knew, and nothing could
be asked.

Derived rather than stored, deliberately. An edge table would have to be
written alongside those rows, which means two places that can disagree about
the same fact; and the first time they disagreed, the lineage graph would be
the one nobody trusts. Walking costs a few queries per hop, which is the right
price for a question asked by a person looking at a page.
"""

from __future__ import annotations

import logging
from typing import Any

from app.shared.errors import NotFoundError, ValidationError

from ..domain.entities import LineageEdge, LineageGraph, LineageNode, NodeKind, NodeRef
from ..domain.ports import (
    DashboardReader,
    DatasetReader,
    ExecutionReader,
    ModelReader,
    PipelineReader,
    ResultReader,
    SourceReader,
    VisualizationReader,
)

logger = logging.getLogger(__name__)

#  One edge to draw, and the node the walk continues to.
Step = tuple[LineageEdge, NodeRef]

UPSTREAM = "up"
DOWNSTREAM = "down"
MAX_DEPTH = 8


class LineageService:
    """Reads across modules, writes nothing."""

    def __init__(
        self,
        *,
        sources: SourceReader,
        datasets: DatasetReader,
        models: ModelReader,
        pipelines: PipelineReader,
        executions: ExecutionReader,
        results: ResultReader,
        visualizations: VisualizationReader,
        dashboards: DashboardReader,
    ):
        self.sources = sources
        self.datasets = datasets
        self.models = models
        self.pipelines = pipelines
        self.executions = executions
        self.results = results
        self.visualizations = visualizations
        self.dashboards = dashboards

    # -- the walk ----------------------------------------------------------
    def graph(
        self,
        kind: str,
        node_id: str,
        *,
        direction: str = UPSTREAM,
        depth: int = 4,
    ) -> LineageGraph:
        """The graph around one node, followed `depth` hops in one direction."""
        if direction not in (UPSTREAM, DOWNSTREAM):
            raise ValidationError(
                f"direction must be '{UPSTREAM}' or '{DOWNSTREAM}'",
                details={"given": direction},
            )
        try:
            root = NodeRef(NodeKind(kind), node_id)
        except ValueError as exc:
            raise ValidationError(
                f"'{kind}' is not something the platform tracks lineage for",
                details={"kinds": [k.value for k in NodeKind]},
            ) from exc

        depth = max(1, min(int(depth), MAX_DEPTH))
        graph = LineageGraph(root=root, direction=direction)

        #  Breadth first, so a shallow depth returns the neighbourhood a reader
        #  can actually take in rather than one long thread of it.
        seen: set[str] = set()
        frontier = [root]
        node = self._describe(root)
        if node is None:
            raise NotFoundError(f"no {kind} '{node_id}'")
        graph.nodes.append(node)
        seen.add(root.key())

        for _ in range(depth):
            following: list[NodeRef] = []
            for ref in frontier:
                for edge, other in self._edges(ref, direction):
                    if edge not in graph.edges:
                        graph.edges.append(edge)
                    if other.key() in seen:
                        continue
                    described = self._describe(other)
                    if described is None:
                        #  A deleted row: the edge that named it is still true,
                        #  and saying so beats hiding the gap.
                        described = LineageNode(
                            ref=other, label="(deleted)", detail={"missing": True}
                        )
                    graph.nodes.append(described)
                    seen.add(other.key())
                    following.append(other)
            if not following:
                break
            frontier = following
        else:
            graph.truncated = bool(frontier)

        return graph

    # -- edges -------------------------------------------------------------
    def _edges(self, ref: NodeRef, direction: str) -> list[Step]:
        """The edges to draw from here, each paired with where to walk next.

        The two are not the same question, and conflating them was wrong. An
        arrow always points the way the data flowed - a version populates its
        dataset, so the arrow is version -> dataset whichever way the reader is
        asking. But "which versions does this dataset have" is a useful step in
        *both* directions, because a dataset is a container and its versions
        are how anything reaches it. Deriving the next node from the arrow made
        containment followable in one direction only, which is why a dataset
        built by a pipeline appeared to have come from nowhere.
        """
        finder = {
            (NodeKind.SOURCE, DOWNSTREAM): self._source_downstream,
            (NodeKind.DATASET, UPSTREAM): self._dataset_upstream,
            (NodeKind.DATASET, DOWNSTREAM): self._dataset_downstream,
            (NodeKind.DATASET_VERSION, UPSTREAM): self._version_upstream,
            (NodeKind.DATASET_VERSION, DOWNSTREAM): self._version_downstream,
            (NodeKind.EXECUTION, UPSTREAM): self._execution_upstream,
            (NodeKind.EXECUTION, DOWNSTREAM): self._execution_downstream,
            (NodeKind.RESULT, UPSTREAM): self._result_upstream,
            (NodeKind.RESULT, DOWNSTREAM): self._result_downstream,
            (NodeKind.MODEL, DOWNSTREAM): self._model_downstream,
            (NodeKind.PIPELINE, UPSTREAM): self._pipeline_upstream,
            (NodeKind.VISUALIZATION, UPSTREAM): self._visualization_upstream,
            (NodeKind.VISUALIZATION, DOWNSTREAM): self._visualization_downstream,
            (NodeKind.DASHBOARD, UPSTREAM): self._dashboard_upstream,
        }.get((ref.kind, direction))
        if finder is None:
            return []
        try:
            return finder(ref.id)
        except Exception:  # noqa: BLE001 - a broken branch must not lose the graph
            logger.warning("lineage: could not follow %s", ref.key(), exc_info=True)
            return []

    def _source_downstream(self, source_id: str) -> list[Step]:
        here = NodeRef(NodeKind.SOURCE, source_id)
        return [
            (
                LineageEdge(here, NodeRef(NodeKind.DATASET, dataset.id), "read into"),
                NodeRef(NodeKind.DATASET, dataset.id),
            )
            for dataset in self.datasets.datasets.list()
            if dataset.source_id == source_id
        ]

    def _dataset_versions(self, dataset_id: str) -> list[Step]:
        """A dataset's versions, followable from either direction.

        Containment, not flow: the arrow points the way the data went, because
        a version populates its dataset, and the walk goes to the version
        either way, because a version is how anything reaches a dataset and how
        anything leaves one.
        """
        dataset = NodeRef(NodeKind.DATASET, dataset_id)
        steps: list[Step] = []
        for version in self.datasets.list_versions(dataset_id):
            ref = NodeRef(NodeKind.DATASET_VERSION, version.id)
            steps.append((LineageEdge(ref, dataset, "version of"), ref))
        return steps

    def _dataset_upstream(self, dataset_id: str) -> list[Step]:
        steps = self._dataset_versions(dataset_id)
        dataset = self.datasets.get(dataset_id)
        if dataset.source_id:
            source = NodeRef(NodeKind.SOURCE, dataset.source_id)
            here = NodeRef(NodeKind.DATASET, dataset_id)
            steps.append((LineageEdge(source, here, "read into"), source))
        return steps

    def _dataset_downstream(self, dataset_id: str) -> list[Step]:
        return self._dataset_versions(dataset_id)

    def _version_upstream(self, version_id: str) -> list[Step]:
        version = self.datasets.get_version(version_id)
        here = NodeRef(NodeKind.DATASET_VERSION, version_id)
        dataset = NodeRef(NodeKind.DATASET, version.dataset_id)
        steps: list[Step] = [(LineageEdge(here, dataset, "version of"), dataset)]

        #  The lineage dict is where "which execution produced this" was
        #  already being written; this is the reader it never had.
        lineage = version.lineage or {}
        execution_id = lineage.get("execution_id")
        if execution_id:
            ref = NodeRef(NodeKind.EXECUTION, execution_id)
            steps.append((LineageEdge(ref, here, "produced"), ref))
        source_id = lineage.get("source_id")
        if source_id:
            ref = NodeRef(NodeKind.SOURCE, source_id)
            steps.append((LineageEdge(ref, here, "read from"), ref))
        return steps

    def _version_downstream(self, version_id: str) -> list[Step]:
        here = NodeRef(NodeKind.DATASET_VERSION, version_id)
        steps: list[Step] = []
        for execution in self.executions.repository.list(
            dataset_version_id=version_id, limit=200
        ):
            ref = NodeRef(NodeKind.EXECUTION, execution.id)
            steps.append((LineageEdge(here, ref, "read by"), ref))
        for viz in self.visualizations.list():
            if viz.dataset_version_id == version_id:
                ref = NodeRef(NodeKind.VISUALIZATION, viz.id)
                steps.append((LineageEdge(here, ref, "charted by"), ref))
        return steps

    def _execution_upstream(self, execution_id: str) -> list[Step]:
        execution = self.executions.get(execution_id)
        here = NodeRef(NodeKind.EXECUTION, execution_id)
        steps: list[Step] = []
        if execution.dataset_version_id:
            ref = NodeRef(NodeKind.DATASET_VERSION, execution.dataset_version_id)
            steps.append((LineageEdge(ref, here, "read by"), ref))
        if execution.model_id:
            ref = NodeRef(NodeKind.MODEL, execution.model_id)
            steps.append((LineageEdge(ref, here, "ran"), ref))

        #  A pipeline step runs an inline definition, so it names no model and
        #  reads no stored version - its input was the previous step's table,
        #  which by design never became a Dataset. Without this hop the trail
        #  ends at the step, and the dataset a twelve-step pipeline produced
        #  looks like it came from nowhere. The pipeline was recorded in the
        #  execution's context all along.
        pipeline_id = (execution.context or {}).get("pipeline_id")
        if pipeline_id:
            ref = NodeRef(NodeKind.PIPELINE, pipeline_id)
            steps.append((LineageEdge(ref, here, "step of"), ref))
        return steps

    def _execution_downstream(self, execution_id: str) -> list[Step]:
        execution = self.executions.get(execution_id)
        here = NodeRef(NodeKind.EXECUTION, execution_id)
        if not execution.result_id:
            return []
        ref = NodeRef(NodeKind.RESULT, execution.result_id)
        return [(LineageEdge(here, ref, "produced"), ref)]

    def _result_upstream(self, result_id: str) -> list[Step]:
        result = self.results.get(result_id)
        ref = NodeRef(NodeKind.EXECUTION, result.execution_id)
        here = NodeRef(NodeKind.RESULT, result_id)
        return [(LineageEdge(ref, here, "produced"), ref)]

    def _result_downstream(self, result_id: str) -> list[Step]:
        result = self.results.get(result_id)
        here = NodeRef(NodeKind.RESULT, result_id)
        steps: list[Step] = []
        if result.dataset_version_id:
            ref = NodeRef(NodeKind.DATASET_VERSION, result.dataset_version_id)
            steps.append((LineageEdge(here, ref, "materialised as"), ref))
        for viz in self.visualizations.list():
            if viz.result_id == result_id:
                ref = NodeRef(NodeKind.VISUALIZATION, viz.id)
                steps.append((LineageEdge(here, ref, "charted by"), ref))
        return steps

    def _model_downstream(self, model_id: str) -> list[Step]:
        here = NodeRef(NodeKind.MODEL, model_id)
        steps: list[Step] = []
        for execution in self.executions.repository.list(model_id=model_id, limit=200):
            ref = NodeRef(NodeKind.EXECUTION, execution.id)
            steps.append((LineageEdge(here, ref, "ran"), ref))
        return steps

    def _pipeline_upstream(self, pipeline_id: str) -> list[Step]:
        """What a pipeline reads: the dataset it starts from."""
        pipeline = self.pipelines.get(pipeline_id)
        if not pipeline.input_dataset_id:
            return []
        ref = NodeRef(NodeKind.DATASET, pipeline.input_dataset_id)
        here = NodeRef(NodeKind.PIPELINE, pipeline_id)
        return [(LineageEdge(ref, here, "read by"), ref)]

    def _visualization_upstream(self, viz_id: str) -> list[Step]:
        viz = self.visualizations.get(viz_id)
        here = NodeRef(NodeKind.VISUALIZATION, viz_id)
        steps: list[Step] = []
        if viz.dataset_version_id:
            ref = NodeRef(NodeKind.DATASET_VERSION, viz.dataset_version_id)
            steps.append((LineageEdge(ref, here, "charted by"), ref))
        elif viz.dataset_id:
            #  A chart bound to the dataset rather than to one of its versions
            #  still came from somewhere.
            ref = NodeRef(NodeKind.DATASET, viz.dataset_id)
            steps.append((LineageEdge(ref, here, "charted by"), ref))
        if viz.result_id:
            ref = NodeRef(NodeKind.RESULT, viz.result_id)
            steps.append((LineageEdge(ref, here, "charted by"), ref))
        return steps

    def _visualization_downstream(self, viz_id: str) -> list[Step]:
        here = NodeRef(NodeKind.VISUALIZATION, viz_id)
        steps: list[Step] = []
        for dashboard in self.dashboards.list():
            if any(tile.visualization_id == viz_id for tile in dashboard.tiles):
                ref = NodeRef(NodeKind.DASHBOARD, dashboard.id)
                steps.append((LineageEdge(here, ref, "shown on"), ref))
        return steps

    def _dashboard_upstream(self, dashboard_id: str) -> list[Step]:
        dashboard = self.dashboards.get(dashboard_id)
        here = NodeRef(NodeKind.DASHBOARD, dashboard_id)
        steps: list[Step] = []
        for tile in dashboard.tiles:
            ref = NodeRef(NodeKind.VISUALIZATION, tile.visualization_id)
            steps.append((LineageEdge(ref, here, "shown on"), ref))
        return steps

    # -- labels ------------------------------------------------------------
    def _describe(self, ref: NodeRef) -> LineageNode | None:
        try:
            return self._describers()[ref.kind](ref.id)
        except NotFoundError:
            return None
        except Exception:  # noqa: BLE001 - an unnameable node is still a node
            logger.warning("lineage: could not describe %s", ref.key(), exc_info=True)
            return LineageNode(ref=ref, label=ref.id)

    def _describers(self) -> dict[NodeKind, Any]:
        return {
            NodeKind.SOURCE: self._source_node,
            NodeKind.DATASET: self._dataset_node,
            NodeKind.DATASET_VERSION: self._version_node,
            NodeKind.MODEL: self._model_node,
            NodeKind.PIPELINE: self._pipeline_node,
            NodeKind.EXECUTION: self._execution_node,
            NodeKind.RESULT: self._result_node,
            NodeKind.VISUALIZATION: self._visualization_node,
            NodeKind.DASHBOARD: self._dashboard_node,
        }

    def _source_node(self, source_id: str) -> LineageNode:
        source = self.sources.get(source_id)
        return LineageNode(
            ref=NodeRef(NodeKind.SOURCE, source_id),
            label=source.name,
            detail={"type": source.type.value},
        )

    def _dataset_node(self, dataset_id: str) -> LineageNode:
        dataset = self.datasets.get(dataset_id)
        return LineageNode(
            ref=NodeRef(NodeKind.DATASET, dataset_id),
            label=dataset.name,
            detail={"origin": dataset.origin.value},
        )

    def _version_node(self, version_id: str) -> LineageNode:
        version = self.datasets.get_version(version_id)
        return LineageNode(
            ref=NodeRef(NodeKind.DATASET_VERSION, version_id),
            label=f"v{version.version}",
            detail={"rows": version.row_count, "dataset_id": version.dataset_id},
        )

    def _model_node(self, model_id: str) -> LineageNode:
        model = self.models.get(model_id)
        return LineageNode(
            ref=NodeRef(NodeKind.MODEL, model_id),
            label=model.name,
            detail={"provider": model.provider},
        )

    def _pipeline_node(self, pipeline_id: str) -> LineageNode:
        pipeline = self.pipelines.get(pipeline_id)
        return LineageNode(
            ref=NodeRef(NodeKind.PIPELINE, pipeline_id),
            label=pipeline.name,
            detail={"steps": len(pipeline.steps)},
        )

    def _execution_node(self, execution_id: str) -> LineageNode:
        execution = self.executions.get(execution_id)
        return LineageNode(
            ref=NodeRef(NodeKind.EXECUTION, execution_id),
            label=execution.kind.value,
            detail={
                "status": execution.status.value,
                "created_at": execution.created_at.isoformat(),
            },
        )

    def _result_node(self, result_id: str) -> LineageNode:
        result = self.results.get(result_id)
        return LineageNode(
            ref=NodeRef(NodeKind.RESULT, result_id),
            label=result.kind.value,
            detail={"row_count": result.row_count},
        )

    def _visualization_node(self, viz_id: str) -> LineageNode:
        viz = self.visualizations.get(viz_id)
        return LineageNode(
            ref=NodeRef(NodeKind.VISUALIZATION, viz_id),
            label=viz.name,
            detail={"chart_type": viz.spec.chart_type.value},
        )

    def _dashboard_node(self, dashboard_id: str) -> LineageNode:
        dashboard = self.dashboards.get(dashboard_id)
        return LineageNode(
            ref=NodeRef(NodeKind.DASHBOARD, dashboard_id),
            label=dashboard.name,
            detail={"tiles": len(dashboard.tiles)},
        )
