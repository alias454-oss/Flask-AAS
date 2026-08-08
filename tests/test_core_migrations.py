import unittest

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text

from app.core.extensions import migrate
from app.core.migrations import (
    core_migration_include_name,
    core_migration_include_object,
)
from app.models.plugin import PluginRegistration
from app.plugins.example.models import ExampleItem, ExampleSettings


class CoreMigrationOwnershipTests(unittest.TestCase):
    def test_core_flask_migrate_configures_plugin_namespace_filters(self):
        self.assertIs(
            migrate.alembic_ctx_kwargs["include_name"],
            core_migration_include_name,
        )
        self.assertIs(
            migrate.alembic_ctx_kwargs["include_object"],
            core_migration_include_object,
        )

    def test_core_autogenerate_owns_registration_and_ignores_plugin_schema(self):
        self.assertIn("plugin_registrations", PluginRegistration.metadata.tables)
        self.assertIn("plugin_example_settings", ExampleSettings.metadata.tables)
        self.assertIn("plugin_example_items", ExampleItem.metadata.tables)

        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "CREATE TABLE plugin_example_legacy "
                        "(id INTEGER PRIMARY KEY)"
                    )
                )
                context = MigrationContext.configure(
                    connection,
                    opts={
                        "include_name": core_migration_include_name,
                        "include_object": core_migration_include_object,
                    },
                )
                diffs = compare_metadata(context, PluginRegistration.metadata)
        finally:
            engine.dispose()

        added_tables = {
            diff[1].name
            for diff in diffs
            if diff[0] == "add_table"
        }
        removed_tables = {
            diff[1].name
            for diff in diffs
            if diff[0] == "remove_table"
        }

        self.assertIn("plugin_registrations", added_tables)
        self.assertNotIn("plugin_example_settings", added_tables)
        self.assertNotIn("plugin_example_items", added_tables)
        self.assertNotIn("plugin_example_legacy", removed_tables)


if __name__ == "__main__":
    unittest.main()
