# routes/admin/settings.py
import logging
import os

import pytz
from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    EmailField,
    IntegerField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Email, InputRequired, Length, NumberRange, Optional

from app.core.auth import admin_required, login_required
from app.core.cache import get_cached_env_settings
from app.core.decorators import log_view_action
from app.core.extensions import db, limiter
from app.core.mailer import (
    MailConfigurationError,
    database_mail_override_present,
    decrypt_smtp_password,
    encrypt_smtp_password,
    environment_mail_configuration,
    get_mail_configuration_state,
    mail_config_ui_enabled,
    mail_encryption_available,
    validate_mail_override_fields,
)
from app.core.meta import page_metadata
from app.core.security import get_client_ip
from app.core.trackers import get_admin_quick_stats, log_action
from app.models import Role

logger = logging.getLogger(__name__)

settings_bp = Blueprint("settings", __name__, url_prefix="/admin/settings")

SMTP_FIELD_NAMES = {
    "smtp_host",
    "smtp_port",
    "smtp_security",
    "smtp_user",
    "smtp_pass",
    "smtp_default_sender",
    "clear_smtp_override",
}
NON_MODEL_FIELD_NAMES = {"csrf_token", "submit", "clear_smtp_override"}


class AdminSettingsForm(FlaskForm):
    # Basic Site Identity
    site_name = StringField("Site Name", validators=[DataRequired()])
    site_url = StringField("Site URL", validators=[DataRequired()])
    site_lang = SelectField("Language", choices=[], validators=[Optional()])
    site_timezone = SelectField("Timezone", choices=[], validators=[Optional()])
    description = TextAreaField("Site Description", validators=[Optional()])
    keywords = TextAreaField("Site Keywords", validators=[Optional()])

    # Admin Contact
    admin_name = StringField("Admin Name", validators=[Optional()])
    admin_email = EmailField("Admin Email", validators=[Optional(), Email()])

    # User System Environment
    site_mode = SelectField(
        "Site Mode",
        choices=[(0, "Multi-User"), (1, "Single User")],
        coerce=int,
        default=0,
        validators=[InputRequired()],
    )
    default_role_id = SelectField(
        "Default Role",
        choices=[],
        coerce=int,
        validators=[Optional()],
    )
    users_per_page = IntegerField(
        "Show Users Per Page",
        validators=[Optional(), NumberRange(min=1, max=100)],
    )
    users_stored_path = StringField("User Storage Path", validators=[Optional()])

    # UI and Look & Feel
    template = SelectField("Main Site Template", choices=[], validators=[Optional()])

    # Features
    use_mfa = BooleanField("Enable User MFA Setup")
    use_verify_email = BooleanField("Require Email Verification")
    use_user_approval = BooleanField("Require Approval for Users")
    use_user_location = BooleanField("Use User Location")
    use_captcha = BooleanField("Enable CAPTCHA")
    maint_mode = BooleanField("Maintenance Mode")
    visitor_tracking = BooleanField("Track Online Users")
    use_fancy_urls = BooleanField("Enable Fancy URLs")

    # Maintenance
    enable_delete_old_users = BooleanField("Auto-delete Old Users")
    users_delete_after_days = IntegerField(
        "Delete After X Days",
        validators=[Optional(), NumberRange(min=0)],
    )
    email_after_days = IntegerField(
        "Send Email After X Days",
        validators=[Optional(), NumberRange(min=0)],
    )

    # Email
    use_smtp = BooleanField("Enable Outbound Email")
    smtp_host = StringField(
        "SMTP Host",
        validators=[Optional(), Length(max=255)],
    )
    smtp_port = IntegerField(
        "SMTP Port",
        default=587,
        validators=[Optional(), NumberRange(min=1, max=65535)],
    )
    smtp_security = SelectField(
        "Connection Security",
        choices=[
            ("starttls", "STARTTLS"),
            ("ssl", "Implicit TLS"),
            ("none", "None"),
        ],
        default="starttls",
        validators=[Optional()],
    )
    smtp_user = StringField(
        "SMTP Username",
        validators=[Optional(), Length(max=255)],
    )
    smtp_pass = PasswordField(
        "SMTP Password",
        validators=[Optional(), Length(max=1024)],
        render_kw={
            "autocomplete": "new-password",
            "placeholder": "Leave blank to keep the saved password",
        },
    )
    smtp_default_sender = EmailField(
        "Default Sender",
        validators=[Optional(), Email(), Length(max=255)],
    )
    clear_smtp_override = BooleanField("Clear Site Settings SMTP Override")

    # Optional Advanced
    enable_analytics = BooleanField("Enable Site Analytics")
    allow_custom_themes = BooleanField("Allow Custom Themes")

    # Lockout settings for security
    max_failed_attempts = IntegerField(
        "Max Failed Login Attempts",
        default=5,
        validators=[Optional(), NumberRange(min=0)],
    )
    lockout_duration_seconds = IntegerField(
        "Lockout Duration in Seconds",
        default=900,
        validators=[Optional(), NumberRange(min=1, max=65535)],
    )

    enable_logging = BooleanField("Enable Audit Logging")
    log_level = SelectField(
        "Application Log Level (not audit logging)",
        choices=[
            ("DEBUG", "DEBUG"),
            ("INFO", "INFO"),
            ("WARNING", "WARNING"),
            ("ERROR", "ERROR"),
        ],
        default="INFO",
        validators=[Optional()],
    )

    submit = SubmitField("Update Settings")


def _strip_or_none(value):
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


def _form_mail_password_available(form, env) -> bool:
    return bool(_strip_or_none(form.smtp_pass.data) or getattr(env, "smtp_pass", None))


def validate_mail_settings(form, env) -> bool:
    """Validate the prospective outbound-email and SMTP configuration."""
    ui_enabled = mail_config_ui_enabled()
    override_available = False

    if ui_enabled and not form.clear_smtp_override.data:
        password_available = _form_mail_password_available(form, env)
        errors = validate_mail_override_fields(
            host=form.smtp_host.data,
            port=form.smtp_port.data,
            security=form.smtp_security.data,
            username=form.smtp_user.data,
            password_available=password_available,
            default_sender=form.smtp_default_sender.data,
        )

        if _strip_or_none(form.smtp_pass.data) and not mail_encryption_available():
            errors.append(
                "A valid MAIL_CONFIG_ENCRYPTION_KEY is required before saving "
                "an SMTP password."
            )

        if password_available and not _strip_or_none(form.smtp_pass.data):
            try:
                decrypt_smtp_password(env.smtp_pass)
            except MailConfigurationError as exc:
                errors.append(str(exc))

        if errors:
            form.smtp_host.errors.extend(errors)
        else:
            override_available = any(
                (
                    _strip_or_none(form.smtp_host.data),
                    _strip_or_none(form.smtp_user.data),
                    password_available,
                    _strip_or_none(form.smtp_default_sender.data),
                )
            )

    debug_available = bool(
        current_app.config.get("MAIL_DEBUG", False)
        or current_app.config.get("TESTING", False)
    )
    environment_available = environment_mail_configuration() is not None
    transport_available = debug_available or override_available or environment_available

    if form.use_smtp.data and not transport_available:
        form.use_smtp.errors.append(
            "Outbound email requires a complete Site Settings SMTP override "
            "or complete deployment mail configuration."
        )

    if form.use_verify_email.data and not form.use_smtp.data:
        form.use_verify_email.errors.append(
            "Email verification cannot be required while outbound email is disabled."
        )
    elif form.use_verify_email.data and not transport_available:
        form.use_verify_email.errors.append(
            "Email verification requires an available outbound email transport."
        )

    return not any(
        (
            form.smtp_host.errors,
            form.use_smtp.errors,
            form.use_verify_email.errors,
        )
    )


def update_env_settings(form, env, db):
    """Apply settings updates to the caller-owned transaction."""
    ui_enabled = mail_config_ui_enabled()

    for field in form:
        if field.name in NON_MODEL_FIELD_NAMES or field.name == "smtp_pass":
            continue
        if field.name in SMTP_FIELD_NAMES and not ui_enabled:
            continue
        if hasattr(env, field.name):
            setattr(env, field.name, field.data)

    env.default_role_id = form.default_role_id.data

    if ui_enabled:
        if form.clear_smtp_override.data:
            env.smtp_host = None
            env.smtp_port = 587
            env.smtp_security = "starttls"
            env.smtp_user = None
            env.smtp_pass = None
            env.smtp_default_sender = None
        else:
            env.smtp_host = _strip_or_none(form.smtp_host.data)
            env.smtp_port = form.smtp_port.data
            env.smtp_security = form.smtp_security.data
            env.smtp_user = _strip_or_none(form.smtp_user.data)
            env.smtp_default_sender = _strip_or_none(form.smtp_default_sender.data)

            submitted_password = _strip_or_none(form.smtp_pass.data)
            if submitted_password:
                env.smtp_pass = encrypt_smtp_password(submitted_password)

            if not database_mail_override_present(env):
                env.smtp_pass = None

    db.session.add(env)


def safe_changed_fields(form, env) -> list[str]:
    """Return safe audit field names without credential values."""
    ui_enabled = mail_config_ui_enabled()
    changed_fields = []

    for field in form:
        if field.name in NON_MODEL_FIELD_NAMES or field.name == "smtp_pass":
            continue
        if field.name in SMTP_FIELD_NAMES and not ui_enabled:
            continue
        if not hasattr(env, field.name):
            continue

        old_value = getattr(env, field.name, None)
        if str(field.data) != str(old_value):
            changed_fields.append(field.name)

    if ui_enabled:
        if form.clear_smtp_override.data and database_mail_override_present(env):
            changed_fields.append("smtp_override_cleared")
        elif _strip_or_none(form.smtp_pass.data):
            changed_fields.append("smtp_password_changed")

    return sorted(set(changed_fields))


def get_available_templates():
    try:
        themes_path = os.path.join(current_app.root_path, "templates", "themes")
        return [
            (name, name)
            for name in os.listdir(themes_path)
            if os.path.isdir(os.path.join(themes_path, name))
        ]
    except FileNotFoundError:
        return [("default", "default")]


def get_supported_languages():
    return [("en", "English")]


def get_timezones():
    return [(tz, tz) for tz in pytz.all_timezones]


@settings_bp.route("/", methods=["GET", "POST"])
@limiter.limit("10 per minute", key_func=get_client_ip)
@log_view_action()
@login_required
@admin_required
def settings():
    meta = page_metadata.get("admin_settings", {})
    env = get_cached_env_settings()

    role_choices = [
        (role.id, role.name)
        for role in Role.query.order_by(Role.name).all()
    ]

    if request.method == "POST":
        form_data = request.form.copy()
        if not mail_config_ui_enabled():
            for field_name in SMTP_FIELD_NAMES:
                form_data.pop(field_name, None)
        elif form_data.get("clear_smtp_override"):
            # Clearing must remain possible even when the saved override is
            # malformed or was created by an older schema. Normalize the
            # credential fields before WTForms validates the request.
            form_data["smtp_host"] = ""
            form_data["smtp_port"] = "587"
            form_data["smtp_security"] = "starttls"
            form_data["smtp_user"] = ""
            form_data["smtp_pass"] = ""
            form_data["smtp_default_sender"] = ""
        form = AdminSettingsForm(form_data)
    else:
        form = AdminSettingsForm(obj=env)
        form.smtp_pass.data = ""
        form.clear_smtp_override.data = False
        if not form.smtp_port.data:
            form.smtp_port.data = 587
        if not form.smtp_security.data:
            form.smtp_security.data = "starttls"

    form.template.choices = get_available_templates()
    form.site_lang.choices = get_supported_languages()
    form.site_timezone.choices = get_timezones()
    form.default_role_id.choices = role_choices

    if not form.default_role_id.data:
        form.default_role_id.data = env.default_role_id

    form_valid = form.validate_on_submit()
    mail_valid = validate_mail_settings(form, env) if form_valid else False

    if form_valid and mail_valid:
        changed_fields = safe_changed_fields(form, env)

        try:
            update_env_settings(form, env, db)
            log_action(
                action="update_site_settings",
                user_id=current_user.id,
                target="/admin/settings",
                extra_data={
                    "fields_updated": changed_fields,
                    "ip": get_client_ip(),
                    "user_agent": request.headers.get("User-Agent"),
                },
            )
            db.session.commit()
            flash(
                f"Site settings updated by {current_user.username}",
                "success",
            )
            return redirect(url_for("settings.settings"))
        except Exception:
            db.session.rollback()
            logger.exception("Failed to update environment settings")
            flash("An error occurred while updating the settings.", "error")
    elif request.method == "POST":
        flash("Please correct the highlighted settings.", "error")

    mail_state = get_mail_configuration_state(env)
    quick_stats = get_admin_quick_stats()

    return render_template(
        "admin/settings.html",
        form=form,
        settings=env,
        mail_state=mail_state,
        mail_config_ui_enabled=mail_config_ui_enabled(),
        mail_encryption_available=mail_encryption_available(),
        quick_stats=quick_stats,
        **meta,
    )
