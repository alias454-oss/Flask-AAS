# routes/contact.py
import logging

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, Length

from app.core.cache import get_cached_env_settings
from app.core.decorators import log_view_action
from app.core.extensions import limiter
from app.core.mailer import contact_form_available, send_contact_email
from app.core.meta import page_metadata
from app.core.security import get_client_ip, normalize_email, redact_email
from app.core.spam import check_spam
from app.core.trackers import (
    audit_activity_enabled,
    current_route,
    log_action_isolated,
)

from .captcha import CaptchaRequired

logger = logging.getLogger(__name__)
contact_bp = Blueprint("contact", __name__)


class ContactForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=50)])
    email = StringField(
        "Email",
        validators=[DataRequired(), Email(), Length(max=120)],
    )
    subject = StringField("Subject", validators=[Length(max=100)])
    message = TextAreaField(
        "Message",
        validators=[DataRequired(), Length(max=2000)],
    )
    captcha = StringField("Enter CAPTCHA", validators=[CaptchaRequired()])
    nobot_check = StringField("Leave empty")  # hidden in template
    submit = SubmitField("Send Message")

    # Always define captcha at the class level but unbind it if disabled
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        env = get_cached_env_settings()
        if not env or not env.use_captcha:
            # Remove captcha field if CAPTCHA is disabled
            del self.captcha


@contact_bp.route("/contact", methods=["GET", "POST"])
@limiter.limit("10 per hour", key_func=get_client_ip)
@log_view_action()
def contact():
    env = get_cached_env_settings()
    if not contact_form_available(env):
        abort(404)

    form = ContactForm()
    meta = page_metadata.get("contact", {})
    ip = get_client_ip()
    user_agent = (
        request.headers.get("User-Agent", "Unknown")
        .replace("\r", " ")
        .replace("\n", " ")[:255]
    )

    if form.validate_on_submit():
        email = normalize_email(form.email.data)
        redacted_email = redact_email(email)

        if form.nobot_check.data:
            logger.warning("Honeypot field triggered from IP %s", ip)
            if audit_activity_enabled():
                log_action_isolated(
                    user_id=getattr(current_user, "id", None),
                    action="honeypot_triggered",
                    target=current_route(),
                    extra_data={
                        "ip": ip,
                        "email": redacted_email,
                        "ua": user_agent,
                    },
                )
            return redirect(url_for("contact.contact"))

        if getattr(env, "spam_check_enabled", True):
            spam_provider = str(getattr(env, "spam_check_provider", "local"))
            spam_result = check_spam(form.message.data, spam_provider)
            if not spam_result.passed:
                logger.warning(
                    "Spam message blocked by provider=%s from IP %s",
                    spam_provider,
                    ip,
                )
                if audit_activity_enabled():
                    log_action_isolated(
                        user_id=getattr(current_user, "id", None),
                        action="spam_blocked",
                        target=current_route(),
                        extra_data={
                            "ip": ip,
                            "email": redacted_email,
                            "ua": user_agent,
                            "provider": spam_provider,
                        },
                    )
                flash(
                    spam_result.message or "Your message appears to be spam.",
                    "danger",
                )
                return redirect(url_for("contact.contact"))

        try:
            mail_status = send_contact_email(
                name=form.name.data,
                email=email,
                message=form.message.data,
                subject=form.subject.data,
            )
        except Exception:
            logger.exception("Unexpected contact-email failure from IP %s", ip)
            mail_status = "failed"

        if audit_activity_enabled():
            log_action_isolated(
                user_id=getattr(current_user, "id", None),
                action="contact_attempt",
                target=current_route(),
                extra_data={
                    "email": redacted_email,
                    "ip": ip,
                    "user_agent": user_agent,
                    "status": mail_status,
                },
            )

        if mail_status == "queued":
            logger.info(
                "Contact message accepted from IP %s for %s",
                ip,
                redacted_email,
            )
            flash("Your message was accepted for delivery.", "success")
        else:
            logger.warning(
                "Contact message was not queued from IP %s for %s: status=%s",
                ip,
                redacted_email,
                mail_status,
            )
            flash(
                "Message delivery is currently unavailable. Please try again later.",
                "danger",
            )

        return redirect(url_for("contact.contact"))

    if form.errors:
        logger.info(
            "Contact form validation failed from IP %s: fields=%s",
            ip,
            sorted(form.errors),
        )
        if audit_activity_enabled():
            log_action_isolated(
                user_id=getattr(current_user, "id", None),
                action="contact_form_invalid",
                target=current_route(),
                extra_data={
                    "ip": ip,
                    "user_agent": user_agent,
                    "fields": sorted(form.errors),
                },
            )

    return render_template("contact.html", form=form, **meta)
