import tempfile
import unittest
from pathlib import Path

from flask import Flask

from app.core.extensions import db
from app.core.seeder import seed_bundled_plugins
from app.models.plugin import PluginRegistration
from app.plugins.bundled import bundled_plugin_registrations


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

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        self.app_context.pop()

    def test_example_is_bundled_for_fresh_deployments(self):
        bundled = {entry.plugin_id: entry for entry in bundled_plugin_registrations()}

        self.assertIn("example", bundled)
        self.assertEqual(
            bundled["example"].import_path,
            "app.plugins.example.plugin:plugin",
        )

    def test_seed_registers_bundled_plugins_disabled_by_default(self):
        seed_bundled_plugins()

        record = PluginRegistration.query.filter_by(plugin_id="example").one()
        self.assertEqual(record.import_path, "app.plugins.example.plugin:plugin")
        self.assertFalse(record.enabled)
        self.assertFalse(record.configured)

    def test_repeat_seed_preserves_administrator_requested_state(self):
        seed_bundled_plugins()
        record = PluginRegistration.query.filter_by(plugin_id="example").one()
        record.enabled = True
        record.configured = True
        db.session.commit()

        seed_bundled_plugins()

        db.session.refresh(record)
        self.assertEqual(PluginRegistration.query.count(), 1)
        self.assertTrue(record.enabled)
        self.assertTrue(record.configured)
