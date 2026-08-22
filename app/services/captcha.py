# app/services/captcha.py
import hashlib
import hmac
import logging
import math
import secrets
from datetime import datetime, timezone

from flask import current_app, session

from app.core.cache import get_cached_env_settings
from app.core.extensions import cache

logger = logging.getLogger(__name__)

CAPTCHA_EXPIRY_MINUTES = 5
CAPTCHA_MAX_ATTEMPTS = 3
CAPTCHA_LENGTH = 6
CAPTCHA_SESSION_KEY = "captcha_challenge_id"
CAPTCHA_CACHE_PREFIX = "captcha:"


def is_captcha_enabled():
    settings = get_cached_env_settings()
    return settings.use_captcha if settings else False


def generate_captcha_text(length=CAPTCHA_LENGTH):
    chars = "23456789abcdefghjkmnpqrstvwxyzABCDEFGHJKLMNPQRSTVWXYZ"
    return "".join(secrets.choice(chars) for _ in range(length))


def _current_timestamp():
    return datetime.now(timezone.utc).timestamp()


def _captcha_cache_key(challenge_id):
    return f"{CAPTCHA_CACHE_PREFIX}{challenge_id}"


def _normalize_captcha_answer(answer):
    return answer.casefold()


def _hash_captcha_answer(challenge_id, answer):
    secret_key = current_app.secret_key
    if isinstance(secret_key, str):
        secret_key = secret_key.encode("utf-8")

    message = f"{challenge_id}:{_normalize_captcha_answer(answer)}".encode("utf-8")
    return hmac.new(secret_key, message, hashlib.sha256).hexdigest()


def _clear_captcha_session():
    session.pop(CAPTCHA_SESSION_KEY, None)
    # Remove state left by the previous client-readable implementation.
    session.pop("captcha_code", None)
    session.pop("captcha_expiry", None)
    session.pop("captcha_attempts", None)


def _delete_captcha_challenge(challenge_id):
    if not challenge_id:
        return
    try:
        cache.delete(_captcha_cache_key(challenge_id))
    except Exception:
        logger.exception("Failed to delete CAPTCHA challenge")


def issue_captcha_challenge(answer):
    challenge_id = secrets.token_urlsafe(32)
    expires_at = _current_timestamp() + (CAPTCHA_EXPIRY_MINUTES * 60)
    challenge = {
        "answer_hash": _hash_captcha_answer(challenge_id, answer),
        "attempts": 0,
        "expires_at": expires_at,
    }

    if not cache.set(
        _captcha_cache_key(challenge_id),
        challenge,
        timeout=CAPTCHA_EXPIRY_MINUTES * 60,
    ):
        logger.error("Failed to store CAPTCHA challenge")
        return False

    previous_challenge_id = session.get(CAPTCHA_SESSION_KEY)
    if previous_challenge_id != challenge_id:
        _delete_captcha_challenge(previous_challenge_id)
    _clear_captcha_session()
    session[CAPTCHA_SESSION_KEY] = challenge_id
    return True


def validate_captcha(user_input):
    challenge_id = session.get(CAPTCHA_SESSION_KEY)
    try:
        if not challenge_id:
            _clear_captcha_session()
            return False, "CAPTCHA missing. Please reload the page."

        cache_key = _captcha_cache_key(challenge_id)
        challenge = cache.get(cache_key)
        if not isinstance(challenge, dict):
            _delete_captcha_challenge(challenge_id)
            _clear_captcha_session()
            return False, "CAPTCHA expired or missing. Please reload the page."

        answer_hash = challenge.get("answer_hash")
        attempts = challenge.get("attempts")
        expires_at = challenge.get("expires_at")
        if (
            not isinstance(answer_hash, str)
            or not isinstance(attempts, int)
            or not isinstance(expires_at, (int, float))
        ):
            _delete_captcha_challenge(challenge_id)
            _clear_captcha_session()
            return False, "CAPTCHA expired or missing. Please reload the page."

        now_ts = _current_timestamp()
        if now_ts >= expires_at:
            _delete_captcha_challenge(challenge_id)
            _clear_captcha_session()
            return False, "CAPTCHA expired. Please reload the page."

        if attempts >= CAPTCHA_MAX_ATTEMPTS:
            _delete_captcha_challenge(challenge_id)
            _clear_captcha_session()
            return False, "Too many CAPTCHA attempts. Please reload the page."

        submitted_hash = _hash_captcha_answer(challenge_id, user_input)
        if hmac.compare_digest(submitted_hash, answer_hash):
            _delete_captcha_challenge(challenge_id)
            _clear_captcha_session()
            return True, ""

        attempts += 1
        if attempts >= CAPTCHA_MAX_ATTEMPTS:
            _delete_captcha_challenge(challenge_id)
            _clear_captcha_session()
            return False, "Too many CAPTCHA attempts. Please reload the page."

        challenge["attempts"] = attempts
        remaining_seconds = max(1, math.ceil(expires_at - now_ts))
        if not cache.set(cache_key, challenge, timeout=remaining_seconds):
            _delete_captcha_challenge(challenge_id)
            _clear_captcha_session()
            return False, "CAPTCHA unavailable. Please reload the page."

        return False, "Incorrect CAPTCHA. Please try again."

    except Exception:
        logger.exception("CAPTCHA validation failed")
        _delete_captcha_challenge(challenge_id)
        _clear_captcha_session()
        return False, "CAPTCHA validation error. Please reload the page."
