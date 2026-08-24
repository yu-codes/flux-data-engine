"""Model application services.

No branching on model type happens here: the provider named in the definition
is resolved through the registry and the plugin contract does the rest.
"""

from __future__ import annotations

from typing import Any

from app.shared.contracts import Contract, ValidationResult
from app.shared.errors import ConflictError, NotFoundError, PluginError, ValidationError
from app.shared.ids import slugify

from ..domain.entities import (
    ModelDefinition,
    ModelStatus,
    ModelType,
    ModelVersion,
    RuntimeKind,
)
from ..domain.plugin import ExecutionKind
from ..domain.ports import ModelRepository
from ..domain.registry import PluginRegistry


class ModelService:
    def __init__(self, repository: ModelRepository, registry: PluginRegistry):
        self.repository = repository
        self.registry = registry

    # -- reads -------------------------------------------------------------
    def get(self, model_id: str) -> ModelDefinition:
        model = self.repository.get(model_id) or self.repository.get_by_slug(model_id)
        if not model:
            raise NotFoundError(f"model '{model_id}' not found")
        return model

    def list(self, **filters) -> list[ModelDefinition]:
        return self.repository.list(**filters)

    def list_versions(self, model_id: str) -> list[ModelVersion]:
        return self.repository.list_versions(self.get(model_id).id)

    def get_version(self, version_id: str) -> ModelVersion:
        version = self.repository.get_version(version_id)
        if not version:
            raise NotFoundError(f"model version '{version_id}' not found")
        return version

    def current_version(self, model_id: str) -> ModelVersion | None:
        model = self.get(model_id)
        return (
            self.repository.get_version(model.current_version_id)
            if model.current_version_id
            else None
        )

    def find_version(self, version_id: str | None) -> ModelVersion | None:
        """Version lookup that tolerates a missing id, for callers in other modules."""
        return self.repository.get_version(version_id) if version_id else None

    def providers(self) -> list[dict]:
        return [d.to_dict() for d in self.registry.descriptors()]

    # -- writes ------------------------------------------------------------
    def create(
        self,
        *,
        name: str,
        provider: str,
        description: str = "",
        model_type: str | None = None,
        runtime: str | None = None,
        configuration: dict[str, Any] | None = None,
        input_contract: dict | None = None,
        parameter_contract: dict | None = None,
        output_contract: dict | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ModelDefinition:
        if self.repository.get_by_name(name):
            raise ConflictError(f"a model named '{name}' already exists")
        plugin = self.registry.get(provider)
        descriptor = plugin.describe()

        definition = ModelDefinition(
            name=name,
            slug=self._unique_slug(name),
            provider=provider,
            type=ModelType(model_type) if model_type else descriptor.model_type,
            runtime=RuntimeKind(runtime) if runtime else descriptor.runtime,
            description=description or descriptor.description,
            configuration=configuration or {},
            #  A model may narrow its provider's contracts but inherits them by
            #  default, so every model always has all three.
            input_contract=Contract.from_dict(input_contract)
            if input_contract
            else descriptor.input_contract,
            parameter_contract=Contract.from_dict(parameter_contract)
            if parameter_contract
            else descriptor.parameter_contract,
            output_contract=Contract.from_dict(output_contract)
            if output_contract
            else descriptor.output_contract,
            tags=tags or [],
            metadata={
                **(metadata or {}),
                "trainable": descriptor.trainable,
                "supported_kinds": [k.value for k in descriptor.supported_kinds],
            },
        )

        validation = plugin.validate(definition)
        if not validation.valid:
            raise ValidationError(
                f"model definition rejected by provider '{provider}'",
                details=validation.to_dict(),
            )

        self.repository.add(definition)
        #  Every model starts at v1, so it is executable and reproducible at once.
        self.publish_version(definition.id, notes="initial version")
        return self.get(definition.id)

    def has_unpublished_changes(self, model: ModelDefinition) -> bool:
        """Whether the working definition differs from what actually executes.

        This is computed, never stored. A model's row is the definition you
        edit; its current version is the definition that runs. When they differ,
        pressing Run does not do what the screen appears to describe — so the
        difference has to be visible, and a flag somebody sets by hand would
        eventually disagree with the facts.
        """
        if not model.current_version_id:
            #  Nothing published: the working definition is all there is, and it
            #  is what an unpinned execution will run.
            return False
        try:
            version = self.get_version(model.current_version_id)
        except NotFoundError:
            return False
        if not version.definition_snapshot:
            return False
        return _behaviour(version.definition_snapshot) != _behaviour(_snapshot(model))

    def set_status(self, model_id: str, status: str) -> ModelDefinition:
        """Offer a model for new work, or stop offering it.

        Deprecating never breaks anything that already runs: existing executions
        and pinned versions keep working, which is the whole point of marking it
        rather than deleting it.
        """
        model = self.get(model_id)
        model.status = ModelStatus(status)
        return self.repository.update(model)

    def update(self, model_id: str, changes: dict[str, Any]) -> ModelDefinition:
        """Edit the working definition. Publishing is what makes it execute."""
        model = self.get(model_id)
        for key in ("description", "tags"):
            if key in changes and changes[key] is not None:
                setattr(model, key, changes[key])
        if changes.get("configuration") is not None:
            model.configuration = changes["configuration"]
        for key, contract_field in (
            ("input_contract", "input_contract"),
            ("parameter_contract", "parameter_contract"),
            ("output_contract", "output_contract"),
        ):
            if changes.get(key) is not None:
                setattr(model, contract_field, Contract.from_dict(changes[key]))

        plugin = self.registry.get(model.provider)
        validation = plugin.validate(model)
        if not validation.valid:
            raise ValidationError(
                "updated model definition is invalid", details=validation.to_dict()
            )
        return self.repository.update(model)

    def publish_version(
        self,
        model_id: str,
        *,
        parameters: dict[str, Any] | None = None,
        artifact_uri: str | None = None,
        metrics: dict[str, Any] | None = None,
        created_by_execution_id: str | None = None,
        notes: str = "",
    ) -> ModelVersion:
        """Freeze the current definition as the next immutable version."""
        model = self.get(model_id)
        number = self.repository.next_version_number(model.id)
        version = self.repository.add_version(
            ModelVersion(
                model_id=model.id,
                version=number,
                definition_snapshot=_snapshot(model),
                parameters=parameters or dict(model.configuration),
                artifact_uri=artifact_uri,
                metrics=metrics or {},
                created_by_execution_id=created_by_execution_id,
                notes=notes,
            )
        )
        model.current_version_id = version.id
        self.repository.update(model)
        return version

    def validate_definition(self, model_id: str) -> ValidationResult:
        model = self.get(model_id)
        return self.registry.get(model.provider).validate(model)

    def supported_kinds(self, model_id: str) -> list[ExecutionKind]:
        """What this model can be asked to do, according to its provider.

        Only this module may ask the registry, so callers above - a page
        listing an application's tools, for one - ask here instead of reaching
        down for it. A provider that is no longer registered answers "nothing"
        rather than raising: a model whose plugin was removed is a tool that
        cannot run, not a page that cannot render.
        """
        model = self.get(model_id)
        try:
            return list(self.registry.get(model.provider).describe().supported_kinds)
        except PluginError:
            return []

    def delete(self, model_id: str) -> None:
        self.repository.delete(self.get(model_id).id)

    # -- internals ---------------------------------------------------------
    def _unique_slug(self, name: str) -> str:
        base = slugify(name)
        slug = base
        suffix = 2
        while self.repository.get_by_slug(slug):
            slug = f"{base}-{suffix}"
            suffix += 1
        return slug


#  What a version records, versus what makes two versions behave differently.
#  A version stores the whole definition — it is worth knowing what a model was
#  called last March — but renaming one does not change a single result, so
#  drift is measured on the fields that decide the answer.
_BEHAVIOURAL = (
    "provider",
    "runtime",
    "type",
    "input_contract",
    "parameter_contract",
    "output_contract",
    "configuration",
)


def _behaviour(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {key: snapshot.get(key) for key in _BEHAVIOURAL}


def _snapshot(model: ModelDefinition) -> dict[str, Any]:
    return {
        "name": model.name,
        "slug": model.slug,
        "type": model.type.value,
        "provider": model.provider,
        "runtime": model.runtime.value,
        "description": model.description,
        "input_contract": model.input_contract.to_dict(),
        "parameter_contract": model.parameter_contract.to_dict(),
        "output_contract": model.output_contract.to_dict(),
        "configuration": model.configuration,
        "metadata": model.metadata,
    }
