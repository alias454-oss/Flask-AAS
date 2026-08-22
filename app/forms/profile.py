"""Shared user-profile form fields and normalization."""

from flask_wtf import FlaskForm
from wtforms import SelectField, StringField
from wtforms.validators import Length, Optional

from app.services.geo import country_choices, zone_choices

PROFILE_FIELD_NAMES = (
    "company_name",
    "first_name",
    "last_name",
    "phone",
    "alt_phone",
    "fax",
    "country_code",
    "address",
    "city",
    "zone_code",
    "postal_code",
)

LOCATION_FIELD_NAMES = (
    "country_code",
    "address",
    "city",
    "zone_code",
    "postal_code",
)


def normalize_optional_text(value):
    """Normalize optional single-line profile text before persistence."""
    if value is None:
        return None
    normalized = (
        str(value)
        .replace("\x00", "")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )
    return normalized or None


def form_field_data(form, field_names=PROFILE_FIELD_NAMES):
    """Return model-ready values for named fields present on a form."""
    return {
        field_name: form[field_name].data if field_name in form else None
        for field_name in field_names
    }


def apply_form_fields(target, form, field_names, *, submitted_fields=None):
    """Apply named form fields to a model and return the changed field names."""
    changed_fields = []
    for field_name in field_names:
        if field_name not in form:
            continue
        if submitted_fields is not None and field_name not in submitted_fields:
            continue

        new_value = form[field_name].data
        if getattr(target, field_name, None) == new_value:
            continue

        setattr(target, field_name, new_value)
        changed_fields.append(field_name)
    return changed_fields


def configure_profile_location_fields(form, env) -> None:
    """Configure or remove profile location fields from current Site Settings."""
    if not env or not env.use_user_location:
        for field_name in LOCATION_FIELD_NAMES:
            del form[field_name]
        return
    form.country_code.choices = country_choices()
    form.zone_code.choices = zone_choices(form.country_code.data)


class ProfileFieldsForm(FlaskForm):
    """Canonical persisted profile fields shared by user and admin forms."""

    company_name = StringField(
        "Company Name",
        validators=[Optional(), Length(max=255)],
        filters=[normalize_optional_text],
    )
    first_name = StringField(
        "First Name",
        validators=[Optional(), Length(max=50)],
        filters=[normalize_optional_text],
    )
    last_name = StringField(
        "Last Name",
        validators=[Optional(), Length(max=100)],
        filters=[normalize_optional_text],
    )
    phone = StringField(
        "Phone",
        validators=[Optional(), Length(max=20)],
        filters=[normalize_optional_text],
    )
    alt_phone = StringField(
        "Alternate Phone",
        validators=[Optional(), Length(max=50)],
        filters=[normalize_optional_text],
    )
    fax = StringField(
        "Fax",
        validators=[Optional(), Length(max=50)],
        filters=[normalize_optional_text],
    )
    country_code = SelectField("Country", choices=[], validators=[Optional()])
    address = StringField(
        "Address",
        validators=[Optional(), Length(max=255)],
        filters=[normalize_optional_text],
    )
    city = StringField(
        "City",
        validators=[Optional(), Length(max=100)],
        filters=[normalize_optional_text],
    )
    zone_code = SelectField(
        "Region / Subdivision",
        choices=[],
        validators=[Optional()],
    )
    postal_code = StringField(
        "Postal Code",
        validators=[Optional(), Length(max=20)],
        filters=[normalize_optional_text],
    )
