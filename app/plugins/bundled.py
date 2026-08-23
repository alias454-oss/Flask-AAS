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


def bundled_plugin_registrations() -> tuple[BundledPluginRegistration, ...]:
    """Discover in-tree plugin manifests without importing plugin code."""

    return tuple(
        BundledPluginRegistration(manifest=load_plugin_manifest(manifest_path))
        for manifest_path in sorted(
            Path(__file__).resolve().parent.glob("*/plugin.toml")
        )
        if manifest_path.is_file()
    )
