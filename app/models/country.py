# app/models/country.py
from app.core.extensions import db


class Country(db.Model):
    __tablename__ = "country"

    country_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(128), nullable=False)
    iso_code_2 = db.Column(db.String(2), nullable=False, unique=True, index=True)
    iso_code_3 = db.Column(db.String(3), nullable=False, unique=True, index=True)
    address_format = db.Column(db.Text, nullable=False, server_default="")
    active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=db.true(),
        index=True,
    )

    def __repr__(self):
        return f"<Country {self.name} ({self.iso_code_2})>"
