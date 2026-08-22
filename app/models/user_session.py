# models/user_session.py
import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import and_, or_, select, update

from app.core.extensions import db


class UserSession(db.Model):
    __tablename__ = 'user_sessions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', name='fk_user_sessions_user_id_users'),
        nullable=False,
        index=True,
    )
    token_hash = db.Column(
        db.String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    ip_address = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)
    remembered = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=db.false(),
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now(),
    )
    last_active_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now(),
    )
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    ended_at = db.Column(db.DateTime(timezone=True), nullable=True)

    user = db.relationship('User', back_populates='user_sessions')

    @staticmethod
    def hash_token(token):
        if not token:
            raise ValueError('Session token is required')
        return hashlib.sha256(str(token).encode('utf-8')).hexdigest()

    @staticmethod
    def _clean(value, max_length):
        if value is None:
            return None
        cleaned = (
            str(value)
            .replace('\x00', '')
            .replace('\r', ' ')
            .replace('\n', ' ')
            .strip()
        )
        return cleaned[:max_length] or None

    @classmethod
    def issue_for_user(
        cls,
        user,
        *,
        ip_address=None,
        user_agent=None,
        remembered=False,
        now=None,
    ):
        if now is None:
            now = datetime.now(timezone.utc)

        token = secrets.token_urlsafe(32)
        record = cls(
            user_id=user.id,
            token_hash=cls.hash_token(token),
            ip_address=cls._clean(ip_address, 45),
            user_agent=cls._clean(user_agent, 255),
            remembered=bool(remembered),
            created_at=now,
            last_active_at=now,
        )
        db.session.add(record)
        db.session.flush()
        user.bind_session_identity(
            token,
            record.id,
            remembered=record.remembered,
            last_active_at=record.last_active_at,
        )
        return record

    @classmethod
    def active_record(cls, user_id, token):
        if not token:
            return None

        return db.session.scalar(
            select(cls)
            .where(
                cls.user_id == user_id,
                cls.token_hash == cls.hash_token(token),
                cls.revoked_at.is_(None),
                cls.ended_at.is_(None),
            )
            .limit(1)
        )

    @classmethod
    def active_record_id(cls, user_id, token):
        record = cls.active_record(user_id, token)
        return None if record is None else record.id

    @classmethod
    def active_for_user(cls, user_id):
        return (
            cls.query
            .filter(
                cls.user_id == user_id,
                cls.revoked_at.is_(None),
                cls.ended_at.is_(None),
            )
            .order_by(cls.created_at.desc(), cls.id.desc())
            .all()
        )

    @classmethod
    def previous_login_at(cls, user_id, current_session_id):
        if current_session_id is None:
            return None

        current_record = db.session.get(cls, current_session_id)
        if current_record is None or current_record.user_id != user_id:
            return None

        return db.session.scalar(
            select(cls.created_at)
            .where(
                cls.user_id == user_id,
                or_(
                    cls.created_at < current_record.created_at,
                    and_(
                        cls.created_at == current_record.created_at,
                        cls.id < current_record.id,
                    ),
                ),
            )
            .order_by(cls.created_at.desc(), cls.id.desc())
            .limit(1)
        )

    @classmethod
    def revoke_for_user(cls, user_id, *, revoked_at=None, exclude_id=None):
        if revoked_at is None:
            revoked_at = datetime.now(timezone.utc)

        query = cls.query.filter(
            cls.user_id == user_id,
            cls.revoked_at.is_(None),
            cls.ended_at.is_(None),
        )
        if exclude_id is not None:
            query = query.filter(cls.id != exclude_id)

        records = query.order_by(cls.id).all()
        for record in records:
            record.revoked_at = revoked_at
        return [record.id for record in records]

    @classmethod
    def touch_isolated(cls, session_id, user_id, *, now=None):
        if session_id is None or user_id is None:
            return False
        if now is None:
            now = datetime.now(timezone.utc)

        with db.engine.begin() as connection:
            result = connection.execute(
                update(cls.__table__)
                .where(
                    cls.id == session_id,
                    cls.user_id == user_id,
                    cls.revoked_at.is_(None),
                    cls.ended_at.is_(None),
                )
                .values(last_active_at=now)
            )
        return bool(result.rowcount)

    @classmethod
    def end_isolated(cls, session_id, user_id, *, ended_at=None):
        if session_id is None or user_id is None:
            return False
        if ended_at is None:
            ended_at = datetime.now(timezone.utc)

        with db.engine.begin() as connection:
            result = connection.execute(
                update(cls.__table__)
                .where(
                    cls.id == session_id,
                    cls.user_id == user_id,
                    cls.revoked_at.is_(None),
                    cls.ended_at.is_(None),
                )
                .values(ended_at=ended_at)
            )
        return bool(result.rowcount)

    def __repr__(self):
        return (
            f'<UserSession id={self.id} user_id={self.user_id} '
            f'remembered={self.remembered}>'
        )
