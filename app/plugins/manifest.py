# plugins/manifiest.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib


class PluginManifestError(ValueError):
    """Raised when plugin.toml does not satisfy the static manifest contract."""


@dataclass(frozen=True)
class PluginManifest:
    """Package-owned metadata that may be inspected without importing plugin code."""

    plugin_id: str
    name: str
    version: str
    api_version: int
    entrypoint: str
    migrations: str | None
    path: Path

    @property
    def table_prefix(self) -> str:
        """Database table namespace reserved for this plugin."""

        return f"plugin_{self.plugin_id}_"

    @property
    def version_table(self) -> str:
        """Independent Alembic version table for this plugin."""

        return f"{self.table_prefix}alembic_version"

    @property
    def migration_path(self) -> Path | None:
        """Absolute path to the plugin-owned Alembic environment, if declared."""

        if self.migrations is None:
            return None
        return (self.path.parent / self.migrations).resolve()


def _nonempty_string(value: object, field_name: str, *, path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PluginManifestError(
            f"{path}: plugin.{field_name} must be a non-empty string"
        )
    return value.strip()


def _plugin_id(value: object, *, path: Path) -> str:
    plugin_id = _nonempty_string(value, "id", path=path)
    if plugin_id != value or re.fullmatch(r"[a-z0-9][a-z0-9_-]*", plugin_id) is None:
        raise PluginManifestError(
            f"{path}: plugin.id may contain only lowercase letters, digits, "
            "underscores, and hyphens"
        )
    return plugin_id


def _entrypoint(value: object, *, path: Path) -> str:
    entrypoint = _nonempty_string(value, "entrypoint", path=path)
    module_name, separator, attribute_name = entrypoint.partition(":")
    valid_module = bool(module_name) and all(
        part.isidentifier() for part in module_name.split(".")
    )
    if (
        not separator
        or not valid_module
        or not attribute_name.isidentifier()
        or ":" in attribute_name
        or module_name != module_name.strip()
        or attribute_name != attribute_name.strip()
    ):
        raise PluginManifestError(
            f"{path}: plugin.entrypoint must use the form "
            "'package.module:attribute'"
        )
    return entrypoint


def _migration_directory(value: object, *, path: Path) -> str | None:
    if value is None:
        return None

    migrations = _nonempty_string(value, "migrations", path=path)
    relative_path = Path(migrations)
    if (
        relative_path.is_absolute()
        or migrations != relative_path.as_posix()
        or any(part in {"", ".", ".."} for part in relative_path.parts)
    ):
        raise PluginManifestError(
            f"{path}: plugin.migrations must be a relative plugin-package path"
        )

    resolved = (path.parent / relative_path).resolve()
    try:
        resolved.relative_to(path.parent.resolve())
    except ValueError as exc:
        raise PluginManifestError(
            f"{path}: plugin.migrations must remain inside the plugin package"
        ) from exc

    return relative_path.as_posix()


def load_plugin_manifest(path: str | Path) -> PluginManifest:
    """Load and validate a plugin.toml file without importing plugin Python code."""

    manifest_path = Path(path).resolve()
    try:
        with manifest_path.open("rb") as manifest_file:
            payload = tomllib.load(manifest_file)
    except FileNotFoundError as exc:
        raise PluginManifestError(
            f"Plugin manifest does not exist: {manifest_path}"
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise PluginManifestError(
            f"Invalid TOML in plugin manifest {manifest_path}: {exc}"
        ) from exc

    plugin_section = payload.get("plugin")
    if not isinstance(plugin_section, dict):
        raise PluginManifestError(
            f"{manifest_path}: required [plugin] table is missing"
        )

    plugin_id = _plugin_id(plugin_section.get("id"), path=manifest_path)
    name = _nonempty_string(plugin_section.get("name"), "name", path=manifest_path)
    version = _nonempty_string(
        plugin_section.get("version"),
        "version",
        path=manifest_path,
    )

    api_version = plugin_section.get("api_version")
    if isinstance(api_version, bool) or not isinstance(api_version, int):
        raise PluginManifestError(
            f"{manifest_path}: plugin.api_version must be an integer"
        )
    if api_version < 1:
        raise PluginManifestError(
            f"{manifest_path}: plugin.api_version must be greater than zero"
        )

    entrypoint = _entrypoint(plugin_section.get("entrypoint"), path=manifest_path)
    migrations = _migration_directory(
        plugin_section.get("migrations"),
        path=manifest_path,
    )

    return PluginManifest(
        plugin_id=plugin_id,
        name=name,
        version=version,
        api_version=api_version,
        entrypoint=entrypoint,
        migrations=migrations,
        path=manifest_path,
    )
