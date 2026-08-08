"""Plugin-owned CLI commands for the Plugin API reference application."""

import click


@click.group()
def cli():
    """Reference application commands."""


@cli.command("status")
def status():
    """Confirm that the reference plugin CLI surface is available."""

    click.echo("Example Application plugin CLI is available.")
