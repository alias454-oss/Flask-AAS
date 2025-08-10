# models/online_user.py
from ipaddress import ip_address
from app.core.extensions import db

class OnlineUser(db.Model):
    __tablename__ = 'onlineusers'

    GUEST_USER = "guest"

    id = db.Column(db.Integer, primary_key=True)
    user = db.Column(db.String(60), nullable=False, index=True)
    last_active = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now(), index=True)
    ip_address = db.Column(db.String(45), nullable=True, index=True)

    def __repr__(self):
        return f"<OnlineUser id={self.id} user={self.user} last_active={self.last_active}>"

    # IP helpers as property getter/setter
    @property
    def ip_obj(self):
        if self.ip_address is not None:
            return ip_address(self.ip_address)
        return None

    @ip_obj.setter
    def ip_obj(self, ip_obj):
        self.ip_address = str(ip_obj)

    @property
    def is_guest(self):
        return self.user.lower() == self.GUEST_USER
