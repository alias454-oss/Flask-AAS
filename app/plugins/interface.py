"""Supported Flask-AAS application plugin contract."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


PLUGIN_API_VERSION = 1


class PluginContractError(ValueError):
    """Raised when a plugin does not satisfy the supported host contract."""


class PluginCompatibilityError(PluginContractError):
    """Raised when a plugin targets an unsupported Plugin API generation."""


@dataclass(frozen=True)
class PluginConfiguration:
    """Current plugin configuration viability.

    ``configured`` is derived by the plugin from its current configuration. It
    is not an administrator-controlled lifecycle switch. ``reason`` must be a
    non-secret operator-safe explanation suitable for an administrative UI.
    """

    configured: bool
    reason: str | None = None


class ApplicationPlugin(ABC):
    """Plugin API v1 interface implemented by Flask-AAS application plugins."""

    plugin_id: str
    name: str
    version: str
    api_version: int

    @abstractmethod
    def validate_config(self) -> PluginConfiguration:
        """Return whether the plugin's current configuration is viable."""

    @abstractmethod
    def clear_secrets(self) -> None:
        """Remove plugin-managed persisted secrets when the plugin is disabled.

        Deployment-owned environment values, provider identities, and external
        secret-manager material are outside this method's ownership boundary.
        Implementations must not clear ordinary plugin configuration or business
        data here.
        """

    @abstractmethod
    def register(self, app: Any) -> None:
        """Register runtime Flask surfaces during application startup."""


def validate_plugin_contract(plugin: ApplicationPlugin) -> ApplicationPlugin:
    """Validate Plugin API v1 identity and compatibility metadata."""

    if not isinstance(plugin, ApplicationPlugin):
        raise PluginContractError(
            "Plugin must implement the Flask-AAS ApplicationPlugin interface"
        )

    plugin_id = getattr(plugin, "plugin_id", None)
    if not isinstance(plugin_id, str) or not plugin_id.strip():
        raise PluginContractError("plugin_id must be a non-empty string")
    if plugin_id != plugin_id.strip() or not all(
        character.islower() or character.isdigit() or character in {"_", "-"}
        for character in plugin_id
    ):
        raise PluginContractError(
            "plugin_id may contain only lowercase letters, digits, underscores, and hyphens"
        )

    for field_name in ("name", "version"):
        value = getattr(plugin, field_name, None)
        if not isinstance(value, str) or not value.strip():
            raise PluginContractError(f"{field_name} must be a non-empty string")

    api_version = getattr(plugin, "api_version", None)
    if not isinstance(api_version, int) or isinstance(api_version, bool):
        raise PluginContractError(
            "api_version must be declared as an integer"
        )
    if api_version != PLUGIN_API_VERSION:
        raise PluginCompatibilityError(
            f"Plugin {plugin_id!r} requires Plugin API v{api_version}; "
            f"this host supports v{PLUGIN_API_VERSION}"
        )


    return plugin
