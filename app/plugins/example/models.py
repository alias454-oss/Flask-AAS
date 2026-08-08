# plugins/example/models.py
"""Plugin-owned persistence for the Flask-AAS reference application."""

from app.core.extensions import db


DEFAULT_GREETING = "Hello from Example"


class ExampleSettings(db.Model):
    """Reference plugin configuration owned by the plugin package."""

    __tablename__ = "plugin_example_settings"

    id = db.Column(db.Integer, primary_key=True)
    greeting = db.Column(
        db.String(255),
        nullable=False,
        default=DEFAULT_GREETING,
    )
    # Reference-only persisted credential used to exercise lifecycle cleanup.
    # Real plugins may instead rely on deployment-owned environment/IAM/external
    # secret-manager credentials, which clear_secrets() must not modify.
    managed_secret = db.Column(db.String(512), nullable=True)


class ExampleItem(db.Model):
    """Representative plugin-owned business data."""

    __tablename__ = "plugin_example_items"

    id = db.Column(db.Integer, primary_key=True)
    value = db.Column(db.String(255), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now(),
    )


def get_example_settings(*, create: bool = False) -> ExampleSettings | None:
    """Return the singleton Example settings row, optionally creating it."""

    settings = db.session.get(ExampleSettings, 1)
    if settings is None and create:
        settings = ExampleSettings(id=1, greeting=DEFAULT_GREETING)
        db.session.add(settings)
        db.session.flush()
    return settings
