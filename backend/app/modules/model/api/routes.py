"""Model API: the model library, versions, experiments and evaluation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, status
from pydantic import Field

from app.api.deps import (
    ModelServiceDep,
    ProjectServiceDep,
    RegistryDep,
)
from app.api.schema_base import ApiModel

from ..domain.entities import ModelDefinition, ModelType, ModelVersion

router = APIRouter(tags=["models"])


# --------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------
class ModelCreate(ApiModel):
    name: str = Field(min_length=1, max_length=255)
    provider: str
    description: str = ""
    type: str | None = None
    runtime: str | None = None
    configuration: dict[str, Any] = Field(default_factory=dict)
    input_contract: dict | None = None
    parameter_contract: dict | None = None
    output_contract: dict | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelStatusIn(ApiModel):
    status: str = Field(pattern="^(active|deprecated)$")


class ModelUpdate(ApiModel):
    description: str | None = None
    tags: list[str] | None = None
    configuration: dict[str, Any] | None = None
    input_contract: dict | None = None
    parameter_contract: dict | None = None
    output_contract: dict | None = None


class ModelCapabilities(ApiModel):
    """What a model can do, so a client never has to ask what it *is*.

    A UI that switches on `type` acquires a branch per category and needs a new
    one for every category added. Capabilities are the same question asked in a
    way that stays finite: an interface renders what a model supports.
    """

    executable: bool
    execution_kinds: list[str]
    trainable: bool
    versionable: bool
    configurable: bool
    #  A contract the provider validates itself, rather than one that names
    #  fields — an image, a tensor or a file is described this way.
    open_input: bool
    open_output: bool


class ModelOut(ApiModel):
    id: str
    name: str
    slug: str
    description: str
    type: str
    status: str
    #  Computed, not stored: the working definition versus what actually runs.
    has_unpublished_changes: bool
    capabilities: ModelCapabilities
    provider: str
    runtime: str
    trainable: bool
    tags: list[str]
    configuration: dict[str, Any]
    input_contract: dict
    parameter_contract: dict
    output_contract: dict
    metadata: dict[str, Any]
    current_version_id: str | None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    #  Where this is filed. Null means shared: it shows under every project
    #  rather than none, which is what the library relies on.
    project_id: str | None = None


class ModelVersionOut(ApiModel):
    id: str
    model_id: str
    version: int
    parameters: dict[str, Any]
    metrics: dict[str, Any]
    artifact_uri: str | None
    created_by_execution_id: str | None
    notes: str
    created_at: datetime


# --------------------------------------------------------------------------
# providers (the model factory's catalogue)
# --------------------------------------------------------------------------
@router.get("/model-providers", summary="Registered model providers")
def list_providers(registry: RegistryDep):
    return {"providers": [d.to_dict() for d in registry.descriptors()]}


@router.get(
    "/transforms",
    summary="The standard transform vocabulary, with each one's parameters",
)
def list_transforms():
    """What a pipeline step can be built from.

    The UI reads this to render a parameter form per transform, so composing a
    pipeline never means hand-writing a JSON configuration.
    """
    from app.plugins.python_function import library

    return {"transforms": library.catalogue()}


@router.get("/model-types", summary="Model type catalogue")
def list_model_types(registry: RegistryDep):
    by_type: dict[str, list[dict]] = {t.value: [] for t in ModelType}
    for descriptor in registry.descriptors():
        by_type[descriptor.model_type.value].append(
            {"key": descriptor.key, "name": descriptor.name,
             "trainable": descriptor.trainable}
        )
    return {"types": [{"type": key, "providers": value} for key, value in by_type.items()]}


# --------------------------------------------------------------------------
# models
# --------------------------------------------------------------------------
@router.get(
    "/models",
    response_model=list[ModelOut],
    summary="The model library; pipeline-step models are excluded by default",
)
def list_models(
    service: ModelServiceDep,
    model_type: str | None = Query(None),
    provider: str | None = Query(None),
    search: str | None = Query(None, max_length=200),
    include_deprecated: bool = Query(
        False, description="deprecated models still execute; they are just not offered"
    ),
):
    """Default to the library.

    A twelve-step pipeline owns twelve models. They are real models and they
    execute through the same path, but listing them here buries the ones
    somebody actually curated, so the collection a person browses is the
    library and the rest are asked for explicitly.
    """
    filters: dict[str, str] = {}
    if model_type:
        filters["model_type"] = model_type
    if provider:
        filters["provider"] = provider
    if search:
        filters["search"] = search
    if not include_deprecated:
        filters["status"] = "active"
    return [_model_out(m) for m in service.list(**filters)]


@router.post("/models", response_model=ModelOut, status_code=status.HTTP_201_CREATED)
def create_model(payload: ModelCreate, service: ModelServiceDep):
    model = service.create(
        name=payload.name,
        provider=payload.provider,
        description=payload.description,
        model_type=payload.type,
        runtime=payload.runtime,
        configuration=payload.configuration,
        input_contract=payload.input_contract,
        parameter_contract=payload.parameter_contract,
        output_contract=payload.output_contract,
        tags=payload.tags,
        metadata=payload.metadata,
    )
    return _model_out(model)


@router.get("/models/{model_id}", response_model=ModelOut)
def get_model(model_id: str, service: ModelServiceDep):
    model = service.get(model_id)
    #  Only on the detail view: computing drift needs the current version, and
    #  a list of two hundred models should not fetch two hundred of them.
    return _model_out(model, drifted=service.has_unpublished_changes(model))


@router.patch("/models/{model_id}", response_model=ModelOut)
def update_model(model_id: str, payload: ModelUpdate, service: ModelServiceDep):
    model = service.update(model_id, payload.model_dump(exclude_unset=True))
    return _model_out(model, drifted=service.has_unpublished_changes(model))


class ModelFileIn(ApiModel):
    #  Null is a real value here, not an omission: it shares the definition
    #  across every project. Which is why this is its own endpoint rather than
    #  a field on ModelUpdate, where null means "leave it alone".
    project_id: str | None = None


@router.post(
    "/models/{model_id}/project",
    response_model=ModelOut,
    summary="File this definition under a project, or share it across all",
)
def file_model(
    model_id: str,
    payload: ModelFileIn,
    service: ModelServiceDep,
    projects: ProjectServiceDep,
):
    """Where a definition is filed, changed on purpose.

    A model definition is the one thing on the platform worth reusing across
    pieces of work — a scorecard or a threshold rule is about arithmetic, not
    about the fleet — so it can be shared. Its runs and results stay where the
    work happened, and are not moved by this.
    """
    #  Validated here rather than in the service: the service would otherwise
    #  need the project repository, and "does this project exist" is the same
    #  question the API answers everywhere else.
    if payload.project_id is not None:
        projects.get(payload.project_id)
    model = service.file_under(model_id, payload.project_id)
    return _model_out(model, drifted=service.has_unpublished_changes(model))


@router.post(
    "/models/{model_id}/status",
    response_model=ModelOut,
    summary="Offer this model for new work, or stop offering it",
)
def set_model_status(model_id: str, payload: ModelStatusIn, service: ModelServiceDep):
    """Deprecating hides a model from pickers without breaking anything.

    Executions that already ran keep their results, and a version somebody
    pinned still executes — which is exactly why this exists instead of delete.
    """
    model = service.set_status(model_id, payload.status)
    return _model_out(model, drifted=service.has_unpublished_changes(model))


@router.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model(model_id: str, service: ModelServiceDep) -> None:
    service.delete(model_id)


@router.get("/models/{model_id}/validate")
def validate_model(model_id: str, service: ModelServiceDep):
    return service.validate_definition(model_id).to_dict()


@router.get("/models/{model_id}/versions", response_model=list[ModelVersionOut])
def list_model_versions(model_id: str, service: ModelServiceDep):
    return [_version_out(v) for v in service.list_versions(model_id)]


@router.post(
    "/models/{model_id}/versions",
    response_model=ModelVersionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Freeze the current definition as a new immutable version",
)
def publish_model_version(
    model_id: str, service: ModelServiceDep, notes: str = Query("")
):
    return _version_out(service.publish_version(model_id, notes=notes))


def _capabilities(model: ModelDefinition) -> ModelCapabilities:
    """Read the provider's descriptor, which is the only thing that knows."""
    from app.modules.model.domain.registry import registry as plugin_registry

    try:
        descriptor = plugin_registry.get(model.provider).describe()
    except Exception:  # noqa: BLE001 - a missing plugin must not break a listing
        return ModelCapabilities(
            executable=False,
            execution_kinds=[],
            trainable=False,
            versionable=True,
            configurable=bool(model.configuration),
            open_input=False,
            open_output=False,
        )
    return ModelCapabilities(
        executable=bool(descriptor.supported_kinds),
        execution_kinds=[kind.value for kind in descriptor.supported_kinds],
        trainable=descriptor.trainable,
        versionable=True,
        configurable=bool(descriptor.configuration_contract.fields),
        open_input=_is_open(model.input_contract),
        open_output=_is_open(model.output_contract),
    )


def _is_open(contract) -> bool:
    """Whether the provider validates this payload rather than a field list.

    Two things mean the same to a reader: a FREE shape, and a contract that
    declares no fields — neither can check anything, so in both cases the
    provider is what decides. The detail page already said "this contract is
    open" for the second; the capability now agrees instead of contradicting it
    two cards further up.
    """
    from app.shared.contracts import ContractShape as Shape

    return contract.shape is Shape.FREE or not contract.fields


def _model_out(model: ModelDefinition, *, drifted: bool = False) -> ModelOut:
    return ModelOut(
        id=model.id,
        name=model.name,
        slug=model.slug,
        description=model.description,
        type=model.type.value,
        status=model.status.value,
        has_unpublished_changes=drifted,
        capabilities=_capabilities(model),
        provider=model.provider,
        runtime=model.runtime.value,
        trainable=model.is_trainable,
        tags=model.tags,
        configuration=model.configuration,
        input_contract=model.input_contract.to_dict(),
        parameter_contract=model.parameter_contract.to_dict(),
        output_contract=model.output_contract.to_dict(),
        metadata=model.metadata,
        current_version_id=model.current_version_id,
        created_by=model.created_by,
        created_at=model.created_at,
        updated_at=model.updated_at,
        project_id=model.project_id,
    )


def _version_out(version: ModelVersion) -> ModelVersionOut:
    return ModelVersionOut(
        id=version.id,
        model_id=version.model_id,
        version=version.version,
        parameters=version.parameters,
        metrics=version.metrics,
        artifact_uri=version.artifact_uri,
        created_by_execution_id=version.created_by_execution_id,
        notes=version.notes,
        created_at=version.created_at,
    )
