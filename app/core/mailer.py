import logging
import threading
from dataclasses import dataclass
from typing import Literal, Mapping, Any

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app, render_template, url_for
from flask_mailman import EmailMultiAlternatives
from flask_mailman.backends.smtp import EmailBackend
from jinja2 import TemplateNotFound

from app.models.env_settings import EnvSettings

logger = logging.getLogger(__name__)

MailStatus = Literal["queued", "disabled", "failed"]
MailSource = Literal["debug", "database", "environment"]
MailStateSource = Literal[
    "disabled",
    "debug",
    "database",
    "environment",
    "not_configured",
]
MailSecurity = Literal["starttls", "ssl", "none"]


class MailConfigurationError(ValueError):
    """Raised when a configured mail source cannot be used safely."""


def get_mail_env_settings():
    """Read mail policy/configuration from the database for each dispatch."""
    return EnvSettings.query.first()


@dataclass(frozen=True)
class MailConfiguration:
    """Immutable SMTP configuration captured before a delivery thread starts."""

    source: MailSource
    default_sender: str
    host: str | None = None
    port: int | None = None
    security: MailSecurity = "none"
    username: str | None = None
    password: str | None = None

    @property
    def use_tls(self) -> bool:
        return self.security == "starttls"

    @property
    def use_ssl(self) -> bool:
        return self.security == "ssl"


@dataclass(frozen=True)
class MailConfigurationState:
    """Safe configuration status for application logic and the admin UI."""

    enabled: bool
    available: bool
    source: MailStateSource
    ui_enabled: bool
    override_present: bool
    override_error: str | None = None

    @property
    def source_label(self) -> str:
        labels = {
            "disabled": "Disabled",
            "debug": "Debug / mock delivery",
            "database": "Site Settings",
            "environment": "Environment",
            "not_configured": "Not configured",
        }
        return labels[self.source]


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _config_mapping(config: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    return config if config is not None else current_app.config


def mail_config_ui_enabled(config: Mapping[str, Any] | None = None) -> bool:
    return bool(_config_mapping(config).get("MAIL_CONFIG_UI_ENABLED", False))


def _fernet(config: Mapping[str, Any] | None = None) -> Fernet:
    key = _clean_text(_config_mapping(config).get("MAIL_CONFIG_ENCRYPTION_KEY"))
    if not key:
        raise MailConfigurationError(
            "MAIL_CONFIG_ENCRYPTION_KEY is required for UI-managed SMTP passwords"
        )

    try:
        return Fernet(key.encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise MailConfigurationError(
            "MAIL_CONFIG_ENCRYPTION_KEY is not a valid Fernet key"
        ) from exc


def mail_encryption_available(config: Mapping[str, Any] | None = None) -> bool:
    try:
        _fernet(config)
    except MailConfigurationError:
        return False
    return True


def encrypt_smtp_password(
    password: str,
    config: Mapping[str, Any] | None = None,
) -> str:
    cleaned = _clean_text(password)
    if not cleaned:
        raise MailConfigurationError("SMTP password cannot be empty")
    return _fernet(config).encrypt(cleaned.encode("utf-8")).decode("utf-8")


def decrypt_smtp_password(
    encrypted_password: str,
    config: Mapping[str, Any] | None = None,
) -> str:
    token = _clean_text(encrypted_password)
    if not token:
        raise MailConfigurationError("Stored SMTP password is empty")

    try:
        return _fernet(config).decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise MailConfigurationError(
            "Stored SMTP password cannot be decrypted with the configured key"
        ) from exc


def _validate_port(port: Any) -> int:
    try:
        resolved = int(port)
    except (TypeError, ValueError) as exc:
        raise MailConfigurationError("SMTP port must be an integer") from exc

    if resolved < 1 or resolved > 65535:
        raise MailConfigurationError("SMTP port must be between 1 and 65535")
    return resolved


def _validate_security(security: Any) -> MailSecurity:
    resolved = _clean_text(security) or "starttls"
    if resolved not in {"starttls", "ssl", "none"}:
        raise MailConfigurationError("SMTP security mode is invalid")
    return resolved  # type: ignore[return-value]


def _validate_authentication_pair(
    username: str | None,
    password: str | None,
) -> None:
    if bool(username) != bool(password):
        raise MailConfigurationError(
            "SMTP username and password must both be supplied or both be empty"
        )


def environment_mail_configuration(
    config: Mapping[str, Any] | None = None,
) -> MailConfiguration | None:
    """Resolve a complete environment SMTP configuration without using the DB."""
    app_config = _config_mapping(config)
    host = _clean_text(app_config.get("MAIL_SERVER"))
    sender = _clean_text(app_config.get("MAIL_DEFAULT_SENDER"))
    username = _clean_text(app_config.get("MAIL_USERNAME"))
    password = _clean_text(app_config.get("MAIL_PASSWORD"))

    if not host and not sender and not username and not password:
        return None

    if not host or not sender:
        return None

    use_tls = bool(app_config.get("MAIL_USE_TLS", False))
    use_ssl = bool(app_config.get("MAIL_USE_SSL", False))
    if use_tls and use_ssl:
        return None

    try:
        port = _validate_port(app_config.get("MAIL_PORT", 587))
        _validate_authentication_pair(username, password)
    except MailConfigurationError:
        return None

    security: MailSecurity = "none"
    if use_ssl:
        security = "ssl"
    elif use_tls:
        security = "starttls"

    return MailConfiguration(
        source="environment",
        default_sender=sender,
        host=host,
        port=port,
        security=security,
        username=username,
        password=password,
    )


def database_mail_override_present(env: Any) -> bool:
    """Return whether the database contains an intentional SMTP override."""
    if env is None:
        return False

    port = getattr(env, "smtp_port", None)
    security = _clean_text(getattr(env, "smtp_security", None)) or "starttls"
    non_default_port = port not in (None, 587, "587")
    non_default_security = security != "starttls"

    return bool(
        any(
            _clean_text(value)
            for value in (
                getattr(env, "smtp_host", None),
                getattr(env, "smtp_user", None),
                getattr(env, "smtp_pass", None),
                getattr(env, "smtp_default_sender", None),
            )
        )
        or non_default_port
        or non_default_security
    )


def database_mail_configuration(env: Any) -> MailConfiguration | None:
    """Resolve a complete database SMTP override without environment blending."""
    if not database_mail_override_present(env):
        return None

    host = _clean_text(getattr(env, "smtp_host", None))
    sender = _clean_text(getattr(env, "smtp_default_sender", None))
    username = _clean_text(getattr(env, "smtp_user", None))
    encrypted_password = _clean_text(getattr(env, "smtp_pass", None))

    if not host or not sender:
        raise MailConfigurationError(
            "Saved SMTP override requires a host and default sender"
        )

    port = _validate_port(getattr(env, "smtp_port", None))
    security = _validate_security(getattr(env, "smtp_security", None))

    password = None
    if encrypted_password:
        password = decrypt_smtp_password(encrypted_password)

    _validate_authentication_pair(username, password)

    return MailConfiguration(
        source="database",
        default_sender=sender,
        host=host,
        port=port,
        security=security,
        username=username,
        password=password,
    )


def validate_mail_override_fields(
    *,
    host: Any,
    port: Any,
    security: Any,
    username: Any,
    password_available: bool,
    default_sender: Any,
) -> list[str]:
    """Validate a proposed all-or-nothing UI SMTP override."""
    resolved_host = _clean_text(host)
    resolved_username = _clean_text(username)
    resolved_sender = _clean_text(default_sender)

    non_default_port = port not in (None, "", 587, "587")
    resolved_security = _clean_text(security) or "starttls"
    non_default_security = resolved_security != "starttls"
    override_present = any(
        (
            resolved_host,
            resolved_username,
            password_available,
            resolved_sender,
            non_default_port,
            non_default_security,
        )
    )
    if not override_present:
        return []

    errors = []
    if not resolved_host:
        errors.append("SMTP host is required for a Site Settings override.")
    if not resolved_sender:
        errors.append("Default sender is required for a Site Settings override.")

    try:
        _validate_port(port)
    except MailConfigurationError as exc:
        errors.append(str(exc))

    try:
        _validate_security(security)
    except MailConfigurationError as exc:
        errors.append(str(exc))

    if bool(resolved_username) != bool(password_available):
        errors.append(
            "SMTP username and password must both be supplied or both be empty."
        )

    return errors


def _resolve_mail_configuration(
    env: Any,
) -> tuple[MailConfiguration | None, MailConfigurationState]:
    ui_enabled = mail_config_ui_enabled()
    override_present = database_mail_override_present(env)
    enabled = bool(env and getattr(env, "use_smtp", False))

    if not enabled:
        return None, MailConfigurationState(
            enabled=False,
            available=False,
            source="disabled",
            ui_enabled=ui_enabled,
            override_present=override_present,
        )

    if current_app.config.get("MAIL_DEBUG", False) or current_app.config.get(
        "TESTING",
        False,
    ):
        sender = (
            _clean_text(current_app.config.get("MAIL_DEFAULT_SENDER"))
            or "noreply@localhost"
        )
        return MailConfiguration(
            source="debug",
            default_sender=sender,
        ), MailConfigurationState(
            enabled=True,
            available=True,
            source="debug",
            ui_enabled=ui_enabled,
            override_present=override_present,
        )

    override_error = None
    if ui_enabled and override_present:
        try:
            database_config = database_mail_configuration(env)
        except MailConfigurationError:
            override_error = (
                "Saved SMTP override is incomplete or cannot be decrypted; "
                "environment fallback is being evaluated."
            )
        else:
            if database_config is not None:
                return database_config, MailConfigurationState(
                    enabled=True,
                    available=True,
                    source="database",
                    ui_enabled=True,
                    override_present=True,
                )

    environment_config = environment_mail_configuration()
    if environment_config is not None:
        return environment_config, MailConfigurationState(
            enabled=True,
            available=True,
            source="environment",
            ui_enabled=ui_enabled,
            override_present=override_present,
            override_error=override_error,
        )

    return None, MailConfigurationState(
        enabled=True,
        available=False,
        source="not_configured",
        ui_enabled=ui_enabled,
        override_present=override_present,
        override_error=override_error,
    )


def resolve_mail_configuration(env: Any = None) -> MailConfiguration | None:
    if env is None:
        env = get_mail_env_settings()
    configuration, _ = _resolve_mail_configuration(env)
    return configuration


def get_mail_configuration_state(env: Any = None) -> MailConfigurationState:
    if env is None:
        env = get_mail_env_settings()
    _, state = _resolve_mail_configuration(env)
    return state


def _send_async_email(app, message: EmailMultiAlternatives) -> None:
    """Deliver a queued message inside the application's context."""
    with app.app_context():
        try:
            message.send()
            logger.info("Async email sent to %s", message.to)
        except Exception:
            logger.exception("Async email delivery failed for %s", message.to)


def send_email(
    subject: str,
    recipient: str | None,
    text_body: str,
    html_body: str | None = None,
) -> MailStatus:
    """
    Queue an email for asynchronous delivery.

    The return value describes dispatch only. Final SMTP success or failure is
    recorded by the background worker.
    """
    if not recipient or not recipient.strip():
        logger.error("Email not queued: recipient is missing")
        return "failed"

    if not text_body and not html_body:
        logger.error("Email not queued for %s: message body is empty", recipient)
        return "failed"

    try:
        env = get_mail_env_settings()
        configuration, state = _resolve_mail_configuration(env)
    except Exception:
        logger.exception("Email not queued for %s: mail policy lookup failed", recipient)
        return "failed"

    if state.override_error:
        logger.warning("%s", state.override_error)

    if not state.enabled or configuration is None:
        logger.info("Email delivery unavailable; message not queued for %s", recipient)
        return "disabled"

    if configuration.source == "debug":
        logger.info("[MOCK EMAIL] To: %s | Subject: %s", recipient, subject)
        return "queued"

    reply_to = _clean_text(current_app.config.get("REPLY_TO_EMAIL"))

    try:
        connection = EmailBackend(
            host=configuration.host,
            port=configuration.port,
            username=configuration.username,
            password=configuration.password,
            use_tls=configuration.use_tls,
            use_ssl=configuration.use_ssl,
            fail_silently=False,
        )
        message = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            to=[recipient],
            from_email=configuration.default_sender,
            reply_to=[reply_to] if reply_to else None,
            connection=connection,
        )

        if html_body:
            message.attach_alternative(html_body, "text/html")

        app = current_app._get_current_object()
        thread = threading.Thread(
            target=_send_async_email,
            args=(app, message),
            name="flask-aas-email",
        )
        thread.start()
    except Exception:
        logger.exception("Failed to queue email for %s", recipient)
        return "failed"

    logger.info(
        "Email queued for asynchronous delivery to %s using %s configuration",
        recipient,
        configuration.source,
    )
    return "queued"


def render_email(template_name: str, **context) -> tuple[str, str]:
    """
    Render both plain-text and HTML versions of an email.

    The HTML template is required. The plain-text template may fall back to a
    compatibility message when it is not present.
    """
    context.setdefault(
        "site_name",
        current_app.config.get("SITE_NAME", "Flask-AAS"),
    )

    try:
        html = render_template(
            f"emails/html/{template_name}.html",
            **context,
        )
    except TemplateNotFound as exc:
        logger.error(
            "Missing HTML email template '%s': %s",
            template_name,
            exc,
        )
        return "", ""

    try:
        text = render_template(
            f"emails/txt/{template_name}.txt",
            **context,
        )
    except TemplateNotFound:
        text = "Please view this email in an HTML-compatible client."

    return text, html


# === Business Logic Senders ===
def send_contact_email(
    name: str,
    email: str,
    message: str,
    subject: str | None = None,
) -> MailStatus:
    to_email = current_app.config.get("SUPPORT_EMAIL")
    subject = subject or f"Contact Form: {name}"

    text, html = render_email(
        "contact",
        name=name,
        email=email,
        message=message,
    )
    return send_email(subject, to_email, text, html)


def send_welcome_email(
    to_email: str,
    username: str,
    temp_password: str | None = None,
) -> MailStatus:
    """Queue the account welcome message."""
    site_name = current_app.config.get("SITE_NAME", "Flask-AAS")
    subject = f"Welcome to {site_name}"
    invite_link = url_for("login.login", _external=True)

    text, html = render_email(
        "welcome",
        username=username,
        invite_link=invite_link,
        temp_password=temp_password,
    )
    return send_email(subject, to_email, text, html)


def send_verification_email(
    to_email: str,
    username: str,
    verify_url: str,
    temp_password: str | None = None,
) -> MailStatus:
    site_name = current_app.config.get("SITE_NAME", "Flask-AAS")
    subject = f"Verify your email for {site_name}"

    text, html = render_email(
        "verify_email",
        username=username,
        verify_url=verify_url,
        temp_password=temp_password,
    )
    return send_email(subject, to_email, text, html)


def send_password_reset_email(to_email: str, token: str) -> MailStatus:
    reset_url = url_for(
        "reset.reset_password",
        token=token,
        _external=True,
    )
    subject = "Password Reset Request"

    text, html = render_email(
        "reset_password",
        reset_url=reset_url,
    )
    return send_email(subject, to_email, text, html)


def send_mfa_change_email(
    to_email: str,
    username: str,
    action: str,
) -> MailStatus:
    site_name = current_app.config.get("SITE_NAME", "Flask-AAS")
    subject = f"MFA security change for {site_name}"

    text, html = render_email(
        "mfa_changed",
        username=username,
        action=action,
    )
    return send_email(subject, to_email, text, html)
