# routes/mfa/mfa.py
import pyotp
import qrcode
import io
import base64
import time
import logging
from datetime import datetime, timezone

from flask import Blueprint, render_template, current_app, request, flash, redirect, url_for, session
from flask_login import login_required, login_user, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField
from wtforms.validators import DataRequired, Length

from app.core.cache import get_cached_env_settings
from app.core.extensions import db, limiter
from app.core.security import get_client_ip, reset_lockout_attempts
from app.core.meta import page_metadata
from app.core.decorators import nocache, log_view_action
from app.core.trackers import (
    LOGIN_FAILURE_MFA_EXPIRED,
    LOGIN_FAILURE_MFA_FAILED,
    LOGIN_FAILURE_REJECTED,
    audit_activity_enabled,
    audit_login_enabled,
    current_route,
    log_action,
    log_action_isolated,
    log_login,
)
from app.models import User

logger = logging.getLogger(__name__)

mfa_bp = Blueprint('mfa', __name__)


def _pending_login_identity(user=None):
    username = session.get('pre_2fa_username')
    if not username and user is not None:
        username = user.username

    ip = session.get('pre_2fa_ip') or get_client_ip()
    return username, ip


def _log_pending_mfa_login(success, failure_reason=None, user=None):
    username, ip = _pending_login_identity(user)
    if not username or not audit_login_enabled():
        return

    log_login(
        username=username,
        ip=ip,
        user_agent=request.headers.get('User-Agent'),
        referer=request.referrer,
        success=success,
        failure_reason=failure_reason,
    )


def _clear_pending_mfa_state():
    session.pop('pre_2fa_user_id', None)
    session.pop('pre_2fa_username', None)
    session.pop('pre_2fa_ip', None)
    session.pop('pre_2fa_time', None)
    session.pop('mfa_fail_count', None)
    session.pop('remember_me', None)


def generate_otp_secret():
    return pyotp.random_base32()

def generate_qr_image(secret, username, issuer='FlaskApp'):
    otp_uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=username,
        issuer_name=issuer
    )

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(otp_uri)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)

    return base64.b64encode(buf.read()).decode('utf-8')

class MFASetupForm(FlaskForm):
    """
    Used ONLY during setup.
    Strictly enforces 6-digit numeric TOTP.
    """
    code = StringField(
        "Enter the code from your authenticator app:",
        validators=[
            DataRequired(message="Please enter the code."),
            Length(min=6, max=6, message="The code must be 6 digits.")
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
            Length(min=6, max=10, message="The code must be 6 digits.")
        ],
        render_kw={"placeholder": "Code or Recovery Key", "autofocus": True, "autocomplete": "one-time-code"}
    )
    submit = SubmitField("Verify")

class DisableMfaForm(FlaskForm):
    submit = SubmitField("Disable MFA")

@mfa_bp.route('/mfa/setup', methods=['GET', 'POST'])
@nocache
@limiter.limit("10 per minute", key_func=get_client_ip)
@log_view_action()
@login_required
def mfa_setup():
    meta = page_metadata.get("mfa", {})

    ip = get_client_ip()
    user = current_user

    form = MFASetupForm()

    if not user.otp_secret:
        user.otp_secret = generate_otp_secret()
        db.session.commit()
        logger.info(f"Generated new OTP secret for user_id={user.id} ip={ip}")

    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        totp = pyotp.TOTP(user.otp_secret)
        if totp.verify(code, valid_window=1):  # <-- add valid_window=1 here
            user.mfa_enabled = True

            if audit_activity_enabled():
                log_action(
                    user_id=user.id,
                    action="mfa_enabled",
                    target=current_route(),
                    extra_data={
                        "ip": ip,
                        "user_roles": [role.name for role in user.roles]
                    }
                )

            db.session.commit()
            logger.info(f"2FA setup completed successfully for user_id={user.id} ip={ip}")

            flash("2FA successfully enabled.", "success")
            # Optional: Add otp_enabled = True if you use a flag
            return redirect(url_for('dashboard.dashboard'))
        else:
            logger.warning(f"Invalid 2FA setup code attempt for user_id={user.id} ip={ip}")
            flash("Invalid code. Try again.", "danger")

    env = get_cached_env_settings()
    site_name = env.site_name if env.site_name else current_app.config.get('SITE_NAME', 'YourApp')
    qr_base64 = generate_qr_image(user.otp_secret, user.username, site_name)
    return render_template('mfa/setup.html', form=form, qr_base64=qr_base64, **meta)


@mfa_bp.route('/mfa/verify', methods=['GET', 'POST'])
@nocache
@limiter.limit("10 per minute; 50 per 30 minutes", key_func=get_client_ip)
@log_view_action()
def mfa_verify():
    meta = page_metadata.get("mfa", {})
    ip = get_client_ip()

    user_id = session.get('pre_2fa_user_id')
    pre_2fa_time = session.get('pre_2fa_time')

    if not user_id or not pre_2fa_time or time.time() - pre_2fa_time > 300:
        _log_pending_mfa_login(
            success=False,
            failure_reason=LOGIN_FAILURE_MFA_EXPIRED,
        )
        logger.warning(f"MFA verify session expired or invalid: user_id={user_id} ip={ip}")
        session.clear()
        flash("Session expired or invalid. Please log in again.", "warning")
        return redirect(url_for('login.login'))

    user = db.session.get(User, user_id)
    if not user or not user.otp_secret or not user.mfa_enabled or not user.is_active:
        _log_pending_mfa_login(
            success=False,
            failure_reason=LOGIN_FAILURE_REJECTED,
            user=user,
        )
        logger.warning(f"Invalid MFA session or incomplete setup: user_id={user_id} ip={ip}")
        session.clear()
        flash("Invalid 2FA session or setup incomplete. Please log in again.", "danger")
        return redirect(url_for('login.login'))

    form = TwoFactorForm()

    fail_count = session.get('mfa_fail_count', 0)
    if fail_count >= 5:
        _log_pending_mfa_login(
            success=False,
            failure_reason=LOGIN_FAILURE_MFA_FAILED,
            user=user,
        )
        logger.warning(f"Too many MFA failures for user_id={user_id} ip={ip}")
        session.clear()
        flash("Too many invalid attempts. Please log in again.", "danger")
        return redirect(url_for('login.login'))

    if form.validate_on_submit():
        code = form.code.data.strip()
        totp = pyotp.TOTP(user.otp_secret)
        if totp.verify(code, valid_window=1):
            remember = bool(session.get('remember_me', False))
            accepted = login_user(user, remember=remember, fresh=True)
            if not accepted:
                _log_pending_mfa_login(
                    success=False,
                    failure_reason=LOGIN_FAILURE_REJECTED,
                    user=user,
                )
                logger.warning(f"Flask-Login rejected MFA user_id={user.id} ip={ip}")
                session.clear()
                flash("Invalid login state. Please log in again.", "danger")
                return redirect(url_for('login.login'))

            username, login_ip = _pending_login_identity(user)
            user.last_active = datetime.now(timezone.utc)
            user.ip_address = login_ip
            db.session.commit()

            _log_pending_mfa_login(success=True, user=user)
            reset_lockout_attempts(username, login_ip)

            if audit_activity_enabled():
                log_action_isolated(
                    user_id=user.id,
                    action="mfa_verified",
                    target=current_route(),
                    extra_data={"ip": ip},
                )

            session['mfa_verified'] = True
            _clear_pending_mfa_state()

            logger.info(f"MFA verification succeeded for user_id={user.id} ip={ip}")
            flash("2FA verification successful", "success")
            return redirect(url_for('dashboard.dashboard'))

        fail_count += 1
        if fail_count >= 5:
            _log_pending_mfa_login(
                success=False,
                failure_reason=LOGIN_FAILURE_MFA_FAILED,
                user=user,
            )
            logger.warning(f"Too many MFA failures for user_id={user_id} ip={ip}")
            session.clear()
            flash("Too many invalid attempts. Please log in again.", "danger")
            return redirect(url_for('login.login'))

        session['mfa_fail_count'] = fail_count
        logger.warning(f"Invalid MFA code attempt {fail_count} for user_id={user_id} ip={ip}")
        flash("Invalid 2FA code. Please try again.", "danger")

    return render_template('mfa/verify.html', form=form, **meta)


@mfa_bp.route('/mfa/disable', methods=['GET', 'POST'])
@nocache
@limiter.limit("10 per minute", key_func=get_client_ip)
@log_view_action()
@login_required
def mfa_disable():
    form = DisableMfaForm()

    if request.method == 'POST':
        if form.validate_on_submit():
            ip = get_client_ip()
            user = current_user

            user.otp_secret = None
            user.mfa_enabled = False

            if audit_activity_enabled():
                log_action(
                    user_id=user.id,
                    action="mfa_disabled",
                    target=current_route(),
                    extra_data={
                        "ip": ip,
                        "user_roles": [role.name for role in user.roles]
                    }
                )

            db.session.commit()
            logger.info(f"2FA disabled for user_id={user.id} ip={ip}")

            flash("2FA disabled", "info")
            return redirect(url_for('dashboard.dashboard'))
        else:
            flash("Invalid CSRF token. Please try again.", "danger")
            return redirect(url_for('mfa.mfa_disable'))

    # GET request: render the disable form
    return render_template('mfa/disable.html', form=form)
