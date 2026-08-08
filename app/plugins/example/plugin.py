"""Minimal built-in compatibility fixture for Plugin API v1."""

from pathlib import Path
from typing import Any

from sqlalchemy import inspect

from app.core.extensions import db
from app.plugins import ApplicationPlugin, PluginConfiguration, load_plugin_manifest
from app.plugins.example.cli import cli as example_cli
from app.plugins.example.models import ExampleSettings, get_example_settings
from app.plugins.example.routes import example_bp
from app.plugins.navigation import register_plugin_navigation


class ExamplePlugin(ApplicationPlugin):
    manifest = load_plugin_manifest(Path(__file__).with_name("plugin.toml"))
    plugin_id = manifest.plugin_id
    name = manifest.name
    version = manifest.version
    api_version = manifest.api_version

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
        if not inspect(db.engine).has_table(ExampleSettings.__tablename__):
            return

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
