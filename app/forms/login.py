# app/forms/login.py
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired

from app.forms.captcha import CaptchaForm
from app.routes.captcha import CaptchaRequired


class LoginForm(CaptchaForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember_me = BooleanField('Remember Me')
    captcha = StringField("Enter CAPTCHA", validators=[CaptchaRequired()])
    submit = SubmitField('Login')
