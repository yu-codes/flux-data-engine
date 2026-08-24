"""What the lineage walk needs from everything below it.

This module owns no tables, so its ports are not repositories - they are the
reads it makes of other modules. Declaring them is worth more here than
elsewhere: a graph that walks six modules by duck typing is a graph that breaks
whenever any of the six renames a method, and the failure surfaces as a missing
branch in a picture rather than as an error.

Narrow on purpose. Each protocol lists the calls this module actually makes,
so the blast radius of a change downstream is readable from here.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SourceReader(Protocol):
    def get(self, source_id: str) -> Any: ...


@runtime_checkable
class DatasetReader(Protocol):
    def get(self, dataset_id: str) -> Any: ...
    def get_version(self, version_id: str) -> Any: ...
    def list_versions(self, dataset_id: str) -> list[Any]: ...


@runtime_checkable
class ModelReader(Protocol):
    def get(self, model_id: str) -> Any: ...


@runtime_checkable
class PipelineReader(Protocol):
    def get(self, pipeline_id: str) -> Any: ...


@runtime_checkable
class ExecutionReader(Protocol):
    def get(self, execution_id: str) -> Any: ...


@runtime_checkable
class ResultReader(Protocol):
    def get(self, result_id: str) -> Any: ...


@runtime_checkable
class VisualizationReader(Protocol):
    def get(self, visualization_id: str) -> Any: ...
    def list(self) -> list[Any]: ...


@runtime_checkable
class DashboardReader(Protocol):
    def get(self, dashboard_id: str) -> Any: ...
    def list(self) -> list[Any]: ...
