import logging
import threading
from typing import Literal

from flask import current_app, render_template, url_for
from flask_mailman import EmailMessage
from jinja2 import TemplateNotFound

from app.core.cache import get_cached_env_settings

logger = logging.getLogger(__name__)

MailStatus = Literal["queued", "disabled", "failed"]


def _send_async_email(app, message: EmailMessage) -> None:
    """Deliver a queued message inside the application's context."""
    with app.app_context():
        try:
            message.send()
            logger.info("Async email sent to %s", message.to)
        except Exception:
            logger.exception("Async email delivery failed for %s", message.to)


def _smtp_enabled() -> bool:
    """Return the administrator-controlled SMTP enable state."""
    env = get_cached_env_settings()
    return bool(env and getattr(env, "use_smtp", False))


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

    if current_app.config.get("MAIL_DEBUG", False) or current_app.config.get(
        "TESTING",
        False,
    ):
        logger.info("[MOCK EMAIL] To: %s | Subject: %s", recipient, subject)
        return "queued"

    try:
        if not _smtp_enabled():
            logger.info("Email delivery disabled; message not queued for %s", recipient)
            return "disabled"
    except Exception:
        logger.exception("Email not queued for %s: SMTP policy lookup failed", recipient)
        return "failed"

    from_email = (
        current_app.config.get("MAIL_DEFAULT_SENDER")
        or current_app.config.get("DEFAULT_FROM_EMAIL")
        or "noreply@localhost"
    )
    reply_to = current_app.config.get("REPLY_TO_EMAIL")

    try:
        message = EmailMessage(
            subject=subject,
            body=text_body,
            to=[recipient],
            from_email=from_email,
            reply_to=[reply_to] if reply_to else None,
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

    logger.info("Email queued for asynchronous delivery to %s", recipient)
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
