# app/core/mailer.py
import logging
import threading
from flask import current_app, render_template, url_for
from flask_mailman import EmailMessage
from jinja2 import TemplateNotFound

logger = logging.getLogger(__name__)

def send_email(subject, recipient, body, html=None):
    if current_app.config.get("MAIL_DEBUG", False):
        print(f"[MOCK EMAIL] To: {recipient}")
        print(f"Subject: {subject}")
        print(f"Body:\n{body}")
        if html:
            print(f"HTML:\n{html}")
        return

    try:
        msg = EmailMessage(
            subject=subject,
            body=html if html else body,
            to=[recipient],
            from_email=current_app.config.get("DEFAULT_FROM_EMAIL", "noreply@example.com"),
            reply_to=[current_app.config.get("REPLY_TO_EMAIL", "support@example.com")]
        )
        if html:
            msg.content_subtype = "html"

        use_threading = True
        if use_threading:
            thread = threading.Thread(target=msg.send)
            thread.start()
        else:
            msg.send()
        logger.info(f"Email sent to {recipient} with subject '{subject}'")
    except Exception as e:
        logger.exception(f"Failed to send email to {recipient}: {e}")

def render_email(template_name, **context):
    try:
        html = render_template(f"emails/{template_name}.html", **context)
        text = render_template(f"emails/{template_name}.txt", **context)
        return text, html
    except TemplateNotFound as e:
        logger.exception(f"Missing email template: {e}")
        raise

def send_contact_email(name: str, email: str, message: str, subject: str = None):
    """Send a contact form message to the site support address."""
    to_email = current_app.config.get("SUPPORT_EMAIL", "support@example.com")
    subject = subject or "New Contact Form Submission"

    text_body, html_body = render_email("contact", name=name, email=email, message=message, subject=subject)
    send_email(subject=subject, recipient=to_email, body=text_body, html=html_body)

def send_welcome_email(to_email: str, username: str, invite_link: str = None, temp_password: str = None):
    subject = "Welcome to the site!"
    # subject = "Welcome to " + current_app.config.get("SITE_NAME", "Our Site"),
    # Fallback if no link is provided
    invite_link = invite_link or f"{current_app.config['SITE_URL']}/login"

    text_body, html_body = render_email("welcome", username=username, invite_link=invite_link, temp_password=temp_password)
    send_email(subject=subject, recipient=to_email, body=text_body, html=html_body)

def send_password_reset_email(to_email, token):
    reset_url = url_for("reset.reset_password", token=token, _external=True)
    subject = "Password Reset Request"

    text_body, html_body = render_email("reset_password", reset_url=reset_url)
    send_email(subject=subject, recipient=to_email, body=text_body, html=html_body)

def send_verification_email(to_email: str, username: str, verify_url: str):
    subject = "Verify Your Email Address"

    text_body, html_body = render_email("verify_email", username=username, verify_url=verify_url)
    send_email(subject=subject, recipient=to_email, body=text_body, html=html_body)
