# models/audit_activity.py
import json
from ipaddress import ip_address

from sqlalchemy import Text, cast
from sqlalchemy.ext.hybrid import hybrid_property

from app.core.extensions import db


def serialize_extra_data(value):
    """Serialize audit metadata once using portable JSON stored as text."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError("extra_data must be a dictionary if provided.")

    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise TypeError("extra_data must contain JSON-serializable values.") from exc

    if len(serialized.encode('utf-8')) > 32768:
        raise ValueError("extra_data must not exceed 32 KiB when serialized.")

    return serialized


class AuditActivity(db.Model):
    __tablename__ = 'audit_activities'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    action = db.Column(db.String(255), nullable=False, server_default='')
    target = db.Column(db.String(255), nullable=True)
    timestamp = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), index=True)
    _extra_data = db.Column('extra_data', db.Text, nullable=True)

    user = db.relationship('User', backref='audit_activities')

    def __repr__(self):
        return f"<AuditActivity user_id={self.user_id} action={self.action} at {self.timestamp}>"

    @hybrid_property
    def extra_data(self):
        if not self._extra_data:
            return None

        try:
            decoded = json.loads(self._extra_data)
        except (json.JSONDecodeError, TypeError):
            return None

        # Existing rows may have been encoded twice by the old tracker helper.
        if isinstance(decoded, str):
            try:
                decoded = json.loads(decoded)
            except (json.JSONDecodeError, TypeError):
                return None

        return decoded if isinstance(decoded, dict) else None

    @extra_data.setter
    def extra_data(self, value):
        self._extra_data = serialize_extra_data(value)

    @extra_data.expression
    def extra_data(cls):
        return cast(cls._extra_data, Text)

    @property
    def ip_obj(self):
        if self.ip_address is not None:
            return ip_address(self.ip_address)
        return None

    @ip_obj.setter
    def ip_obj(self, ip_obj):
        self.ip_address = str(ip_obj)
