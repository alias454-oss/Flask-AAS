# routes/login.py
import logging
import time
from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from flask_login import login_user
from flask_wtf import FlaskForm
from wtforms import StringField, BooleanField, PasswordField, SubmitField
from wtforms.validators import DataRequired
from datetime import datetime, timezone

from app.core.cache import get_cached_env_settings
from app.core.extensions import db, limiter
from app.core.security import normalize_username, get_client_ip, is_locked_out, track_lockout_attempts, reset_lockout_attempts
from app.core.meta import page_metadata
from app.core.decorators import log_view_action
from app.core.trackers import current_route, log_action, log_login, audit_login_enabled, audit_activity_enabled
from app.models.user import User, bcrypt
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

@login_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute", key_func=get_client_ip)
@log_view_action()
def login():
    meta = page_metadata.get("login", {})

    form = LoginForm()
    ip = get_client_ip()
    ua = request.headers.get('User-Agent')
    ref = request.referrer

    if form.validate_on_submit():
        username = normalize_username(form.username.data)
        password = form.password.data

        if is_locked_out(username, ip):
            logger.warning(f"Lockout enforced for {username} from {ip}")
            flash("Too many failed attempts. Try again later.", "danger")
            return render_template('login.html', form=form, **meta)

        user = User.query.filter_by(username=username).first()
        env = get_cached_env_settings()

        if not user:
            logger.warning(f"Login attempt for non-existent user '{username}' from {ip}")
            flash('Invalid credentials.', 'error')
            track_lockout_attempts(username, ip)
            return render_template('login.html', form=form, **meta)

        if env.use_verify_email and not user.activated:
            flash("Please verify your email before logging in.", "warning")
            return redirect(url_for("auth.login"))

        success = user and bcrypt.check_password_hash(user.hashed_password, password)

        if audit_login_enabled():
            log_login(
                username=username,
                ip=ip,
                user_agent=ua,
                referer=ref,
                success=success
            )

        if success:
            session.clear()  # Prevent session fixation early

            user.last_active = datetime.now(timezone.utc)
            user.ip_address = ip
            db.session.commit()

            logger.info(f"User '{username}' logged in successfully from {ip}")

            # MFA logic gate
            if env.use_mfa and user.mfa_enabled:
                session.clear()
                session['pre_2fa_user_id'] = user.id
                session['remember_me'] = form.remember_me.data  # carry this forward
                session['pre_2fa_time'] = time.time()  # so we can expire MFA attempts
                session['mfa_verified'] = False  # force MFA before full login
                flash("Enter your 2FA code to complete login.", "info")
                return redirect(url_for('mfa.mfa_verify'))

            if audit_activity_enabled():
                log_action(
                    user_id=user.id,
                    action="login",
                    target=current_route(),
                    extra_data={
                        "title": meta.get("title"),
                        "user_roles": [role.name for role in user.roles],
                        "remembered": form.remember_me.data
                    }
                )

            reset_lockout_attempts(username, ip)

            login_user(user, remember=form.remember_me.data, fresh=True)
            session.permanent = form.remember_me.data

            if user.is_admin:  # Adjust this based on your role logic
                return redirect(url_for('admin.admin_home'))
            return redirect(url_for('dashboard.dashboard'))
        else:
            logger.warning(f"Failed login attempt for user '{username}' from {ip}")
            flash('Invalid credentials.', 'error')
            track_lockout_attempts(username, ip)

    return render_template('login.html', form=form, **meta)
