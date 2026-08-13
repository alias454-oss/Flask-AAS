import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.core.extensions import db
from app.core.seeder import seed_bundled_plugins
from app.models.plugin import PluginRegistration
from app.plugins.bundled import (
    bundled_plugin_model_modules,
    bundled_plugin_registrations,
)


class BundledPluginSeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(cls.temp_dir.name) / "plugin-bundled-tests.db"

        cls.app = Flask(__name__)
        cls.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path}",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(cls.app)

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

        self.plugins_dir = tempfile.TemporaryDirectory()
        plugins_root = Path(self.plugins_dir.name)
        plugin_dir = plugins_root / "downstream"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            "\n".join(
                (
                    "[plugin]",
                    'id = "downstream"',
                    'name = "Downstream Application"',
                    'version = "1.0.0"',
                    "api_version = 1",
                    'entrypoint = "downstream.plugin:plugin"',
                    "",
                )
            ),
            encoding="utf-8",
        )
        self.bundled_file = str(plugins_root / "bundled.py")

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        self.plugins_dir.cleanup()
        self.app_context.pop()

    def test_catalog_discovers_manifests_without_runtime_import(self):
        with patch("app.plugins.bundled.__file__", self.bundled_file):
            bundled = bundled_plugin_registrations()

        self.assertEqual(len(bundled), 1)
        self.assertEqual(bundled[0].plugin_id, "downstream")
        self.assertEqual(bundled[0].import_path, "downstream.plugin:plugin")
        self.assertEqual(bundled[0].manifest.name, "Downstream Application")

    def test_model_module_declaration_is_derived_from_entrypoint(self):
        with patch("app.plugins.bundled.__file__", self.bundled_file):
            modules = bundled_plugin_model_modules()

        self.assertEqual(modules, ("downstream.models",))

    def test_seed_registers_discovered_plugins_disabled_by_default(self):
        with patch("app.plugins.bundled.__file__", self.bundled_file):
            seed_bundled_plugins()

        record = PluginRegistration.query.filter_by(plugin_id="downstream").one()
        self.assertEqual(record.import_path, "downstream.plugin:plugin")
        self.assertFalse(record.enabled)
        self.assertFalse(record.configured)

    def test_repeat_seed_preserves_administrator_requested_state(self):
        with patch("app.plugins.bundled.__file__", self.bundled_file):
            seed_bundled_plugins()
            record = PluginRegistration.query.filter_by(plugin_id="downstream").one()
            record.enabled = True
            record.configured = True
            db.session.commit()
            seed_bundled_plugins()

        db.session.refresh(record)
        self.assertEqual(PluginRegistration.query.count(), 1)
        self.assertTrue(record.enabled)
        self.assertTrue(record.configured)

    def test_seed_rejects_conflicting_existing_import_path(self):
        db.session.add(
            PluginRegistration(
                plugin_id="downstream",
                import_path="somewhere.else:plugin",
                enabled=False,
                configured=False,
            )
        )
        db.session.commit()

        with patch("app.plugins.bundled.__file__", self.bundled_file):
            with self.assertRaisesRegex(ValueError, "unexpected path"):
                seed_bundled_plugins()


if __name__ == "__main__":
    unittest.main()
