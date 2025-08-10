# models/audit_activity.py
import json
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy import cast, Text
from ipaddress import ip_address
from app.core.extensions import db

class AuditActivity(db.Model):
    __tablename__ = 'audit_activities'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    action = db.Column(db.String(255), nullable=False, server_default='')
    target = db.Column(db.String(255), nullable=True)
    timestamp = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), index=True)
    _extra_data = db.Column('extra_data', db.Text, nullable=True)  # store JSON as string

    user = db.relationship('User', backref='audit_activities')

    def __repr__(self):
        return f"<AuditActivity user_id={self.user_id} action={self.action} at {self.timestamp}>"

    # Property for JSON decoding/encoding
    @hybrid_property
    def extra_data(self):
        if self._extra_data:
            try:
                return json.loads(self._extra_data)
            except json.JSONDecodeError:
                return None
        return None

    @extra_data.setter
    def extra_data(self, value):
        if value is None:
            self._extra_data = None
        else:
            self._extra_data = json.dumps(value)

    @extra_data.expression
    def extra_data(cls):
        # just return raw _extra_data (as text) for queries
        return cast(cls._extra_data, Text)

    # IP helpers as property getter/setter
    @property
    def ip_obj(self):
        if self.ip_address is not None:
            return ip_address(self.ip_address)
        return None

    @ip_obj.setter
    def ip_obj(self, ip_obj):
        self.ip_address = str(ip_obj)