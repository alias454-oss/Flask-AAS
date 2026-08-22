"""Tests for the shared user-profile web-form contract."""

from unittest.mock import patch

from flask import Flask
from werkzeug.datastructures import MultiDict
from wtforms.validators import Length

from app.forms.profile import ProfileFieldsForm
from app.forms.register import RegisterForm


WEB_PROFILE_LIMITS = {
    "company_name": 255,
    "first_name": 50,
    "last_name": 100,
    "phone": 20,
    "address": 255,
    "city": 100,
    "postal_code": 20,
}


def _max_length(field):
    return next(
        validator.max
        for validator in field.validators
        if isinstance(validator, Length)
    )


def _forms(formdata=None):
    app = Flask(__name__)
    app.config.update(SECRET_KEY="profile-form-test", WTF_CSRF_ENABLED=False)

    with app.test_request_context(method="POST"), patch(
        "app.forms.register.get_cached_env_settings",
        return_value=None,
    ), patch(
        "app.forms.register.configure_profile_location_fields",
    ):
        profile = ProfileFieldsForm(formdata=formdata)
        register = RegisterForm(formdata=formdata, captcha_enabled=False)

    return profile, register


def test_profile_and_registration_forms_share_web_field_limits():
    profile, register = _forms()

    for field_name, expected_max in WEB_PROFILE_LIMITS.items():
        assert _max_length(profile[field_name]) == expected_max
        assert _max_length(register[field_name]) == expected_max


def test_registration_normalizes_optional_profile_text():
    raw_value = "  Example\nValue\x00  "
    formdata = MultiDict(
        (field_name, raw_value)
        for field_name in WEB_PROFILE_LIMITS
    )
    _, register = _forms(formdata=formdata)

    for field_name in WEB_PROFILE_LIMITS:
        assert register[field_name].data == "Example Value"
