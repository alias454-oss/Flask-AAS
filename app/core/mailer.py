import logging
import threading
from flask import current_app, render_template, url_for
from flask_mailman import EmailMessage
from jinja2 import TemplateNotFound

logger = logging.getLogger(__name__)

# Helper to send email inside a thread with the correct App Context
def _send_async_email(app, msg):
    with app.app_context():
        try:
            msg.send()
            logger.info(f"Async email sent to {msg.to}")
        except Exception as e:
            logger.exception(f"Async email failed: {e}")

def send_email(subject, recipient, text_body, html_body=None):
    """
    Core sender. Handles context switching for threading.
    """
    # 1. Debug Mode Bypass
    if current_app.config.get("MAIL_DEBUG", False) or current_app.config.get("TESTING", False):
        logger.info(f"[MOCK EMAIL] To: {recipient} | Subject: {subject}")
        return

    try:
        msg = EmailMessage(
            subject=subject,
            body=text_body,
            to=[recipient],
            from_email=current_app.config.get("DEFAULT_FROM_EMAIL", "noreply@localhost"),
            reply_to=[current_app.config.get("REPLY_TO_EMAIL", "support@localhost")]
        )

        if html_body:
            msg.attach_alternative(html_body, "text/html")

        # 2. Context-Aware Threading
        # We must pass the actual 'app' object to the thread, 
        # because 'current_app' is a proxy that doesn't exist in the new thread.
        app = current_app._get_current_object()

        thr = threading.Thread(target=_send_async_email, args=[app, msg])
        thr.start()

    except Exception as e:
        logger.exception(f"Failed to initiate email to {recipient}: {e}")

def render_email(template_name, **context):
    """
    Renders both TXT and HTML versions.
    Injects global site variables automatically.
    """
    # Inject common variables (Site Name, Year, etc.) if not present
    if 'site_name' not in context:
        context['site_name'] = current_app.config.get("SITE_NAME", "YATSEE Refinery")

    try:
        html = render_template(f"emails/{template_name}.html", **context)
        # Try to render text, fall back to simple string if missing (optional)
        try:
            text = render_template(f"emails/{template_name}.txt", **context)
        except TemplateNotFound:
            text = "Please view this email in an HTML-compatible client."

        return text, html
    except TemplateNotFound as e:
        logger.error(f"Missing email template: {e}")
        return "", ""

# === Business Logic Senders ===
def send_contact_email(name: str, email: str, message: str, subject: str = None):
    to_email = current_app.config.get("SUPPORT_EMAIL")
    subject = subject or f"Contact Form: {name}"

    text, html = render_email("contact", name=name, email=email, message=message)
    send_email(subject, to_email, text, html)

def send_welcome_email(to_email: str, username: str, temp_password: str = None):
    """
    Aligned with register.py logic.
    Auto-generates the login link.
    """
    site_name = current_app.config.get("SITE_NAME", "YATSEE")
    subject = f"Welcome to {site_name}"

    # Generate the link internally so the route doesn't have to
    invite_link = url_for('login.login', _external=True)

    text, html = render_email(
        "welcome",
        username=username,
        invite_link=invite_link,
        temp_password=temp_password
    )
    send_email(subject, to_email, text, html)

def send_verification_email(to_email: str, username: str, verify_url: str, temp_password: str = None):
    site_name = current_app.config.get("SITE_NAME", "YATSEE")
    subject = f"Verify your email for {site_name}"

    # Includes temp_password support for the 'Auto-Register' workflow.
    text, html = render_email(
        "verify_email",
        username=username,
        verify_url=verify_url,
        temp_password=temp_password  # Pass this to the template!
    )
    send_email(subject, to_email, text, html)

def send_password_reset_email(to_email, token):
    reset_url = url_for("reset.reset_password", token=token, _external=True)
    subject = "Password Reset Request"

    text, html = render_email("reset_password", reset_url=reset_url)
    send_email(subject, to_email, text, html)