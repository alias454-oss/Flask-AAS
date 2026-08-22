# app/services/passwords.py
"""Password policy, generation, and reputation-provider checks."""

import logging
import secrets
import string
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from flask import current_app, has_app_context
from sqlalchemy.exc import SQLAlchemyError
from wtforms.validators import ValidationError

from app.core.config import settings

logger = logging.getLogger(__name__)

GENERATED_PASSWORD_DEFAULT_LENGTH = 20
GENERATED_PASSWORD_SPECIAL_CHARACTERS = "!@#$%^&*()-_=+"


@dataclass(frozen=True)
class PasswordCheckResult:
    passed: bool
    message: str | None = None


class PasswordCheckProvider(ABC):
    """Small provider contract for new-password reputation/blocklist checks."""

    key = ""
    label = ""

    @abstractmethod
    def check(self, password: str) -> PasswordCheckResult:
        """Return a pass/fail result for the exact submitted password."""


_PASSWORD_CHECK_PROVIDERS: dict[str, type[PasswordCheckProvider]] = {}


def register_password_check_provider(provider_class: type[PasswordCheckProvider]) -> None:
    """Register a provider class for runtime checks and Site Settings selection."""
    if not isinstance(provider_class, type) or not issubclass(
        provider_class, PasswordCheckProvider
    ):
        raise TypeError("Password check providers must extend PasswordCheckProvider")

    key = str(provider_class.key).strip()
    label = str(provider_class.label).strip()
    if not key or not label:
        raise ValueError("Password check providers require non-empty key and label values")
    if len(key) > 50:
        raise ValueError("Password check provider keys cannot exceed 50 characters")

    existing = _PASSWORD_CHECK_PROVIDERS.get(key)
    if existing is not None and existing is not provider_class:
        raise ValueError(f"Password check provider {key!r} is already registered")

    _PASSWORD_CHECK_PROVIDERS[key] = provider_class


def password_check_provider_choices() -> list[tuple[str, str]]:
    """Return provider choices suitable for a WTForms SelectField."""
    return [
        (key, provider_class.label)
        for key, provider_class in sorted(_PASSWORD_CHECK_PROVIDERS.items())
    ]


def check_password(password: str, provider_key: str) -> PasswordCheckResult:
    """Run the configured provider and fail closed on invalid provider/runtime errors."""
    provider_class = _PASSWORD_CHECK_PROVIDERS.get(provider_key)
    if provider_class is None:
        logger.error("Unknown password check provider configured: %s", provider_key)
        return PasswordCheckResult(
            False,
            "Password checking is unavailable. Please contact the site administrator.",
        )

    try:
        result = provider_class().check(password)
    except Exception:
        logger.exception("Password check provider %s failed", provider_key)
        return PasswordCheckResult(
            False,
            "Password checking is unavailable. Please contact the site administrator.",
        )

    if not isinstance(result, PasswordCheckResult):
        logger.error("Password check provider %s returned an invalid result", provider_key)
        return PasswordCheckResult(
            False,
            "Password checking is unavailable. Please contact the site administrator.",
        )

    return result


@lru_cache(maxsize=1)
def _local_blocklist() -> frozenset[str]:
    blocklist_path = Path(__file__).resolve().parent.parent / "data" / "common_passwords.txt"
    with blocklist_path.open("r", encoding="utf-8") as handle:
        return frozenset(
            line.casefold()
            for raw_line in handle
            if (line := raw_line.rstrip("\r\n")) and not line.startswith("#")
        )


class LocalPasswordCheckProvider(PasswordCheckProvider):
    key = "local"
    label = "Built-in Local Blocklist"

    def check(self, password: str) -> PasswordCheckResult:
        if password.casefold() in _local_blocklist():
            return PasswordCheckResult(
                False,
                "This password is too common. Please choose a different password.",
            )
        return PasswordCheckResult(True)


register_password_check_provider(LocalPasswordCheckProvider)


def _password_setting(name, default):
    if has_app_context():
        if "sqlalchemy" in current_app.extensions:
            try:
                from app.core.cache import get_cached_env_settings

                env = get_cached_env_settings()
                attribute_name = name.lower()
                if env is not None and hasattr(env, attribute_name):
                    return getattr(env, attribute_name)
            except SQLAlchemyError:
                pass

        return current_app.config.get(name, default)
    return getattr(settings, name, default)


def password_policy_errors(password: str) -> list[str]:
    """Return active password-policy failures without altering the password."""
    if not _password_setting("PASSWORD_POLICY_ENABLED", True):
        return []

    if not isinstance(password, str):
        return ["Password is required."]

    errors = []
    minimum_length = int(_password_setting("PASSWORD_MIN_LENGTH", 20))

    if len(password) < minimum_length:
        errors.append(
            f"Password must be at least {minimum_length} characters long."
        )
    if _password_setting("PASSWORD_REQUIRE_UPPERCASE", False) and not any(
        character.isupper() for character in password
    ):
        errors.append("Password must contain at least one uppercase letter.")
    if _password_setting("PASSWORD_REQUIRE_LOWERCASE", False) and not any(
        character.islower() for character in password
    ):
        errors.append("Password must contain at least one lowercase letter.")
    if _password_setting("PASSWORD_REQUIRE_NUMBER", False) and not any(
        character.isdigit() for character in password
    ):
        errors.append("Password must contain at least one number.")
    if _password_setting("PASSWORD_REQUIRE_SPECIAL", False) and not any(
        not character.isalnum() for character in password
    ):
        errors.append("Password must contain at least one non-alphanumeric character.")

    return errors


def password_policy(form, field):
    """WTForms validator for a supplied password; blank optional fields are ignored."""
    if field.data is None or field.data == "":
        return

    errors = password_validation_errors(field.data)
    if errors:
        raise ValidationError(" ".join(errors))


def generate_random_password(length: int | None = None) -> str:
    """Generate a password that always satisfies the active password policy."""
    requested_length = GENERATED_PASSWORD_DEFAULT_LENGTH if length is None else int(length)
    if requested_length < 1:
        raise ValueError("Generated password length must be at least 1")

    policy_enabled = bool(_password_setting("PASSWORD_POLICY_ENABLED", True))
    minimum_length = int(_password_setting("PASSWORD_MIN_LENGTH", 20))
    target_length = max(requested_length, minimum_length) if policy_enabled else requested_length

    required_characters = []
    alphabet = string.ascii_letters + string.digits

    if policy_enabled and _password_setting("PASSWORD_REQUIRE_UPPERCASE", False):
        required_characters.append(secrets.choice(string.ascii_uppercase))
    if policy_enabled and _password_setting("PASSWORD_REQUIRE_LOWERCASE", False):
        required_characters.append(secrets.choice(string.ascii_lowercase))
    if policy_enabled and _password_setting("PASSWORD_REQUIRE_NUMBER", False):
        required_characters.append(secrets.choice(string.digits))
    if policy_enabled and _password_setting("PASSWORD_REQUIRE_SPECIAL", False):
        required_characters.append(
            secrets.choice(GENERATED_PASSWORD_SPECIAL_CHARACTERS)
        )
        alphabet += GENERATED_PASSWORD_SPECIAL_CHARACTERS

    target_length = max(target_length, len(required_characters))
    characters = required_characters + [
        secrets.choice(alphabet)
        for _ in range(target_length - len(required_characters))
    ]
    secrets.SystemRandom().shuffle(characters)
    password = "".join(characters)

    errors = password_validation_errors(password)
    if errors:
        raise RuntimeError("Generated password did not satisfy the active policy")

    return password


def password_validation_errors(password: str) -> list[str]:
    """Return all active new-password validation failures."""
    errors = password_policy_errors(password)
    if not isinstance(password, str):
        return errors

    if _password_setting("PASSWORD_CHECK_ENABLED", False):
        result = check_password(
            password,
            str(_password_setting("PASSWORD_CHECK_PROVIDER", "local")),
        )
        if not result.passed:
            errors.append(
                result.message
                or "This password cannot be used. Please choose a different password."
            )

    return errors
