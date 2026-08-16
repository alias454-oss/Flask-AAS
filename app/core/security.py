# app/core/security.py
import re
import logging
import hashlib
import ipaddress
from datetime import datetime, timedelta, timezone
from typing import Optional

from flask import request, current_app
import jwt
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.core.cache import get_cached_env_settings
from app.core.config import settings
from app.core.extensions import cache
from app.core.password_hashing import verify_password_hash

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
    """
    Sanitize and normalize username input for queries and storage
    """
    username = re.sub(r'[\x00-\x1f\x7f]', '', username) # Remove control characters, newlines, etc.
    username = username.strip()                                     # Trim whitespace
    username = username.lower()                                     # Lowercase for normalization
    return username[:60]                                            # Limit length to DB column max (e.g., 60)

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
    raw = current_app.config.get('TRUSTED_PROXIES', [])
    trusted = []
    for net in raw:
        try:
            trusted.append(ipaddress.ip_network(net, strict=False))
        except ValueError:
            logger.warning(f"Malformed trusted proxy network entry skipped: {net}")
            continue
    return trusted


def get_trusted_proxies():
    if not hasattr(current_app, '_trusted_proxies_cache'):
        current_app._trusted_proxies_cache = _load_trusted_proxies()
    return current_app._trusted_proxies_cache


def _parse_forwarded_header(value):
    parts = []
    for segment in value.split(','):
        for item in segment.split(';'):
            key, separator, raw_value = item.strip().partition('=')
            if separator and key.lower() == 'for':
                parts.append(raw_value.strip().strip('"'))
    return parts


def _normalize_forwarded_ip(value):
    candidate = value.strip().split('%', 1)[0]
    if not candidate or candidate.lower() == 'unknown' or candidate.startswith('_'):
        return None

    if candidate.startswith('['):
        closing = candidate.find(']')
        if closing == -1:
            return None
        candidate = candidate[1:closing]
    elif candidate.count(':') == 1:
        host, port = candidate.rsplit(':', 1)
        if port.isdigit():
            candidate = host

    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


def _original_peer_address():
    original = request.environ.get('werkzeug.proxy_fix.orig', {})
    peer = original.get('REMOTE_ADDR') if isinstance(original, dict) else None
    peer = peer or request.remote_addr
    return _normalize_forwarded_ip(peer) if peer else None


def get_client_ip():
    peer = _original_peer_address()
    if peer is None:
        return 'unknown'

    proxy_hops = current_app.config.get('PROXY_HOPS', 0)
    trusted_proxies = get_trusted_proxies()
    peer_is_trusted = any(peer in network for network in trusted_proxies)

    # Direct deployments must ignore all client-supplied forwarding headers.
    if not proxy_hops or not peer_is_trusted:
        return str(peer)

    forwarded_values = []
    forwarded_header = request.headers.get('Forwarded')
    if forwarded_header:
        forwarded_values = _parse_forwarded_header(forwarded_header)
    else:
        for header in (
            'X-Forwarded-For',
            'X-Real-IP',
            'CF-Connecting-IP',
            'True-Client-IP',
        ):
            value = request.headers.get(header)
            if value:
                forwarded_values = [item.strip() for item in value.split(',')]
                break

    chain = [
        parsed
        for parsed in (_normalize_forwarded_ip(value) for value in forwarded_values)
        if parsed is not None
    ]
    chain.append(peer)

    for candidate in reversed(chain):
        if any(candidate in network for network in trusted_proxies):
            continue
        return str(candidate)

    return str(peer)
