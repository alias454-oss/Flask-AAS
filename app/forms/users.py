# app/forms/users.py
from wtforms import (
    BooleanField,
    SelectMultipleField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Email, Length, Optional
from wtforms.widgets import CheckboxInput, ListWidget

from app.core.cache import get_cached_env_settings
from app.core.security import normalize_email, normalize_username
from app.forms.profile import (
    ProfileFieldsForm,
    configure_profile_location_fields,
    normalize_optional_text,
)


class AdminUserForm(ProfileFieldsForm):
    """Validate and canonicalize administrator-managed user profile fields."""

    username = StringField(
        'Username',
        validators=[DataRequired(), Length(max=60)],
        filters=[normalize_username],
    )
    email = StringField(
        'Email',
        validators=[DataRequired(), Email(), Length(max=255)],
        filters=[normalize_email],
    )
    # Preserve the historical administrator-facing label.
    alt_phone = StringField(
        'Alt Phone',
        validators=[Optional(), Length(max=50)],
        filters=[normalize_optional_text],
    )
    roles = SelectMultipleField(
        "Assigned Roles",
        choices=[],
        coerce=int,
        option_widget=CheckboxInput(),
        widget=ListWidget(prefix_label=False),
    )
    activated = BooleanField('Activated')
    approved = BooleanField('Approved')
    notes = TextAreaField('User Notes')
    admin_notes = TextAreaField('Admin Notes')
    submit = SubmitField('Update')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        env = get_cached_env_settings()
        if not env.use_user_approval:
            del self.approved
        if not env.use_verify_email:
            del self.activated
        configure_profile_location_fields(self, env)
