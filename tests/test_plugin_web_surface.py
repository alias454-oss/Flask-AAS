import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.core.extensions import db
from app.models import EnvSettings, PluginRegistration, User
from app.plugins.example import plugin as example_plugin
from app.plugins.interface import PluginConfiguration
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

        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="plugin-web-surface-test",
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path}",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.app.before_request(enforce_plugin_access)

        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        EnvSettings._cached_instance = None

        owner = User(
            username="example-plugin-owner",
            email="example-plugin-owner@example.test",
            hashed_password="not-used",
            activated=True,
            approved=True,
        )
        db.session.add(owner)
        db.session.flush()

        env = EnvSettings(
            user_id=owner.id,
            site_name="Plugin Test",
            site_lang="en",
            site_timezone="UTC",
            description="",
            keywords="",
            users_per_page=20,
            users_stored_path="/tmp/users",
            enable_plugins=True,
        )
        self.registration = PluginRegistration(
            plugin_id="example",
            import_path="app.plugins.example.plugin:plugin",
            enabled=True,
            configured=False,
        )
        db.session.add_all([env, self.registration])
        db.session.commit()

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

        response = client.get("/example/static/example.css")
        self.assertEqual(response.status_code, 200)
        self.assertIn(".example-plugin", response.get_data(as_text=True))

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
