"""Plugin-owned CLI commands for the Plugin API reference application."""

import click
from sqlalchemy import func, inspect, select

from app.core.extensions import db
from app.models.plugin import PluginRegistration
from app.plugins.registry import refresh_configuration
from app.plugins.example.models import (
    DEFAULT_GREETING,
    ExampleItem,
    ExampleSettings,
    ensure_example_schema,
    get_example_settings,
)


@click.group()
def cli():
    """Reference application commands."""


def _registration() -> PluginRegistration:
    registration = PluginRegistration.query.filter_by(plugin_id="example").first()
    if registration is None:
        raise click.ClickException("Plugin 'example' is not registered.")
    return registration


def _refresh_host_configuration(registration: PluginRegistration):
    # Import lazily to avoid a module cycle while plugin.py imports this CLI.
    from app.plugins.example.plugin import plugin

    return refresh_configuration(registration, plugin)


@cli.command("status")
def status():
    """Show reference configuration and persistence state without secrets."""

    registration = PluginRegistration.query.filter_by(plugin_id="example").first()
    schema_ready = inspect(db.engine).has_table(ExampleSettings.__tablename__)

    click.echo("Example Application plugin CLI is available.")
    click.echo(f"registered={'yes' if registration is not None else 'no'}")
    click.echo(
        f"configured={'yes' if registration is not None and registration.configured else 'no'}"
    )
    click.echo(f"persistence={'ready' if schema_ready else 'not-initialized'}")

    if not schema_ready:
        click.echo("greeting=<missing>")
        click.echo("managed_secret=missing")
        click.echo("items=0")
        return

    settings = get_example_settings()
    item_count = db.session.scalar(select(func.count(ExampleItem.id))) or 0
    click.echo(f"greeting={settings.greeting if settings is not None else '<missing>'}")
    click.echo(
        "managed_secret="
        + ("set" if settings is not None and settings.managed_secret else "missing")
    )
    click.echo(f"items={item_count}")


@cli.command("configure")
@click.option(
    "--greeting",
    default=DEFAULT_GREETING,
    show_default=True,
    help="Ordinary plugin configuration retained across disable/enable cycles.",
)
def configure(greeting: str):
    """Configure Example, prompting securely for its managed credential."""

    greeting = greeting.strip()
    if not greeting:
        raise click.ClickException("Greeting must not be empty.")

    managed_secret = click.prompt(
        "Managed secret",
        hide_input=True,
        confirmation_prompt=True,
    )
    if not managed_secret:
        raise click.ClickException("Managed secret must not be empty.")

    registration = _registration()
    try:
        ensure_example_schema()
        settings = get_example_settings(create=True)
        settings.greeting = greeting
        settings.managed_secret = managed_secret
        configuration = _refresh_host_configuration(registration)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    click.echo(
        "Example configuration saved; "
        f"configured={'yes' if configuration.configured else 'no'}."
    )


@cli.command("add-item")
@click.argument("value")
def add_item(value: str):
    """Create representative plugin-owned business data."""

    value = value.strip()
    if not value:
        raise click.ClickException("Item value must not be empty.")

    ensure_example_schema()
    db.session.add(ExampleItem(value=value))
    db.session.commit()
    click.echo("Example item added.")
