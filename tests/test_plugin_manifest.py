import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from app.plugins import PluginManifestError, load_plugin_manifest
from app.plugins.example import plugin as example_plugin


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_MANIFEST = REPOSITORY_ROOT / "app" / "plugins" / "example" / "plugin.toml"


class PluginManifestTests(unittest.TestCase):
    def _write_manifest(self, content: str) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "plugin.toml"
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_example_manifest_is_canonical_plugin_metadata(self):
        manifest = load_plugin_manifest(EXAMPLE_MANIFEST)

        self.assertEqual(manifest.plugin_id, "example")
        self.assertEqual(manifest.name, "Example Application")
        self.assertEqual(manifest.version, "0.1.0")
        self.assertEqual(manifest.api_version, 1)
        self.assertEqual(
            manifest.entrypoint,
            "app.plugins.example.plugin:plugin",
        )
        self.assertEqual(manifest.migrations, "migrations")
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
        script = textwrap.dedent(
            """
            import sys
            from app.plugins.bundled import bundled_plugin_registrations

            bundled = bundled_plugin_registrations()[0]
            assert bundled.plugin_id == "example"
            assert bundled.import_path == "app.plugins.example.plugin:plugin"
            assert bundled.manifest.name == "Example Application"
            assert "app.plugins.example.plugin" not in sys.modules
            assert "app.plugins.example.models" not in sys.modules
            """
        )

        subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
