# app/core/config.py
import os
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional, ClassVar, Literal

from pydantic_settings import BaseSettings
from pydantic import field_validator

logger = logging.getLogger(__name__)

class Settings(BaseSettings):
    APP_NAME: str = "Flask Authentication & Audit System"
    API_V1_STR: str = "/api/v1"

    # Security
    SECRET_KEY: str = secrets.token_hex(32)  # Fallback if not in .env
    ADMIN_SECRET: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    ALGORITHM: str = "HS256"

    # Single database URI — can be PostgreSQL, SQLite, etc.
    SQLALCHEMY_DATABASE_URI: str
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False

    # Registration control
    REGISTRATION_ENABLED: bool = True

    # --- CORS ---
    # Control which frontend domains can access the API. [https://example.com,https://app.example.com]
    BACKEND_CORS_ORIGINS: List[str] = []

    # Additional CSP sources required by a downstream application.
    # Keep these empty in the base and add only narrowly scoped origins.
    CSP_CONNECT_SRC: List[str] = []
    CSP_IMG_SRC: List[str] = []
    CSP_MEDIA_SRC: List[str] = []

    # TRUSTED_PROXIES should contain the IP addresses or CIDR ranges of any reverse proxies,
    # load balancers, or gateways that sit in front of your Flask app and forward client requests
    TRUSTED_PROXIES: List[str] = []

    @field_validator(
        "BACKEND_CORS_ORIGINS",
        "CSP_CONNECT_SRC",
        "CSP_IMG_SRC",
        "CSP_MEDIA_SRC",
        mode="before",
    )
    def parse_string_list(cls, value):
        """Allow comma-separated lists while rejecting CSP directive injection."""
        if isinstance(value, str):
            values = [item.strip() for item in value.split(",") if item.strip()]
        else:
            values = value or []

        for item in values:
            if any(character in item for character in (";", "\r", "\n")):
                raise ValueError("Configured origins must not contain CSP delimiters")

        return values

    # --- Cache ---
    CACHE_TYPE: str = "SimpleCache"
    CACHE_DEFAULT_TIMEOUT: int = 300
    CACHE_REDIS_URL: Optional[str] = "redis://localhost:6379/0"

    # --- Session ---
    SESSION_COOKIE_SECURE: Optional[bool] = False  # TRUE recommended in prod with HTTPS
    SESSION_COOKIE_HTTPONLY: Optional[bool] = True
    SESSION_COOKIE_SAMESITE: Optional[str] = 'Lax'
    PERMANENT_SESSION_LIFETIME: timedelta = timedelta(minutes=30)

    # --- Online tracking ---
    EXPIRE_INTERVAL_SECONDS: Optional[int] = 900  # None disables expiration
    LAST_EXPIRE_RUN: datetime = datetime.now(timezone.utc).isoformat()

    # --- Email ---
    MAIL_DEBUG: bool = False
    MAIL_SERVER: Optional[str] = None
    MAIL_PORT: int = 587
    MAIL_USE_TLS: bool = True
    MAIL_USERNAME: Optional[str] = None
    MAIL_PASSWORD: Optional[str] = None
    MAIL_DEFAULT_SENDER: Optional[str] = None

    # --- Environment ---
    FLASK_ENV: str = "production"

    # Determine which env file to use
    @staticmethod
    def detect_env_file() -> str | None:
        override = os.environ.get("ENV_FILE_PATH")
        if override and os.path.exists(override):
            return override
        if os.path.exists(".env"):
            return ".env"
        return None

    # store it as non-field classvar so Pydantic ignores it
    env_file_path: ClassVar[str | None] = detect_env_file()

    model_config = {
        "env_file": env_file_path,
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Generate SECRET_KEY if missing
        if not self.SECRET_KEY:
            self.SECRET_KEY = secrets.token_hex(32)
            logger.warning(
                "Generated fallback SECRET_KEY. "
                "Set a strong key in your .env file for production."
            )

        # Validate EXPIRE_INTERVAL_SECONDS toggle logic
        if self.EXPIRE_INTERVAL_SECONDS is not None and self.EXPIRE_INTERVAL_SECONDS <= 0:
            logger.warning("EXPIRE_INTERVAL_SECONDS is <= 0. Disabling expiration checks.")
            self.EXPIRE_INTERVAL_SECONDS = None

settings = Settings()
