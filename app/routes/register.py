# routes/register.py
import logging
from datetime import datetime, timezone, timedelta
from flask import Blueprint, render_template, redirect, url_for, flash, abort
from flask_login import current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, BooleanField
from wtforms.validators import DataRequired, Email, Optional, Length
from sqlalchemy.exc import IntegrityError

from app.core.cache import get_cached_env_settings
from app.core.extensions import db, bcrypt, limiter
from app.core.security import generate_random_password, generate_token, normalize_username, normalize_email, get_client_ip, is_locked_out, track_lockout_attempts, reset_lockout_attempts
from app.core.meta import page_metadata
from app.core.decorators import log_view_action
from app.core.trackers import current_route, log_action, log_action_isolated, audit_activity_enabled
from app.core.mailer import send_verification_email,  send_welcome_email
from app.models import User, Role
from .captcha import CaptchaRequired

logger = logging.getLogger(__name__)

register_bp = Blueprint('register', __name__)

# === Token Management ===
EMAIL_VERIFY_SALT = "app.tokens.email.verify"

# Form class for registration
class RegisterForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=50)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[Optional(), Length(min=6)])  # Optional to allow auto-generated
    company_name = StringField('Company Name', validators=[Optional(), Length(max=100)])
    first_name = StringField('First Name', validators=[Optional(), Length(max=50)])
    last_name = StringField('Last Name', validators=[Optional(), Length(max=50)])
    phone = StringField('Phone', validators=[Optional(), Length(max=20)])
    country = StringField('Country Code', validators=[Optional(), Length(max=10)])
    address = StringField('Address', validators=[Optional(), Length(max=150)])
    city = StringField('City', validators=[Optional(), Length(max=50)])
    state = StringField('State', validators=[Optional(), Length(max=50)])
    zip = StringField('Zip Code', validators=[Optional(), Length(max=20)])
    agree = BooleanField('I agree to the terms of service', validators=[DataRequired()])
    captcha = StringField("Enter CAPTCHA", validators=[CaptchaRequired()])
    nobot_check = StringField('Leave empty')  # hidden in template
    submit = SubmitField('Register')

    # Always define captcha at the class level but unbind it if disabled
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        env = get_cached_env_settings()
        if not env.use_captcha:
            # Remove captcha field if CAPTCHA is disabled
            del self.captcha

        if not env.use_user_location:
            # Remove location fields if location is disabled
            del self.country
            del self.address
            del self.city
            del self.state
            del self.zip

@register_bp.route('/register', methods=['GET', 'POST'])
@limiter.limit("5 per hour", key_func=get_client_ip)
@log_view_action()
def register():
    # Block access in single-user mode unless admin
    is_admin = current_user.is_authenticated and current_user.has_role('admin')

    # Check registration is allowed or single site mode
    env = get_cached_env_settings()
    if env and not env.allow_registration:
        # Check for admin access
        if not is_admin:
            abort(404)

    meta = page_metadata.get("register", {})

    form = RegisterForm()
    errors = []
    error_flags = {}

    if form.validate_on_submit():
        ip = get_client_ip()
        email = normalize_email(form.email.data)
        username = normalize_username(form.username.data)

        if form.nobot_check.data:
            logger.warning(f"Honeypot field triggered from IP {ip}")
            track_lockout_attempts("SYSTEM_HONEYPOT", ip)
            if audit_activity_enabled():
                log_action_isolated(
                    user_id=current_user.id if current_user.is_authenticated else None,
                    action="honeypot_triggered",
                    target=current_route(),
                    extra_data={
                        "email": email,
                        "ip": ip,
                        "errors": errors,
                    }
                )
            return redirect(url_for("register.register"))

        if is_locked_out(email, ip):
            flash("Too many attempts. Try again later.", "danger")
            return redirect(url_for("register.register"))

        # Check duplicates
        if User.query.filter(db.or_(User.username == username, User.email == email)).first():
            logger.warning(f"Duplicate registration attempt for user {username} or email {email} from IP {ip}")
            # Not flagging errors in UI so as not to give away details in case of enumeration attempts
            errors.append('Username or email already in use.')
        if not form.agree.data:
            errors.append('You must agree to the Terms of Service.')
            error_flags['agree_error'] = True

        # If there are errors, show them all and redisplay form
        if errors:
            track_lockout_attempts(email, ip)
            if audit_activity_enabled():
                log_action_isolated(
                    user_id=current_user.id if current_user.is_authenticated else None,
                    action="failed_register_attempt",
                    target=current_route(),
                    extra_data={
                        "email": email,
                        "ip": ip,
                        "errors": errors,
                    }
                )
            for error in errors:
                flash(error, 'error')
            return render_template('register.html', title="Register", form=form, error_flags=error_flags, **meta)

        raw_password = form.password.data
        password_was_generated = False

        # Admin Bypass Logic
        if not raw_password:
            if is_admin:
                raw_password = generate_random_password()
                password_was_generated = True
            else:
                form.password.errors.append("Password is required.")
                return render_template("register.html", form=form)

        # 2. Hash the CORRECT password (raw_password)
        hashed_pw = bcrypt.generate_password_hash(raw_password).decode('utf-8')

        # Safely get optional fields — check if attribute exists first
        def get_field_data(field_name):
            field = getattr(form, field_name, None)
            return field.data if field else None

        user = User(
            username=username,
            ip_address=ip,
            email=email,
            hashed_password=hashed_pw,
            company_name=get_field_data('company_name'),
            first_name=get_field_data('first_name'),
            last_name=get_field_data('last_name'),
            phone=get_field_data('phone'),
            country=get_field_data('country'),
            address=get_field_data('address'),
            city=get_field_data('city'),
            state=get_field_data('state'),
            zip=get_field_data('zip'),
            reg_date=datetime.now(timezone.utc),
            last_active=datetime.now(timezone.utc),
            activated=False,
            approved=False
        )

        default_role = None
        if env and env.default_role_id:
            default_role = db.session.get(Role, env.default_role_id)

        if default_role:
            user.roles.append(default_role)

        if is_admin:
            user.approved = True

        try:
            db.session.add(user)

            # FLUSH: Send the INSERT to Postgres, which generates and returns the user.id
            db.session.flush()
            if audit_activity_enabled():
                log_action(
                    user_id=current_user.id if is_admin else user.id,
                    action="admin_create_user" if is_admin else "register",
                    target=current_route(),
                    extra_data={
                        "subject_user_id": user.id, # The SUBJECT of the action
                        "ip": ip,
                        "password_generated": password_was_generated # From your other fix
                    }
                )

            db.session.commit()
            reset_lockout_attempts(email, ip)

            # Use email verification to authorize account
            use_verify_email = env.use_verify_email if env else False
            # PATH A: Admin is creating the user
            if is_admin:
                # Send Welcome with the password (if generated)
                temp_pass = raw_password if password_was_generated else None
                send_welcome_email(user.email, user.username, temp_password=temp_pass)

                flash(f"User {user.username} created successfully.", "success")
                return redirect(url_for('register.register'))

            # PATH B: Public User (Verification Enabled)
            elif use_verify_email:
                token = generate_token(user.email, EMAIL_VERIFY_SALT)
                verify_url = url_for('verify.verify', token=token, _external=True)

                send_verification_email(user.email, user.username, verify_url)

                flash("Account created! Please check your email to verify.", "info")
                return redirect(url_for('login.login'))

            # PATH C: Public User (No Verification)
            else:
                send_welcome_email(user.email, user.username)
                flash("Account created successfully. Please log in.", "success")
                return redirect(url_for('login.login'))

        except IntegrityError as e:
            db.session.rollback()
            logger.exception(f"Registration failed due to DB error {e}")
            flash('A database error occurred during registration.', 'error')
            track_lockout_attempts(email, ip)
    else:
        # If WTForms validation errors, flash them and set flags for each field
        for fieldName, fieldErrors in form.errors.items():
            for errorMsg in fieldErrors:
                flash(errorMsg, 'error')
            # For example, you can set error flags for fields to highlight in template
            error_flags[f'{fieldName}_error'] = True

    return render_template('register.html', form=form, error_flags=error_flags, **meta)
