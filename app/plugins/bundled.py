"""Trusted application plugin registrations shipped with Flask-AAS."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BundledPluginRegistration:
    """Stable identity needed to seed one bundled application registration."""

    plugin_id: str
    import_path: str


BUNDLED_PLUGIN_REGISTRATIONS = (
    BundledPluginRegistration(
        plugin_id="example",
        import_path="app.plugins.example.plugin:plugin",
    ),
)


def bundled_plugin_registrations() -> tuple[BundledPluginRegistration, ...]:
    """Return plugins that are installed as part of this Flask-AAS build."""

    return BUNDLED_PLUGIN_REGISTRATIONS
