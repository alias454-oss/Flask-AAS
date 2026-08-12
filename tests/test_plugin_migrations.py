import tempfile
import unittest
from pathlib import Path

from flask import Flask
from sqlalchemy import inspect, text

from app.core.extensions import db
from app.plugins.example.plugin import plugin as example_plugin
from app.plugins.example.models import ExampleSettings
from app.plugins.manifest import PluginManifest
from app.plugins.migrations import PluginMigrationError, PluginMigrationManager


class PluginMigrationInitializationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.plugin_dir = Path(self.temp_dir.name) / "sample"
        self.plugin_dir.mkdir()
        manifest_path = self.plugin_dir / "plugin.toml"
        manifest_path.write_text("[plugin]\n", encoding="utf-8")
        self.manifest = PluginManifest(
            plugin_id="sample",
            name="Sample Application",
            version="0.1.0",
            api_version=1,
            entrypoint="sample.plugin:plugin",
            migrations="migrations",
            path=manifest_path,
        )
        self.manager = PluginMigrationManager(self.manifest)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_manager_can_exist_before_migration_environment_is_initialized(self):
        self.assertFalse(self.manager.initialized())

        with self.assertRaisesRegex(
            PluginMigrationError,
            "plugin run sample db init",
        ):
            self.manager.head_revision()

    def test_initialize_creates_canonical_plugin_migration_environment(self):
        migration_path = self.manager.initialize()

        self.assertEqual(migration_path, self.plugin_dir / "migrations")
        self.assertTrue(self.manager.initialized())
        self.assertTrue((migration_path / "versions").is_dir())
        env_source = (migration_path / "env.py").read_text(encoding="utf-8")
        script_source = (migration_path / "script.py.mako").read_text(encoding="utf-8")
        self.assertIn("run_plugin_migration_environment(context)", env_source)
        self.assertIn("revision: str = ${repr(up_revision)}", script_source)
        self.assertIn("${upgrades if upgrades else \"pass\"}", script_source)

    def test_initialize_refuses_to_overwrite_initialized_environment(self):
        self.manager.initialize()

        with self.assertRaisesRegex(PluginMigrationError, "already initialized"):
            self.manager.initialize()

    def test_initialize_refuses_nonempty_partial_environment(self):
        migration_path = self.plugin_dir / "migrations"
        migration_path.mkdir()
        marker = migration_path / "keep.txt"
        marker.write_text("keep me", encoding="utf-8")

        with self.assertRaisesRegex(PluginMigrationError, "not empty"):
            self.manager.initialize()

        self.assertEqual(marker.read_text(encoding="utf-8"), "keep me")


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
