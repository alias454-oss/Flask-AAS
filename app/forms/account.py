# app/forms/account.py
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired
from wtforms import SubmitField

from app.core.cache import get_cached_env_settings
from app.forms.profile import ProfileFieldsForm, configure_profile_location_fields


class ProfileImageForm(FlaskForm):
    image = FileField(
        'Profile Image',
        validators=[FileRequired(message='Select an image to upload.')],
    )


class RemoveProfileImageForm(FlaskForm):
    pass


class ProfileForm(ProfileFieldsForm):
    submit = SubmitField('Save Profile')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        configure_profile_location_fields(self, get_cached_env_settings())
