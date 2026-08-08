import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from flask import Blueprint, Flask
from flask_login import LoginManager

from app.core.extensions import csrf, db, limiter
from app.models import EnvSettings, PluginRegistration, Role, User
from app.plugins import PLUGIN_API_VERSION, ApplicationPlugin, PluginConfiguration
from app.plugins.manifest import PluginManifest
from app.plugins.loader import (
    PLUGIN_RUNTIME_EXTENSION,
    STATUS_ACTIVE,
    STATUS_DISABLED,
    STATUS_NEEDS_CONFIGURATION,
    STATUS_NEEDS_MIGRATION,
    PluginRuntime,
    PluginRuntimeState,
)
from app.plugins.reload import AppConfigReloadUnavailable
from app.routes.admin.plugins import plugins_bp


class AdminPlugin(ApplicationPlugin):
    plugin_id = "admin-plugin"
    name = "Admin Plugin"
    version = "1.0.0"
    api_version = PLUGIN_API_VERSION

    def __init__(self, *, configured=True, cleanup_error=None):
        self.configured = configured
        self.cleanup_error = cleanup_error
        self.clear_calls = 0

    def validate_config(self):
        return PluginConfiguration(
            configured=self.configured,
            reason=None if self.configured else "API credential is required",
        )

    def clear_secrets(self):
        self.clear_calls += 1
        if self.cleanup_error:
            raise RuntimeError(self.cleanup_error)
        self.configured = False

    def register(self, app):
        return None


class MigratingAdminPlugin(AdminPlugin):
    manifest = PluginManifest(
        plugin_id="admin-plugin",
        name="Admin Plugin",
        version="1.0.0",
        api_version=PLUGIN_API_VERSION,
        entrypoint="tests.fake_admin_plugin:plugin",
        migrations="migrations",
        path=Path("/tmp/admin-plugin/plugin.toml"),
    )


class IncompatibleMigratingAdminPlugin(MigratingAdminPlugin):
    api_version = PLUGIN_API_VERSION + 1
    manifest = PluginManifest(
        plugin_id="admin-plugin",
        name="Admin Plugin",
        version="1.0.0",
        api_version=PLUGIN_API_VERSION + 1,
        entrypoint="tests.fake_admin_plugin:plugin",
        migrations="migrations",
        path=Path("/tmp/admin-plugin/plugin.toml"),
    )


class PluginAdminRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(cls.temp_dir.name) / "plugin-admin-tests.db"
        template_path = Path(__file__).resolve().parents[1] / "app" / "templates"
        static_path = Path(__file__).resolve().parents[1] / "app" / "static"

        cls.app = Flask(
            __name__,
            template_folder=str(template_path),
            static_folder=str(static_path),
        )
        cls.app.config.update(
            TESTING=True,
            SECRET_KEY="plugin-admin-test-secret",
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path}",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            RATELIMIT_ENABLED=False,
        )

        db.init_app(cls.app)
        csrf.init_app(cls.app)
        limiter.init_app(cls.app)

        login_manager = LoginManager(cls.app)
        login_manager.login_view = "login.login"

        @login_manager.user_loader
        def load_user(session_id):
            return User.load_from_session_id(
                session_id,
                require_session_record=False,
            )

        login_bp = Blueprint("login", __name__)

        @login_bp.route("/login")
        def login():
            return "login"

        index_bp = Blueprint("index", __name__)

        @index_bp.route("/")
        def index():
            return "index"

        cls.app.register_blueprint(login_bp)
        cls.app.register_blueprint(index_bp)
        cls.app.register_blueprint(plugins_bp)

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

        admin_role = Role(name="admin")
        user_role = Role(name="user")
        admin = User(
            username="plugin-admin",
            email="plugin-admin@example.test",
            hashed_password="not-used",
            activated=True,
            approved=True,
        )
        admin.roles.append(admin_role)
        regular = User(
            username="plugin-user",
            email="plugin-user@example.test",
            hashed_password="not-used",
            activated=True,
            approved=True,
        )
        regular.roles.append(user_role)
        db.session.add_all([admin, regular])
        db.session.flush()

        self.settings = EnvSettings(
            user_id=admin.id,
            site_name="Plugin Admin Test",
            site_lang="en",
            site_timezone="UTC",
            description="",
            keywords="",
            users_per_page=20,
            users_stored_path="/tmp/users",
            enable_plugins=True,
        )
        db.session.add(self.settings)
        db.session.commit()
        self.admin_id = admin.id
        self.regular_id = regular.id
        EnvSettings._cached_instance = None

        self.client = self.app.test_client()
        self.app.extensions[PLUGIN_RUNTIME_EXTENSION] = PluginRuntime(
            system_enabled=True
        )

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        EnvSettings._cached_instance = None
        self.app.extensions.pop(PLUGIN_RUNTIME_EXTENSION, None)
        self.app_context.pop()

    def _login(self, user_id):
        user = db.session.get(User, user_id)
        with self.client.session_transaction() as session:
            session["_user_id"] = user.get_id()
            session["_fresh"] = True

    def _registration(self, *, enabled=False, configured=True):
        record = PluginRegistration(
            plugin_id="admin-plugin",
            import_path="tests.fake_admin_plugin:plugin",
            enabled=enabled,
            configured=configured,
        )
        db.session.add(record)
        db.session.commit()
        return record

    def test_non_admin_cannot_view_plugin_administration(self):
        self._login(self.regular_id)

        response = self.client.get("/admin/plugins/", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))

    def test_admin_list_reports_persisted_and_runtime_state(self):
        self._login(self.admin_id)
        record = self._registration(enabled=True, configured=True)
        self.app.extensions[PLUGIN_RUNTIME_EXTENSION].plugins["admin-plugin"] = (
            PluginRuntimeState(
                plugin_id="admin-plugin",
                status=STATUS_ACTIVE,
                name="Admin Plugin",
                version="1.0.0",
            )
        )

        with patch(
            "app.core.decorators.audit_activity_enabled",
            return_value=False,
        ), patch(
            "app.routes.admin.plugins.get_admin_quick_stats",
            return_value={},
        ), patch(
            "app.routes.admin.plugins.render_template",
            return_value="plugins",
        ) as render:
            response = self.client.get("/admin/plugins/")

        self.assertEqual(response.status_code, 200)
        row = render.call_args.kwargs["rows"][0]
        self.assertEqual(row.registration.id, record.id)
        self.assertEqual(row.runtime_status, STATUS_ACTIVE)
        self.assertEqual(row.access_status, "Available")
        self.assertFalse(row.restart_required)
        self.assertFalse(render.call_args.kwargs["app_reload_required"])

    def test_enable_persists_requested_state_and_configuration(self):
        self._login(self.admin_id)
        record = self._registration(enabled=False, configured=False)
        plugin = AdminPlugin(configured=True)

        with self.assertLogs("app.routes.admin.plugins", level="INFO") as logs, patch(
            "app.routes.admin.plugins.resolve_plugin",
            return_value=plugin,
        ), patch("app.routes.admin.plugins.log_action"):
            response = self.client.post(
                f"/admin/plugins/{record.id}/enable",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(record)
        self.assertTrue(record.enabled)
        self.assertTrue(record.configured)
        self.assertIn(
            "Admin user=plugin-admin enabled plugin=admin-plugin configured=True "
            "app_config_reload_required=True",
            "\n".join(logs.output),
        )

    def test_enable_can_leave_plugin_needing_configuration(self):
        self._login(self.admin_id)
        record = self._registration(enabled=False, configured=True)
        plugin = AdminPlugin(configured=False)

        with patch(
            "app.routes.admin.plugins.resolve_plugin",
            return_value=plugin,
        ), patch("app.routes.admin.plugins.log_action"):
            response = self.client.post(
                f"/admin/plugins/{record.id}/enable",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(record)
        self.assertTrue(record.enabled)
        self.assertFalse(record.configured)

    def test_disable_clears_managed_secrets_and_preserves_registration(self):
        self._login(self.admin_id)
        record = self._registration(enabled=True, configured=True)
        plugin = AdminPlugin(configured=True)

        with self.assertLogs("app.routes.admin.plugins", level="INFO") as logs, patch(
            "app.routes.admin.plugins.resolve_plugin",
            return_value=plugin,
        ), patch("app.routes.admin.plugins.log_action"):
            response = self.client.post(
                f"/admin/plugins/{record.id}/disable",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(record)
        self.assertFalse(record.enabled)
        self.assertFalse(record.configured)
        self.assertEqual(plugin.clear_calls, 1)
        self.assertEqual(PluginRegistration.query.count(), 1)
        self.assertIn(
            "Admin user=plugin-admin disabled plugin=admin-plugin configured=False "
            "secrets_cleared=True app_config_reload_required=True",
            "\n".join(logs.output),
        )

    def test_disable_cleanup_failure_rolls_back_enabled_state(self):
        self._login(self.admin_id)
        record = self._registration(enabled=True, configured=True)
        plugin = AdminPlugin(configured=True, cleanup_error="cleanup failed")

        with patch(
            "app.routes.admin.plugins.resolve_plugin",
            return_value=plugin,
        ), patch("app.routes.admin.plugins.log_action"):
            response = self.client.post(
                f"/admin/plugins/{record.id}/disable",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(record)
        self.assertTrue(record.enabled)
        self.assertTrue(record.configured)
        self.assertEqual(plugin.clear_calls, 1)

    def test_disabled_loaded_plugin_reports_access_blocked_immediately(self):
        self._login(self.admin_id)
        self._registration(enabled=False, configured=True)
        self.app.extensions[PLUGIN_RUNTIME_EXTENSION].plugins["admin-plugin"] = (
            PluginRuntimeState(
                plugin_id="admin-plugin",
                status=STATUS_ACTIVE,
                name="Admin Plugin",
                version="1.0.0",
            )
        )

        with patch(
            "app.core.decorators.audit_activity_enabled",
            return_value=False,
        ), patch(
            "app.routes.admin.plugins.get_admin_quick_stats",
            return_value={},
        ), patch(
            "app.routes.admin.plugins.render_template",
            return_value="plugins",
        ) as render:
            response = self.client.get("/admin/plugins/")

        self.assertEqual(response.status_code, 200)
        row = render.call_args.kwargs["rows"][0]
        self.assertEqual(row.access_status, "Disabled")
        self.assertTrue(row.restart_required)

    def test_requested_enablement_change_requires_restart(self):
        self._login(self.admin_id)
        self._registration(enabled=True, configured=True)
        self.app.extensions[PLUGIN_RUNTIME_EXTENSION].plugins["admin-plugin"] = (
            PluginRuntimeState(
                plugin_id="admin-plugin",
                status=STATUS_DISABLED,
            )
        )

        with patch(
            "app.core.decorators.audit_activity_enabled",
            return_value=False,
        ), patch(
            "app.routes.admin.plugins.get_admin_quick_stats",
            return_value={},
        ), patch(
            "app.routes.admin.plugins.render_template",
            return_value="plugins",
        ) as render:
            response = self.client.get("/admin/plugins/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(render.call_args.kwargs["rows"][0].restart_required)
        self.assertTrue(render.call_args.kwargs["app_reload_required"])

    def test_needs_migration_is_unavailable_and_keeps_reload_action_visible(self):
        self._login(self.admin_id)
        self._registration(enabled=True, configured=False)
        self.app.extensions[PLUGIN_RUNTIME_EXTENSION].plugins["admin-plugin"] = (
            PluginRuntimeState(
                plugin_id="admin-plugin",
                status=STATUS_NEEDS_MIGRATION,
                reason="Schema migration required",
                name="Admin Plugin",
                version="1.0.0",
            )
        )

        with patch(
            "app.core.decorators.audit_activity_enabled",
            return_value=False,
        ), patch(
            "app.routes.admin.plugins.get_admin_quick_stats",
            return_value={},
        ), patch(
            "app.routes.admin.plugins.render_template",
            return_value="plugins",
        ) as render:
            response = self.client.get("/admin/plugins/")

        self.assertEqual(response.status_code, 200)
        row = render.call_args.kwargs["rows"][0]
        self.assertEqual(row.access_status, "Needs migration")
        self.assertTrue(row.can_upgrade_schema)
        self.assertTrue(row.restart_required)
        self.assertTrue(render.call_args.kwargs["app_reload_required"])

    def test_completed_configuration_requires_reload_from_needs_configuration(self):
        self._login(self.admin_id)
        self._registration(enabled=True, configured=True)
        self.app.extensions[PLUGIN_RUNTIME_EXTENSION].plugins["admin-plugin"] = (
            PluginRuntimeState(
                plugin_id="admin-plugin",
                status=STATUS_NEEDS_CONFIGURATION,
                reason="Configuration was incomplete at worker startup",
                name="Admin Plugin",
                version="1.0.0",
            )
        )

        with patch(
            "app.core.decorators.audit_activity_enabled",
            return_value=False,
        ), patch(
            "app.routes.admin.plugins.get_admin_quick_stats",
            return_value={},
        ), patch(
            "app.routes.admin.plugins.render_template",
            return_value="plugins",
        ) as render:
            response = self.client.get("/admin/plugins/")

        self.assertEqual(response.status_code, 200)
        row = render.call_args.kwargs["rows"][0]
        self.assertEqual(row.runtime_status, STATUS_NEEDS_CONFIGURATION)
        self.assertEqual(row.access_status, "Needs configuration")
        self.assertFalse(row.can_upgrade_schema)
        self.assertTrue(row.restart_required)
        self.assertTrue(render.call_args.kwargs["app_reload_required"])

    def test_configuration_invalidated_under_active_worker_requires_reload(self):
        self._login(self.admin_id)
        self._registration(enabled=True, configured=False)
        self.app.extensions[PLUGIN_RUNTIME_EXTENSION].plugins["admin-plugin"] = (
            PluginRuntimeState(
                plugin_id="admin-plugin",
                status=STATUS_ACTIVE,
                name="Admin Plugin",
                version="1.0.0",
            )
        )

        with patch(
            "app.core.decorators.audit_activity_enabled",
            return_value=False,
        ), patch(
            "app.routes.admin.plugins.get_admin_quick_stats",
            return_value={},
        ), patch(
            "app.routes.admin.plugins.render_template",
            return_value="plugins",
        ) as render:
            response = self.client.get("/admin/plugins/")

        self.assertEqual(response.status_code, 200)
        row = render.call_args.kwargs["rows"][0]
        self.assertEqual(row.runtime_status, STATUS_ACTIVE)
        self.assertEqual(row.access_status, "Needs configuration")
        self.assertFalse(row.can_upgrade_schema)
        self.assertTrue(row.restart_required)
        self.assertTrue(render.call_args.kwargs["app_reload_required"])

    def test_admin_can_upgrade_enabled_plugin_schema_to_head(self):
        self._login(self.admin_id)
        record = self._registration(enabled=True, configured=True)
        plugin = MigratingAdminPlugin(configured=True)
        manager = MagicMock()
        manager.current_revision.return_value = None
        manager.head_revision.return_value = "0001"
        manager.upgrade.return_value = "0001"

        with self.assertLogs("app.routes.admin.plugins", level="INFO") as logs, patch(
            "app.routes.admin.plugins.resolve_plugin",
            return_value=plugin,
        ), patch(
            "app.routes.admin.plugins.PluginMigrationManager",
            return_value=manager,
        ) as manager_cls, patch(
            "app.routes.admin.plugins.log_action"
        ) as log_action_mock:
            response = self.client.post(
                f"/admin/plugins/{record.id}/upgrade-schema",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        manager_cls.assert_called_once_with(plugin.manifest)
        manager.upgrade.assert_called_once_with("head")
        db.session.refresh(record)
        self.assertFalse(record.configured)
        self.assertEqual(
            log_action_mock.call_args.kwargs["action"],
            "upgrade_plugin_schema",
        )
        self.assertEqual(
            log_action_mock.call_args.kwargs["extra_data"]["outcome"],
            "success",
        )
        self.assertIn(
            "Admin user=plugin-admin upgraded plugin=admin-plugin schema from=<base> to=0001",
            "\n".join(logs.output),
        )

    def test_admin_schema_upgrade_is_idempotent_when_already_current(self):
        self._login(self.admin_id)
        record = self._registration(enabled=True, configured=False)
        plugin = MigratingAdminPlugin(configured=False)
        manager = MagicMock()
        manager.current_revision.return_value = "0001"
        manager.head_revision.return_value = "0001"

        with patch(
            "app.routes.admin.plugins.resolve_plugin",
            return_value=plugin,
        ), patch(
            "app.routes.admin.plugins.PluginMigrationManager",
            return_value=manager,
        ), patch(
            "app.routes.admin.plugins.log_action"
        ) as log_action_mock:
            response = self.client.post(
                f"/admin/plugins/{record.id}/upgrade-schema",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        manager.upgrade.assert_not_called()
        log_action_mock.assert_not_called()

    def test_disabled_plugin_schema_cannot_be_upgraded_from_admin_ui(self):
        self._login(self.admin_id)
        record = self._registration(enabled=False, configured=False)

        with patch(
            "app.routes.admin.plugins.resolve_plugin"
        ) as resolve, patch(
            "app.routes.admin.plugins.PluginMigrationManager"
        ) as manager_cls:
            response = self.client.post(
                f"/admin/plugins/{record.id}/upgrade-schema",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        resolve.assert_not_called()
        manager_cls.assert_not_called()

    def test_incompatible_plugin_schema_is_not_upgraded(self):
        self._login(self.admin_id)
        record = self._registration(enabled=True, configured=False)
        plugin = IncompatibleMigratingAdminPlugin(configured=False)

        with patch(
            "app.routes.admin.plugins.resolve_plugin",
            return_value=plugin,
        ), patch(
            "app.routes.admin.plugins.PluginMigrationManager"
        ) as manager_cls, patch(
            "app.routes.admin.plugins.log_action"
        ) as log_action_mock:
            response = self.client.post(
                f"/admin/plugins/{record.id}/upgrade-schema",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        manager_cls.assert_not_called()
        self.assertEqual(
            log_action_mock.call_args.kwargs["extra_data"]["outcome"],
            "failed",
        )

    def test_schema_upgrade_failure_is_audited_without_marking_configured(self):
        self._login(self.admin_id)
        record = self._registration(enabled=True, configured=False)
        plugin = MigratingAdminPlugin(configured=False)
        manager = MagicMock()
        manager.current_revision.return_value = None
        manager.head_revision.return_value = "0001"
        manager.upgrade.side_effect = RuntimeError("migration failed")

        with patch(
            "app.routes.admin.plugins.resolve_plugin",
            return_value=plugin,
        ), patch(
            "app.routes.admin.plugins.PluginMigrationManager",
            return_value=manager,
        ), patch(
            "app.routes.admin.plugins.log_action"
        ) as log_action_mock:
            response = self.client.post(
                f"/admin/plugins/{record.id}/upgrade-schema",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(record)
        self.assertFalse(record.configured)
        self.assertEqual(
            log_action_mock.call_args.kwargs["action"],
            "upgrade_plugin_schema",
        )
        self.assertEqual(
            log_action_mock.call_args.kwargs["extra_data"]["outcome"],
            "failed",
        )
        self.assertEqual(
            log_action_mock.call_args.kwargs["extra_data"]["error_type"],
            "RuntimeError",
        )

    def test_non_admin_cannot_upgrade_plugin_schema(self):
        self._login(self.regular_id)
        record = self._registration(enabled=True, configured=False)

        with patch(
            "app.routes.admin.plugins.PluginMigrationManager"
        ) as manager_cls:
            response = self.client.post(
                f"/admin/plugins/{record.id}/upgrade-schema",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        manager_cls.assert_not_called()

    def test_admin_reload_applies_pending_runtime_change(self):
        self._login(self.admin_id)
        self._registration(enabled=True, configured=True)
        self.app.extensions[PLUGIN_RUNTIME_EXTENSION].plugins["admin-plugin"] = (
            PluginRuntimeState(
                plugin_id="admin-plugin",
                status=STATUS_DISABLED,
            )
        )

        with self.assertLogs("app.routes.admin.plugins", level="INFO") as logs, patch(
            "app.routes.admin.plugins.log_action"
        ) as log_action_mock, patch(
            "app.routes.admin.plugins.reload_app_config"
        ) as reload_mock:
            response = self.client.post(
                "/admin/plugins/reload",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Refresh"], "2; url=/admin/plugins/")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        reload_mock.assert_called_once_with()
        log_action_mock.assert_called_once()
        self.assertEqual(
            log_action_mock.call_args.kwargs["action"],
            "reload_app_config",
        )
        self.assertIn(
            "Admin user=plugin-admin requested app config reload via Gunicorn SIGHUP",
            "\n".join(logs.output),
        )

    def test_admin_reload_skips_when_no_runtime_change_is_pending(self):
        self._login(self.admin_id)
        self._registration(enabled=True, configured=True)
        self.app.extensions[PLUGIN_RUNTIME_EXTENSION].plugins["admin-plugin"] = (
            PluginRuntimeState(
                plugin_id="admin-plugin",
                status=STATUS_ACTIVE,
                name="Admin Plugin",
                version="1.0.0",
            )
        )

        with patch("app.routes.admin.plugins.log_action") as log_action_mock, patch(
            "app.routes.admin.plugins.reload_app_config"
        ) as reload_mock:
            response = self.client.post(
                "/admin/plugins/reload",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        reload_mock.assert_not_called()
        log_action_mock.assert_not_called()

    def test_non_admin_cannot_reload_app_config(self):
        self._login(self.regular_id)
        self._registration(enabled=True, configured=True)
        self.app.extensions[PLUGIN_RUNTIME_EXTENSION].plugins["admin-plugin"] = (
            PluginRuntimeState(
                plugin_id="admin-plugin",
                status=STATUS_DISABLED,
            )
        )

        with patch("app.routes.admin.plugins.reload_app_config") as reload_mock:
            response = self.client.post(
                "/admin/plugins/reload",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        reload_mock.assert_not_called()

    def test_admin_reload_reports_unsupported_server_without_500(self):
        self._login(self.admin_id)
        self._registration(enabled=True, configured=True)
        self.app.extensions[PLUGIN_RUNTIME_EXTENSION].plugins["admin-plugin"] = (
            PluginRuntimeState(
                plugin_id="admin-plugin",
                status=STATUS_DISABLED,
            )
        )

        with patch("app.routes.admin.plugins.log_action"), patch(
            "app.routes.admin.plugins.reload_app_config",
            side_effect=AppConfigReloadUnavailable("unsupported deployment"),
        ):
            response = self.client.post(
                "/admin/plugins/reload",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
