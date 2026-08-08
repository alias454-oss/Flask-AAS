"""Generic Flask-AAS CLI dispatch for registered application plugins."""

from __future__ import annotations

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


def _invoke_plugin_cli(plugin_id: str, args: Sequence[str]) -> None:
    """Invoke a plugin-owned Click surface without adding its commands to core."""

    plugin = _registered_plugin(plugin_id)

    try:
        command = plugin.get_cli()
    except Exception as exc:
        logger.exception("Plugin %s failed while exposing its CLI", plugin_id)
        raise click.ClickException(
            f"Plugin {plugin_id!r} CLI is unavailable. Check application logs."
        ) from exc

    if command is None:
        raise click.ClickException(f"Plugin {plugin_id!r} does not provide CLI commands.")
    if not isinstance(command, click.Command):
        raise click.ClickException(
            f"Plugin {plugin_id!r} returned an invalid CLI command surface."
        )

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
    """Run a plugin-owned command: plugin run PLUGIN_ID [ARGS]..."""

    _invoke_plugin_cli(plugin_id, plugin_args)
