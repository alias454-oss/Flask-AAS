# app/models/user_auth_token.py
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.extensions import db

TOKEN_PURPOSE_PASSWORD_RESET = "app.tokens.password.reset"
TOKEN_PURPOSE_PASSWORD_SETUP = "app.tokens.password.setup"
TOKEN_PURPOSE_MFA_LOGIN = "app.tokens.mfa.login"
_TOKEN_PURPOSES = frozenset(
    {
        TOKEN_PURPOSE_PASSWORD_RESET,
        TOKEN_PURPOSE_PASSWORD_SETUP,
        TOKEN_PURPOSE_MFA_LOGIN,
    }
)


class UserAuthToken(db.Model):
    """Hashed, expiring, purpose-bound user authentication capability."""

    __tablename__ = "user_auth_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    auth_version = db.Column(db.Integer, nullable=True)
    failed_attempts = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    consumed_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)

    user = db.relationship("User", back_populates="auth_tokens")

    @staticmethod
    def _validate_purpose(purpose):
        if purpose not in _TOKEN_PURPOSES:
            raise ValueError("Unsupported user-auth-token purpose.")
        return purpose

    @classmethod
    def hash_token(cls, token, *, purpose=TOKEN_PURPOSE_PASSWORD_RESET):
        """Hash a token with purpose-domain separation."""
        if not token:
            return None
        purpose = cls._validate_purpose(purpose)
        value = f"{purpose}\x00{token}".encode("utf-8")
        return hashlib.sha256(value).hexdigest()

    @staticmethod
    def _legacy_hash_token(token):
        """Support reset links issued before purpose-domain separation."""
        if not token:
            return None
        return hashlib.sha256(str(token).encode("utf-8")).hexdigest()

    @classmethod
    def _candidate_hashes(cls, token, *, purpose):
        purpose = cls._validate_purpose(purpose)
        token_hash = cls.hash_token(token, purpose=purpose)
        if token_hash is None:
            return []

        hashes = [token_hash]
        if purpose == TOKEN_PURPOSE_PASSWORD_RESET:
            legacy_hash = cls._legacy_hash_token(token)
            if legacy_hash != token_hash:
                hashes.append(legacy_hash)
        return hashes

    @classmethod
    def issue_for_user(
        cls,
        user,
        *,
        purpose=TOKEN_PURPOSE_PASSWORD_RESET,
        lifetime=timedelta(hours=1),
        auth_version=None,
        now=None,
    ):
        """Issue a purpose-bound token without invalidating other active tokens."""
        purpose = cls._validate_purpose(purpose)
        if lifetime.total_seconds() <= 0:
            raise ValueError("User auth token lifetime must be positive.")

        issued_at = now or datetime.now(timezone.utc)
        plaintext_token = secrets.token_urlsafe(32)
        token = cls(
            user_id=user.id,
            token_hash=cls.hash_token(plaintext_token, purpose=purpose),
            auth_version=auth_version,
            expires_at=issued_at + lifetime,
        )
        db.session.add(token)
        return token, plaintext_token

    @classmethod
    def find_active(
        cls,
        plaintext_token,
        *,
        purpose=TOKEN_PURPOSE_PASSWORD_RESET,
        now=None,
    ):
        token_hashes = cls._candidate_hashes(plaintext_token, purpose=purpose)
        if not token_hashes:
            return None

        checked_at = now or datetime.now(timezone.utc)
        return (
            cls.query
            .filter(
                cls.token_hash.in_(token_hashes),
                cls.consumed_at.is_(None),
                cls.revoked_at.is_(None),
                cls.expires_at > checked_at,
            )
            .one_or_none()
        )

    @classmethod
    def consume(
        cls,
        plaintext_token,
        *,
        purpose=TOKEN_PURPOSE_PASSWORD_RESET,
        auth_version=None,
        max_failed_attempts=None,
        now=None,
    ):
        """Atomically consume one active purpose-bound token and return its record."""
        token_hashes = cls._candidate_hashes(plaintext_token, purpose=purpose)
        if not token_hashes:
            return None

        consumed_at = now or datetime.now(timezone.utc)
        token = cls.query.filter(cls.token_hash.in_(token_hashes)).one_or_none()
        if token is None:
            return None

        query = cls.query.filter(
            cls.id == token.id,
            cls.consumed_at.is_(None),
            cls.revoked_at.is_(None),
            cls.expires_at > consumed_at,
        )
        if auth_version is not None:
            query = query.filter(cls.auth_version == auth_version)
        if max_failed_attempts is not None:
            if max_failed_attempts <= 0:
                raise ValueError("Maximum failed attempts must be positive.")
            query = query.filter(cls.failed_attempts < max_failed_attempts)

        updated = query.update(
            {cls.consumed_at: consumed_at},
            synchronize_session=False,
        )
        if updated != 1:
            return None

        return token

    @classmethod
    def record_failed_attempt(
        cls,
        plaintext_token,
        *,
        purpose,
        max_attempts,
        now=None,
    ):
        """Atomically add one failed attempt to an active bounded challenge."""
        if max_attempts <= 0:
            raise ValueError("Maximum attempts must be positive.")

        token_hashes = cls._candidate_hashes(plaintext_token, purpose=purpose)
        if not token_hashes:
            return None

        checked_at = now or datetime.now(timezone.utc)
        updated = (
            cls.query
            .filter(
                cls.token_hash.in_(token_hashes),
                cls.consumed_at.is_(None),
                cls.revoked_at.is_(None),
                cls.expires_at > checked_at,
                cls.failed_attempts < max_attempts,
            )
            .update(
                {cls.failed_attempts: cls.failed_attempts + 1},
                synchronize_session=False,
            )
        )
        if updated != 1:
            return None

        return db.session.scalar(
            select(cls.failed_attempts)
            .where(cls.token_hash.in_(token_hashes))
            .limit(1)
        )

    @classmethod
    def revoke_for_user(cls, user_id, *, revoked_at=None, exclude_id=None):
        """Revoke every outstanding user-auth token except an optional record."""
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
