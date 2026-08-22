# app/forms/captcha.py
from flask_wtf import FlaskForm


class CaptchaForm(FlaskForm):
    """Base form that removes the CAPTCHA field when the feature is disabled."""

    def __init__(self, *args, captcha_enabled, **kwargs):
        super().__init__(*args, **kwargs)
        if not captcha_enabled:
            del self.captcha
