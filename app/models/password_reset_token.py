import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from app.core.extensions import db


class PasswordResetToken(db.Model):
    """Hashed, expiring, single-use password-reset capability."""

    __tablename__ = "password_reset_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now(),
    )
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    consumed_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)

    user = db.relationship("User", back_populates="password_reset_tokens")

    @staticmethod
    def hash_token(token):
        if not token:
            return None
        return hashlib.sha256(str(token).encode("utf-8")).hexdigest()

    @classmethod
    def issue_for_user(cls, user, *, lifetime=timedelta(hours=1), now=None):
        """Issue a token without invalidating links through an anonymous request."""
        if lifetime.total_seconds() <= 0:
            raise ValueError("Password-reset token lifetime must be positive.")

        issued_at = now or datetime.now(timezone.utc)
        plaintext_token = secrets.token_urlsafe(32)
        token = cls(
            user_id=user.id,
            token_hash=cls.hash_token(plaintext_token),
            expires_at=issued_at + lifetime,
        )
        db.session.add(token)
        return token, plaintext_token

    @classmethod
    def find_active(cls, plaintext_token, *, now=None):
        token_hash = cls.hash_token(plaintext_token)
        if token_hash is None:
            return None

        checked_at = now or datetime.now(timezone.utc)
        return (
            cls.query
            .filter(
                cls.token_hash == token_hash,
                cls.consumed_at.is_(None),
                cls.revoked_at.is_(None),
                cls.expires_at > checked_at,
            )
            .one_or_none()
        )

    @classmethod
    def consume(cls, plaintext_token, *, now=None):
        """Atomically consume one active token and return its record."""
        token_hash = cls.hash_token(plaintext_token)
        if token_hash is None:
            return None

        consumed_at = now or datetime.now(timezone.utc)
        token = cls.query.filter_by(token_hash=token_hash).one_or_none()
        if token is None:
            return None

        updated = (
            cls.query
            .filter(
                cls.id == token.id,
                cls.consumed_at.is_(None),
                cls.revoked_at.is_(None),
                cls.expires_at > consumed_at,
            )
            .update(
                {cls.consumed_at: consumed_at},
                synchronize_session=False,
            )
        )
        if updated != 1:
            return None

        return token

    @classmethod
    def revoke_for_user(cls, user_id, *, revoked_at=None, exclude_id=None):
        """Revoke every outstanding token for a user except an optional record."""
        query = cls.query.filter(
            cls.user_id == user_id,
            cls.consumed_at.is_(None),
            cls.revoked_at.is_(None),
        )
        if exclude_id is not None:
            query = query.filter(cls.id != exclude_id)

        return query.update(
            {cls.revoked_at: revoked_at or datetime.now(timezone.utc)},
            synchronize_session=False,
        )

    def revoke(self, *, revoked_at=None):
        if self.consumed_at is not None or self.revoked_at is not None:
            return False
        self.revoked_at = revoked_at or datetime.now(timezone.utc)
        return True
