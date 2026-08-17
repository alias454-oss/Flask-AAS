"""Application configuration and deployment validation."""

import os
import logging
import secrets
from datetime import timedelta
from typing import List, Optional, ClassVar, Literal

from pydantic_settings import BaseSettings
from pydantic import field_validator, model_validator

from app.core.site import DEFAULT_SITE_URL, normalize_site_url

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    APP_NAME: str = "Flask Authentication & Audit System"
    API_V1_STR: str = "/api/v1"

    # Security
    SECRET_KEY: str = secrets.token_hex(32)  # Fallback if not in .env
    ADMIN_SECRET: str
    ADMIN_EMAIL: str = "admin@yoursite.com"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    ALGORITHM: str = "HS256"

    # Password policy. Length-first defaults support passphrases; composition
    # requirements remain optional for deployments that explicitly need them.
    PASSWORD_POLICY_ENABLED: bool = True
    PASSWORD_MIN_LENGTH: int = 20
    PASSWORD_REQUIRE_UPPERCASE: bool = False
    PASSWORD_REQUIRE_LOWERCASE: bool = False
    PASSWORD_REQUIRE_NUMBER: bool = False
    PASSWORD_REQUIRE_SPECIAL: bool = False

    # Single database URI — can be PostgreSQL, SQLite, etc.
    SQLALCHEMY_DATABASE_URI: str
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False

    @field_validator("SQLALCHEMY_DATABASE_URI")
    def normalize_database_uri(cls, value):
        # SQLAlchemy still maps a bare postgresql:// URL to psycopg2. Flask-AAS
        # uses Psycopg 3, so preserve legacy/generic deployment URLs by making
        # the selected driver explicit before Flask-SQLAlchemy creates an engine.
        legacy_prefix = "postgresql://"
        if value.startswith(legacy_prefix):
            return "postgresql+psycopg://" + value[len(legacy_prefix):]
        return value

    # Registration control
    REGISTRATION_ENABLED: bool = True

    # Public application origin. This seeds EnvSettings.site_url on a fresh
    # database; persisted Site Settings becomes authoritative on later starts.
    SITE_URL: str = DEFAULT_SITE_URL

    # --- CORS ---
    # Control which frontend domains can access the API.
    # [https://example.com,https://app.example.com]
    BACKEND_CORS_ORIGINS: List[str] = []

    # Additional CSP sources required by a downstream application.
    # Keep these empty in the base and add only narrowly scoped origins.
    CSP_CONNECT_SRC: List[str] = []
    CSP_IMG_SRC: List[str] = []
    CSP_MEDIA_SRC: List[str] = []

    # TRUSTED_PROXIES should contain the IP addresses or CIDR ranges of any
    # reverse proxies, load balancers, or gateways that sit in front of your
    # Flask app and forward client requests.
    TRUSTED_PROXIES: List[str] = []
    PROXY_HOPS: int = 0

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
                raise ValueError(
                    "Configured origins must not contain CSP delimiters"
                )

        return values

    @field_validator("SITE_URL")
    def validate_site_url(cls, value):
        return normalize_site_url(value)

    @field_validator("PASSWORD_MIN_LENGTH")
    def validate_password_min_length(cls, value):
        if value < 1:
            raise ValueError("PASSWORD_MIN_LENGTH must be at least 1")
        return value

    @field_validator("PROXY_HOPS")
    def validate_proxy_hops(cls, value):
        if value < 0 or value > 10:
            raise ValueError("PROXY_HOPS must be between 0 and 10")
        return value

    # --- Cache ---
    CACHE_TYPE: str = "SimpleCache"
    CACHE_DEFAULT_TIMEOUT: int = 300
    CACHE_REDIS_URL: Optional[str] = "redis://localhost:6379/0"

    # --- Session ---
    SESSION_COOKIE_SECURE: Optional[bool] = False
    SESSION_COOKIE_HTTPONLY: Optional[bool] = True
    SESSION_COOKIE_SAMESITE: Optional[str] = "Lax"
    PERMANENT_SESSION_LIFETIME: timedelta = timedelta(minutes=30)
    SESSION_INACTIVITY_TIMEOUT_SECONDS: int = 900

    REMEMBER_COOKIE_SECURE: Optional[bool] = False
    REMEMBER_COOKIE_HTTPONLY: Optional[bool] = True
    REMEMBER_COOKIE_SAMESITE: Optional[str] = "Lax"

    @field_validator("SESSION_INACTIVITY_TIMEOUT_SECONDS")
    def validate_session_inactivity_timeout(cls, value):
        if value is not None and value < 0:
            raise ValueError(
                "SESSION_INACTIVITY_TIMEOUT_SECONDS must be zero or greater"
            )
        return value

    # --- Email ---
    MAIL_DEBUG: bool = False
    MAIL_CONFIG_UI_ENABLED: bool = False
    MAIL_CONFIG_ENCRYPTION_KEY: Optional[str] = None
    MAIL_SERVER: Optional[str] = None
    MAIL_PORT: int = 587
    MAIL_USE_TLS: bool = True
    MAIL_USE_SSL: bool = False
    MAIL_USERNAME: Optional[str] = None
    MAIL_PASSWORD: Optional[str] = None
    MAIL_DEFAULT_SENDER: Optional[str] = None

    @field_validator("MAIL_PORT")
    def validate_mail_port(cls, value):
        if value < 1 or value > 65535:
            raise ValueError("MAIL_PORT must be between 1 and 65535")
        return value

    @model_validator(mode="after")
    def validate_mail_transport_security(self):
        if self.MAIL_USE_TLS and self.MAIL_USE_SSL:
            raise ValueError(
                "MAIL_USE_TLS and MAIL_USE_SSL cannot both be enabled"
            )
        return self

    # --- Environment ---
    FLASK_ENV: Literal["development", "testing", "production"] = "production"
    DEBUG: bool = False
    TESTING: bool = False

    @model_validator(mode="after")
    def configure_environment(self):
        if self.FLASK_ENV == "production":
            self.SESSION_COOKIE_SECURE = True
            self.REMEMBER_COOKIE_SECURE = True
            self.DEBUG = False
            self.TESTING = False

            # Reject known development/example credentials in production.
            if self.SECRET_KEY == "put-your-secret-key-here":
                raise ValueError(
                    "SECRET_KEY must not use the example default in production"
                )

            if self.ADMIN_SECRET == "adminpass":
                raise ValueError(
                    "ADMIN_SECRET must not use the development default in production"
                )

        elif self.FLASK_ENV == "testing":
            self.DEBUG = False
            self.TESTING = True

        return self

    # Determine which env file to use
    @staticmethod
    def detect_env_file() -> str | None:
        override = os.environ.get("ENV_FILE_PATH")
        if override and os.path.exists(override):
            return override
        if os.path.exists(".env"):
            return ".env"
        return None

    # Store it as non-field ClassVar so Pydantic ignores it.
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


settings = Settings()
