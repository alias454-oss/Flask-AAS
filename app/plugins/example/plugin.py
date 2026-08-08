"""Minimal built-in compatibility fixture for Plugin API v1."""

from typing import Any

from app.plugins import ApplicationPlugin, PluginConfiguration


class ExamplePlugin(ApplicationPlugin):
    plugin_id = "example"
    name = "Example Application"
    version = "0.1.0"
    api_version = 1

    def validate_config(self) -> PluginConfiguration:
        # AAS-039 will give the reference plugin real configuration and Flask
        # surfaces. For AAS-037 it proves the configuration contract only.
        return PluginConfiguration(configured=True)

    def clear_secrets(self) -> None:
        # The AAS-037 example plugin stores no managed secrets.
        return None

    def register(self, app: Any) -> None:
        # Runtime registration surfaces are intentionally added in AAS-039.
        return None


plugin = ExamplePlugin()
