# app/core/spam.py
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpamCheckResult:
    passed: bool
    message: str | None = None


class SpamCheckProvider(ABC):
    """Small provider contract for submitted-message spam checks."""

    key = ""
    label = ""

    @abstractmethod
    def check(self, message: str) -> SpamCheckResult:
        """Return a pass/fail result for the submitted message."""


_SPAM_CHECK_PROVIDERS: dict[str, type[SpamCheckProvider]] = {}


def register_spam_check_provider(provider_class: type[SpamCheckProvider]) -> None:
    """Register a provider class for runtime checks and Site Settings selection."""
    if not isinstance(provider_class, type) or not issubclass(
        provider_class, SpamCheckProvider
    ):
        raise TypeError("Spam check providers must extend SpamCheckProvider")

    key = str(provider_class.key).strip()
    label = str(provider_class.label).strip()
    if not key or not label:
        raise ValueError("Spam check providers require non-empty key and label values")
    if len(key) > 50:
        raise ValueError("Spam check provider keys cannot exceed 50 characters")

    existing = _SPAM_CHECK_PROVIDERS.get(key)
    if existing is not None and existing is not provider_class:
        raise ValueError(f"Spam check provider {key!r} is already registered")

    _SPAM_CHECK_PROVIDERS[key] = provider_class


def spam_check_provider_choices() -> list[tuple[str, str]]:
    """Return provider choices suitable for a WTForms SelectField."""
    return [
        (key, provider_class.label)
        for key, provider_class in sorted(_SPAM_CHECK_PROVIDERS.items())
    ]


def check_spam(message: str, provider_key: str) -> SpamCheckResult:
    """Run the configured provider; provider failures preserve contact availability."""
    provider_class = _SPAM_CHECK_PROVIDERS.get(provider_key)
    if provider_class is None:
        logger.error("Unknown spam check provider configured: %s", provider_key)
        return SpamCheckResult(True)

    try:
        result = provider_class().check(message)
    except Exception:
        logger.exception("Spam check provider %s failed", provider_key)
        return SpamCheckResult(True)

    if not isinstance(result, SpamCheckResult):
        logger.error("Spam check provider %s returned an invalid result", provider_key)
        return SpamCheckResult(True)

    return result


@lru_cache(maxsize=1)
def _local_spam_phrases() -> tuple[str, ...]:
    phrases_path = Path(__file__).resolve().parent.parent / "data" / "spam_phrases.txt"
    with phrases_path.open("r", encoding="utf-8") as handle:
        return tuple(
            line.casefold()
            for raw_line in handle
            if (line := raw_line.rstrip("\r\n")) and not line.startswith("#")
        )


class LocalSpamCheckProvider(SpamCheckProvider):
    key = "local"
    label = "Built-in Local Phrase List"

    def check(self, message: str) -> SpamCheckResult:
        normalized_message = message.casefold()
        if any(phrase in normalized_message for phrase in _local_spam_phrases()):
            return SpamCheckResult(
                False,
                "Your message appears to be spam.",
            )
        return SpamCheckResult(True)


register_spam_check_provider(LocalSpamCheckProvider)
