import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.core.extensions import db
from app.models import EnvSettings, PluginRegistration, User
from app.plugins import (
    PLUGIN_API_VERSION,
    ApplicationPlugin,
    PluginConfiguration,
    load_plugin_manifest,
)
from app.plugins.loader import (
    STATUS_ACTIVE,
    STATUS_DISABLED,
    STATUS_ERROR,
    STATUS_INCOMPATIBLE,
    STATUS_NEEDS_CONFIGURATION,
    STATUS_NEEDS_MIGRATION,
    PluginRuntime,
    PluginRuntimeState,
    enforce_plugin_access,
    initialize_plugins,
)


class LifecyclePlugin(ApplicationPlugin):
    plugin_id = "lifecycle"
    name = "Lifecycle Application"
    version = "1.0.0"
    api_version = PLUGIN_API_VERSION

    def __init__(self, *, configured=True, register_error=None):
        self.configured = configured
        self.register_error = register_error
        self.register_calls = 0

    def validate_config(self):
        return PluginConfiguration(
            configured=self.configured,
            reason=None if self.configured else "Required configuration is missing",
        )

    def clear_secrets(self):
        return None

    def register(self, app):
        self.register_calls += 1
        if self.register_error:
            raise RuntimeError(self.register_error)


class IncompatibleLifecyclePlugin(LifecyclePlugin):
    plugin_id = "incompatible"
    api_version = PLUGIN_API_VERSION + 1


class EndpointLifecyclePlugin(LifecyclePlugin):
    plugin_id = "endpoint-plugin"

    def register(self, app):
        super().register(app)
        app.view_functions["endpoint_plugin.surface"] = lambda: None


class InvalidConfigEndpointPlugin(LifecyclePlugin):
    plugin_id = "invalid-config-endpoint"

    def validate_config(self):
        return PluginConfiguration(
            configured=False,
            reason="Configuration required",
            admin_endpoint="not_owned.settings",
        )


EXAMPLE_MANIFEST = load_plugin_manifest(
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "plugin_app" / "plugin.toml"
)


class MigratingLifecyclePlugin(LifecyclePlugin):
    manifest = EXAMPLE_MANIFEST
    plugin_id = manifest.plugin_id
    name = manifest.name
    version = manifest.version
    api_version = manifest.api_version


class PluginLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(cls.temp_dir.name) / "plugin-lifecycle-tests.db"

        cls.app = Flask(__name__)
        cls.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path}",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(cls.app)

        @cls.app.route("/guarded-plugin-test", endpoint="guarded_plugin_test")
        def guarded_plugin_test():
            return "guarded"

        cls.app.before_request(enforce_plugin_access)

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        cls.temp_dir.cleanup()

    def setUp(self):
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.session.remove()
        db.drop_all()
        db.create_all()
        EnvSettings._cached_instance = None

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        EnvSettings._cached_instance = None
        self.app.extensions.pop("flask_aas_plugins", None)
        self.app_context.pop()

    def _add_settings(self, *, enable_plugins=None):
        owner = User(
            username="plugin-settings-owner",
            email="plugin-settings-owner@example.test",
            hashed_password="not-used",
            activated=True,
            approved=True,
        )
        db.session.add(owner)
        db.session.flush()

        kwargs = {}
        if enable_plugins is not None:
            kwargs["enable_plugins"] = enable_plugins

        env = EnvSettings(
            user_id=owner.id,
            site_name="Plugin Lifecycle Test",
            site_lang="en",
            site_timezone="UTC",
            description="",
            keywords="",
            users_per_page=20,
            users_stored_path="/tmp/users",
            **kwargs,
        )
        db.session.add(env)
        db.session.commit()
        return env

    def _add_registration(
        self,
        *,
        plugin_id="lifecycle",
        import_path="tests.fake_plugins:plugin",
        enabled=True,
        configured=False,
    ):
        registration = PluginRegistration(
            plugin_id=plugin_id,
            import_path=import_path,
            enabled=enabled,
            configured=configured,
        )
        db.session.add(registration)
        db.session.commit()
        return registration

    def test_plugin_system_database_toggle_defaults_disabled(self):
        env = self._add_settings()
        db.session.refresh(env)
        self.assertFalse(env.enable_plugins)

    def test_global_toggle_off_skips_registered_enabled_plugins(self):
        self._add_settings(enable_plugins=False)
        self._add_registration(enabled=True)

        with self.assertLogs("app.plugins.loader", level="INFO") as logs, patch(
            "app.plugins.loader.resolve_plugin"
        ) as resolve:
            runtime = initialize_plugins(self.app)

        self.assertFalse(runtime.system_enabled)
        self.assertEqual(runtime.plugins, {})
        resolve.assert_not_called()
        self.assertIn(
            "Application plugin loader disabled by site settings",
            "\n".join(logs.output),
        )

    def test_disabled_registration_is_not_imported(self):
        self._add_settings(enable_plugins=True)
        self._add_registration(enabled=False, configured=True)

        with self.assertLogs("app.plugins.loader", level="INFO") as logs, patch(
            "app.plugins.loader.resolve_plugin"
        ) as resolve:
            runtime = initialize_plugins(self.app)

        self.assertTrue(runtime.system_enabled)
        self.assertEqual(runtime.plugins["lifecycle"].status, STATUS_DISABLED)
        resolve.assert_not_called()
        output = "\n".join(logs.output)
        self.assertIn("Plugin lifecycle disabled; runtime loading skipped", output)
        self.assertIn(
            "Application plugin startup complete: active=0 disabled=1 "
            "pending_config=0 pending_migration=0 incompatible=0 errors=0",
            output,
        )

    def test_loader_records_plugin_owned_endpoints(self):
        self._add_settings(enable_plugins=True)
        self._add_registration(
            plugin_id="endpoint-plugin",
            import_path="tests.fake_plugins:endpoint_plugin",
            enabled=True,
        )
        plugin = EndpointLifecyclePlugin(configured=True)

        with patch("app.plugins.loader.resolve_plugin", return_value=plugin):
            runtime = initialize_plugins(self.app)

        self.assertEqual(
            runtime.plugin_for_endpoint("endpoint_plugin.surface"),
            "endpoint-plugin",
        )

    def test_enabled_configured_plugin_becomes_active(self):
        self._add_settings(enable_plugins=True)
        registration = self._add_registration(enabled=True, configured=False)
        plugin = LifecyclePlugin(configured=True)

        with self.assertLogs("app.plugins.loader", level="INFO") as logs, patch(
            "app.plugins.loader.resolve_plugin", return_value=plugin
        ):
            runtime = initialize_plugins(self.app)

        db.session.refresh(registration)
        self.assertEqual(runtime.plugins["lifecycle"].status, STATUS_ACTIVE)
        self.assertIs(runtime.instance_for("lifecycle"), plugin)
        self.assertEqual(plugin.register_calls, 1)
        self.assertTrue(registration.configured)
        output = "\n".join(logs.output)
        self.assertIn(
            "Activated application plugin lifecycle version=1.0.0 api=1",
            output,
        )
        self.assertIn(
            "Application plugin startup complete: active=1 disabled=0 "
            "pending_config=0 pending_migration=0 incompatible=0 errors=0",
            output,
        )

    def test_enabled_plugin_with_outdated_schema_needs_migration_before_registration(self):
        self._add_settings(enable_plugins=True)
        registration = self._add_registration(
            plugin_id="example",
            import_path="tests.fake_plugins:example",
            enabled=True,
            configured=True,
        )
        plugin = MigratingLifecyclePlugin(configured=True)

        with patch("app.plugins.loader.resolve_plugin", return_value=plugin):
            runtime = initialize_plugins(self.app)

        db.session.refresh(registration)
        state = runtime.plugins["example"]
        self.assertEqual(state.status, STATUS_NEEDS_MIGRATION)
        self.assertIn("plugin run example db upgrade", state.reason)
        self.assertEqual(plugin.register_calls, 0)
        self.assertFalse(registration.configured)

    def test_enabled_unconfigured_plugin_registers_structural_surfaces(self):
        self._add_settings(enable_plugins=True)
        registration = self._add_registration(enabled=True, configured=True)
        plugin = LifecyclePlugin(configured=False)

        with patch("app.plugins.loader.resolve_plugin", return_value=plugin):
            runtime = initialize_plugins(self.app)

        db.session.refresh(registration)
        state = runtime.plugins["lifecycle"]
        self.assertEqual(state.status, STATUS_NEEDS_CONFIGURATION)
        self.assertIn("Required configuration", state.reason)
        self.assertEqual(plugin.register_calls, 1)
        self.assertFalse(registration.configured)

    def test_invalid_configuration_endpoint_fails_plugin_closed(self):
        self._add_settings(enable_plugins=True)
        registration = self._add_registration(
            plugin_id="invalid-config-endpoint",
            import_path="tests.fake_plugins:invalid_config_endpoint",
            enabled=True,
            configured=False,
        )
        plugin = InvalidConfigEndpointPlugin(configured=False)

        with patch("app.plugins.loader.resolve_plugin", return_value=plugin):
            runtime = initialize_plugins(self.app)

        db.session.refresh(registration)
        self.assertEqual(runtime.plugins["invalid-config-endpoint"].status, STATUS_ERROR)
        self.assertFalse(registration.configured)
        self.assertIsNone(runtime.instance_for("invalid-config-endpoint"))

    def test_configuration_endpoint_bypass_is_site_admin_only(self):
        self._add_settings(enable_plugins=True)
        self._add_registration(enabled=True, configured=False)
        runtime = PluginRuntime(system_enabled=True)
        runtime.plugins["lifecycle"] = PluginRuntimeState(
            plugin_id="lifecycle",
            status=STATUS_NEEDS_CONFIGURATION,
            configuration_endpoint="guarded_plugin_test",
        )
        runtime.endpoints["guarded_plugin_test"] = "lifecycle"
        self.app.extensions["flask_aas_plugins"] = runtime
        client = self.app.test_client()

        denied_user = type(
            "DeniedUser",
            (),
            {"is_authenticated": True, "has_role": lambda self, role: False},
        )()
        with patch("app.plugins.loader.current_user", denied_user):
            response = client.get("/guarded-plugin-test")
        self.assertEqual(response.status_code, 404)

        admin_user = type(
            "AdminUser",
            (),
            {"is_authenticated": True, "has_role": lambda self, role: role == "admin"},
        )()
        with patch("app.plugins.loader.current_user", admin_user):
            response = client.get("/guarded-plugin-test")
        self.assertEqual(response.status_code, 200)

    def test_loaded_plugin_access_tracks_current_enabled_and_configured_state(self):
        self._add_settings(enable_plugins=True)
        registration = self._add_registration(
            enabled=True,
            configured=False,
        )
        runtime = PluginRuntime(system_enabled=True)
        runtime.plugins["lifecycle"] = PluginRuntimeState(
            plugin_id="lifecycle",
            status=STATUS_NEEDS_CONFIGURATION,
        )
        runtime.endpoints["guarded_plugin_test"] = "lifecycle"
        self.app.extensions["flask_aas_plugins"] = runtime
        client = self.app.test_client()

        # Structural route exists, but pending configuration keeps it closed.
        response = client.get("/guarded-plugin-test")
        self.assertEqual(response.status_code, 404)

        # Completing configuration makes the already-loaded route usable.
        registration.configured = True
        db.session.commit()
        response = client.get("/guarded-plugin-test")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), "guarded")

        # Disabling the plugin blocks access immediately without route mutation.
        registration.enabled = False
        db.session.commit()
        response = client.get("/guarded-plugin-test")
        self.assertEqual(response.status_code, 404)

    def test_incompatible_plugin_isolated_from_core_startup(self):
        self._add_settings(enable_plugins=True)
        self._add_registration(
            plugin_id="incompatible",
            import_path="tests.fake_plugins:incompatible",
            enabled=True,
        )
        plugin = IncompatibleLifecyclePlugin()

        with patch("app.plugins.loader.resolve_plugin", return_value=plugin):
            runtime = initialize_plugins(self.app)

        self.assertEqual(
            runtime.plugins["incompatible"].status,
            STATUS_INCOMPATIBLE,
        )
        self.assertEqual(plugin.register_calls, 0)

    def test_registration_failure_becomes_error_without_raising(self):
        self._add_settings(enable_plugins=True)
        self._add_registration(enabled=True)
        plugin = LifecyclePlugin(register_error="registration exploded")

        with patch("app.plugins.loader.resolve_plugin", return_value=plugin):
            runtime = initialize_plugins(self.app)

        state = runtime.plugins["lifecycle"]
        self.assertEqual(state.status, STATUS_ERROR)
        self.assertIn("Check application logs", state.reason)

    def test_broken_plugin_does_not_block_another_plugin(self):
        self._add_settings(enable_plugins=True)
        self._add_registration(
            plugin_id="broken",
            import_path="tests.fake_plugins:broken",
            enabled=True,
        )
        self._add_registration(
            plugin_id="working",
            import_path="tests.fake_plugins:working",
            enabled=True,
        )

        class BrokenPlugin(LifecyclePlugin):
            plugin_id = "broken"

        class WorkingPlugin(LifecyclePlugin):
            plugin_id = "working"

        broken = BrokenPlugin(register_error="broken plugin")
        working = WorkingPlugin()

        def resolve(import_path):
            if import_path.endswith(":broken"):
                return broken
            return working

        with patch("app.plugins.loader.resolve_plugin", side_effect=resolve):
            runtime = initialize_plugins(self.app)

        self.assertEqual(runtime.plugins["broken"].status, STATUS_ERROR)
        self.assertEqual(runtime.plugins["working"].status, STATUS_ACTIVE)
        self.assertEqual(working.register_calls, 1)

    def test_missing_database_tables_fail_closed_during_bootstrap(self):
        db.drop_all()

        runtime = initialize_plugins(self.app)

        self.assertFalse(runtime.system_enabled)
        self.assertEqual(runtime.plugins, {})
