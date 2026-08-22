# app/forms/captcha.py
from flask_wtf import FlaskForm
from wtforms import StringField
from wtforms.validators import ValidationError

from app.services import captcha as captcha_service


class CaptchaRequired:
    def __call__(self, form, field):
        if not captcha_service.is_captcha_enabled():
            return

        valid, message = captcha_service.validate_captcha(field.data)
        if not valid:
            raise ValidationError(message)


class CaptchaForm(FlaskForm):
    """Base form that removes the CAPTCHA field when the feature is disabled."""

    captcha = StringField("Enter CAPTCHA", validators=[CaptchaRequired()])

    def __init__(self, *args, captcha_enabled, **kwargs):
        super().__init__(*args, **kwargs)
        if not captcha_enabled:
            del self.captcha
