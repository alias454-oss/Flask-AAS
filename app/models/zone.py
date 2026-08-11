# app/models/zone.py
from app.core.extensions import db


class Zone(db.Model):
    __tablename__ = "zone"

    zone_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    country_id = db.Column(
        db.Integer,
        db.ForeignKey("country.country_id"),
        nullable=False,
        index=True,
    )
    code = db.Column(db.String(16), nullable=False, unique=True, index=True)
    name = db.Column(db.String(128), nullable=False)
    type = db.Column(db.String(64), nullable=True)
    active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=db.true(),
        index=True,
    )
    parent_zone_id = db.Column(
        db.Integer,
        db.ForeignKey("zone.zone_id"),
        nullable=True,
        index=True,
    )

    country = db.relationship("Country", backref="zones")
    parent = db.relationship(
        "Zone",
        remote_side=[zone_id],
        backref="children",
    )

    def __repr__(self):
        return f"<Zone {self.code} {self.name} country_id={self.country_id}>"
