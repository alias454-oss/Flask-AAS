import tempfile
import unittest
from pathlib import Path

from flask import Flask
from sqlalchemy import inspect, text

from app.core.extensions import db
from app.plugins.example.plugin import plugin as example_plugin
from app.plugins.example.models import ExampleSettings
from app.plugins.migrations import PluginMigrationError, PluginMigrationManager


class PluginMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "plugin-migrations.db"

        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path}",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.manager = PluginMigrationManager(example_plugin.manifest)

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.app_context.pop()
        self.temp_dir.cleanup()

    def test_fresh_namespace_bootstraps_current_schema_and_stamps_head(self):
        self.assertIsNone(self.manager.current_revision())
        self.assertEqual(self.manager.head_revision(), "0001")

        revision = self.manager.upgrade()

        self.assertEqual(revision, "0001")
        self.assertTrue(self.manager.schema_current())
        table_names = set(inspect(db.engine).get_table_names())
        self.assertIn("plugin_example_settings", table_names)
        self.assertIn("plugin_example_items", table_names)
        self.assertIn("plugin_example_alembic_version", table_names)
        self.assertNotIn("users", table_names)

    def test_upgrade_is_idempotent_at_head(self):
        self.assertEqual(self.manager.upgrade(), "0001")
        self.assertEqual(self.manager.upgrade(), "0001")
        self.assertEqual(self.manager.current_revision(), "0001")

    def test_existing_unversioned_plugin_tables_fail_closed(self):
        ExampleSettings.__table__.create(bind=db.engine, checkfirst=True)

        with self.assertRaisesRegex(PluginMigrationError, "unversioned owned tables"):
            self.manager.upgrade()

        self.assertIsNone(self.manager.current_revision())
        self.assertFalse(
            inspect(db.engine).has_table(example_plugin.manifest.version_table)
        )

    def test_plugin_upgrade_does_not_touch_unrelated_tables(self):
        with db.engine.begin() as connection:
            connection.execute(text("CREATE TABLE core_sentinel (id INTEGER PRIMARY KEY)"))

        self.manager.upgrade()

        self.assertIn("core_sentinel", inspect(db.engine).get_table_names())

    def test_explicit_downgrade_uses_plugin_history_only(self):
        self.manager.upgrade()

        revision = self.manager.downgrade("base")

        self.assertIsNone(revision)
        table_names = set(inspect(db.engine).get_table_names())
        self.assertNotIn("plugin_example_settings", table_names)
        self.assertNotIn("plugin_example_items", table_names)


if __name__ == "__main__":
    unittest.main()
