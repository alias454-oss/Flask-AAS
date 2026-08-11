# routes/register.py
import logging
from datetime import datetime, timedelta, timezone
from flask import Blueprint, current_app, render_template, redirect, url_for, flash, abort
from flask_login import current_user
from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, Optional, Length
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.cache import get_cached_env_settings
from app.core.extensions import db, limiter
from app.core.passwords import generate_random_password, password_policy
from app.core.security import generate_token, normalize_username, normalize_email, get_client_ip, is_locked_out, track_lockout_attempts, reset_lockout_attempts
from app.core.meta import page_metadata
from app.core.decorators import log_view_action
from app.core.trackers import current_route, log_action, log_action_isolated, audit_activity_enabled
from app.core.locations import configure_location_choices
from app.core.mailer import (
    get_mail_configuration_state,
    send_password_setup_email,
    send_verification_email,
    send_welcome_email,
)
from app.models import PasswordResetToken, User, Role
from app.models.password_reset_token import TOKEN_PURPOSE_SETUP
from .captcha import CaptchaRequired

logger = logging.getLogger(__name__)

register_bp = Blueprint('register', __name__)

# === Token Management ===
EMAIL_VERIFY_SALT = "app.tokens.email.verify"
PASSWORD_SETUP_TOKEN_LIFETIME = timedelta(hours=48)

# Form class for registration
class RegisterForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=50)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[password_policy])  # Blank permits admin-issued password setup.
    company_name = StringField('Company Name', validators=[Optional(), Length(max=100)])
    first_name = StringField('First Name', validators=[Optional(), Length(max=50)])
    last_name = StringField('Last Name', validators=[Optional(), Length(max=50)])
    phone = StringField('Phone', validators=[Optional(), Length(max=20)])
    country_code = SelectField('Country', choices=[], validators=[Optional()])
    address = StringField('Address', validators=[Optional(), Length(max=150)])
    city = StringField('City', validators=[Optional(), Length(max=50)])
    zone_code = SelectField('Region / Subdivision', choices=[], validators=[Optional()])
    postal_code = StringField('Postal Code', validators=[Optional(), Length(max=20)])
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
            del self.country_code
            del self.address
            del self.city
            del self.zone_code
            del self.postal_code
        else:
            configure_location_choices(self)

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
            return render_template('register.html', form=form, error_flags=error_flags, **meta)

        raw_password = form.password.data
        password_setup_required = False

        # Admin may leave the password blank and issue a bounded setup capability.
        if not raw_password:
            if is_admin:
                mail_state = get_mail_configuration_state(env)
                if not mail_state.enabled or not mail_state.available:
                    flash(
                        "A password is required when outbound email is unavailable.",
                        "error",
                    )
                    return render_template(
                        "register.html",
                        form=form,
                        error_flags=error_flags,
                        **meta,
                    )

                # hashed_password is non-nullable. This random placeholder is never
                # disclosed and is replaced when the setup capability is consumed.
                raw_password = generate_random_password()
                password_setup_required = True
            else:
                form.password.errors.append("Password is required.")
                return render_template("register.html", form=form)

        if not is_admin and env and env.use_verify_email:
            mail_state = get_mail_configuration_state()
            if not env.use_smtp or not mail_state.available:
                logger.error(
                    "Public registration blocked because required email verification "
                    "has no available outbound transport"
                )
                flash(
                    "Registration is temporarily unavailable because email "
                    "verification cannot be delivered.",
                    "error",
                )
                return render_template(
                    "register.html",
                    form=form,
                    error_flags=error_flags,
                    **meta,
                )

        # Safely get optional fields — check if attribute exists first
        def get_field_data(field_name):
            field = getattr(form, field_name, None)
            return field.data if field else None

        user = User(
            username=username,
            ip_address=ip,
            email=email,
            company_name=get_field_data('company_name'),
            first_name=get_field_data('first_name'),
            last_name=get_field_data('last_name'),
            phone=get_field_data('phone'),
            country_code=get_field_data('country_code'),
            address=get_field_data('address'),
            city=get_field_data('city'),
            zone_code=get_field_data('zone_code'),
            postal_code=get_field_data('postal_code'),
            reg_date=datetime.now(timezone.utc),
            last_active=datetime.now(timezone.utc),
            activated=False,
            approved=False
        )
        user.set_password(raw_password)

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

            setup_token_record = None
            setup_plaintext_token = None
            if password_setup_required:
                setup_token_record, setup_plaintext_token = PasswordResetToken.issue_for_user(
                    user,
                    purpose=TOKEN_PURPOSE_SETUP,
                    lifetime=PASSWORD_SETUP_TOKEN_LIFETIME,
                )

            if audit_activity_enabled():
                log_action(
                    user_id=current_user.id if is_admin else user.id,
                    action="admin_create_user" if is_admin else "register",
                    target=current_route(),
                    extra_data={
                        "subject_user_id": user.id,
                        "ip": ip,
                        "password_setup_required": password_setup_required,
                    }
                )

            db.session.commit()
            reset_lockout_attempts(email, ip)

            # Use email verification to authorize account
            use_verify_email = env.use_verify_email if env else False

            # PATH A: Admin is creating the user
            if is_admin:
                if password_setup_required:
                    mail_status = send_password_setup_email(
                        user.email,
                        user.username,
                        setup_plaintext_token,
                    )
                    if mail_status != "queued":
                        if setup_token_record is not None:
                            setup_token_record.revoke()
                            try:
                                db.session.commit()
                            except SQLAlchemyError:
                                db.session.rollback()
                                logger.exception(
                                    "Failed to revoke undelivered password setup token id=%s",
                                    setup_token_record.id,
                                )
                        logger.warning(
                            "Password setup email dispatch status=%s for user_id=%s",
                            mail_status,
                            user.id,
                        )
                        flash(
                            f"User {user.username} was created, but the password setup "
                            "email could not be queued.",
                            "warning",
                        )
                    else:
                        flash(
                            f"User {user.username} created successfully. "
                            "A password setup link was emailed to the user.",
                            "success",
                        )
                    return redirect(url_for("register.register"))

                mail_status = send_welcome_email(user.email, user.username)
                if mail_status != "queued":
                    logger.warning(
                        "Welcome email dispatch status=%s for user_id=%s",
                        mail_status,
                        user.id,
                    )

                flash(f"User {user.username} created successfully.", "success")
                return redirect(url_for("register.register"))

            # PATH B: Public User (Verification Enabled)
            if use_verify_email:
                token = generate_token(user.email, EMAIL_VERIFY_SALT)
                verify_url = url_for(
                    "verify.verify_email_token",
                    token=token,
                    _external=True,
                    _scheme=current_app.config["PREFERRED_URL_SCHEME"],
                )
                mail_status = send_verification_email(
                    user.email,
                    user.username,
                    verify_url,
                )

                if mail_status == "queued":
                    flash(
                        "Account created. Check your email shortly to verify your account.",
                        "info",
                    )
                elif mail_status == "disabled":
                    logger.warning(
                        "Verification email delivery disabled for user_id=%s",
                        user.id,
                    )
                    flash(
                        "Account created, but email verification is currently unavailable. "
                        "Contact an administrator.",
                        "warning",
                    )
                else:
                    logger.error(
                        "Verification email could not be queued for user_id=%s",
                        user.id,
                    )
                    flash(
                        "Account created, but the verification email could not be queued. "
                        "Contact an administrator.",
                        "warning",
                    )
                return redirect(url_for("login.login"))

            # PATH C: Public User (No Verification)
            mail_status = send_welcome_email(user.email, user.username)
            if mail_status != "queued":
                logger.warning(
                    "Welcome email dispatch status=%s for user_id=%s",
                    mail_status,
                    user.id,
                )
            flash("Account created successfully. Please log in.", "success")
            return redirect(url_for("login.login"))

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
