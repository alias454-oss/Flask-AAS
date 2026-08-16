# app/core/password_hashing.py
"""Password-hash primitives and legacy-hash migration support."""

import hashlib

import bcrypt as legacy_bcrypt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type

# OWASP's current minimum Argon2id profile balances password-cracking cost with
# the low-memory deployment targets Flask-AAS intentionally supports. Keep all
# parameters explicit so a dependency update cannot silently change new hashes.
_ARGON2 = PasswordHasher(
    time_cost=2,
    memory_cost=19_456,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)

# Fixed valid hashes are used only to keep login verification on the same
# expensive Argon2id + legacy-bcrypt path when a username is absent or uses the
# other supported scheme. Their plaintexts are intentionally public because
# these are timing-work factors, not account credentials.
_ARGON2_DUMMY_PASSWORD = "flask-aas-argon2-dummy-password"
_ARGON2_DUMMY_HASH = (
    "$argon2id$v=19$m=19456,t=2,p=1$ap4fsZLHtuIoXYtJ2wT7/g$"
    "2EeaxBvq0238JMWxXtnkdOOAUh2pDZE8iG0VeskD190"
)
_LEGACY_BCRYPT_DUMMY_PASSWORD = "flask-aas-legacy-dummy-password"
_LEGACY_BCRYPT_DUMMY_HASH = (
    "$2b$12$QpBL9cOBbn6s2KnJegiMHugHs1dR57PnpB.tj5W1htsKYYozcMmM6"
)

_BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")


def hash_password(password: str) -> str:
    """Hash a non-empty password with the current Argon2id profile."""
    if not isinstance(password, str) or not password:
        raise ValueError("Password must be a non-empty string")
    return _ARGON2.hash(password)


def _is_argon2_hash(stored_hash: str) -> bool:
    return stored_hash.startswith("$argon2")


def _is_legacy_bcrypt_hash(stored_hash: str) -> bool:
    return stored_hash.startswith(_BCRYPT_PREFIXES)


def _legacy_bcrypt_password(password: str) -> bytes:
    """Reproduce Flask-Bcrypt's former BCRYPT_HANDLE_LONG_PASSWORDS mode."""
    digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return digest.encode("ascii")


def _verify_argon2(stored_hash: str, password: str) -> bool:
    try:
        return bool(_ARGON2.verify(stored_hash, password))
    except (VerificationError, InvalidHashError, TypeError, ValueError):
        return False


def _verify_legacy_bcrypt(stored_hash: str, password: str) -> bool:
    try:
        return legacy_bcrypt.checkpw(
            _legacy_bcrypt_password(password),
            stored_hash.encode("ascii"),
        )
    except (TypeError, ValueError, UnicodeEncodeError):
        return False


def verify_password_hash(stored_hash: str, password: str) -> bool:
    """Verify current Argon2 hashes and transitional Flask-Bcrypt hashes."""
    if not isinstance(stored_hash, str) or not stored_hash:
        return False
    if not isinstance(password, str):
        return False

    if _is_argon2_hash(stored_hash):
        return _verify_argon2(stored_hash, password)

    if _is_legacy_bcrypt_hash(stored_hash):
        return _verify_legacy_bcrypt(stored_hash, password)

    return False


def verify_login_password(stored_hash: str | None, password: str) -> bool:
    """Verify login credentials without a scheme-dependent fast path.

    Legacy bcrypt remains supported only for login-time migration. While both
    schemes are accepted, every login attempt deliberately performs one Argon2
    verification and one bcrypt verification. This avoids turning an unknown
    username or the user's stored hash scheme into an obvious timing oracle.
    """
    if not isinstance(password, str):
        return False

    is_argon2 = isinstance(stored_hash, str) and _is_argon2_hash(stored_hash)
    is_bcrypt = isinstance(stored_hash, str) and _is_legacy_bcrypt_hash(stored_hash)

    argon2_match = _verify_argon2(
        stored_hash if is_argon2 else _ARGON2_DUMMY_HASH,
        password if is_argon2 else _ARGON2_DUMMY_PASSWORD,
    )
    bcrypt_match = _verify_legacy_bcrypt(
        stored_hash if is_bcrypt else _LEGACY_BCRYPT_DUMMY_HASH,
        password if is_bcrypt else _LEGACY_BCRYPT_DUMMY_PASSWORD,
    )

    if is_argon2:
        return argon2_match
    if is_bcrypt:
        return bcrypt_match
    return False


def password_hash_needs_rehash(stored_hash: str) -> bool:
    """Return whether an accepted stored hash should be replaced."""
    if not isinstance(stored_hash, str) or not stored_hash:
        return False

    if _is_legacy_bcrypt_hash(stored_hash):
        return True

    if _is_argon2_hash(stored_hash):
        try:
            return _ARGON2.check_needs_rehash(stored_hash)
        except (InvalidHashError, TypeError, ValueError):
            return False

    return False
