"""Minimal built-in compatibility fixture for Plugin API v1."""

from typing import Any

from app.plugins import ApplicationPlugin, PluginConfiguration
from app.plugins.example.cli import cli as example_cli
from app.plugins.example.routes import example_bp
from app.plugins.navigation import register_plugin_navigation


class ExamplePlugin(ApplicationPlugin):
    plugin_id = "example"
    name = "Example Application"
    version = "0.1.0"
    api_version = 1

    def validate_config(self) -> PluginConfiguration:
        # The reference web surface has no required configuration yet.
        return PluginConfiguration(configured=True)

    def clear_secrets(self) -> None:
        # The reference plugin currently stores no managed secrets.
        return None

    def get_cli(self):
        return example_cli

    def register(self, app: Any) -> None:
        app.register_blueprint(example_bp)
        register_plugin_navigation(
            app,
            plugin_id=self.plugin_id,
            label="Example",
            endpoint="example.index",
        )


plugin = ExamplePlugin()
