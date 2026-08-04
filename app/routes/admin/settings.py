# routes/admin/settings.py
import os
import pytz
import logging
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, BooleanField, SubmitField, IntegerField, EmailField, SelectField, PasswordField
from wtforms.validators import DataRequired, Optional, NumberRange, Email

from app.core.cache import get_cached_env_settings
from app.core.extensions import db, limiter
from app.core.security import get_client_ip
from app.core.auth import login_required, admin_required
from app.core.meta import page_metadata
from app.core.decorators import log_view_action
from app.core.trackers import log_action
from app.models import Role

logger = logging.getLogger(__name__)

settings_bp = Blueprint('settings', __name__, url_prefix='/admin/settings')

class AdminSettingsForm(FlaskForm):
    # Basic Site Identity
    site_name = StringField("Site Name", validators=[DataRequired()])
    site_url = StringField("Site URL", validators=[DataRequired()])
    site_lang = SelectField("Language", choices=[], validators=[Optional()])
    site_timezone = SelectField("Timezone", choices=[], validators=[Optional()])
    description = TextAreaField("Site Description", validators=[Optional()])
    keywords = TextAreaField("Site Keywords", validators=[Optional()])  # Changed to TextAreaField

    # Admin Contact
    admin_name = StringField("Admin Name", validators=[Optional()])
    admin_email = EmailField("Admin Email", validators=[Optional(), Email()])

    # User System Environment
    # 0 = public/multi-user, 1 = single-user
    site_mode = SelectField(
        "Site Mode",
        choices=[(0, "Multi-User"), (1, "Single User")],
        coerce=int,
        default=0,
        validators=[DataRequired()]
    )
    default_role_id = SelectField(
        "Default Role",
        choices=[],  # to be filled dynamically in the view
        coerce=int,
        validators=[Optional()],
    )
    users_per_page = IntegerField("Show Users Per Page", validators=[Optional(), NumberRange(min=1, max=100)])
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
    users_delete_after_days = IntegerField("Delete After X Days", validators=[Optional(), NumberRange(min=0)])
    email_after_days = IntegerField("Send Email After X Days", validators=[Optional(), NumberRange(min=0)])

    # Email
    use_smtp = BooleanField("Enable SMTP")
    smtp_host = StringField("SMTP Host", validators=[Optional()])
    smtp_port = IntegerField("SMTP Port", default=25, validators=[Optional(), NumberRange(min=1, max=65535)])
    smtp_user = StringField("SMTP Username", validators=[Optional()])
    smtp_pass = PasswordField("SMTP Password", validators=[Optional()])

    # Optional Advanced
    enable_analytics = BooleanField("Enable Site Analytics")
    allow_custom_themes = BooleanField("Allow Custom Themes")

    # Lockout settings for security
    max_failed_attempts = IntegerField("Max Failed Login Attempts", default=5, validators=[Optional(), NumberRange(min=0)])
    lockout_duration_seconds = IntegerField("Lockout Duration in Seconds", default=900, validators=[Optional(), NumberRange(min=1, max=65535)])

    enable_logging = BooleanField("Enable Audit Logging")
    log_level = SelectField(
        "Application Log Level(not audit logging)",
        choices=[("DEBUG", "DEBUG"), ("INFO", "INFO"), ("WARNING", "WARNING"), ("ERROR", "ERROR")],
        default="INFO",
        validators=[Optional()],
    )

    submit = SubmitField("Update Settings")

def update_env_settings(form, env, db):
    """Apply settings updates to the caller-owned transaction."""
    form.populate_obj(env)
    env.default_role_id = form.default_role_id.data
    db.session.add(env)

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

    role_choices = [(role.id, role.name) for role in Role.query.order_by(Role.name).all()]

    if request.method == "POST":
        form = AdminSettingsForm(request.form)
    else:
        form = AdminSettingsForm(obj=env)

    # Always set choices before any validation
    form.template.choices = get_available_templates()
    form.site_lang.choices = get_supported_languages()
    form.site_timezone.choices = get_timezones()
    form.default_role_id.choices = role_choices

    # Optional: repopulate default_role_id if needed (usually not required now)
    if not form.default_role_id.data:
        form.default_role_id.data = env.default_role_id

    if form.validate_on_submit():
        # Detect changed fields only
        changed_fields = []
        for field in form:
            if field.name == 'csrf_token':
                continue
            old_value = getattr(env, field.name, None)
            # Cast to string for consistent comparison (handles types gracefully)
            if str(field.data) != str(old_value):
                changed_fields.append(field.name)

        try:
            update_env_settings(form, env, db)
            flash(f"Site settings updated by {current_user.username}", "success")

            # Always log Admin actions
            log_action(
                action="update_site_settings",
                user_id=current_user.id,
                target="/admin/settings",
                extra_data={
                    "fields_updated": changed_fields,
                    "ip": request.headers.get("X-Forwarded-For", request.remote_addr),
                    "user_agent": request.headers.get("User-Agent")
                }
            )
            db.session.commit()
            return redirect(url_for("settings.settings"))
        except Exception:
            db.session.rollback()
            logger.exception("Failed to update environment settings")
            flash("An error occurred while updating the settings.", "error")

    return render_template("admin/settings.html", form=form, settings=env, **meta)
