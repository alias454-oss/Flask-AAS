# routes/contact.py
import logging
from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, SubmitField
from wtforms.validators import DataRequired, Email, Length

from app.core.cache import get_cached_env_settings
from app.core.extensions import db, limiter
from app.core.security import get_client_ip, normalize_email, redact_email, is_locked_out, track_lockout_attempts, reset_lockout_attempts
from app.core.meta import page_metadata
from app.core.decorators import log_view_action
from app.core.trackers import current_route, log_action, audit_activity_enabled
from app.core.mailer import send_contact_email
from .captcha import CaptchaRequired

logger = logging.getLogger(__name__)
contact_bp = Blueprint('contact', __name__)

def is_spam(message: str) -> bool:
    # Simple now but maybe integrate Akismet or an ML-based spam service later
    blacklist = ["buy now", "free bitcoin", "click here", "viagra"]
    return any(word in message.lower() for word in blacklist)

class ContactForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=50)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=120)])
    subject = StringField("Subject", validators=[Length(max=100)])
    message = TextAreaField("Message", validators=[DataRequired(), Length(max=2000)])
    captcha = StringField("Enter CAPTCHA", validators=[CaptchaRequired()])
    nobot_check = StringField('Leave empty')  # hidden in template
    submit = SubmitField("Send Message")

    # Always define captcha at the class level but unbind it if disabled
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        env = get_cached_env_settings()
        if not env.use_captcha:
            # Remove captcha field if CAPTCHA is disabled
            del self.captcha

@contact_bp.route("/contact", methods=["GET", "POST"])
@limiter.limit("10 per hour", key_func=get_client_ip)
@log_view_action()
def contact():
    form = ContactForm()
    meta = page_metadata.get("contact", {})
    ip = get_client_ip()
    user_agent = request.headers.get("User-Agent", "Unknown")

    if form.validate_on_submit():
        email = normalize_email(form.email.data)

        if form.nobot_check.data:
            logger.warning(f"Honeypot field triggered from IP {ip}")
            if audit_activity_enabled():
                log_action(
                    user_id=getattr(current_user, "id", None),
                    action="honeypot_triggered",
                    target=current_route(),
                    extra_data={"ip": ip, "email": redact_email(email), "ua": user_agent}
                )
            return redirect(url_for("contact.contact"))

        if is_spam(form.message.data):
            logger.warning(f"Spam message blocked from {ip}")
            if audit_activity_enabled():
                log_action(
                    user_id=getattr(current_user, "id", None),
                    action="spam_blocked",
                    target=current_route(),
                    extra_data={"ip": ip, "email": redact_email(email), "ua": user_agent}
                )
            flash("Your message appears to be spam.", "danger")
            return redirect(url_for("contact.contact"))

        try:
            send_contact_email(name=form.name.data, email=email, message=form.message.data, subject=form.subject.data)

            if audit_activity_enabled():
                log_action(
                    user_id=getattr(current_user, "id", None),
                    action="contact_attempt",
                    target=current_route(),
                    extra_data={
                        "email": redact_email(email),
                        "ip": ip,
                        "user_agent": user_agent,
                        "status": "success"
                    }
                )

            logger.info(f"Contact form sent successfully from {ip} ({email})")
            flash("Message sent successfully. We'll get back to you soon.", "success")
            return redirect(url_for("contact.contact"))

        except Exception as e:
            logger.error(f"Failed to send contact form from {ip}: {e}")
            if audit_activity_enabled():
                log_action(
                    user_id=getattr(current_user, "id", None),
                    action="contact_attempt",
                    target=current_route(),
                    extra_data={
                        "email": redact_email(email),
                        "ip": ip,
                        "user_agent": user_agent,
                        "status": "failure",
                        "error": str(e)
                    }
                )
            flash("Something went wrong while sending your message.", "danger")

    elif form.errors:
        logger.info(f"Contact form validation failed from {ip} - errors: {form.errors}")
        if audit_activity_enabled():
            log_action(
                user_id=getattr(current_user, "id", None),
                action="contact_form_invalid",
                target=current_route(),
                extra_data={
                    "ip": ip,
                    "user_agent": user_agent,
                    "errors": dict(form.errors)
                }
            )

    return render_template("contact.html", form=form, **meta)


@contact_bp.route("/test-email")
def test_email():
    # Only allow this route to run if the app is in DEBUG mode
    if not get_cached_env_settings().get("DEBUG"):
        return "Not Found", 404

    from app.core.mailer import send_email
    try:
        send_email(
            subject="Test Email",
            recipient="your_email@example.com",
            body="This is a plain text test email.",
            html="<p>This is a <strong>test</strong> email.</p>"
        )
        return "Test email sent!"
    except Exception as e:
        return f"Error: {e}"