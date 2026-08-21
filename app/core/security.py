"""Security helpers for authentication, tokens, lockout, and request identity."""

# app/core/security.py
import re
import logging
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from flask import request, current_app
import jwt
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.core.cache import get_cached_env_settings
from app.core.config import settings
from app.core.extensions import cache
from app.core.password_hashing import verify_password_hash
from app.core.proxy import (
    address_is_trusted,
    normalize_ip,
    parse_trusted_proxy_networks,
)

logger = logging.getLogger(__name__)

# # === Lockout Tracking ===

def make_cache_key(username: str, ip: str, prefix: str) -> str:
    key_raw = f"{prefix}:{username.lower()}:{ip}"
    return hashlib.sha256(key_raw.encode("utf-8")).hexdigest()

def _fail_key(username: str, ip: str) -> str:
    return make_cache_key(username, ip, "failcount")

def _lockout_key(username: str, ip: str) -> str:
    return make_cache_key(username, ip, "lockout")

def is_locked_out(username: str, ip: str) -> bool:
    key = _lockout_key(username, ip)
    return cache.get(key) is not None

def track_lockout_attempts(username: str, ip: str):
    env = get_cached_env_settings()
    if not env:
        # If env not loaded, default to safe lockout policy or skip
        logger.warning("Env settings unavailable; skipping lockout tracking.")
        return

    fail_key = _fail_key(username, ip)
    lock_key = _lockout_key(username, ip)

    lockout_attempts = cache.get(fail_key) or 0
    lockout_attempts += 1
    cache.set(fail_key, lockout_attempts, timeout=env.lockout_duration_seconds)

    if lockout_attempts >= env.max_failed_attempts:
        cache.set(lock_key, True, timeout=env.lockout_duration_seconds)

def reset_lockout_attempts(username: str, ip: str):
    cache.delete(_fail_key(username, ip))
    cache.delete(_lockout_key(username, ip))

def normalize_email(email: str) -> str:
    """Normalize email by trimming whitespace and lowercasing."""
    if not email:
        return ''
    return email.strip().lower()

def redact_email(email):
    if not email or "@" not in email:
        return email
    user, domain = email.split("@", 1)
    user = user[0] + "***" + user[-1] if len(user) > 2 else user[0] + "***"
    return f"{user}@{domain}"

def normalize_username(username: str) -> str:
    """Normalize a username without silently changing overlong input.

    Storage forms enforce their own database-backed length limits. Lookup paths
    must not truncate an attacker-controlled username into a different valid
    account identifier.

    :param username: Raw username supplied by a user.
    :return: Control-character-free, trimmed, lowercase username.
    """
    normalized = re.sub(r"[\x00-\x1f\x7f]", "", username or "")
    return normalized.strip().lower()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password with the application's supported hash stack."""
    return verify_password_hash(hashed_password, plain_password)


def old_password_match(user, new_password: str) -> bool:
    return verify_password_hash(user.hashed_password, new_password)

def get_serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'])

def generate_token(data, salt):
    return get_serializer().dumps(data, salt=salt)

def confirm_token(token, salt, expiration=86400):
    try:
        data = get_serializer().loads(token, salt=salt, max_age=expiration)
        return data
    except (SignatureExpired, BadSignature):
        return None

def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta
        or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def _load_trusted_proxies():
    return parse_trusted_proxy_networks(
        current_app.config.get('TRUSTED_PROXIES', [])
    )


def get_trusted_proxies():
    if not hasattr(current_app, '_trusted_proxies_cache'):
        current_app._trusted_proxies_cache = _load_trusted_proxies()
    return current_app._trusted_proxies_cache


def _original_peer_address():
    original = request.environ.get('werkzeug.proxy_fix.orig', {})
    peer = original.get('REMOTE_ADDR') if isinstance(original, dict) else None
    peer = peer or request.remote_addr
    return normalize_ip(peer)


def get_client_ip():
    peer = _original_peer_address()
    if peer is None:
        return 'unknown'

    proxy_hops = current_app.config.get('PROXY_HOPS', 0)
    trusted_proxies = get_trusted_proxies()

    # Direct/untrusted peers must never influence client identity with headers.
    if not proxy_hops or not address_is_trusted(peer, trusted_proxies):
        return str(peer)

    # A trusted immediate proxy may provide the effective client directly in
    # X-Real-IP. Prefer that single, validated value when present. This avoids
    # treating an intermediate CDN hop in X-Forwarded-For as the client.
    real_ip = normalize_ip(request.headers.get('X-Real-IP'))
    if real_ip is not None:
        return str(real_ip)

    # Fall back to the trusted X-Forwarded-For chain when X-Real-IP is absent
    # or malformed. Other forwarding header families are intentionally ignored.
    forwarded = request.headers.get('X-Forwarded-For')
    forwarded_values = (
        [item.strip() for item in forwarded.split(',')]
        if forwarded
        else []
    )

    chain = [
        parsed
        for parsed in (normalize_ip(value) for value in forwarded_values)
        if parsed is not None
    ]
    chain.append(peer)

    # Walk from the application back toward the client and return the nearest
    # untrusted address. This tolerates multiple trusted proxy hops without
    # accepting a spoofed value placed farther left in the chain.
    for candidate in reversed(chain):
        if address_is_trusted(candidate, trusted_proxies):
            continue
        return str(candidate)

    return str(peer)
