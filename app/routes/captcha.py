# routes/captcha.py
import logging
import os
import io
import random
import secrets
import numpy as np
from datetime import datetime, timezone, timedelta
from flask import Blueprint, session, send_file, abort
from wtforms.validators import ValidationError
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from PIL.Image import Resampling
from app.core.cache import get_cached_env_settings
from app.core.extensions import limiter
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
        font_path = random.choice(random.choice(FONTS))
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
    try:
        now_ts = datetime.now(timezone.utc).timestamp()

        if 'captcha_code' not in session or 'captcha_expiry' not in session:
            return False, "CAPTCHA missing. Please reload the page."

        if now_ts > session['captcha_expiry']:
            return False, "CAPTCHA expired. Please reload the page."

        attempts = session.get('captcha_attempts', 0)
        if attempts >= CAPTCHA_MAX_ATTEMPTS:
            return False, "Too many CAPTCHA attempts. Please reload the page."

        if user_input.lower() == session['captcha_code'].lower():
            # Success - clear session captcha data
            session.pop('captcha_code', None)
            session.pop('captcha_expiry', None)
            session.pop('captcha_attempts', None)
            return True, ""
        else:
            # Failure - increment attempts
            session['captcha_attempts'] = attempts + 1
            return False, "Incorrect CAPTCHA. Please try again."

    except Exception as e:
        # Catch any session or internal errors
        return False, f"CAPTCHA validation error: {str(e)}"

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

    # Generate new CAPTCHA text
    captcha_text = generate_captcha_text()
    session['captcha_code'] = captcha_text
    session['captcha_expiry'] = (datetime.now(timezone.utc) + timedelta(minutes=CAPTCHA_EXPIRY_MINUTES)).timestamp()
    session['captcha_attempts'] = 0

    img_buf = generate_captcha_image(captcha_text)
    return send_file(io.BytesIO(img_buf), mimetype='image/png')
