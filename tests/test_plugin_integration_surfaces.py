import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.core.extensions import db
from app.models import EnvSettings, PluginRegistration, User
from app.plugins.cli import plugin_cli
from app.plugins.example import plugin as example_plugin
from app.plugins.example.models import ExampleSettings
from app.plugins.interface import PluginConfiguration
from app.plugins.loader import STATUS_NEEDS_CONFIGURATION, initialize_plugins
from app.plugins.navigation import get_plugin_navigation, visible_plugin_navigation


class ExamplePluginIntegrationSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "example-plugin-integration.db"

        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="plugin-integration-surface-test",
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path}",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.app.cli.add_command(plugin_cli)

        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        EnvSettings._cached_instance = None

        owner = User(
            username="example-plugin-integration-owner",
            email="example-plugin-integration-owner@example.test",
            hashed_password="not-used",
            activated=True,
            approved=True,
        )
        db.session.add(owner)
        db.session.flush()

        self.env = EnvSettings(
            user_id=owner.id,
            site_name="Plugin Integration Test",
            site_lang="en",
            site_timezone="UTC",
            description="",
            keywords="",
            users_per_page=20,
            users_stored_path="/tmp/users",
            enable_plugins=True,
        )
        self.example_settings = ExampleSettings(
            id=1,
            greeting="Integration greeting",
            managed_secret="integration-managed-secret",
        )
        self.registration = PluginRegistration(
            plugin_id="example",
            import_path="app.plugins.example.plugin:plugin",
            enabled=True,
            configured=False,
        )
        db.session.add_all([self.env, self.example_settings, self.registration])
        db.session.commit()

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        EnvSettings._cached_instance = None
        self.app_context.pop()
        self.temp_dir.cleanup()

    def test_generic_host_cli_passes_through_to_plugin_owned_commands(self):
        # Explicit operator CLI access is independent of web-runtime activation.
        self.env.enable_plugins = False
        self.registration.enabled = False
        self.registration.configured = False
        db.session.commit()

        runner = self.app.test_cli_runner()
        with self.assertLogs("app.plugins.cli", level="INFO") as logs:
            result = runner.invoke(args=["plugin", "run", "example", "status"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Example Application plugin CLI is available.", result.output)
        self.assertNotIn("example", self.app.cli.commands)
        self.assertIn(
            "Dispatching plugin CLI plugin=example command=status",
            "\n".join(logs.output),
        )

    def test_plugin_cli_logging_does_not_include_arguments(self):
        runner = self.app.test_cli_runner()
        with self.assertLogs("app.plugins.cli", level="INFO") as logs:
            result = runner.invoke(
                args=["plugin", "run", "example", "status", "opaque-secret-value"]
            )

        self.assertNotEqual(result.exit_code, 0)
        output = "\n".join(logs.output)
        self.assertIn(
            "Dispatching plugin CLI plugin=example command=status",
            output,
        )
        self.assertNotIn("opaque-secret-value", output)


    def test_plugin_cli_configure_refreshes_host_configuration_without_logging_secret(self):
        self.example_settings.managed_secret = None
        self.registration.configured = False
        db.session.commit()

        runner = self.app.test_cli_runner()
        with self.assertLogs("app.plugins.cli", level="INFO") as logs:
            result = runner.invoke(
                args=[
                    "plugin",
                    "run",
                    "example",
                    "configure",
                    "--greeting",
                    "Configured from CLI",
                ],
                input="new-managed-secret\nnew-managed-secret\n",
            )

        self.assertEqual(result.exit_code, 0, result.output)
        db.session.refresh(self.registration)
        db.session.refresh(self.example_settings)
        self.assertTrue(self.registration.configured)
        self.assertEqual(self.example_settings.greeting, "Configured from CLI")
        self.assertEqual(self.example_settings.managed_secret, "new-managed-secret")
        self.assertIn("configured=yes", result.output)
        log_output = "\n".join(logs.output)
        self.assertIn(
            "Dispatching plugin CLI plugin=example command=configure",
            log_output,
        )
        self.assertNotIn("new-managed-secret", log_output)

    def test_plugin_cli_help_is_passed_through(self):
        result = self.app.test_cli_runner().invoke(
            args=["plugin", "run", "example", "--help"]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Reference application commands.", result.output)
        self.assertIn("status", result.output)

    def test_unknown_plugin_cli_fails_cleanly(self):
        result = self.app.test_cli_runner().invoke(
            args=["plugin", "run", "missing", "status"]
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Plugin 'missing' is not registered.", result.output)

    def test_reference_plugin_registers_navigation_surface(self):
        initialize_plugins(self.app)

        navigation = get_plugin_navigation(self.app)
        self.assertEqual(len(navigation), 1)
        self.assertEqual(navigation[0].plugin_id, "example")
        self.assertEqual(navigation[0].label, "Example")
        self.assertEqual(navigation[0].endpoint, "example.index")
        self.assertEqual(visible_plugin_navigation(), navigation)

    def test_unconfigured_plugin_hides_navigation_until_ready(self):
        pending = PluginConfiguration(
            configured=False,
            reason="Reference plugin configuration is pending",
        )
        with patch.object(example_plugin, "validate_config", return_value=pending):
            runtime = initialize_plugins(self.app)

        db.session.refresh(self.registration)
        self.assertEqual(runtime.plugins["example"].status, STATUS_NEEDS_CONFIGURATION)
        self.assertEqual(len(get_plugin_navigation(self.app)), 1)
        self.assertEqual(visible_plugin_navigation(), [])

        # Configuration can make an already-loaded application visible without
        # re-registering its Blueprint or navigation contribution.
        self.registration.configured = True
        db.session.commit()
        self.assertEqual(len(visible_plugin_navigation()), 1)

        # Disable removes navigation immediately even before structural unload.
        self.registration.enabled = False
        db.session.commit()
        self.assertEqual(visible_plugin_navigation(), [])

    def test_disabled_plugin_does_not_register_navigation(self):
        self.registration.enabled = False
        db.session.commit()

        initialize_plugins(self.app)

        self.assertEqual(get_plugin_navigation(self.app), [])
        self.assertEqual(visible_plugin_navigation(), [])


if __name__ == "__main__":
    unittest.main()
