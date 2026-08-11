import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Blueprint, Flask, g, render_template
from flask_login import LoginManager
from jinja2 import ChoiceLoader, DictLoader

from app.core.extensions import db
from app.models import EnvSettings, PluginRegistration, Role, User
from app.plugins.example import plugin as example_plugin
from app.plugins.example.models import ExampleItem, ExampleSettings
from app.plugins.interface import PluginConfiguration
from app.plugins.migrations import PluginMigrationManager
from app.plugins.loader import (
    STATUS_ACTIVE,
    STATUS_DISABLED,
    STATUS_NEEDS_CONFIGURATION,
    enforce_plugin_access,
    initialize_plugins,
)


class ExamplePluginWebSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "example-plugin-web.db"

        host_templates = Path(__file__).resolve().parents[1] / "app" / "templates"
        self.app = Flask(__name__, template_folder=str(host_templates))
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="plugin-web-surface-test",
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path}",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)

        self.login_manager = LoginManager()
        self.login_manager.login_view = "login.login"
        self.login_manager.init_app(self.app)

        @self.login_manager.user_loader
        def load_user(session_id):
            return User.load_from_session_id(
                session_id,
                require_session_record=False,
            )

        self.app.before_request(enforce_plugin_access)

        for blueprint_name, route_path in (
            ("index", "/host-index"),
            ("about", "/host-about"),
            ("login", "/host-login"),
        ):
            blueprint = Blueprint(blueprint_name, __name__)
            blueprint.add_url_rule(
                route_path,
                endpoint=blueprint_name,
                view_func=lambda: "",
            )
            self.app.register_blueprint(blueprint)

        @self.app.context_processor
        def inject_host_template_context():
            return {
                "tpl_path": "themes/default",
                "env": SimpleNamespace(
                    site_name="Plugin Test",
                    description="",
                    keywords="",
                    contact_enabled=False,
                    allow_registration=False,
                ),
                "nonce": "",
                "sidebar_position": "none",
                "current_user": SimpleNamespace(is_authenticated=False),
                "current_year": 2026,
                "page_gen_time": 0,
            }

        self.app_context = self.app.app_context()
        self.app_context.push()
        core_tables = [
            table
            for table in db.metadata.tables.values()
            if not table.name.startswith(example_plugin.manifest.table_prefix)
        ]
        db.metadata.create_all(bind=db.engine, tables=core_tables)
        PluginMigrationManager(example_plugin.manifest).upgrade()
        EnvSettings._cached_instance = None

        self.owner = User(
            username="example-plugin-owner",
            email="example-plugin-owner@example.test",
            hashed_password="not-used",
            activated=True,
            approved=True,
        )
        self.app_user_role = Role(
            name="app_user",
            description="Example coarse application-access role",
        )
        db.session.add_all([self.owner, self.app_user_role])
        db.session.flush()

        env = EnvSettings(
            user_id=self.owner.id,
            site_name="Plugin Test",
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
            greeting="Hello from persisted Example config",
            managed_secret="reference-managed-secret",
        )
        self.example_item = ExampleItem(value="persisted business data")
        self.registration = PluginRegistration(
            plugin_id="example",
            import_path="app.plugins.example.plugin:plugin",
            enabled=True,
            configured=False,
        )
        db.session.add_all(
            [env, self.example_settings, self.example_item, self.registration]
        )
        db.session.commit()

    def _login_owner(self, client):
        with client.session_transaction() as flask_session:
            flask_session["_user_id"] = self.owner.get_id()
            flask_session["_fresh"] = True

        # This test keeps one application context open across requests. Clear
        # Flask-Login's cached anonymous user so the next request reloads the
        # authenticated identity from the session.
        g.pop("_login_user", None)

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        EnvSettings._cached_instance = None
        self.app_context.pop()
        self.temp_dir.cleanup()

    def test_disabled_reference_plugin_does_not_register_blueprint(self):
        self.registration.enabled = False
        db.session.commit()

        runtime = initialize_plugins(self.app)

        self.assertEqual(runtime.plugins["example"].status, STATUS_DISABLED)
        self.assertNotIn("example.index", self.app.view_functions)
        self.assertNotIn("example.static", self.app.view_functions)
        self.assertEqual(self.app.test_client().get("/example/").status_code, 404)

    def test_reference_plugin_serves_template_and_static_asset(self):
        runtime = initialize_plugins(self.app)

        self.assertEqual(runtime.plugins["example"].status, STATUS_ACTIVE)
        self.assertEqual(runtime.plugin_for_endpoint("example.index"), "example")
        self.assertEqual(runtime.plugin_for_endpoint("example.static"), "example")

        client = self.app.test_client()
        response = client.get("/example/")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Example Application", body)
        self.assertIn("Plugin ID", body)
        self.assertIn("example", body)
        self.assertIn("Plugin API", body)
        self.assertIn("v1", body)
        self.assertIn("Hello from persisted Example config", body)
        self.assertIn("Stored Items", body)
        self.assertIn(">1<", body)
        host_css = "/static/themes/default/style.css"
        plugin_css = "/example/static/example.css"
        self.assertIn(host_css, body)
        self.assertIn(plugin_css, body)
        self.assertLess(body.index(host_css), body.index(plugin_css))
        self.assertIn('class="site-header"', body)
        self.assertIn('class="site-main"', body)
        self.assertIn('class="site-footer"', body)

        response = client.get("/example/static/example.css")
        self.assertEqual(response.status_code, 200)
        self.assertIn(".example-plugin", response.get_data(as_text=True))

    def test_plugin_base_preserves_selected_theme_extra_styles(self):
        original_loader = self.app.jinja_loader
        self.app.jinja_loader = ChoiceLoader(
            [
                DictLoader(
                    {
                        "themes/audit/base.html": """
<!doctype html>
<html>
<head>
{% block extra_styles %}<link rel=\"stylesheet\" href=\"/audit-theme.css\">{% endblock %}
</head>
<body>{% block header %}{% endblock %}{% block content %}{% endblock %}{% block footer %}{% endblock %}</body>
</html>
""",
                        "themes/audit/header.html": "<header>Audit Header</header>",
                        "themes/audit/footer.html": "<footer>Audit Footer</footer>",
                        "audit-plugin-page.html": """
{% extends "plugins/base.html" %}
{% block plugin_styles %}<link rel=\"stylesheet\" href=\"/audit-plugin.css\">{% endblock %}
{% block content %}<main>Audit Plugin</main>{% endblock %}
""",
                    }
                ),
                original_loader,
            ]
        )
        try:
            with self.app.test_request_context("/"):
                body = render_template(
                    "audit-plugin-page.html",
                    tpl_path="themes/audit",
                )
        finally:
            self.app.jinja_loader = original_loader
            self.app.jinja_env.cache.clear()

        self.assertIn('/audit-theme.css', body)
        self.assertIn('/audit-plugin.css', body)
        self.assertLess(body.index('/audit-theme.css'), body.index('/audit-plugin.css'))
        self.assertIn('Audit Header', body)
        self.assertIn('Audit Plugin', body)
        self.assertIn('Audit Footer', body)

    def test_reference_plugin_owns_route_authorization_policy(self):
        runtime = initialize_plugins(self.app)

        self.assertEqual(runtime.plugins["example"].status, STATUS_ACTIVE)

        client = self.app.test_client()

        # Plugin lifecycle allows the application to run, but the plugin owns
        # authorization for each of its routes. The index remains public.
        self.assertEqual(client.get("/example/").status_code, 200)

        response = client.get("/example/authenticated")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/host-login", response.headers["Location"])

        self._login_owner(client)
        response = client.get("/example/authenticated")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_data(as_text=True),
            "Authenticated Example user",
        )

        # A plugin may use a host role for coarse admission without teaching
        # Flask-AAS any application-specific role or permission semantics.
        self.assertEqual(client.get("/example/restricted").status_code, 403)

        self.owner.roles.append(self.app_user_role)
        db.session.commit()

        response = client.get("/example/restricted")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_data(as_text=True),
            "Restricted Example user",
        )

    def test_reference_plugin_surface_tracks_configuration_and_disable_state(self):
        pending = PluginConfiguration(
            configured=False,
            reason="Reference plugin configuration is pending",
        )
        with patch.object(example_plugin, "validate_config", return_value=pending):
            runtime = initialize_plugins(self.app)

        db.session.refresh(self.registration)
        self.assertEqual(
            runtime.plugins["example"].status,
            STATUS_NEEDS_CONFIGURATION,
        )
        self.assertFalse(self.registration.configured)
        self.assertIn("example.index", self.app.view_functions)

        client = self.app.test_client()

        # The Blueprint is structurally loaded, but access is gated while the
        # plugin remains unconfigured.
        self.assertEqual(client.get("/example/").status_code, 404)
        self.assertEqual(
            client.get("/example/static/example.css").status_code,
            404,
        )

        # Configuration can become valid without mutating Flask's route map.
        self.registration.configured = True
        db.session.commit()
        self.assertEqual(client.get("/example/").status_code, 200)
        self.assertEqual(
            client.get("/example/static/example.css").status_code,
            200,
        )

        # Disabling immediately closes the already-loaded application surface.
        self.registration.enabled = False
        db.session.commit()
        self.assertEqual(client.get("/example/").status_code, 404)
        self.assertEqual(
            client.get("/example/static/example.css").status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
