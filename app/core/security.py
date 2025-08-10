# app/core/security.py
import re
import logging
import secrets
import hashlib
import string
import ipaddress
from flask import request, current_app
from datetime import datetime, timezone, timedelta
from typing import Optional
from jose import jwt
from passlib.context import CryptContext
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.core.cache import get_cached_env_settings
from app.core.extensions import bcrypt, cache
from app.core.config import settings

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

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

def generate_random_password(length=12):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def old_password_match(user, new_password: str) -> bool:
    return bcrypt.check_password_hash(user.hashed_password, new_password)

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

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    token = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return token

def _load_trusted_proxies():
    raw = current_app.config.get('TRUSTED_PROXIES', [])
    trusted = []
    for net in raw:
        try:
            trusted.append(ipaddress.ip_network(net))
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
        segment = segment.strip()
        for kv in segment.split(';'):
            if kv.lower().startswith('for='):
                ip = kv[4:].strip('"[]')
                parts.append(ip)
    return parts

def get_client_ip():
    headers_to_check = [
        'X-Forwarded-For',
        'X-Real-IP',
        'CF-Connecting-IP',
        'True-Client-IP',
        'X-Client-IP',
        'Forwarded',
    ]

    trusted_proxies = get_trusted_proxies()

    ips = []

    for header in headers_to_check:
        val = request.headers.get(header, '')
        if not val:
            continue

        if header.lower() == 'forwarded':
            ips = _parse_forwarded_header(val)
            if ips:
                break
        else:
            ips = [ip.strip() for ip in val.split(',') if ip.strip()]
            if ips:
                break

    if not ips:
        ips = list(request.access_route)
        if request.remote_addr:
            ips.append(request.remote_addr)

    cleaned_ips = []
    for ip_str in reversed(ips):
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            continue

        if any(ip_obj in net for net in trusted_proxies):
            continue
        cleaned_ips.append(ip_str)

    if cleaned_ips:
        return cleaned_ips[-1]

    return request.remote_addr or 'unknown'

def a_get_client_ip():
    """
    Retrieve the client's IP address, considering trusted proxies and headers.

    If TRUSTED_PROXIES (list of IPs or CIDRs) is configured in app config, headers like
    'X-Forwarded-For' will be trusted only if the immediate client IP is in that list.

    Otherwise, only request.remote_addr is returned.

    Returns:
        str: Client IP address as a string.
    """
    trusted_proxies = current_app.config.get('TRUSTED_PROXIES', [])
    remote_addr = request.remote_addr or 'unknown'

    def ip_in_trusted(ip):
        try:
            ip_obj = ipaddress.ip_address(ip)
            for net in trusted_proxies:
                if ip_obj in ipaddress.ip_network(net):
                    return True
        except ValueError:
            return False
        return False

    if not trusted_proxies or not ip_in_trusted(remote_addr):
        # No trusted proxies set or remote_addr not trusted, ignore headers
        return remote_addr

    # Now remote_addr is trusted proxy, try to get real client IP from headers
    headers_to_check = [
        'X-Forwarded-For',
        'X-Real-IP',
        'CF-Connecting-IP',
        'True-Client-IP',
        'X-Client-IP',
        'Forwarded',
    ]

    for header in headers_to_check:
        ip = request.headers.get(header, None)
        if ip:
            if header.lower() == 'forwarded':
                parts = ip.split(';')
                for part in parts:
                    if part.strip().lower().startswith('for='):
                        ip_candidate = part.split('=')[1].strip().strip('"')
                        try:
                            # Validate IP candidate
                            ipaddress.ip_address(ip_candidate)
                            return ip_candidate
                        except ValueError:
                            continue
            else:
                first_ip = ip.split(',')[0].strip()
                try:
                    ipaddress.ip_address(first_ip)
                    return first_ip
                except ValueError:
                    continue

    return remote_addr