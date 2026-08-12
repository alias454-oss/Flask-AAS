# plugins/migrations.py
from pathlib import Path
import shutil
import tempfile

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import inspect

from app.core.extensions import db
from app.plugins.manifest import PluginManifest


_PLUGIN_SCRIPT_TEMPLATE = '''"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, Sequence[str], None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    """Upgrade schema."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Downgrade schema."""
    ${downgrades if downgrades else "pass"}
'''


class PluginMigrationError(RuntimeError):
    """Raised when plugin-owned migration state cannot be handled safely."""


class PluginMigrationManager:
    """Execute one plugin's independent Alembic history against the host database."""

    def __init__(self, manifest: PluginManifest):
        if manifest.migration_path is None:
            raise PluginMigrationError(
                f"Plugin {manifest.plugin_id!r} does not declare migrations"
            )
        self.manifest = manifest

    @property
    def script_location(self) -> Path:
        migration_path = self.manifest.migration_path
        if migration_path is None:
            raise PluginMigrationError(
                f"Plugin {self.manifest.plugin_id!r} does not declare migrations"
            )
        return migration_path

    def initialized(self) -> bool:
        """Return whether the declared plugin migration environment is complete."""

        path = self.script_location
        return bool(
            path.is_dir()
            and (path / "env.py").is_file()
            and (path / "script.py.mako").is_file()
            and (path / "versions").is_dir()
        )

    def _require_initialized(self) -> None:
        path = self.script_location
        if self.initialized():
            return

        if not path.exists():
            raise PluginMigrationError(
                f"Plugin migration environment is not initialized: {path}. "
                f"Run 'python manage.py plugin run {self.manifest.plugin_id} db init'."
            )

        raise PluginMigrationError(
            f"Plugin migration environment is incomplete: {path}. Expected env.py, "
            "script.py.mako, and versions/. Reconcile or remove the incomplete "
            f"directory before running 'python manage.py plugin run "
            f"{self.manifest.plugin_id} db init'."
        )

    def initialize(self) -> Path:
        """Create the canonical Alembic environment for one plugin package."""

        path = self.script_location
        if self.initialized():
            raise PluginMigrationError(
                f"Plugin migration environment is already initialized: {path}"
            )

        if path.exists():
            if not path.is_dir():
                raise PluginMigrationError(
                    f"Plugin migration path exists and is not a directory: {path}"
                )
            if any(path.iterdir()):
                raise PluginMigrationError(
                    f"Plugin migration directory is not empty: {path}. Refusing to "
                    "overwrite existing files."
                )

        parent = path.parent
        parent.mkdir(parents=True, exist_ok=True)
        env_source = (
            "# migrations/env.py\n"
            "from alembic import context\n\n"
            "from app.plugins.migrations import run_plugin_migration_environment\n\n\n"
            "run_plugin_migration_environment(context)\n"
        )

        with tempfile.TemporaryDirectory(
            prefix=f".{path.name}-init-",
            dir=parent,
        ) as temp_dir:
            staged = Path(temp_dir) / path.name
            staged.mkdir()
            (staged / "versions").mkdir()
            (staged / "env.py").write_text(env_source, encoding="utf-8")
            (staged / "script.py.mako").write_text(
                _PLUGIN_SCRIPT_TEMPLATE,
                encoding="utf-8",
            )

            if path.exists():
                path.rmdir()
            shutil.move(str(staged), str(path))

        return path

    def _config(self, *, connection=None) -> Config:
        self._require_initialized()
        config = Config()
        config.set_main_option("script_location", str(self.script_location))
        config.set_main_option("file_template", "%%(rev)s_%%(slug)s")
        config.attributes["target_metadata"] = db.metadata
        config.attributes["plugin_table_prefix"] = self.manifest.table_prefix
        config.attributes["plugin_version_table"] = self.manifest.version_table
        if connection is not None:
            config.attributes["connection"] = connection
        return config

    def head_revision(self) -> str | None:
        try:
            return ScriptDirectory.from_config(self._config()).get_current_head()
        except CommandError as exc:
            raise PluginMigrationError(str(exc)) from exc

    def current_revision(self) -> str | None:
        with db.engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"version_table": self.manifest.version_table},
            )
            try:
                return context.get_current_revision()
            except CommandError as exc:
                raise PluginMigrationError(str(exc)) from exc

    def schema_current(self) -> bool:
        head = self.head_revision()
        if head is None:
            return False
        return self.current_revision() == head

    def _owned_tables(self, connection) -> tuple[str, ...]:
        names = inspect(connection).get_table_names()
        return tuple(
            sorted(
                name
                for name in names
                if name.startswith(self.manifest.table_prefix)
                and name != self.manifest.version_table
            )
        )

    def _model_tables(self):
        return [
            table
            for table in db.metadata.tables.values()
            if table.name.startswith(self.manifest.table_prefix)
            and table.name != self.manifest.version_table
        ]

    def _bootstrap_head(self, connection) -> None:
        head = self.head_revision()
        if head is None:
            raise PluginMigrationError(
                f"Plugin {self.manifest.plugin_id!r} has no migration head"
            )

        model_tables = self._model_tables()
        if not model_tables:
            raise PluginMigrationError(
                f"Plugin {self.manifest.plugin_id!r} exposes no owned model tables "
                "for fresh-schema bootstrap"
            )

        db.metadata.create_all(
            bind=connection,
            tables=model_tables,
            checkfirst=True,
        )
        command.stamp(self._config(connection=connection), head)

    def upgrade(self, revision: str = "head") -> str | None:
        """Upgrade or bootstrap this plugin without touching another schema owner."""

        try:
            with db.engine.begin() as connection:
                inspector = inspect(connection)
                version_table_present = inspector.has_table(self.manifest.version_table)
                owned_tables = self._owned_tables(connection)

                if not version_table_present and owned_tables:
                    raise PluginMigrationError(
                        f"Plugin {self.manifest.plugin_id!r} has unversioned owned tables: "
                        f"{', '.join(owned_tables)}. This pre-release database must be "
                        "reset or explicitly reconciled before migration adoption."
                    )

                if (
                    revision == "head"
                    and not version_table_present
                    and not owned_tables
                ):
                    self._bootstrap_head(connection)
                else:
                    command.upgrade(
                        self._config(connection=connection),
                        revision,
                    )
        except PluginMigrationError:
            raise
        except CommandError as exc:
            raise PluginMigrationError(str(exc)) from exc

        return self.current_revision()

    def downgrade(self, revision: str = "-1") -> str | None:
        """Explicitly downgrade this plugin's migration history."""

        try:
            with db.engine.begin() as connection:
                command.downgrade(
                    self._config(connection=connection),
                    revision,
                )
        except CommandError as exc:
            raise PluginMigrationError(str(exc)) from exc

        return self.current_revision()

    def migrate(self, message: str) -> str:
        """Autogenerate a plugin-owned revision against only its table namespace."""

        message = message.strip()
        if not message:
            raise PluginMigrationError("Migration message must not be empty")

        try:
            with db.engine.begin() as connection:
                script = command.revision(
                    self._config(connection=connection),
                    message=message,
                    autogenerate=True,
                )
        except CommandError as exc:
            raise PluginMigrationError(str(exc)) from exc

        revision = getattr(script, "revision", None)
        if not isinstance(revision, str) or not revision:
            raise PluginMigrationError("Alembic did not return a generated revision")
        return revision


def run_plugin_migration_environment(alembic_context) -> None:
    """Run an Alembic environment constrained to one plugin-owned namespace."""

    if alembic_context.is_offline_mode():
        raise PluginMigrationError("Plugin migrations require an application database connection")

    config = alembic_context.config
    target_metadata = config.attributes.get("target_metadata")
    table_prefix = config.attributes.get("plugin_table_prefix")
    version_table = config.attributes.get("plugin_version_table")

    if target_metadata is None or not isinstance(table_prefix, str) or not table_prefix:
        raise PluginMigrationError("Plugin migration configuration is incomplete")
    if not isinstance(version_table, str) or not version_table:
        raise PluginMigrationError("Plugin migration version table is not configured")

    def owns_table(name: str | None) -> bool:
        return bool(
            name
            and name.startswith(table_prefix)
            and name != version_table
        )

    def include_name(name, type_, parent_names):
        if type_ == "table":
            return owns_table(name)
        return True

    def include_object(obj, name, type_, reflected, compare_to):
        if type_ == "table":
            return owns_table(name)

        table = getattr(obj, "table", None)
        if table is not None:
            return owns_table(table.name)
        return True

    def run(connection) -> None:
        alembic_context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=version_table,
            include_name=include_name,
            include_object=include_object,
            compare_type=True,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with alembic_context.begin_transaction():
            alembic_context.run_migrations()

    connection = config.attributes.get("connection")
    if connection is not None:
        run(connection)
        return

    with db.engine.connect() as connection:
        run(connection)
