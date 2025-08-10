# models/user_role.py
from app.core.extensions import db

class UserRole(db.Model):
    __tablename__ = 'user_roles'

    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), primary_key=True)
    role_id = db.Column(db.Integer, db.ForeignKey('roles.id'), primary_key=True)

    def __repr__(self):
        return f"<UserRole user_id={self.user_id} role_id={self.role_id}>"
