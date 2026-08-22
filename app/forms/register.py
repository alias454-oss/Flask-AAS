# app/forms/register.py
from wtforms import BooleanField, PasswordField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, Length, Optional

from app.core.cache import get_cached_env_settings
from app.forms.captcha import CaptchaForm
from app.forms.profile import configure_profile_location_fields
from app.services.passwords import password_policy


class RegisterForm(CaptchaForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=50)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[password_policy])  # Blank permits admin-issued password setup.
    company_name = StringField('Company Name', validators=[Optional(), Length(max=100)])
    first_name = StringField('First Name', validators=[Optional(), Length(max=50)])
    last_name = StringField('Last Name', validators=[Optional(), Length(max=50)])
    phone = StringField('Phone', validators=[Optional(), Length(max=20)])
    country_code = SelectField('Country', choices=[], validators=[Optional()])
    address = StringField('Address', validators=[Optional(), Length(max=150)])
    city = StringField('City', validators=[Optional(), Length(max=50)])
    zone_code = SelectField('Region / Subdivision', choices=[], validators=[Optional()])
    postal_code = StringField('Postal Code', validators=[Optional(), Length(max=20)])
    agree = BooleanField('I agree to the terms of service', validators=[DataRequired()])
    nobot_check = StringField('Leave empty')  # hidden in template
    submit = SubmitField('Register')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        configure_profile_location_fields(self, get_cached_env_settings())
