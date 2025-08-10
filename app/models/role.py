# models/role.py
from datetime import datetime, timezone
from app.core.extensions import db

class Role(db.Model):
    __tablename__ = 'roles'

    _cached_instance = None  # class-level cache

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(255), nullable=True)

    users = db.relationship('User', secondary='user_roles', back_populates='roles')

    def __repr__(self):
        return f"<Role id={self.id} name={self.name} description={self.description}>"

    @classmethod
    def get_instance(cls):
        cls._cached_instance = cls.query.all()
        return cls._cached_instance

    @classmethod
    def get_cached_instance(cls):
        if cls._cached_instance is None:
            cls._cached_instance = cls.query.all()
        return cls._cached_instance