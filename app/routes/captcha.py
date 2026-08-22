# routes/captcha.py
import io
import math
import os
import random
from flask import Blueprint, abort, send_file
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageFilter
from PIL.Image import Resampling
from app.core.decorators import log_view_action
from app.core.extensions import limiter
from app.core.security import get_client_ip
from app.services import captcha as captcha_service


captcha_bp = Blueprint("captcha", __name__)

# Image configuration
CAPTCHA_WIDTH = 160
CAPTCHA_HEIGHT = 50


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

    # Distort each row with a sine-wave offset. Pillow's ImageChops.offset
    # wraps pixels at the row edges, matching the previous NumPy roll behavior
    # without carrying NumPy solely for this tiny image operation.
    distorted = Image.new(img.mode, img.size)
    for y in range(CAPTCHA_HEIGHT):
        offset = int(5.0 * math.sin(2 * math.pi * y / 30))
        row = img.crop((0, y, CAPTCHA_WIDTH, y + 1))
        distorted.paste(ImageChops.offset(row, offset, 0), (0, y))
    img = distorted

    # Slight blur
    img = img.filter(ImageFilter.GaussianBlur(1))

    # Output to buffer
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


@captcha_bp.route('/captcha_image')
@limiter.limit("10 per minute; 50 per 5 minutes", key_func=get_client_ip)
@log_view_action(action="generate_captcha")
def captcha_image():
    if not captcha_service.is_captcha_enabled():
        # Return 404 if captcha is disabled
        abort(404)

    captcha_text = captcha_service.generate_captcha_text()
    img_buf = generate_captcha_image(captcha_text)

    if not captcha_service.issue_captcha_challenge(captcha_text):
        abort(503)

    return send_file(io.BytesIO(img_buf), mimetype='image/png')
