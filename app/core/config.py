# app/core/config.py
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Union

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

    # TRUSTED_PROXIES should contain the IP addresses or CIDR ranges of any reverse proxies,
    # load balancers, or gateways that sit in front of your Flask app and forward client requests
    TRUSTED_PROXIES: List[str] = []

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    def parse_origins(cls, v):
        """Allow comma-separated list in .env"""
        if isinstance(v, str):
            return [i.strip() for i in v.split(",") if i.strip()]
        return v or []

    # --- Cache ---
    CACHE_TYPE: str
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
    MAIL_DEBUG: bool = True
    MAIL_SERVER: Optional[str] = None
    MAIL_PORT: int = 587
    MAIL_USE_TLS: bool = True
    MAIL_USERNAME: Optional[str] = None
    MAIL_PASSWORD: Optional[str] = None
    MAIL_DEFAULT_SENDER: Optional[str] = None

    # --- Environment ---
    FLASK_ENV: str = "prod"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

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
            logger.warning(
                "EXPIRE_INTERVAL_SECONDS is <= 0. Disabling expiration checks."
            )
            self.EXPIRE_INTERVAL_SECONDS = None

settings = Settings()
