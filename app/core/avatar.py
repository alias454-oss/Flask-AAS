# app/core/avatar.py
"""Local profile-image validation, normalization, storage, and host rendering."""

import base64
import io
import logging
import os
import re
import warnings
from pathlib import Path
from uuid import uuid4

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.cache import get_cached_env_settings

logger = logging.getLogger(__name__)

DEFAULT_MAX_IMAGE_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_IMAGE_PIXELS = 24_000_000
PROFILE_IMAGE_UPLOAD_OVERHEAD_BYTES = 256 * 1024
PROFILE_IMAGE_SIZE = (256, 256)
ALLOWED_IMAGE_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})
PROFILE_IMAGE_NAME_RE = re.compile(r"^[0-9a-f]{32}\.webp$")


class ProfileImageError(ValueError):
    """Raised when a submitted profile image is unsafe or unsupported."""


def max_image_bytes() -> int:
    """Return the maximum accepted source-image size."""

    value = int(current_app.config.get("USER_IMAGE_MAX_BYTES", DEFAULT_MAX_IMAGE_BYTES))
    return max(value, 1)


def max_image_pixels() -> int:
    """Return the maximum decoded pixel count accepted for one image."""

    value = int(current_app.config.get("USER_IMAGE_MAX_PIXELS", DEFAULT_MAX_IMAGE_PIXELS))
    return max(value, 1)


def max_upload_request_bytes() -> int:
    """Return the multipart request limit for one profile-image upload."""

    return max_image_bytes() + PROFILE_IMAGE_UPLOAD_OVERHEAD_BYTES


def profile_image_root() -> Path:
    """Resolve the complete administrator-configured user storage directory.

    Relative paths are resolved from the Flask-AAS project root. Absolute paths
    are used as configured. Flask-AAS does not append its own storage suffixes.
    """

    env = get_cached_env_settings()
    configured = str(getattr(env, "users_stored_path", "") or "").strip()
    if not configured:
        raise RuntimeError("User Storage Path is not configured.")
    if "\x00" in configured:
        raise RuntimeError("User Storage Path contains an invalid null byte.")

    root = Path(configured).expanduser()
    if not root.is_absolute():
        root = Path(current_app.root_path).parent / root
    return root.resolve()


def profile_image_path(filename: str) -> Path:
    """Resolve an internally generated profile-image filename."""

    if not filename or not PROFILE_IMAGE_NAME_RE.fullmatch(filename):
        raise ValueError("Invalid profile-image filename")
    return profile_image_root() / filename


def _read_upload(upload) -> bytes:
    limit = max_image_bytes()
    payload = upload.stream.read(limit + 1)
    if not payload:
        raise ProfileImageError("The uploaded image is empty.")
    if len(payload) > limit:
        raise ProfileImageError(
            f"Profile images must be {limit // (1024 * 1024)} MB or smaller."
        )
    return payload


def _load_and_normalize(payload: bytes) -> Image.Image:
    source = None
    oriented = None
    converted = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            source = Image.open(io.BytesIO(payload))

            if source.format not in ALLOWED_IMAGE_FORMATS:
                raise ProfileImageError("Only JPEG, PNG, and WebP images are supported.")
            if getattr(source, "is_animated", False):
                raise ProfileImageError("Animated profile images are not supported.")

            width, height = source.size
            if width <= 0 or height <= 0 or width * height > max_image_pixels():
                raise ProfileImageError("The uploaded image dimensions are too large.")

            source.load()
            oriented = ImageOps.exif_transpose(source)
            has_alpha = (
                oriented.mode in {"RGBA", "LA"}
                or (oriented.mode == "P" and "transparency" in oriented.info)
            )
            converted = oriented.convert("RGBA" if has_alpha else "RGB")

            return ImageOps.fit(
                converted,
                PROFILE_IMAGE_SIZE,
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
    except ProfileImageError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ProfileImageError("The uploaded image dimensions are too large.") from None
    except (UnidentifiedImageError, OSError, ValueError):
        raise ProfileImageError("The uploaded file is not a valid supported image.") from None
    finally:
        if converted is not None:
            converted.close()
        if oriented is not None and oriented is not source:
            oriented.close()
        if source is not None:
            source.close()


def _atomic_save_webp(image: Image.Image, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        image.save(temporary, format="WEBP", quality=88, method=6)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def store_profile_image(upload) -> str:
    """Validate, normalize, and store one profile image as 256x256 WebP."""

    normalized = _load_and_normalize(_read_upload(upload))
    filename = f"{uuid4().hex}.webp"
    destination = profile_image_path(filename)
    try:
        _atomic_save_webp(normalized, destination)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        normalized.close()
    return filename


def delete_profile_image(filename: str | None) -> None:
    """Best-effort removal of one Flask-AAS-generated profile image."""

    if not filename:
        return

    try:
        profile_image_path(filename).unlink(missing_ok=True)
    except (OSError, RuntimeError, ValueError):
        logger.exception("Profile-image cleanup failed")


def profile_image_data_uri(filename: str | None) -> str | None:
    """Return one generated WebP as an internal host-rendering data URI."""

    if not filename:
        return None

    try:
        path = profile_image_path(filename)
        payload = path.read_bytes()
    except FileNotFoundError:
        return None
    except (OSError, RuntimeError, ValueError):
        logger.warning("Profile image could not be read", exc_info=True)
        return None

    # Generated 256x256 WebPs should remain comfortably below the source limit.
    # Refuse unexpectedly large files rather than embedding arbitrary content.
    if not payload or len(payload) > max_image_bytes():
        logger.warning("Profile image has an unexpected stored size")
        return None

    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:image/webp;base64,{encoded}"
