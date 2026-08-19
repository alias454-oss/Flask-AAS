# routes/login.py
import logging
import time
from datetime import datetime, timezone

from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from flask_login import current_user, login_user, logout_user
from flask_wtf import FlaskForm
from wtforms import StringField, BooleanField, PasswordField, SubmitField
from wtforms.validators import DataRequired
from sqlalchemy.exc import SQLAlchemyError

from app.core.cache import get_cached_env_settings
from app.core.decorators import log_view_action
from app.core.extensions import db, limiter
from app.core.meta import page_metadata
from app.core.password_hashing import (
    password_hash_needs_rehash,
    verify_login_password,
)
from app.core.inactivity import mark_session_activity
from app.core.sessions import close_current_session, create_login_session
from app.core.security import (
    normalize_username,
    get_client_ip,
    is_locked_out,
    track_lockout_attempts,
    reset_lockout_attempts,
)
from app.core.trackers import (
    LOGIN_FAILURE_INVALID_CREDENTIALS,
    LOGIN_FAILURE_LOCKED_OUT,
    LOGIN_FAILURE_REJECTED,
    audit_activity_enabled,
    audit_login_enabled,
    current_route,
    log_action,
    log_login,
)
from app.models.user import User
from .captcha import is_captcha_enabled, CaptchaRequired

logger = logging.getLogger(__name__)

login_bp = Blueprint('login', __name__)


class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember_me = BooleanField('Remember Me')
    captcha = StringField("Enter CAPTCHA", validators=[CaptchaRequired()])
    submit = SubmitField('Login')

    # Always define captcha at the class level but unbind it if disabled
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if not is_captcha_enabled():
            # Remove captcha field if CAPTCHA is disabled
            del self.captcha


def _log_login_result(username, ip, user_agent, referer, success, failure_reason=None):
    if not audit_login_enabled():
        return

    log_login(
        username=username,
        ip=ip,
        user_agent=user_agent,
        referer=referer,
        success=success,
        failure_reason=failure_reason,
    )


@login_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute", key_func=get_client_ip)
@log_view_action()
def login():
    meta = page_metadata.get("login", {})

    form = LoginForm()
    ip = get_client_ip()
    ua = request.headers.get('User-Agent')
    ref = request.referrer

    if not form.validate_on_submit():
        if request.method == 'POST':
            flash('Please correct the highlighted fields.', 'error')
        return render_template('login.html', form=form, **meta)

    username = normalize_username(form.username.data)
    password = form.password.data

    if is_locked_out(username, ip):
        _log_login_result(
            username,
            ip,
            ua,
            ref,
            success=False,
            failure_reason=LOGIN_FAILURE_LOCKED_OUT,
        )
        logger.warning(f"Lockout enforced for {username} from {ip}")
        flash("Too many failed attempts. Try again later.", "danger")
        return render_template('login.html', form=form, **meta)

    user = User.query.filter_by(username=username).first()
    env = get_cached_env_settings()

    # Always perform balanced password-hash work so unknown usernames do not
    # take a shortcut around either supported verification scheme.
    stored_hash = user.hashed_password if user else None
    password_matches = verify_login_password(stored_hash, password)
    credentials_valid = user is not None and password_matches

    if not credentials_valid:
        _log_login_result(
            username,
            ip,
            ua,
            ref,
            success=False,
            failure_reason=LOGIN_FAILURE_INVALID_CREDENTIALS,
        )
        logger.warning(f"Invalid credentials submitted for '{username}' from {ip}")
        flash('Invalid credentials.', 'error')
        track_lockout_attempts(username, ip)
        return render_template('login.html', form=form, **meta)

    failure_reason = user.login_eligibility_failure
    if failure_reason:
        _log_login_result(
            username,
            ip,
            ua,
            ref,
            success=False,
            failure_reason=failure_reason,
        )
        close_current_session()
        logout_user()
        session.clear()
        session['_remember'] = 'clear'
        flash("This account is not currently available for sign-in.", "warning")
        return redirect(url_for('login.login'))

    password_hash_upgraded = False
    if password_hash_needs_rehash(user.hashed_password):
        user.set_password(password)
        password_hash_upgraded = True

    if current_user.is_authenticated:
        close_current_session()
        logout_user()

    session.clear()  # Prevent session fixation before authentication continues.
    session['_remember'] = 'clear'

    # MFA users are not accepted by Flask-Login until the second factor succeeds.
    if env.use_mfa and user.mfa_enabled:
        if password_hash_upgraded:
            try:
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                _log_login_result(
                    username,
                    ip,
                    ua,
                    ref,
                    success=False,
                    failure_reason=LOGIN_FAILURE_REJECTED,
                )
                logger.exception(
                    "Password-hash upgrade failed for user '%s' from %s",
                    username,
                    ip,
                )
                flash('Login could not be completed. Please try again.', 'danger')
                return render_template('login.html', form=form, **meta)

        session['pre_2fa_user_id'] = user.id
        session['pre_2fa_username'] = username
        session['pre_2fa_ip'] = ip
        session['remember_me'] = bool(form.remember_me.data)
        session['pre_2fa_time'] = time.time()
        session['mfa_verified'] = False
        flash("Enter your 2FA code to complete login.", "info")
        return redirect(url_for('mfa.mfa_verify'))

    try:
        create_login_session(
            user,
            remembered=form.remember_me.data,
            ip_address=ip,
            user_agent=ua,
        )
    except SQLAlchemyError:
        db.session.rollback()
        user.clear_session_identity()
        _log_login_result(
            username,
            ip,
            ua,
            ref,
            success=False,
            failure_reason=LOGIN_FAILURE_REJECTED,
        )
        logger.exception(
            "Could not create a login session for user '%s' from %s",
            username,
            ip,
        )
        flash('Login could not be completed. Please try again.', 'danger')
        return render_template('login.html', form=form, **meta)

    accepted = login_user(
        user,
        remember=form.remember_me.data,
        fresh=True,
    )
    if not accepted:
        db.session.rollback()
        user.clear_session_identity()
        _log_login_result(
            username,
            ip,
            ua,
            ref,
            success=False,
            failure_reason=LOGIN_FAILURE_REJECTED,
        )
        logger.warning(f"Flask-Login rejected user '{username}' from {ip}")
        flash('Invalid credentials.', 'error')
        return render_template('login.html', form=form, **meta)

    user.last_active = datetime.now(timezone.utc)
    user.ip_address = ip

    if audit_activity_enabled():
        log_action(
            user_id=user.id,
            action="login",
            target=current_route(),
            extra_data={
                "title": meta.get("title"),
                "user_roles": [role.name for role in user.roles],
                "remembered": form.remember_me.data,
            },
        )

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logout_user()
        session.clear()
        session['_remember'] = 'clear'
        user.clear_session_identity()
        _log_login_result(
            username,
            ip,
            ua,
            ref,
            success=False,
            failure_reason=LOGIN_FAILURE_REJECTED,
        )
        logger.exception(
            "Login commit failed for user '%s' from %s",
            username,
            ip,
        )
        flash('Login could not be completed. Please try again.', 'danger')
        return render_template('login.html', form=form, **meta)

    reset_lockout_attempts(username, ip)

    _log_login_result(
        username,
        ip,
        ua,
        ref,
        success=True,
    )

    session.permanent = form.remember_me.data
    mark_session_activity()
    logger.info(f"User '{username}' logged in successfully from {ip}")

    if user.must_change_password:
        return redirect(url_for('reset.change_password'))

    if user.is_admin:
        return redirect(url_for('admin.admin_home'))
    return redirect(url_for('dashboard.dashboard'))
