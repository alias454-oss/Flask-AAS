# routes/captcha.py
import hashlib
import hmac
import io
import logging
import math
import os
import random
import secrets
import numpy as np
from datetime import datetime, timezone
from flask import Blueprint, current_app, session, send_file, abort
from wtforms.validators import ValidationError
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from PIL.Image import Resampling
from app.core.cache import get_cached_env_settings
from app.core.extensions import cache, limiter
from app.core.decorators import log_view_action
from app.core.security import get_client_ip

logger = logging.getLogger(__name__)

captcha_bp = Blueprint("captcha", __name__)

# Config
CAPTCHA_EXPIRY_MINUTES = 5
CAPTCHA_MAX_ATTEMPTS = 3
CAPTCHA_LENGTH = 6
CAPTCHA_WIDTH = 160
CAPTCHA_HEIGHT = 50
CAPTCHA_SESSION_KEY = "captcha_challenge_id"
CAPTCHA_CACHE_PREFIX = "captcha:"

def is_captcha_enabled():
    settings = get_cached_env_settings()
    return settings.use_captcha if settings else False

def get_fonts_dir():
    current_dir = os.path.dirname(os.path.abspath(__file__))  # app/routes/
    project_root = os.path.abspath(os.path.join(current_dir, ".."))  # app/
    return os.path.join(project_root, "static", "fonts")

def get_fonts():
    fonts_dir = get_fonts_dir()
    font_files = [f for f in os.listdir(fonts_dir) if f.lower().endswith(".ttf")]
    if not font_files:
        raise FileNotFoundError("No TTF font files found in static/fonts")
    return [os.path.join(fonts_dir, f) for f in font_files]

def generate_captcha_text(length=CAPTCHA_LENGTH):
    chars = '23456789abcdefghjkmnpqrstvwxyzABCDEFGHJKLMNPQRSTVWXYZ'
    # Use secrets.choice for secure random characters
    return ''.join(secrets.choice(chars) for _ in range(length))

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

    message = (
        f"{challenge_id}:{_normalize_captcha_answer(answer)}"
    ).encode("utf-8")
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

FONTS = get_fonts()

def generate_captcha_image(code: str) -> bytes:
    # very light pastel blue bg
    # background_color = (245, 250, 255)
    # img = Image.new("RGB", (CAPTCHA_WIDTH, CAPTCHA_HEIGHT), background_color)
    # Darker background, e.g., dark slate gray
    background_color = (30, 30, 60)
    img = Image.new("RGB", (CAPTCHA_WIDTH, CAPTCHA_HEIGHT), background_color)
    draw = ImageDraw.Draw(img)

    # Draw random noise lines in lighter colors
    for _ in range(6):
        start = (random.randint(0, CAPTCHA_WIDTH), random.randint(0, CAPTCHA_HEIGHT))
        end = (random.randint(0, CAPTCHA_WIDTH), random.randint(0, CAPTCHA_HEIGHT))
        # light grayish-blue noise lines
        draw.line([start, end], fill=(180, 180, 220), width=1)

    # Add light random background lines (5 to 10)
    for _ in range(random.randint(5, 10)):
        start = (random.randint(0, CAPTCHA_WIDTH), random.randint(0, CAPTCHA_HEIGHT))
        end = (random.randint(0, CAPTCHA_WIDTH), random.randint(0, CAPTCHA_HEIGHT))
        line_color = tuple(random.randint(180, 220) for _ in range(3))  # light gray-blue tones
        draw.line([start, end], fill=line_color, width=1)

    # Add random colored noise dots (80 to 120)
    for _ in range(random.randint(80, 120)):
        x = random.randint(0, CAPTCHA_WIDTH - 1)
        y = random.randint(0, CAPTCHA_HEIGHT - 1)
        dot_color = tuple(random.randint(150, 230) for _ in range(3))  # soft pastel noise
        draw.point((x, y), fill=dot_color)

    # Draw random arcs to add noise
    for _ in range(5):  # Adjust number of arcs as desired
        x0 = random.randint(0, CAPTCHA_WIDTH - 30)
        y0 = random.randint(0, CAPTCHA_HEIGHT - 15)
        x1 = x0 + random.randint(5, 30)  # Ensure x1 > x0
        y1 = y0 + random.randint(5, 15)  # Ensure y1 > y0
        draw.arc([x0, y0, x1, y1], 0, 360, fill=(180, 180, 220))

    # Draw each character with random font, size, and angle
    char_width = CAPTCHA_WIDTH // len(code)
    for i, char in enumerate(code):
        font_path = random.choice(FONTS)
        font_size = random.randint(36, 38)
        font = ImageFont.truetype(font_path, font_size)

        char_img = Image.new("RGBA", (char_width, CAPTCHA_HEIGHT), (0, 0, 0, 0))
        char_draw = ImageDraw.Draw(char_img)

        w, h = char_draw.textbbox((0, 0), char, font=font)[2:]
        x = (char_width - w) // 2
        y = (CAPTCHA_HEIGHT - h) // 2

        # dark blue text for contrast
        # char_draw.text((x, y), char, font=font, fill=(10, 10, 50))
        # Use a light color for text, e.g., near white with a slight tint
        char_draw.text((x, y), char, font=font, fill=(230, 230, 255))

        # Random rotation
        rotated = char_img.rotate(random.uniform(-15, 20), resample=Resampling.BICUBIC, expand=1)
        img.paste(rotated, (i * char_width, 0), rotated)

    # Distort with sine wave
    def sine_distort(img):
        arr = np.array(img)
        offset_img = np.zeros_like(arr)
        for y in range(CAPTCHA_HEIGHT):
            offset = int(5.0 * np.sin(2 * np.pi * y / 30))
            offset_img[y] = np.roll(arr[y], offset, axis=0)
        return Image.fromarray(offset_img)

    img = sine_distort(img)

    # Slight blur
    img = img.filter(ImageFilter.GaussianBlur(1))

    # Output to buffer
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


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

def validate_captcha_field(field):
    valid, message = validate_captcha(field.data)
    if not valid:
        raise ValidationError(message)

class CaptchaRequired:
    def __call__(self, form, field):
        if not is_captcha_enabled():
            return  # Skip validation if globally disabled

        valid, msg = validate_captcha(field.data)
        if not valid:
            raise ValidationError(msg)

@captcha_bp.route('/captcha_image')
@limiter.limit("10 per minute; 50 per 5 minutes", key_func=get_client_ip)
@log_view_action(action="generate_captcha")
def captcha_image():
    if not is_captcha_enabled():
        # Return 404 if captcha is disabled
        abort(404)

    captcha_text = generate_captcha_text()
    img_buf = generate_captcha_image(captcha_text)

    challenge_id = secrets.token_urlsafe(32)
    expires_at = _current_timestamp() + (CAPTCHA_EXPIRY_MINUTES * 60)
    challenge = {
        "answer_hash": _hash_captcha_answer(challenge_id, captcha_text),
        "attempts": 0,
        "expires_at": expires_at,
    }

    if not cache.set(
        _captcha_cache_key(challenge_id),
        challenge,
        timeout=CAPTCHA_EXPIRY_MINUTES * 60,
    ):
        logger.error("Failed to store CAPTCHA challenge")
        abort(503)

    previous_challenge_id = session.get(CAPTCHA_SESSION_KEY)
    if previous_challenge_id != challenge_id:
        _delete_captcha_challenge(previous_challenge_id)
    _clear_captcha_session()
    session[CAPTCHA_SESSION_KEY] = challenge_id

    return send_file(io.BytesIO(img_buf), mimetype='image/png')
