# plugins/interface.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.plugins.manifest import PluginManifest


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
    manifest: PluginManifest | None = None

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

    def prepare_enable(self) -> None:
        """Prepare plugin-owned persistence before configuration validation.

        Flask-AAS calls this only during an explicit enable operation. Normal
        startup does not call it for disabled plugins. Implementations may use
        this hook to install or verify plugin-owned schema needed by
        ``validate_config()``. The default implementation does nothing.
        """

        return None

    def get_cli(self) -> Any | None:
        """Return the plugin-owned Click command group, if one is provided.

        Plugin commands remain owned by the plugin package. Flask-AAS may
        explicitly dispatch to this surface through its generic plugin
        management CLI without registering application-specific commands on
        the host CLI itself.
        """

        return None

    @abstractmethod
    def register(self, app: Any) -> None:
        """Register structural Flask surfaces during application startup.

        This method must be safe when ``validate_config()`` reports that the
        plugin is not yet configured. Flask-AAS may install the plugin's route
        structure while host-level request gating keeps those application
        surfaces unavailable until configuration becomes valid. Runtime work
        that requires credentials or other completed configuration must not be
        started here.
        """


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

    manifest = getattr(plugin, "manifest", None)
    if manifest is not None:
        if not isinstance(manifest, PluginManifest):
            raise PluginContractError("manifest must be a PluginManifest or None")

        manifest_values = {
            "plugin_id": manifest.plugin_id,
            "name": manifest.name,
            "version": manifest.version,
            "api_version": manifest.api_version,
        }
        for field_name, manifest_value in manifest_values.items():
            if getattr(plugin, field_name, None) != manifest_value:
                raise PluginContractError(
                    f"Plugin {field_name} must match plugin.toml"
                )

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
