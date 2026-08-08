# plugins/bundled.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.plugins.manifest import PluginManifest, load_plugin_manifest


@dataclass(frozen=True)
class BundledPluginRegistration:
    """Deployment-trusted bundled plugin backed by static package metadata."""

    manifest: PluginManifest

    @property
    def plugin_id(self) -> str:
        return self.manifest.plugin_id

    @property
    def import_path(self) -> str:
        return self.manifest.entrypoint

    @property
    def model_modules(self) -> tuple[str, ...]:
        """Return the conventional model module for explicit setup tooling.

        This remains a temporary bridge until plugin-owned Alembic migration
        environments replace direct model-table preparation.
        """

        module_name, _, _ = self.import_path.partition(":")
        package_name, separator, _ = module_name.rpartition(".")
        if not separator:
            return ()
        return (f"{package_name}.models",)


def _bundled_manifest(relative_path: str) -> PluginManifest:
    """Read trusted bundled metadata without importing the plugin package."""

    return load_plugin_manifest(
        Path(__file__).resolve().parent / relative_path
    )


BUNDLED_PLUGIN_REGISTRATIONS = (
    BundledPluginRegistration(
        manifest=_bundled_manifest("example/plugin.toml"),
    ),
)


def bundled_plugin_registrations() -> tuple[BundledPluginRegistration, ...]:
    """Return bundled registrations without importing plugin runtime code."""

    return BUNDLED_PLUGIN_REGISTRATIONS


def bundled_plugin_model_modules() -> tuple[str, ...]:
    """Return trusted model-module declarations without importing them.

    Normal Flask-AAS startup must not import model or application code for a
    disabled plugin. These static module names are retained for explicit
    install/migration tooling, where Python execution is an intentional trust
    boundary.
    """

    return tuple(
        module_name
        for bundled in bundled_plugin_registrations()
        for module_name in bundled.model_modules
    )
