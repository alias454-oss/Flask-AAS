# app/forms/reset.py
from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo

from app.services.passwords import password_policy


class ForgotPasswordForm(FlaskForm):
    email = StringField("Enter Your Email", validators=[DataRequired(), Email()])
    submit = SubmitField("Email Password")


class NewPasswordForm(FlaskForm):
    password = PasswordField("New Password", validators=[DataRequired(), password_policy])
    confirm = PasswordField(
        "Confirm New Password",
        validators=[DataRequired(), EqualTo("password")],
    )


class ResetPasswordForm(NewPasswordForm):
    submit = SubmitField("Reset Password")


class ChangePasswordForm(NewPasswordForm):
    old_password = PasswordField("Current Password", validators=[DataRequired()])
    submit = SubmitField("Update Password")
