# app/core/pwcheck.py
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


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
