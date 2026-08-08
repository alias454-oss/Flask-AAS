"""Minimal built-in compatibility fixture for Plugin API v1."""

from typing import Any

from app.plugins import ApplicationPlugin, PluginConfiguration
from app.plugins.example.cli import cli as example_cli
from app.plugins.example.models import ensure_example_schema, get_example_settings
from app.plugins.example.routes import example_bp
from app.plugins.navigation import register_plugin_navigation


class ExamplePlugin(ApplicationPlugin):
    plugin_id = "example"
    name = "Example Application"
    version = "0.1.0"
    api_version = 1

    def prepare_enable(self) -> None:
        ensure_example_schema()

    def validate_config(self) -> PluginConfiguration:
        settings = get_example_settings()
        if settings is None:
            return PluginConfiguration(
                configured=False,
                reason=(
                    "Example configuration is missing. Run "
                    "'python manage.py plugin run example configure'."
                ),
            )

        if not settings.greeting or not settings.greeting.strip():
            return PluginConfiguration(
                configured=False,
                reason="Example greeting must not be empty.",
            )

        if not settings.managed_secret:
            return PluginConfiguration(
                configured=False,
                reason=(
                    "Example managed secret is missing. Run "
                    "'python manage.py plugin run example configure'."
                ),
            )

        return PluginConfiguration(configured=True)

    def clear_secrets(self) -> None:
        settings = get_example_settings()
        if settings is not None:
            settings.managed_secret = None

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
