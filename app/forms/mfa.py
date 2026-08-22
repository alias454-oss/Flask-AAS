# app/forms/mfa.py
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField
from wtforms.validators import DataRequired, Length, Regexp


class MFASetupForm(FlaskForm):
    """
    Used ONLY during setup.
    Strictly enforces 6-digit numeric TOTP.
    """
    code = StringField(
        "Enter the code from your authenticator app:",
        validators=[
            DataRequired(message="Please enter the code."),
            Regexp(r"^[0-9]{6}$", message="The code must be 6 digits."),
        ],
        render_kw={
            "placeholder": "123456",
            "required": True,
            "autofocus": True,
            "id": "code",
            "type": "text"        }
    )
    submit = SubmitField("Verify")


class TwoFactorForm(FlaskForm):
    """
    Used during login.
    Accepts TOTP (6 digits) OR Recovery Codes (8+ chars).
    """
    code = StringField(
        "Authentication Code",
        validators=[
            DataRequired(),
            # range matches 6 (TOTP) to 8/10 (Recovery Codes)
            Length(min=6, max=24, message="Enter a valid authentication code.")
        ],
        render_kw={"placeholder": "Code or Recovery Key", "autofocus": True, "autocomplete": "one-time-code"}
    )
    submit = SubmitField("Verify")


class DisableMfaForm(FlaskForm):
    code = StringField(
        "Current authentication code",
        validators=[DataRequired(), Length(min=6, max=24)],
        render_kw={"placeholder": "Code or Recovery Key", "autocomplete": "one-time-code"}
    )
    submit = SubmitField("Disable MFA")


class RecoveryCodeForm(FlaskForm):
    submit = SubmitField("Generate New Recovery Codes")
