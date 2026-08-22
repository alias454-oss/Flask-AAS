# app/forms/settings.py
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
from wtforms.validators import (
    DataRequired,
    Email,
    InputRequired,
    Length,
    NumberRange,
    Optional,
    ValidationError,
)

from app.core.site import normalize_site_url

PAGE_CONTENT_MAX_LENGTH = 65535


class PageContentForm(FlaskForm):
    content_html = TextAreaField(
        "Page Content",
        validators=[Optional(), Length(max=PAGE_CONTENT_MAX_LENGTH)],
    )
    submit = SubmitField("Save Changes")
    reset = SubmitField("Reset to Theme Default")


class AdminSettingsForm(FlaskForm):
    # Basic Site Identity
    site_name = StringField("Site Name", validators=[DataRequired()])
    site_url = StringField(
        "Site URL",
        validators=[DataRequired(), Length(max=100)],
    )
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
    users_stored_path = StringField(
        "User Storage Path",
        validators=[DataRequired(), Length(max=255)],
    )

    # UI and Look & Feel
    template = SelectField("Main Site Template", choices=[], validators=[Optional()])

    # Features
    use_mfa = BooleanField("Enable User MFA Setup")
    use_verify_email = BooleanField("Require Email Verification")
    use_user_approval = BooleanField("Require Approval for Users")
    use_user_location = BooleanField("Use User Location")
    use_captcha = BooleanField("Enable CAPTCHA")
    contact_enabled = BooleanField("Enable Contact Form")
    spam_check_enabled = BooleanField("Enable Spam Check")
    spam_check_provider = SelectField(
        "Spam Check Provider",
        choices=[],
        validators=[InputRequired()],
    )
    maint_mode = BooleanField("Maintenance Mode")
    visitor_tracking = BooleanField("Track Online Users")
    use_fancy_urls = BooleanField("Enable Fancy URLs")
    enable_plugins = BooleanField("Enable Application Plugins")

    # Password Policy
    password_policy_enabled = BooleanField("Enable Password Policy")
    password_min_length = IntegerField(
        "Minimum Password Length",
        validators=[InputRequired(), NumberRange(min=1)],
    )
    password_require_uppercase = BooleanField("Require Uppercase Letter")
    password_require_lowercase = BooleanField("Require Lowercase Letter")
    password_require_number = BooleanField("Require Number")
    password_require_special = BooleanField("Require Special Character")
    password_check_enabled = BooleanField("Enable Password Check")
    password_check_provider = SelectField(
        "Password Check Provider",
        choices=[],
        validators=[InputRequired()],
    )

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
        validators=[Optional(), NumberRange(min=1)],
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

    def validate_users_stored_path(self, field):
        normalized = str(field.data or "").strip()
        if not normalized:
            raise ValidationError("User Storage Path is required.")
        if "\x00" in normalized:
            raise ValidationError("User Storage Path contains an invalid null byte.")
        field.data = normalized

    def validate_site_url(self, field):
        try:
            normalized = normalize_site_url(field.data)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if len(normalized) > 100:
            raise ValidationError("Site URL must be 100 characters or fewer.")
        field.data = normalized
