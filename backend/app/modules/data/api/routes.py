"""Data API. Routes stay thin: parse, delegate, serialise."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, Query, UploadFile, status

from app.api.deps import DatasetServiceDep, SourceServiceDep
from app.api.security import CurrentUser, authorize_source_type

from ..domain.entities import Source
from ..infrastructure.readers import supported_source_types
from .schemas import (
    DatasetCreate,
    DatasetDetailOut,
    DatasetOut,
    DatasetVersionOut,
    PreviewOut,
    SchemaOut,
    SourceCreate,
    SourceOut,
)

router = APIRouter(tags=["data"])

def _source_out(source: Source) -> SourceOut:
    return SourceOut(
        id=source.id,
        name=source.name,
        type=source.type.value,
        connection=source.redacted_connection(),
        description=source.description,
        created_at=source.created_at,
           project_id=source.project_id,
    )


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------
@router.get("/sources/types", summary="Supported external source types")
def list_source_types() -> dict:
    return {"types": supported_source_types()}


@router.get("/sources", response_model=list[SourceOut])
def list_sources(service: SourceServiceDep):
    return [_source_out(s) for s in service.list()]


@router.post("/sources", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
def create_source(payload: SourceCreate, service: SourceServiceDep, user: CurrentUser):
    authorize_source_type(user, payload.type)
    source = service.create(
        name=payload.name,
        source_type=payload.type,
        connection=payload.connection,
        description=payload.description,
    )
    return _source_out(source)


@router.get("/sources/{source_id}", response_model=SourceOut)
def get_source(source_id: str, service: SourceServiceDep):
    return _source_out(service.get(source_id))


@router.get("/sources/{source_id}/preview", response_model=PreviewOut)
def preview_source(
    source_id: str, service: SourceServiceDep, limit: int = Query(50, ge=1, le=500)
):
    return PreviewOut(**service.preview(source_id, limit=limit))


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: str, service: SourceServiceDep) -> None:
    service.delete(source_id)


# --------------------------------------------------------------------------
# datasets
# --------------------------------------------------------------------------
@router.get(
    "/datasets",
    response_model=list[DatasetOut],
    summary="Datasets; pipeline intermediates are excluded by default",
)
def list_datasets(
    service: DatasetServiceDep,
    search: str | None = Query(None, max_length=200),
    include: str = Query(
        "curated",
        pattern="^(curated|intermediate|all)$",
        description=(
            "'curated' is what a person ingested, uploaded or shipped; "
            "'intermediate' is what an execution produced along the way."
        ),
    ),
):
    """Default to the datasets somebody meant to create.

    Every pipeline step materialises its output, so a twelve-step pipeline adds
    twelve datasets. They are real datasets — versioned, previewable, chartable
    — but they are working state, and listing them beside the curated ones makes
    the collection unreadable. They stay one query parameter away.
    """
    filters: dict = {}
    if search:
        filters["search"] = search
    if include == "curated":
        filters["origins"] = ["source", "upload", "builtin", "execution"]
    elif include == "intermediate":
        filters["origins"] = ["intermediate"]
    return [DatasetOut(**_dataset_dict(d)) for d in service.list(**filters)]


@router.post(
    "/datasets", response_model=DatasetDetailOut, status_code=status.HTTP_201_CREATED
)
def create_dataset(payload: DatasetCreate, service: DatasetServiceDep):
    dataset, _ = service.create_from_source(
        source_id=payload.source_id,
        name=payload.name,
        description=payload.description,
        options=payload.options,
        tags=payload.tags,
    )
    return _dataset_detail(service, dataset.id)


@router.post(
    "/datasets/upload",
    response_model=DatasetDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a file and register it as a source plus a dataset",
)
def upload_dataset(
    sources: SourceServiceDep,
    datasets: DatasetServiceDep,
    file: UploadFile = File(...),
    name: str = Form(...),
    description: str = Form(""),
):
    source = sources.create_from_upload(
        stream=file.file, filename=file.filename or "", name=name
    )
    dataset, _ = datasets.create_from_source(
        source_id=source.id, name=name, description=description
    )
    return _dataset_detail(datasets, dataset.id)


@router.get("/datasets/{dataset_id}", response_model=DatasetDetailOut)
def get_dataset(dataset_id: str, service: DatasetServiceDep):
    return _dataset_detail(service, dataset_id)


@router.get("/datasets/{dataset_id}/versions", response_model=list[DatasetVersionOut])
def list_dataset_versions(dataset_id: str, service: DatasetServiceDep):
    return [DatasetVersionOut(**v.__dict__) for v in service.list_versions(dataset_id)]


@router.post("/datasets/{dataset_id}/refresh", response_model=DatasetVersionOut)
def refresh_dataset(dataset_id: str, service: DatasetServiceDep):
    version = service.refresh(dataset_id)
    return DatasetVersionOut(**version.__dict__)


@router.get("/datasets/{dataset_id}/preview", response_model=PreviewOut)
def preview_dataset(
    dataset_id: str,
    service: DatasetServiceDep,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    version = service.current_version(dataset_id)
    return PreviewOut(**service.preview(version.id, limit=limit, offset=offset))


@router.delete("/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dataset(dataset_id: str, service: DatasetServiceDep) -> None:
    service.delete(dataset_id)


@router.get("/dataset-versions/{version_id}/preview", response_model=PreviewOut)
def preview_version(
    version_id: str,
    service: DatasetServiceDep,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return PreviewOut(**service.preview(version_id, limit=limit, offset=offset))


# --------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------
@router.get("/schemas/{schema_id}", response_model=SchemaOut)
def get_schema(schema_id: str, service: DatasetServiceDep):
    schema = service.get_schema(schema_id)
    return SchemaOut(
        id=schema.id,
        name=schema.name,
        description=schema.description,
        fields=[f.to_dict() for f in schema.fields],
        created_at=schema.created_at,
    )


@router.get("/schemas", response_model=list[SchemaOut])
def list_schemas(service: DatasetServiceDep):
    return [
        SchemaOut(
            id=s.id,
            name=s.name,
            description=s.description,
            fields=[f.to_dict() for f in s.fields],
            created_at=s.created_at,
        )
        for s in service.schemas.list()
    ]


# --------------------------------------------------------------------------
# serialisation helpers
# --------------------------------------------------------------------------
def _dataset_dict(dataset) -> dict:
    return {
        "id": dataset.id,
        "name": dataset.name,
        "origin": dataset.origin.value,
        "source_id": dataset.source_id,
        "description": dataset.description,
        "tags": dataset.tags,
        "current_version_id": dataset.current_version_id,
        "created_at": dataset.created_at,
        "updated_at": dataset.updated_at,
        "project_id": dataset.project_id,
    }


def _dataset_detail(service, dataset_id: str) -> DatasetDetailOut:
    dataset = service.get(dataset_id)
    versions = service.list_versions(dataset_id)
    fields: list[dict] = []
    if dataset.current_version_id:
        schema = service.schema_of_version(dataset.current_version_id)
        fields = [f.to_dict() for f in schema.fields] if schema else []
    return DatasetDetailOut(
        **_dataset_dict(dataset),
        versions=[DatasetVersionOut(**v.__dict__) for v in versions],
        schema_fields=fields,
    )
