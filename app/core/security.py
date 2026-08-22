# app/core/security.py
"""Security helpers for request trust, authentication, tokens, and redaction."""

import hashlib
import ipaddress
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote, quote_plus

from flask import current_app, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
import jwt
from werkzeug.middleware.proxy_fix import ProxyFix

from app.core.cache import get_cached_env_settings
from app.core.config import settings
from app.core.extensions import cache
from app.core.hash import verify_password_hash

logger = logging.getLogger(__name__)

REDACTED_ROUTE_VALUE = "<redacted>"


def normalize_ip(value):
    """Normalize an IP-like forwarding value to an ipaddress object."""
    if value is None:
        return None

    candidate = str(value).strip().split("%", 1)[0]
    if not candidate or candidate.lower() == "unknown" or candidate.startswith("_"):
        return None

    if candidate.startswith("["):
        closing = candidate.find("]")
        if closing == -1:
            return None
        candidate = candidate[1:closing]
    elif candidate.count(":") == 1:
        host, port = candidate.rsplit(":", 1)
        if port.isdigit():
            candidate = host

    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


def parse_trusted_proxy_networks(values):
    """Parse configured trusted proxy addresses/CIDRs, skipping malformed entries."""
    trusted = []
    for value in values or []:
        try:
            trusted.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            logger.warning("Malformed trusted proxy network entry skipped: %s", value)
    return tuple(trusted)


def address_is_trusted(address, networks):
    return address is not None and any(address in network for network in networks)


class TrustedProxyFix:
    """Apply ProxyFix only when the immediate network peer is explicitly trusted.

    Client IP identity is intentionally left to ``get_client_ip()`` so variable
    proxy chains do not rewrite ``REMOTE_ADDR`` to an intermediate proxy.
    """

    def __init__(self, app, *, trusted_proxies, proxy_hops):
        self.app = app
        self.trusted_networks = parse_trusted_proxy_networks(trusted_proxies)
        self.proxy_app = ProxyFix(
            app,
            x_for=0,
            x_proto=proxy_hops,
            x_host=proxy_hops,
            x_prefix=proxy_hops,
        )

    def __call__(self, environ, start_response):
        peer = normalize_ip(environ.get("REMOTE_ADDR"))
        if not address_is_trusted(peer, self.trusted_networks):
            return self.app(environ, start_response)
        return self.proxy_app(environ, start_response)


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
        return ""
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
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"])


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
        current_app.config.get("TRUSTED_PROXIES", [])
    )


def get_trusted_proxies():
    if not hasattr(current_app, "_trusted_proxies_cache"):
        current_app._trusted_proxies_cache = _load_trusted_proxies()
    return current_app._trusted_proxies_cache


def _original_peer_address():
    original = request.environ.get("werkzeug.proxy_fix.orig", {})
    peer = original.get("REMOTE_ADDR") if isinstance(original, dict) else None
    peer = peer or request.remote_addr
    return normalize_ip(peer)


def get_client_ip():
    peer = _original_peer_address()
    if peer is None:
        return "unknown"

    proxy_hops = current_app.config.get("PROXY_HOPS", 0)
    trusted_proxies = get_trusted_proxies()

    if not proxy_hops or not address_is_trusted(peer, trusted_proxies):
        return str(peer)

    real_ip = normalize_ip(request.headers.get("X-Real-IP"))
    if real_ip is not None:
        return str(real_ip)

    forwarded = request.headers.get("X-Forwarded-For")
    forwarded_values = (
        [item.strip() for item in forwarded.split(",")]
        if forwarded
        else []
    )
    chain = [
        parsed
        for parsed in (normalize_ip(value) for value in forwarded_values)
        if parsed is not None
    ]
    chain.append(peer)

    for candidate in reversed(chain):
        if address_is_trusted(candidate, trusted_proxies):
            continue
        return str(candidate)

    return str(peer)


def _route_redaction_values(redact_params):
    if not redact_params:
        return []

    route_values = request.view_args or {}
    return [
        str(route_values[param])
        for param in redact_params
        if route_values.get(param) is not None
    ]


def redact_route_values(value, redact_params=None):
    """Redact explicitly declared route parameter values from request-derived text."""
    if value is None:
        return None

    redacted = str(value)
    for route_value in _route_redaction_values(redact_params):
        encoded_values = {
            route_value,
            quote(route_value, safe=""),
            quote_plus(route_value, safe=""),
        }
        for encoded_value in sorted(encoded_values, key=len, reverse=True):
            if encoded_value:
                redacted = redacted.replace(encoded_value, REDACTED_ROUTE_VALUE)

    return redacted


def extract_request_metadata(sanitize_headers=True, redact_params=None):
    """Extract useful request metadata for routes that opt into view auditing."""
    headers = dict(request.headers)
    if sanitize_headers:
        for sensitive_key in ["Authorization", "Cookie", "Set-Cookie"]:
            headers.pop(sensitive_key, None)

    headers = {
        key: redact_route_values(value, redact_params)
        for key, value in headers.items()
    }

    return {
        "ip": get_client_ip(),
        "user_agent": redact_route_values(
            request.headers.get("User-Agent"),
            redact_params,
        ),
        "referrer": redact_route_values(request.referrer, redact_params),
        "method": request.method,
        "path": redact_route_values(request.path, redact_params),
        "query_string": redact_route_values(
            request.query_string.decode("utf-8"),
            redact_params,
        ),
        "headers": headers,
    }
