import tempfile
import textwrap
import unittest
from pathlib import Path

from app.plugins import PluginManifestError, load_plugin_manifest
from tests.fixtures.plugin_app import plugin as example_plugin


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_MANIFEST = REPOSITORY_ROOT / "tests" / "fixtures" / "plugin_app" / "plugin.toml"


class PluginManifestTests(unittest.TestCase):
    def _write_manifest(self, content: str) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "plugin.toml"
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_fixture_manifest_is_canonical_plugin_metadata(self):
        manifest = load_plugin_manifest(EXAMPLE_MANIFEST)

        self.assertEqual(manifest.plugin_id, "example")
        self.assertEqual(manifest.name, "Example Application")
        self.assertEqual(manifest.version, "0.1.0")
        self.assertEqual(manifest.api_version, 1)
        self.assertEqual(
            manifest.entrypoint,
            "tests.fixtures.plugin_app.plugin:plugin",
        )
        self.assertEqual(manifest.migrations, "migrations")
        self.assertIsNone(manifest.navigation_label)
        self.assertEqual(manifest.table_prefix, "plugin_example_")
        self.assertEqual(manifest.version_table, "plugin_example_alembic_version")
        self.assertEqual(
            manifest.migration_path,
            EXAMPLE_MANIFEST.parent / "migrations",
        )

        self.assertEqual(example_plugin.manifest, manifest)
        self.assertEqual(example_plugin.plugin_id, manifest.plugin_id)
        self.assertEqual(example_plugin.name, manifest.name)
        self.assertEqual(example_plugin.version, manifest.version)
        self.assertEqual(example_plugin.api_version, manifest.api_version)

    def test_manifest_requires_plugin_table(self):
        path = self._write_manifest(
            """
            name = "Not a plugin table"
            """
        )

        with self.assertRaisesRegex(PluginManifestError, r"\[plugin\]"):
            load_plugin_manifest(path)

    def test_manifest_rejects_invalid_plugin_id(self):
        path = self._write_manifest(
            """
            [plugin]
            id = "Invalid Plugin"
            name = "Invalid"
            version = "1.0.0"
            api_version = 1
            entrypoint = "some.plugin:plugin"
            """
        )

        with self.assertRaisesRegex(PluginManifestError, "plugin.id"):
            load_plugin_manifest(path)

    def test_manifest_requires_integer_api_version(self):
        path = self._write_manifest(
            """
            [plugin]
            id = "fake"
            name = "Fake"
            version = "1.0.0"
            api_version = "1"
            entrypoint = "some.plugin:plugin"
            """
        )

        with self.assertRaisesRegex(PluginManifestError, "plugin.api_version"):
            load_plugin_manifest(path)

    def test_manifest_accepts_optional_navigation_label(self):
        path = self._write_manifest(
            """
            [plugin]
            id = "fake"
            name = "Fake Application"
            navigation_label = "Fake"
            version = "1.0.0"
            api_version = 1
            entrypoint = "some.plugin:plugin"
            """
        )

        manifest = load_plugin_manifest(path)

        self.assertEqual(manifest.name, "Fake Application")
        self.assertEqual(manifest.navigation_label, "Fake")

    def test_manifest_rejects_empty_navigation_label(self):
        path = self._write_manifest(
            """
            [plugin]
            id = "fake"
            name = "Fake Application"
            navigation_label = "   "
            version = "1.0.0"
            api_version = 1
            entrypoint = "some.plugin:plugin"
            """
        )

        with self.assertRaisesRegex(PluginManifestError, "plugin.navigation_label"):
            load_plugin_manifest(path)

    def test_manifest_rejects_migration_path_escape(self):
        path = self._write_manifest(
            """
            [plugin]
            id = "fake"
            name = "Fake"
            version = "1.0.0"
            api_version = 1
            entrypoint = "some.plugin:plugin"
            migrations = "../outside"
            """
        )

        with self.assertRaisesRegex(PluginManifestError, "plugin.migrations"):
            load_plugin_manifest(path)

    def test_manifest_rejects_invalid_entrypoint(self):
        path = self._write_manifest(
            """
            [plugin]
            id = "fake"
            name = "Fake"
            version = "1.0.0"
            api_version = 1
            entrypoint = "not-an-entrypoint"
            """
        )

        with self.assertRaisesRegex(PluginManifestError, "plugin.entrypoint"):
            load_plugin_manifest(path)

    def test_bundled_catalog_reads_manifest_without_importing_plugin(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temp_dir:
            plugins_root = Path(temp_dir)
            plugin_dir = plugins_root / "downstream"
            plugin_dir.mkdir()
            (plugin_dir / "plugin.toml").write_text(
                textwrap.dedent(
                    """
                    [plugin]
                    id = "downstream"
                    name = "Downstream Application"
                    version = "1.0.0"
                    api_version = 1
                    entrypoint = "does.not.exist.plugin:plugin"
                    """
                ),
                encoding="utf-8",
            )

            with patch(
                "app.plugins.bundled.__file__",
                str(plugins_root / "bundled.py"),
            ):
                from app.plugins.bundled import bundled_plugin_registrations

                bundled = bundled_plugin_registrations()

        self.assertEqual(len(bundled), 1)
        self.assertEqual(bundled[0].plugin_id, "downstream")
        self.assertEqual(bundled[0].import_path, "does.not.exist.plugin:plugin")
        self.assertEqual(bundled[0].manifest.name, "Downstream Application")



if __name__ == "__main__":
    unittest.main()
