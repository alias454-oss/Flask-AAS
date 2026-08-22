# app/forms/register.py
from wtforms import BooleanField, PasswordField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, Length, Optional

from app.core.cache import get_cached_env_settings
from app.forms.captcha import CaptchaForm
from app.forms.profile import (
    configure_profile_location_fields,
    normalize_optional_text,
)
from app.services.passwords import password_policy


class RegisterForm(CaptchaForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=50)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[password_policy])  # Blank permits admin-issued password setup.
    company_name = StringField(
        'Company Name',
        validators=[Optional(), Length(max=255)],
        filters=[normalize_optional_text],
    )
    first_name = StringField(
        'First Name',
        validators=[Optional(), Length(max=50)],
        filters=[normalize_optional_text],
    )
    last_name = StringField(
        'Last Name',
        validators=[Optional(), Length(max=100)],
        filters=[normalize_optional_text],
    )
    phone = StringField(
        'Phone',
        validators=[Optional(), Length(max=20)],
        filters=[normalize_optional_text],
    )
    country_code = SelectField('Country', choices=[], validators=[Optional()])
    address = StringField(
        'Address',
        validators=[Optional(), Length(max=255)],
        filters=[normalize_optional_text],
    )
    city = StringField(
        'City',
        validators=[Optional(), Length(max=100)],
        filters=[normalize_optional_text],
    )
    zone_code = SelectField('Region / Subdivision', choices=[], validators=[Optional()])
    postal_code = StringField(
        'Postal Code',
        validators=[Optional(), Length(max=20)],
        filters=[normalize_optional_text],
    )
    agree = BooleanField('I agree to the terms of service', validators=[DataRequired()])
    nobot_check = StringField('Leave empty')  # hidden in template
    submit = SubmitField('Register')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        configure_profile_location_fields(self, get_cached_env_settings())
