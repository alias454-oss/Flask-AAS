"""Persisted registration state for Flask-AAS application plugins."""

from app.core.extensions import db


class PluginRegistration(db.Model):
    __tablename__ = "plugin_registrations"

    id = db.Column(db.Integer, primary_key=True)
    plugin_id = db.Column(db.String(100), nullable=False, unique=True, index=True)
    import_path = db.Column(db.String(255), nullable=False, unique=True)
    # Administrator-requested activation state. AAS-038 will load only enabled
    # registrations at application startup; this does not mean "currently active".
    enabled = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=db.false(),
    )
    # Last validated configuration viability. This is derived from
    # ApplicationPlugin.validate_config(), never an administrator toggle.
    configured = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=db.false(),
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now(),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )

    def __repr__(self):
        return (
            f"<PluginRegistration plugin_id={self.plugin_id!r} "
            f"enabled={self.enabled} configured={self.configured}>"
        )
