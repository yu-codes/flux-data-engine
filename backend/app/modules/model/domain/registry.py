"""The model factory: a registry of providers keyed by plugin key.

The application layer never branches on model type. It asks the registry for
the provider named in the model definition and calls the plugin contract.
"""

from __future__ import annotations

from app.shared.errors import PluginError

from .entities import ModelType
from .plugin import ModelPlugin, PluginDescriptor, is_trainable


class PluginRegistry:
    """Holds every registered model provider for the process."""

    def __init__(self) -> None:
        self._plugins: dict[str, ModelPlugin] = {}

    def register(self, plugin: ModelPlugin) -> None:
        descriptor = plugin.describe()
        if descriptor.key in self._plugins:
            raise PluginError(f"plugin key '{descriptor.key}' is already registered")
        self._plugins[descriptor.key] = plugin

    def get(self, key: str) -> ModelPlugin:
        plugin = self._plugins.get(key)
        if plugin is None:
            raise PluginError(
                f"unknown model provider '{key}'",
                details={"available": sorted(self._plugins)},
            )
        return plugin

    def has(self, key: str) -> bool:
        return key in self._plugins

    def keys(self) -> list[str]:
        return sorted(self._plugins)

    def descriptors(self) -> list[PluginDescriptor]:
        return sorted(
            (p.describe() for p in self._plugins.values()), key=lambda d: d.name
        )

    def by_type(self, model_type: ModelType) -> list[PluginDescriptor]:
        return [d for d in self.descriptors() if d.model_type is model_type]

    def supports_training(self, key: str) -> bool:
        return is_trainable(self.get(key))


#  One registry per process; populated at startup by app.plugins.bootstrap.
registry = PluginRegistry()
