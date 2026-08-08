# plugins/example/cli.py
"""Plugin-owned CLI commands for the Plugin API reference application."""

import click
from sqlalchemy import func, select

from app.core.extensions import db
from app.models.plugin import PluginRegistration
from app.plugins.migrations import PluginMigrationError, PluginMigrationManager
from app.plugins.registry import refresh_configuration
from app.plugins.example.models import (
    DEFAULT_GREETING,
    ExampleItem,
    ExampleSettings,
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


def _migration_manager() -> PluginMigrationManager:
    # Import lazily because plugin.py imports this CLI module.
    from app.plugins.example.plugin import plugin

    return PluginMigrationManager(plugin.manifest)


def _require_current_schema() -> PluginMigrationManager:
    try:
        manager = _migration_manager()
        current = manager.schema_current()
    except PluginMigrationError as exc:
        raise click.ClickException(str(exc)) from exc

    if not current:
        raise click.ClickException(
            "Example schema is not current. Run "
            "'python manage.py plugin run example db upgrade'."
        )
    return manager


def _refresh_host_configuration(registration: PluginRegistration):
    # Import lazily to avoid a module cycle while plugin.py imports this CLI.
    from app.plugins.example.plugin import plugin

    return refresh_configuration(registration, plugin)


@cli.group("db")
def database_commands():
    """Manage Example-owned database schema migrations."""


@database_commands.command("current")
def database_current():
    """Show the current and head Example schema revisions."""

    try:
        manager = _migration_manager()
        current = manager.current_revision()
        head = manager.head_revision()
    except PluginMigrationError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"current={current or '<base>'}")
    click.echo(f"head={head or '<none>'}")


@database_commands.command("upgrade")
@click.argument("revision", required=False, default="head")
def database_upgrade(revision: str):
    """Upgrade the Example schema, bootstrapping a fresh namespace at head."""

    try:
        current = _migration_manager().upgrade(revision)
    except PluginMigrationError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Example schema revision={current or '<base>'}")


@database_commands.command("downgrade")
@click.argument("revision", required=False, default="-1")
def database_downgrade(revision: str):
    """Explicitly downgrade the Example-owned schema."""

    try:
        current = _migration_manager().downgrade(revision)
    except PluginMigrationError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Example schema revision={current or '<base>'}")


@database_commands.command("migrate")
@click.option("-m", "--message", required=True, help="Migration revision message.")
def database_migrate(message: str):
    """Autogenerate a new Example-owned migration revision."""

    try:
        revision = _migration_manager().migrate(message)
    except PluginMigrationError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Generated Example migration {revision}.")


@cli.command("status")
def status():
    """Show reference configuration and persistence state without secrets."""

    registration = PluginRegistration.query.filter_by(plugin_id="example").first()
    try:
        manager = _migration_manager()
        current_revision = manager.current_revision()
        head_revision = manager.head_revision()
        schema_ready = bool(head_revision and current_revision == head_revision)
    except PluginMigrationError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo("Example Application plugin CLI is available.")
    click.echo(f"registered={'yes' if registration is not None else 'no'}")
    click.echo(
        f"configured={'yes' if registration is not None and registration.configured else 'no'}"
    )
    click.echo(f"schema={'current' if schema_ready else 'needs-migration'}")
    click.echo(f"schema_revision={current_revision or '<base>'}")
    click.echo(f"schema_head={head_revision or '<none>'}")

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

    registration = _registration()
    _require_current_schema()

    managed_secret = click.prompt(
        "Managed secret",
        hide_input=True,
        confirmation_prompt=True,
    )
    if not managed_secret:
        raise click.ClickException("Managed secret must not be empty.")

    try:
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

    _require_current_schema()
    db.session.add(ExampleItem(value=value))
    db.session.commit()
    click.echo("Example item added.")
