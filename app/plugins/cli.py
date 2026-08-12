# plugins/cli.py
from __future__ import annotations

import copy
import logging
from typing import Sequence

import click
from flask.cli import with_appcontext
from sqlalchemy.exc import SQLAlchemyError

from app.core.extensions import db
from app.models.plugin import PluginRegistration
from app.plugins.interface import (
    PluginCompatibilityError,
    validate_plugin_contract,
)
from app.plugins.loader import resolve_plugin
from app.plugins.migrations import PluginMigrationError, PluginMigrationManager

logger = logging.getLogger(__name__)


@click.group("plugin")
def plugin_cli():
    """Manage and invoke registered Flask-AAS application plugins."""


def _registered_plugin(plugin_id: str):
    """Resolve one explicitly registered plugin for an operator CLI action."""

    try:
        registration = PluginRegistration.query.filter_by(plugin_id=plugin_id).first()
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise click.ClickException(
            "Plugin registry is unavailable; apply pending database migrations."
        ) from exc

    if registration is None:
        raise click.ClickException(f"Plugin {plugin_id!r} is not registered.")

    try:
        plugin = resolve_plugin(registration.import_path)
        validate_plugin_contract(plugin)
    except PluginCompatibilityError as exc:
        raise click.ClickException(str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to load plugin %s for CLI dispatch", plugin_id)
        raise click.ClickException(
            f"Plugin {plugin_id!r} could not be loaded. Check application logs."
        ) from exc

    if plugin.plugin_id != registration.plugin_id:
        raise click.ClickException(
            f"Registered plugin ID {registration.plugin_id!r} does not match "
            f"imported plugin ID {plugin.plugin_id!r}."
        )

    return plugin


def _plugin_migration_commands(plugin) -> click.Group | None:
    """Build the host-owned migration CLI for one manifest-declared plugin."""

    manifest = getattr(plugin, "manifest", None)
    if manifest is None or manifest.migration_path is None:
        return None

    def migration_manager() -> PluginMigrationManager:
        return PluginMigrationManager(manifest)

    @click.group("db")
    def database_commands():
        """Manage this plugin's independent database schema migrations."""

    @database_commands.command("init")
    def database_init():
        """Initialize the plugin-owned Alembic migration environment."""

        try:
            path = migration_manager().initialize()
        except PluginMigrationError as exc:
            raise click.ClickException(str(exc)) from exc

        click.echo(
            f"Initialized {plugin.name} migration environment at {path}."
        )

    @database_commands.command("current")
    def database_current():
        """Show the current and head plugin schema revisions."""

        try:
            manager = migration_manager()
            current = manager.current_revision()
            head = manager.head_revision()
        except PluginMigrationError as exc:
            raise click.ClickException(str(exc)) from exc

        click.echo(f"current={current or '<base>'}")
        click.echo(f"head={head or '<none>'}")

    @database_commands.command("upgrade")
    @click.argument("revision", required=False, default="head")
    def database_upgrade(revision: str):
        """Upgrade the plugin schema, bootstrapping a fresh namespace at head."""

        try:
            current = migration_manager().upgrade(revision)
        except PluginMigrationError as exc:
            raise click.ClickException(str(exc)) from exc

        click.echo(f"{plugin.name} schema revision={current or '<base>'}")

    @database_commands.command("downgrade")
    @click.argument("revision", required=False, default="-1")
    def database_downgrade(revision: str):
        """Explicitly downgrade the plugin-owned schema."""

        try:
            current = migration_manager().downgrade(revision)
        except PluginMigrationError as exc:
            raise click.ClickException(str(exc)) from exc

        click.echo(f"{plugin.name} schema revision={current or '<base>'}")

    @database_commands.command("migrate")
    @click.option(
        "-m",
        "--message",
        required=True,
        help="Migration revision message.",
    )
    def database_migrate(message: str):
        """Autogenerate a new plugin-owned migration revision."""

        try:
            revision = migration_manager().migrate(message)
        except PluginMigrationError as exc:
            raise click.ClickException(str(exc)) from exc

        click.echo(f"Generated {plugin.name} migration {revision}.")

    return database_commands


def _plugin_command_surface(plugin) -> click.Command | None:
    """Compose plugin-owned commands with manifest-driven host capabilities."""

    try:
        command = plugin.get_cli()
    except Exception as exc:
        logger.exception("Plugin %s failed while exposing its CLI", plugin.plugin_id)
        raise click.ClickException(
            f"Plugin {plugin.plugin_id!r} CLI is unavailable. Check application logs."
        ) from exc

    if command is not None and not isinstance(command, click.Command):
        raise click.ClickException(
            f"Plugin {plugin.plugin_id!r} returned an invalid CLI command surface."
        )

    migration_commands = _plugin_migration_commands(plugin)
    if migration_commands is None:
        return command

    if command is None:
        command = click.Group(
            name="cli",
            help=f"{plugin.name} commands.",
        )
    elif not isinstance(command, click.Group):
        raise click.ClickException(
            f"Plugin {plugin.plugin_id!r} declares migrations, so its custom CLI "
            "must be a Click group that can receive host-provided commands."
        )
    else:
        # Do not mutate the plugin-owned singleton command group. A dispatch may
        # happen more than once in one process during tests or operator tooling.
        command = copy.copy(command)
        command.commands = dict(command.commands)

    if "db" in command.commands:
        raise click.ClickException(
            f"Plugin {plugin.plugin_id!r} declares migrations and may not define "
            "the reserved top-level CLI command 'db'; Flask-AAS provides it "
            "automatically from plugin.toml."
        )

    command.add_command(migration_commands)
    return command


def _invoke_plugin_cli(plugin_id: str, args: Sequence[str]) -> None:
    """Invoke a plugin command surface without adding app-specific commands to core."""

    plugin = _registered_plugin(plugin_id)
    command = _plugin_command_surface(plugin)

    if command is None:
        raise click.ClickException(f"Plugin {plugin_id!r} does not provide CLI commands.")

    command_name = "<default>"
    if args:
        first_arg = str(args[0])
        if first_arg in {"--help", "-h"}:
            command_name = "<help>"
        elif first_arg.startswith("-"):
            command_name = "<option>"
        else:
            command_name = first_arg

    logger.info(
        "Dispatching plugin CLI plugin=%s command=%s",
        plugin_id,
        command_name,
    )

    try:
        command.main(
            args=list(args),
            prog_name=f"plugin {plugin_id}",
            standalone_mode=False,
        )
    except click.ClickException:
        raise
    except Exception as exc:
        logger.exception("Plugin %s CLI command failed", plugin_id)
        raise click.ClickException(
            f"Plugin {plugin_id!r} command failed. Check application logs."
        ) from exc


@plugin_cli.command(
    "run",
    context_settings={
        "ignore_unknown_options": True,
        "allow_extra_args": True,
        # ``--help`` after PLUGIN_ID belongs to the plugin command surface.
        "help_option_names": [],
    },
)
@click.argument("plugin_id")
@click.argument("plugin_args", nargs=-1, type=click.UNPROCESSED)
@with_appcontext
def plugin_run(plugin_id: str, plugin_args: tuple[str, ...]):
    """Run a plugin command: plugin run PLUGIN_ID [ARGS]..."""

    _invoke_plugin_cli(plugin_id, plugin_args)
