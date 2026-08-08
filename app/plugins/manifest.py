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
    path: Path


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

    return PluginManifest(
        plugin_id=plugin_id,
        name=name,
        version=version,
        api_version=api_version,
        entrypoint=entrypoint,
        path=manifest_path,
    )
