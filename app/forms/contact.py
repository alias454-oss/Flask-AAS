# app/forms/contact.py
from wtforms import StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, Length

from app.forms.captcha import CaptchaForm
from app.routes.captcha import CaptchaRequired


class ContactForm(CaptchaForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=50)])
    email = StringField(
        "Email",
        validators=[DataRequired(), Email(), Length(max=120)],
    )
    subject = StringField("Subject", validators=[Length(max=100)])
    message = TextAreaField(
        "Message",
        validators=[DataRequired(), Length(max=2000)],
    )
    captcha = StringField("Enter CAPTCHA", validators=[CaptchaRequired()])
    nobot_check = StringField("Leave empty")  # hidden in template
    submit = SubmitField("Send Message")
