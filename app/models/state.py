# models/state.py
from app.core.extensions import db

class State(db.Model):
    __tablename__ = 'states'

    state_prefix = db.Column(db.String(2), primary_key=True)
    state_name = db.Column(db.String(30), nullable=False)

    def __repr__(self):
        return f"<State state_prefix={self.state_prefix} state_name={self.state_name}>"