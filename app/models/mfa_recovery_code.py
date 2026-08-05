import base64
import hashlib
import secrets
from datetime import datetime, timezone

from app.core.extensions import db


class MfaRecoveryCode(db.Model):
    __tablename__ = "mfa_recovery_codes"
    __table_args__ = (
        db.UniqueConstraint('user_id', 'code_hash', name='uq_mfa_recovery_codes_user_hash'),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    code_hash = db.Column(db.String(64), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now(),
    )
    consumed_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)

    @staticmethod
    def normalize(code):
        return ''.join(character for character in code.upper() if character.isalnum())

    @classmethod
    def hash_code(cls, code):
        normalized = cls.normalize(code)
        return hashlib.sha256(normalized.encode('utf-8')).hexdigest()

    @classmethod
    def generate_for_user(cls, user, count=10):
        cls.query.filter_by(user_id=user.id).delete(synchronize_session=False)

        plaintext_codes = []
        for _ in range(count):
            raw_code = base64.b32encode(secrets.token_bytes(12)).decode('ascii').rstrip('=')
            plaintext_code = '-'.join(
                raw_code[index:index + 4]
                for index in range(0, len(raw_code), 4)
            )
            plaintext_codes.append(plaintext_code)
            db.session.add(
                cls(
                    user_id=user.id,
                    code_hash=cls.hash_code(plaintext_code),
                )
            )

        return plaintext_codes

    @classmethod
    def consume(cls, user_id, code):
        normalized = cls.normalize(code)
        if not normalized:
            return False

        code_hash = cls.hash_code(normalized)
        consumed_at = datetime.now(timezone.utc)
        updated = (
            cls.query
            .filter_by(user_id=user_id, code_hash=code_hash, consumed_at=None)
            .update(
                {cls.consumed_at: consumed_at},
                synchronize_session=False,
            )
        )
        return updated == 1
