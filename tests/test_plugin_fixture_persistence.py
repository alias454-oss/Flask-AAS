import tempfile
import unittest
from pathlib import Path

from flask import Flask
from sqlalchemy import inspect

from app.core.extensions import db
from app.models.plugin import PluginRegistration
from tests.fixtures.plugin_app.models import ExampleItem, ExampleSettings
from tests.fixtures.plugin_app.plugin import plugin as example_plugin
from app.plugins.migrations import PluginMigrationManager
from app.plugins.registry import disable_plugin, enable_plugin, refresh_configuration


class ExamplePluginPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(cls.temp_dir.name) / "example-plugin-persistence.db"

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
        with db.engine.begin() as connection:
            connection.exec_driver_sql(
                f"DROP TABLE IF EXISTS {example_plugin.manifest.version_table}"
            )
        core_tables = [
            table
            for table in db.metadata.tables.values()
            if not table.name.startswith(example_plugin.manifest.table_prefix)
        ]
        db.metadata.create_all(bind=db.engine, tables=core_tables)
        PluginMigrationManager(example_plugin.manifest).upgrade()

        self.registration = PluginRegistration(
            plugin_id="example",
            import_path="tests.fixtures.plugin_app.plugin:plugin",
            enabled=True,
            configured=False,
        )
        self.settings = ExampleSettings(
            id=1,
            greeting="Persistent ordinary configuration",
            managed_secret="managed-secret-to-clear",
        )
        self.item = ExampleItem(value="business-data-survives-disable")
        db.session.add_all([self.registration, self.settings, self.item])
        db.session.commit()

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        self.app_context.pop()

    def test_validate_config_derives_state_from_plugin_owned_settings(self):
        configuration = refresh_configuration(self.registration, example_plugin)
        db.session.commit()

        self.assertTrue(configuration.configured)
        self.assertTrue(self.registration.configured)

        self.settings.managed_secret = None
        configuration = refresh_configuration(self.registration, example_plugin)
        db.session.commit()

        self.assertFalse(configuration.configured)
        self.assertFalse(self.registration.configured)
        self.assertIn("managed secret is missing", configuration.reason.lower())

    def test_disable_wipes_managed_secret_but_preserves_config_and_business_data(self):
        refresh_configuration(self.registration, example_plugin)
        db.session.commit()
        self.assertTrue(self.registration.configured)

        configuration = disable_plugin(self.registration, example_plugin)
        db.session.commit()

        db.session.refresh(self.registration)
        db.session.refresh(self.settings)
        self.assertFalse(self.registration.enabled)
        self.assertFalse(self.registration.configured)
        self.assertFalse(configuration.configured)
        self.assertEqual(
            self.settings.greeting,
            "Persistent ordinary configuration",
        )
        self.assertIsNone(self.settings.managed_secret)
        self.assertEqual(ExampleItem.query.count(), 1)
        self.assertEqual(
            ExampleItem.query.one().value,
            "business-data-survives-disable",
        )

    def test_enable_does_not_install_plugin_schema(self):
        ExampleItem.__table__.drop(bind=db.engine, checkfirst=True)
        ExampleSettings.__table__.drop(bind=db.engine, checkfirst=True)
        with db.engine.begin() as connection:
            connection.exec_driver_sql(
                f"DROP TABLE IF EXISTS {example_plugin.manifest.version_table}"
            )
        self.registration.enabled = False
        self.registration.configured = False
        db.session.commit()

        configuration = enable_plugin(self.registration, example_plugin)
        db.session.commit()

        self.assertTrue(self.registration.enabled)
        self.assertFalse(configuration.configured)
        self.assertIn("db upgrade", configuration.reason)
        table_names = inspect(db.engine).get_table_names()
        self.assertNotIn("plugin_example_settings", table_names)
        self.assertNotIn("plugin_example_items", table_names)
        self.assertNotIn(example_plugin.manifest.version_table, table_names)

    def test_disable_before_schema_install_succeeds(self):
        ExampleItem.__table__.drop(bind=db.engine, checkfirst=True)
        ExampleSettings.__table__.drop(bind=db.engine, checkfirst=True)
        with db.engine.begin() as connection:
            connection.exec_driver_sql(
                f"DROP TABLE IF EXISTS {example_plugin.manifest.version_table}"
            )
        self.registration.enabled = True
        self.registration.configured = False
        db.session.commit()

        configuration = disable_plugin(self.registration, example_plugin)
        db.session.commit()

        self.assertFalse(self.registration.enabled)
        self.assertFalse(self.registration.configured)
        self.assertFalse(configuration.configured)

    def test_reenable_requires_managed_secret_to_be_resupplied(self):
        disable_plugin(self.registration, example_plugin)
        db.session.commit()

        configuration = enable_plugin(self.registration, example_plugin)
        db.session.commit()

        self.assertTrue(self.registration.enabled)
        self.assertFalse(self.registration.configured)
        self.assertFalse(configuration.configured)

        self.settings.managed_secret = "replacement-managed-secret"
        configuration = refresh_configuration(self.registration, example_plugin)
        db.session.commit()

        self.assertTrue(configuration.configured)
        self.assertTrue(self.registration.configured)
        self.assertEqual(
            self.settings.greeting,
            "Persistent ordinary configuration",
        )
        self.assertEqual(ExampleItem.query.count(), 1)


if __name__ == "__main__":
    unittest.main()
