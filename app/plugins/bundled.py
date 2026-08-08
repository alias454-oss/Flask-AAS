"""Trusted application plugin registrations shipped with Flask-AAS."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BundledPluginRegistration:
    """Stable identity needed to seed one bundled application registration."""

    plugin_id: str
    import_path: str
    model_modules: tuple[str, ...] = ()


BUNDLED_PLUGIN_REGISTRATIONS = (
    BundledPluginRegistration(
        plugin_id="example",
        import_path="app.plugins.example.plugin:plugin",
        model_modules=("app.plugins.example.models",),
    ),
)


def bundled_plugin_registrations() -> tuple[BundledPluginRegistration, ...]:
    """Return plugins that are installed as part of this Flask-AAS build."""

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
