# models/zone.py
from app.core.extensions import db

class Zone(db.Model):
    __tablename__ = 'zone'

    zone_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    country_id = db.Column(db.Integer, db.ForeignKey('country.country_id'), nullable=False)
    code = db.Column(db.String(32), nullable=False, default='')
    name = db.Column(db.String(128), nullable=False)

    country = db.relationship('Country', backref='zones')

    def __repr__(self):
        return f"<Zone {self.name} ({self.code}) country_id={self.country_id}>"
