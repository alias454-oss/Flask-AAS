# plugins/interface.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
import re
from typing import Any

from app.plugins.manifest import PluginManifest


PLUGIN_API_VERSION = 1
_DATASET_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")


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

    ``admin_endpoint`` is an optional plugin-owned GET endpoint for completing
    required configuration. Flask-AAS may expose it from the Applications page
    only after runtime registration proves that the endpoint belongs to the
    declaring plugin. Dataset/setup state is deliberately separate from this
    readiness contract.
    """

    configured: bool
    reason: str | None = None
    admin_endpoint: str | None = None


@dataclass(frozen=True)
class PluginDataset:
    """One optional plugin-owned server dataset exposed to host administrators.

    Flask-AAS renders these values but does not interpret dataset meaning or use
    dataset state to determine plugin readiness. ``action_label=None`` exposes
    status only; otherwise the host may dispatch the dataset key back through
    ``run_admin_dataset_action()`` from its own protected POST endpoint.
    """

    key: str
    label: str
    description: str | None = None
    status: str | None = None
    action_label: str | None = None


@dataclass(frozen=True)
class PluginDatasetActionResult:
    """Operator-safe result returned by a plugin-owned dataset action."""

    message: str


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
        """Perform optional trusted setup after plugin schema is current.

        Flask-AAS calls this only during an explicit enable operation and only
        after any declared plugin migration history is current. Implementations
        must not create, migrate, or stamp plugin-owned schema here; schema
        changes belong to the plugin-owned migration CLI.
        """

        return None

    def get_cli(self) -> Any | None:
        """Return optional plugin-owned Click commands.

        Plugin commands remain owned by the plugin package. Flask-AAS may
        explicitly dispatch to this surface through its generic plugin
        management CLI without registering application-specific commands on
        the host CLI itself. If ``plugin.toml`` declares migrations, the host
        automatically composes the reserved top-level ``db`` group into this
        command surface; plugins must not reimplement migration lifecycle
        commands themselves. A plugin with migrations does not need to provide
        any custom CLI solely to receive the host-owned ``db`` commands.
        """

        return None

    def get_admin_datasets(self) -> tuple[PluginDataset, ...]:
        """Return optional server datasets for the host Applications page.

        The plugin owns dataset discovery, status, labels, and semantics. The
        host treats the returned descriptors as non-blocking administration
        metadata and must not infer application readiness from them.
        """

        return ()

    def run_admin_dataset_action(self, dataset_key: str) -> PluginDatasetActionResult:
        """Run one declared dataset action inside the caller-owned transaction.

        Implementations must not commit or roll back the database transaction.
        Flask-AAS owns authorization, CSRF/rate limiting, generic auditing, and
        the final transaction boundary for web-dispatched dataset actions.
        """

        raise PluginContractError(
            f"Plugin {self.plugin_id!r} does not implement dataset action {dataset_key!r}"
        )

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


def _bounded_text(
    value: object,
    *,
    field_name: str,
    maximum: int,
    optional: bool = False,
) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        expected = "a string or None" if optional else "a string"
        raise PluginContractError(f"{field_name} must be {expected}")
    if not value.strip():
        raise PluginContractError(f"{field_name} must not be empty")
    if len(value) > maximum:
        raise PluginContractError(f"{field_name} exceeds {maximum} characters")
    return value


def validate_plugin_datasets(plugin: ApplicationPlugin) -> tuple[PluginDataset, ...]:
    """Validate one plugin's optional host-admin dataset descriptors."""

    datasets = plugin.get_admin_datasets()
    if not isinstance(datasets, tuple):
        raise PluginContractError("get_admin_datasets() must return a tuple")

    validated: list[PluginDataset] = []
    seen_keys: set[str] = set()
    for dataset in datasets:
        if not isinstance(dataset, PluginDataset):
            raise PluginContractError(
                "get_admin_datasets() entries must be PluginDataset instances"
            )
        if not isinstance(dataset.key, str) or not _DATASET_KEY_RE.fullmatch(dataset.key):
            raise PluginContractError(
                "PluginDataset.key must contain only lowercase letters, digits, underscores, "
                "and hyphens and be at most 80 characters"
            )
        if dataset.key in seen_keys:
            raise PluginContractError(f"Duplicate plugin dataset key {dataset.key!r}")
        seen_keys.add(dataset.key)

        _bounded_text(dataset.label, field_name="PluginDataset.label", maximum=120)
        _bounded_text(
            dataset.description,
            field_name="PluginDataset.description",
            maximum=500,
            optional=True,
        )
        _bounded_text(
            dataset.status,
            field_name="PluginDataset.status",
            maximum=300,
            optional=True,
        )
        _bounded_text(
            dataset.action_label,
            field_name="PluginDataset.action_label",
            maximum=120,
            optional=True,
        )
        validated.append(dataset)

    return tuple(validated)


def validate_dataset_action_result(result: object) -> PluginDatasetActionResult:
    """Validate one operator-safe plugin dataset action result."""

    if not isinstance(result, PluginDatasetActionResult):
        raise PluginContractError(
            "run_admin_dataset_action() must return PluginDatasetActionResult"
        )
    _bounded_text(
        result.message,
        field_name="PluginDatasetActionResult.message",
        maximum=500,
    )
    return result


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
