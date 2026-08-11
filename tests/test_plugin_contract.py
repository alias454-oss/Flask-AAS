import tempfile
import unittest
from pathlib import Path

from flask import Flask

from app.core.extensions import db
from app.models.plugin import PluginRegistration
from app.plugins import (
    PLUGIN_API_VERSION,
    ApplicationPlugin,
    PluginCompatibilityError,
    PluginConfiguration,
    PluginContractError,
    PluginDataset,
    PluginDatasetActionResult,
    validate_dataset_action_result,
    validate_plugin_contract,
    validate_plugin_datasets,
)
from app.plugins.example import plugin as example_plugin
from app.plugins.registry import (
    PluginRegistrationError,
    disable_plugin,
    enable_plugin,
    register_plugin,
)


class FakePlugin(ApplicationPlugin):
    plugin_id = "fake"
    name = "Fake Application"
    version = "1.0.0"
    api_version = PLUGIN_API_VERSION

    def __init__(self, *, managed_secret=True, external_secret=True):
        self.managed_secret = managed_secret
        self.external_secret = external_secret
        self.clear_calls = 0
        self.register_calls = 0

    def validate_config(self):
        if not self.managed_secret:
            return PluginConfiguration(
                configured=False,
                reason="A required plugin-managed secret is not configured",
            )
        return PluginConfiguration(configured=True)

    def clear_secrets(self):
        self.clear_calls += 1
        self.managed_secret = False
        # Deployment-owned/provider-owned credentials are outside this method.
        # Keep the test flag untouched to assert that ownership boundary.

    def register(self, app):
        self.register_calls += 1


class ImplicitApiVersionPlugin(ApplicationPlugin):
    plugin_id = "implicit-api"
    name = "Implicit API Application"
    version = "1.0.0"

    def validate_config(self):
        return PluginConfiguration(configured=True)

    def clear_secrets(self):
        return None

    def register(self, app):
        return None


class IncompatiblePlugin(FakePlugin):
    plugin_id = "incompatible"
    api_version = PLUGIN_API_VERSION + 1


class InvalidMetadataPlugin(FakePlugin):
    plugin_id = "Invalid Plugin ID"


class BadConfigReturnPlugin(FakePlugin):
    plugin_id = "bad-config-return"

    def validate_config(self):
        return True




class DatasetPlugin(FakePlugin):
    plugin_id = "dataset-plugin"

    def get_admin_datasets(self):
        return (
            PluginDataset(
                key="reference",
                label="Reference Data",
                description="Optional packaged reference values.",
                status="0 database rows.",
                action_label="Load Data",
            ),
        )

    def run_admin_dataset_action(self, dataset_key):
        if dataset_key != "reference":
            raise KeyError(dataset_key)
        return PluginDatasetActionResult(message="Reference data loaded.")


class InvalidDatasetPlugin(FakePlugin):
    plugin_id = "invalid-dataset-plugin"

    def get_admin_datasets(self):
        return (
            PluginDataset(key="Bad Key", label="Invalid"),
        )


class PluginContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(cls.temp_dir.name) / "plugin-contract-tests.db"

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

    def test_plugin_api_version_is_fixed_at_one(self):
        self.assertEqual(PLUGIN_API_VERSION, 1)

    def test_example_plugin_satisfies_v1_contract(self):
        self.assertIs(validate_plugin_contract(example_plugin), example_plugin)
        status = example_plugin.validate_config()
        
        self.assertIsInstance(status, PluginConfiguration)
        self.assertFalse(status.configured)

    def test_invalid_metadata_is_rejected(self):
        with self.assertRaises(PluginContractError):
            validate_plugin_contract(InvalidMetadataPlugin())

    def test_default_plugin_dataset_surface_is_empty_and_non_blocking(self):
        plugin = FakePlugin()

        self.assertEqual(validate_plugin_datasets(plugin), ())
        self.assertTrue(plugin.validate_config().configured)

    def test_plugin_dataset_descriptors_and_action_results_are_validated(self):
        plugin = DatasetPlugin()

        datasets = validate_plugin_datasets(plugin)
        result = validate_dataset_action_result(
            plugin.run_admin_dataset_action("reference")
        )

        self.assertEqual(datasets[0].key, "reference")
        self.assertEqual(datasets[0].action_label, "Load Data")
        self.assertEqual(result.message, "Reference data loaded.")

    def test_invalid_plugin_dataset_key_is_rejected(self):
        with self.assertRaises(PluginContractError):
            validate_plugin_datasets(InvalidDatasetPlugin())


    def test_plugin_must_declare_api_version_explicitly(self):
        with self.assertRaises(PluginContractError):
            validate_plugin_contract(ImplicitApiVersionPlugin())

    def test_incompatible_plugin_api_is_rejected(self):
        with self.assertRaises(PluginCompatibilityError):
            validate_plugin_contract(IncompatiblePlugin())

    def test_validate_config_must_return_configuration_status(self):
        with self.assertRaises(PluginContractError):
            register_plugin(
                BadConfigReturnPlugin(),
                import_path="app.plugins.bad_config.plugin:plugin",
            )

    def test_registration_persists_disabled_and_configured_state(self):
        plugin = FakePlugin()
        record = register_plugin(
            plugin,
            import_path="app.plugins.fake.plugin:plugin",
        )
        db.session.commit()

        stored = PluginRegistration.query.one()
        self.assertEqual(stored.id, record.id)
        self.assertEqual(stored.plugin_id, "fake")
        self.assertEqual(
            stored.import_path,
            "app.plugins.fake.plugin:plugin",
        )
        self.assertFalse(stored.enabled)
        self.assertTrue(stored.configured)

    def test_registration_is_idempotent_for_same_plugin_and_path(self):
        plugin = FakePlugin()
        first = register_plugin(
            plugin,
            import_path="app.plugins.fake.plugin:plugin",
        )
        db.session.commit()

        second = register_plugin(
            plugin,
            import_path="app.plugins.fake.plugin:plugin",
        )
        db.session.commit()

        self.assertEqual(first.id, second.id)
        self.assertEqual(PluginRegistration.query.count(), 1)

    def test_conflicting_duplicate_plugin_id_is_rejected(self):
        plugin = FakePlugin()
        register_plugin(
            plugin,
            import_path="app.plugins.fake.plugin:plugin",
        )
        db.session.commit()

        with self.assertRaises(PluginRegistrationError):
            register_plugin(
                plugin,
                import_path="somewhere.else:plugin",
            )

    def test_enable_does_not_require_configuration_to_be_valid(self):
        plugin = FakePlugin(managed_secret=False)
        record = register_plugin(
            plugin,
            import_path="app.plugins.fake.plugin:plugin",
        )

        status = enable_plugin(record, plugin)

        self.assertTrue(record.enabled)
        self.assertFalse(record.configured)
        self.assertFalse(status.configured)

    def test_disable_preserves_registration_but_clears_managed_secrets(self):
        plugin = FakePlugin(managed_secret=True, external_secret=True)
        record = register_plugin(
            plugin,
            import_path="app.plugins.fake.plugin:plugin",
        )
        enable_plugin(record, plugin)
        db.session.commit()
        record_id = record.id
        import_path = record.import_path

        status = disable_plugin(record, plugin)
        db.session.commit()

        stored = db.session.get(PluginRegistration, record_id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.import_path, import_path)
        self.assertFalse(stored.enabled)
        self.assertFalse(stored.configured)
        self.assertFalse(status.configured)
        self.assertEqual(plugin.clear_calls, 1)
        self.assertFalse(plugin.managed_secret)
        self.assertTrue(plugin.external_secret)

    def test_reenable_preserves_registration_and_rechecks_configuration(self):
        plugin = FakePlugin()
        record = register_plugin(
            plugin,
            import_path="app.plugins.fake.plugin:plugin",
        )
        enable_plugin(record, plugin)
        disable_plugin(record, plugin)
        self.assertFalse(record.configured)

        # Simulate the administrator restoring the plugin-managed secret while
        # leaving the registration itself intact.
        plugin.managed_secret = True
        status = enable_plugin(record, plugin)

        self.assertTrue(record.enabled)
        self.assertTrue(record.configured)
        self.assertTrue(status.configured)
        self.assertEqual(PluginRegistration.query.count(), 1)

    def test_disable_cleanup_failure_does_not_mark_registration_disabled(self):
        class FailingCleanupPlugin(FakePlugin):
            plugin_id = "cleanup-failure"

            def clear_secrets(self):
                raise RuntimeError("secret cleanup failed")

        plugin = FailingCleanupPlugin()
        record = register_plugin(
            plugin,
            import_path="app.plugins.cleanup_failure.plugin:plugin",
        )
        enable_plugin(record, plugin)
        self.assertTrue(record.enabled)

        with self.assertRaisesRegex(RuntimeError, "secret cleanup failed"):
            disable_plugin(record, plugin)

        self.assertTrue(record.enabled)
        self.assertTrue(record.configured)


if __name__ == "__main__":
    unittest.main()
