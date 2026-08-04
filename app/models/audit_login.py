# models/audit_login.py
from ipaddress import ip_address
from app.core.extensions import db


class AuditLogin(db.Model):
    __tablename__ = 'audit_logins'

    id = db.Column(db.Integer, primary_key=True)
    ip_address = db.Column(db.String(45), nullable=False, index=True)
    username = db.Column(db.String(60), nullable=False, index=True)
    user_agent = db.Column(db.String(255), nullable=True)
    referer = db.Column(db.String(255), nullable=True)
    success = db.Column(db.Boolean, nullable=False, server_default=db.text('false'))
    failure_reason = db.Column(db.String(32), nullable=True, index=True)
    timestamp = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now(), index=True)

    def __repr__(self):
        return (
            f"<AuditLogin {self.username} from {self.ip_address} "
            f"at {self.timestamp} success={self.success} "
            f"failure_reason={self.failure_reason}>"
        )

    # IP helpers as property getter/setter
    @property
    def ip_obj(self):
        if self.ip_address is not None:
            return ip_address(self.ip_address)
        return None

    @ip_obj.setter
    def ip_obj(self, ip_obj):
        self.ip_address = str(ip_obj)
