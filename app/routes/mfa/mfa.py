# routes/mfa/mfa.py
import pyotp
import qrcode
import io
import base64
import hmac
import time
import logging
from datetime import datetime, timezone

from flask import Blueprint, render_template, current_app, request, flash, redirect, url_for, session
from flask_login import confirm_login, login_fresh, login_user, logout_user, current_user

from app.core.auth import login_required
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField
from wtforms.validators import DataRequired, Length
from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError

from app.core.cache import get_cached_env_settings
from app.core.extensions import db, limiter
from app.core.security import get_client_ip, reset_lockout_attempts
from app.core.meta import page_metadata
from app.core.inactivity import mark_session_activity
from app.core.sessions import close_current_session, create_login_session
from app.core.mailer import send_mfa_change_email
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
from app.models import MfaRecoveryCode, User

logger = logging.getLogger(__name__)

mfa_bp = Blueprint('mfa', __name__)

MFA_FRESH_SECONDS = 300
MFA_SETUP_SECONDS = 600
MFA_MAX_ATTEMPTS = 5


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


def _mark_mfa_verified():
    session['mfa_verified'] = True
    session['mfa_verified_at'] = time.time()


def _mfa_is_fresh():
    verified_at = session.get('mfa_verified_at')
    if (
        not isinstance(verified_at, (int, float))
        or not login_fresh()
        or not session.get('mfa_verified', False)
    ):
        return False

    age = time.time() - verified_at
    return 0 <= age <= MFA_FRESH_SECONDS


def _looks_like_recovery_code(code):
    return len(MfaRecoveryCode.normalize(code)) > 6


def _matching_totp_counter(secret, code, valid_window=1):
    if not secret or not code or len(code) != 6 or not code.isdigit():
        return None

    totp = pyotp.TOTP(secret)
    current_counter = int(time.time()) // totp.interval
    for offset in range(-valid_window, valid_window + 1):
        counter = current_counter + offset
        if hmac.compare_digest(totp.at(counter * totp.interval), code):
            return counter
    return None


def _consume_totp(user, code):
    counter = _matching_totp_counter(user.otp_secret, code)
    if counter is None:
        return False

    updated = (
        User.query
        .filter(User.id == user.id)
        .filter(
            or_(
                User.last_totp_counter.is_(None),
                User.last_totp_counter < counter,
            )
        )
        .update(
            {User.last_totp_counter: counter},
            synchronize_session=False,
        )
    )
    return updated == 1


def _verify_mfa_code(user, code):
    if _consume_totp(user, code):
        return 'totp'
    if _looks_like_recovery_code(code) and MfaRecoveryCode.consume(user.id, code):
        return 'recovery'
    return None


def _pending_secret(user):
    created_at = user.pending_otp_created_at
    if created_at is not None and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    if user.pending_otp_secret and created_at is not None:
        age = (now - created_at).total_seconds()
        if 0 <= age <= MFA_SETUP_SECONDS:
            return user.pending_otp_secret

    if not user.mfa_enabled:
        user.otp_secret = None
        user.last_totp_counter = None
    user.pending_otp_secret = generate_otp_secret()
    user.pending_otp_created_at = now
    db.session.commit()
    return user.pending_otp_secret


def _require_fresh_mfa(endpoint, action=None):
    session['mfa_reauth_next'] = endpoint
    session['mfa_reauth_requested_at'] = time.time()
    if action:
        session['mfa_reauth_action'] = action
    else:
        session.pop('mfa_reauth_action', None)
    flash("Verify MFA again before completing this action.", "warning")
    return redirect(url_for('mfa.mfa_reauth'))


def _clear_pending_mfa_state():
    session.pop('pre_2fa_user_id', None)
    session.pop('pre_2fa_username', None)
    session.pop('pre_2fa_ip', None)
    session.pop('pre_2fa_time', None)
    session.pop('mfa_fail_count', None)
    session.pop('remember_me', None)


def _force_full_login(message):
    close_current_session()
    logout_user()
    session.clear()
    session['_remember'] = 'clear'
    flash(message, "danger")
    return redirect(url_for('login.login'))


def _record_failed_attempt(session_key):
    fail_count = session.get(session_key, 0) + 1
    session[session_key] = fail_count
    return fail_count


def _notify_mfa_change(user, action):
    messages = {
        'enabled': "Multi-factor authentication was enabled",
        'replaced': "Your authenticator was replaced",
        'recovery_codes_regenerated': "Your MFA recovery codes were regenerated",
        'disabled': "Multi-factor authentication was disabled",
    }
    try:
        status = send_mfa_change_email(
            user.email,
            user.username,
            messages[action],
        )
    except Exception:
        logger.exception(
            "MFA change notification failed for user_id=%s action=%s",
            user.id,
            action,
        )
        return

    if status == 'failed':
        logger.warning(
            "MFA change notification could not be queued for user_id=%s action=%s",
            user.id,
            action,
        )


def _disable_mfa(user, ip):
    MfaRecoveryCode.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    user.otp_secret = None
    user.pending_otp_secret = None
    user.pending_otp_created_at = None
    user.last_totp_counter = None
    user.mfa_enabled = False

    if audit_activity_enabled():
        log_action(
            user_id=user.id,
            action="mfa_disabled",
            target='mfa.mfa_disable',
            extra_data={
                "ip": ip,
                "user_roles": [role.name for role in user.roles]
            }
        )

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(f"Failed to disable MFA for user_id={user.id} ip={ip}")
        flash("MFA could not be disabled. Please try again.", "danger")
        return redirect(url_for('mfa.mfa_disable'))

    session.pop('mfa_verified', None)
    session.pop('mfa_verified_at', None)
    session.pop('mfa_disable_fail_count', None)
    session.pop('mfa_reauth_action', None)
    session.pop('mfa_reauth_next', None)
    session.pop('mfa_reauth_requested_at', None)
    _notify_mfa_change(user, 'disabled')
    logger.info(f"2FA disabled for user_id={user.id} ip={ip}")

    flash("2FA disabled", "info")
    return redirect(url_for('dashboard.dashboard'))


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

@mfa_bp.route('/mfa/setup', methods=['GET', 'POST'])
@nocache
@limiter.limit("10 per minute", key_func=get_client_ip)
@log_view_action()
@login_required
def mfa_setup():
    meta = page_metadata.get("mfa", {})

    ip = get_client_ip()
    user = current_user

    if not login_fresh():
        logger.warning(f"Non-fresh MFA setup attempt for user_id={user.id} ip={ip}")
        close_current_session()
        logout_user()
        session.clear()
        session['_remember'] = 'clear'
        flash("Please log in again before enabling MFA.", "warning")
        return redirect(url_for('login.login'))

    form = MFASetupForm()

    if user.mfa_enabled:
        flash("MFA is already enabled. Verify MFA again to replace the authenticator.", "warning")
        return redirect(url_for('mfa.mfa_replace'))

    pending_secret = _pending_secret(user)
    if request.method == 'GET':
        logger.info(f"Generated new OTP secret for user_id={user.id} ip={ip}")

    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        counter = _matching_totp_counter(pending_secret, code)
        if counter is not None:
            user.otp_secret = pending_secret
            user.pending_otp_secret = None
            user.pending_otp_created_at = None
            user.last_totp_counter = counter
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

            recovery_codes = MfaRecoveryCode.generate_for_user(user)
            if audit_activity_enabled():
                log_action(
                    user_id=user.id,
                    action="mfa_recovery_codes_generated",
                    target=current_route(),
                    extra_data={"ip": ip},
                )
            db.session.commit()
            _mark_mfa_verified()
            _notify_mfa_change(user, 'enabled')
            logger.info(f"2FA setup completed successfully for user_id={user.id} ip={ip}")

            flash("2FA successfully enabled.", "success")
            recovery_form = RecoveryCodeForm()
            return render_template(
                'mfa/recovery_codes.html',
                form=recovery_form,
                recovery_codes=recovery_codes,
            )

        logger.warning(f"Invalid 2FA setup code attempt for user_id={user.id} ip={ip}")
        flash("Invalid code. Try again.", "danger")

    env = get_cached_env_settings()
    site_name = env.site_name if env.site_name else current_app.config.get('SITE_NAME', 'YourApp')
    qr_base64 = generate_qr_image(pending_secret, user.username, site_name)
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
    if not user or not user.otp_secret or not user.mfa_enabled:
        _log_pending_mfa_login(
            success=False,
            failure_reason=LOGIN_FAILURE_REJECTED,
            user=user,
        )
        logger.warning(f"Invalid MFA session or incomplete setup: user_id={user_id} ip={ip}")
        logout_user()
        session.clear()
        flash("Invalid 2FA session or setup incomplete. Please log in again.", "danger")
        return redirect(url_for('login.login'))

    failure_reason = user.login_eligibility_failure
    if failure_reason:
        _log_pending_mfa_login(
            success=False,
            failure_reason=failure_reason,
            user=user,
        )
        logger.warning(f"MFA login rejected for user_id={user_id} ip={ip}")
        logout_user()
        session.clear()
        flash("This account is not currently available for sign-in.", "warning")
        return redirect(url_for('login.login'))

    form = TwoFactorForm()

    fail_count = session.get('mfa_fail_count', 0)
    if fail_count >= MFA_MAX_ATTEMPTS:
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
        verification_method = _verify_mfa_code(user, code)
        if verification_method:
            db.session.refresh(user)
            failure_reason = user.login_eligibility_failure
            if failure_reason:
                _log_pending_mfa_login(
                    success=False,
                    failure_reason=failure_reason,
                    user=user,
                )
                logger.warning(f"MFA login rejected for user_id={user_id} ip={ip}")
                db.session.rollback()
                logout_user()
                session.clear()
                flash("This account is not currently available for sign-in.", "warning")
                return redirect(url_for('login.login'))

            remember = bool(session.get('remember_me', False))
            username, login_ip = _pending_login_identity(user)
            user.last_active = datetime.now(timezone.utc)
            user.ip_address = login_ip

            try:
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                _log_pending_mfa_login(
                    success=False,
                    failure_reason=LOGIN_FAILURE_REJECTED,
                    user=user,
                )
                logger.exception(f"Failed to finalize MFA login for user_id={user.id} ip={ip}")
                session.clear()
                flash("Login could not be completed. Please try again.", "danger")
                return redirect(url_for('login.login'))

            db.session.refresh(user)
            failure_reason = user.login_eligibility_failure
            if failure_reason:
                _log_pending_mfa_login(
                    success=False,
                    failure_reason=failure_reason,
                    user=user,
                )
                logger.warning(f"MFA login rejected for user_id={user_id} ip={ip}")
                session.clear()
                flash("This account is not currently available for sign-in.", "warning")
                return redirect(url_for('login.login'))

            try:
                create_login_session(
                    user,
                    remembered=remember,
                    ip_address=login_ip,
                    user_agent=request.headers.get('User-Agent'),
                )
            except SQLAlchemyError:
                db.session.rollback()
                user.clear_session_identity()
                _log_pending_mfa_login(
                    success=False,
                    failure_reason=LOGIN_FAILURE_REJECTED,
                    user=user,
                )
                logger.exception(
                    "Could not create an MFA login session for user_id=%s ip=%s",
                    user.id,
                    ip,
                )
                session.clear()
                flash("Login could not be completed. Please try again.", "danger")
                return redirect(url_for('login.login'))

            accepted = login_user(user, remember=remember, fresh=True)
            if not accepted:
                db.session.rollback()
                user.clear_session_identity()
                _log_pending_mfa_login(
                    success=False,
                    failure_reason=LOGIN_FAILURE_REJECTED,
                    user=user,
                )
                logger.warning(f"Flask-Login rejected MFA user_id={user.id} ip={ip}")
                session.clear()
                flash("Invalid login state. Please log in again.", "danger")
                return redirect(url_for('login.login'))

            try:
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                logout_user()
                session.clear()
                session['_remember'] = 'clear'
                user.clear_session_identity()
                _log_pending_mfa_login(
                    success=False,
                    failure_reason=LOGIN_FAILURE_REJECTED,
                    user=user,
                )
                logger.exception(
                    "MFA session commit failed for user_id=%s ip=%s",
                    user.id,
                    ip,
                )
                flash("Login could not be completed. Please try again.", "danger")
                return redirect(url_for('login.login'))

            _log_pending_mfa_login(success=True, user=user)
            reset_lockout_attempts(username, login_ip)

            if audit_activity_enabled():
                log_action_isolated(
                    user_id=user.id,
                    action="mfa_verified",
                    target=current_route(),
                    extra_data={"ip": ip},
                )

            _mark_mfa_verified()
            _clear_pending_mfa_state()
            session.permanent = remember
            mark_session_activity()

            if verification_method == 'recovery' and audit_activity_enabled():
                log_action_isolated(
                    user_id=user.id,
                    action="mfa_recovery_code_used",
                    target=current_route(),
                    extra_data={"ip": ip},
                )

            logger.info(f"MFA verification succeeded for user_id={user.id} ip={ip}")
            flash("2FA verification successful", "success")
            return redirect(url_for('dashboard.dashboard'))

        db.session.rollback()
        if _looks_like_recovery_code(code) and audit_activity_enabled():
            log_action_isolated(
                user_id=user.id,
                action="mfa_recovery_code_failed",
                target=current_route(),
                extra_data={"ip": ip},
            )

        fail_count += 1
        if fail_count >= MFA_MAX_ATTEMPTS:
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

    return render_template(
        'mfa/verify.html',
        form=form,
        show_recovery_hint=fail_count >= 2,
        **meta,
    )


@mfa_bp.route('/mfa/reauth', methods=['GET', 'POST'])
@nocache
@limiter.limit("10 per minute", key_func=get_client_ip)
@log_view_action()
@login_required
def mfa_reauth():
    if not current_user.mfa_enabled or not current_user.otp_secret:
        session.pop('mfa_reauth_next', None)
        session.pop('mfa_reauth_action', None)
        session.pop('mfa_reauth_requested_at', None)
        flash("MFA is not enabled for this account.", "warning")
        return redirect(url_for('dashboard.dashboard'))

    fail_count = session.get('mfa_reauth_fail_count', 0)
    if fail_count >= MFA_MAX_ATTEMPTS:
        logger.warning(
            f"Too many MFA reauthentication failures for user_id={current_user.id} "
            f"ip={get_client_ip()}"
        )
        return _force_full_login("Too many invalid attempts. Please log in again.")

    form = TwoFactorForm()
    if form.validate_on_submit():
        code = form.code.data.strip()
        verification_method = _verify_mfa_code(current_user, code)
        if verification_method:
            try:
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                logger.exception(
                    f"Failed to finalize MFA reauthentication for "
                    f"user_id={current_user.id} ip={get_client_ip()}"
                )
                return _force_full_login("MFA verification could not be completed. Please log in again.")

            confirm_login()
            _mark_mfa_verified()
            session.pop('mfa_reauth_fail_count', None)

            if audit_activity_enabled():
                log_action_isolated(
                    user_id=current_user.id,
                    action="mfa_reauthenticated",
                    target=current_route(),
                    extra_data={"ip": get_client_ip()},
                )
                if verification_method == 'recovery':
                    log_action_isolated(
                        user_id=current_user.id,
                        action="mfa_recovery_code_used",
                        target=current_route(),
                        extra_data={"ip": get_client_ip()},
                    )

            action = session.pop('mfa_reauth_action', None)
            endpoint = session.pop('mfa_reauth_next', None) or 'dashboard.dashboard'
            requested_at = session.pop('mfa_reauth_requested_at', None)
            if requested_at is not None:
                if not isinstance(requested_at, (int, float)):
                    flash("The requested MFA action expired. Please start again.", "warning")
                    return redirect(url_for('dashboard.dashboard'))
                request_age = time.time() - requested_at
                if not 0 <= request_age <= MFA_FRESH_SECONDS:
                    flash("The requested MFA action expired. Please start again.", "warning")
                    return redirect(url_for('dashboard.dashboard'))
            elif action:
                flash("The requested MFA action expired. Please start again.", "warning")
                return redirect(url_for('dashboard.dashboard'))

            if action == 'disable':
                return _disable_mfa(current_user, get_client_ip())

            flash("MFA verification successful.", "success")
            return redirect(url_for(endpoint))

        db.session.rollback()
        if _looks_like_recovery_code(code) and audit_activity_enabled():
            log_action_isolated(
                user_id=current_user.id,
                action="mfa_recovery_code_failed",
                target=current_route(),
                extra_data={"ip": get_client_ip()},
            )

        fail_count = _record_failed_attempt('mfa_reauth_fail_count')
        logger.warning(
            f"Invalid MFA reauthentication attempt {fail_count} for "
            f"user_id={current_user.id} ip={get_client_ip()}"
        )
        if fail_count >= MFA_MAX_ATTEMPTS:
            return _force_full_login("Too many invalid attempts. Please log in again.")
        flash("Invalid authentication code. Please try again.", "danger")

    return render_template('mfa/reauth.html', form=form)


@mfa_bp.route('/mfa/replace', methods=['GET', 'POST'])
@nocache
@limiter.limit("10 per minute", key_func=get_client_ip)
@log_view_action()
@login_required
def mfa_replace():
    if not current_user.mfa_enabled:
        return redirect(url_for('mfa.mfa_setup'))
    if not _mfa_is_fresh():
        return _require_fresh_mfa('mfa.mfa_replace')

    form = MFASetupForm()
    pending_secret = _pending_secret(current_user)

    if form.validate_on_submit():
        counter = _matching_totp_counter(pending_secret, form.code.data.strip())
        if counter is not None:
            current_user.otp_secret = pending_secret
            current_user.pending_otp_secret = None
            current_user.pending_otp_created_at = None
            current_user.last_totp_counter = counter
            recovery_codes = MfaRecoveryCode.generate_for_user(current_user)

            if audit_activity_enabled():
                log_action(
                    user_id=current_user.id,
                    action="mfa_replaced",
                    target=current_route(),
                    extra_data={"ip": get_client_ip()},
                )
                log_action(
                    user_id=current_user.id,
                    action="mfa_recovery_codes_regenerated",
                    target=current_route(),
                    extra_data={"ip": get_client_ip()},
                )

            db.session.commit()
            _mark_mfa_verified()
            _notify_mfa_change(current_user, 'replaced')
            flash("Authenticator replaced successfully.", "success")
            recovery_form = RecoveryCodeForm()
            return render_template(
                'mfa/recovery_codes.html',
                form=recovery_form,
                recovery_codes=recovery_codes,
            )

        flash("Invalid code. Try again.", "danger")

    env = get_cached_env_settings()
    site_name = env.site_name if env.site_name else current_app.config.get('SITE_NAME', 'YourApp')
    qr_base64 = generate_qr_image(pending_secret, current_user.username, site_name)
    return render_template(
        'mfa/setup.html',
        form=form,
        qr_base64=qr_base64,
        form_action=url_for('mfa.mfa_replace'),
        heading="Replace Two-Factor Authentication",
        legend="Replace MFA Token",
    )


@mfa_bp.route('/mfa/recovery-codes', methods=['GET', 'POST'])
@nocache
@limiter.limit("10 per minute", key_func=get_client_ip)
@log_view_action()
@login_required
def mfa_recovery_codes():
    if not current_user.mfa_enabled:
        flash("MFA must be enabled before recovery codes can be generated.", "warning")
        return redirect(url_for('mfa.mfa_setup'))

    form = RecoveryCodeForm()
    if form.validate_on_submit():
        if not _mfa_is_fresh():
            return _require_fresh_mfa('mfa.mfa_recovery_codes')

        recovery_codes = MfaRecoveryCode.generate_for_user(current_user)
        if audit_activity_enabled():
            log_action(
                user_id=current_user.id,
                action="mfa_recovery_codes_regenerated",
                target=current_route(),
                extra_data={"ip": get_client_ip()},
            )
        db.session.commit()
        _notify_mfa_change(current_user, 'recovery_codes_regenerated')
        flash("New recovery codes generated. Previous codes are no longer valid.", "success")
        return render_template(
            'mfa/recovery_codes.html',
            form=form,
            recovery_codes=recovery_codes,
        )

    recovery_codes = None
    return render_template(
        'mfa/recovery_codes.html',
        form=form,
        recovery_codes=recovery_codes,
    )


@mfa_bp.route('/mfa/disable', methods=['GET', 'POST'])
@nocache
@limiter.limit("10 per minute", key_func=get_client_ip)
@log_view_action()
@login_required
def mfa_disable():
    form = DisableMfaForm()
    mfa_fresh = _mfa_is_fresh()

    if request.method == 'POST':
        if not current_user.mfa_enabled or not current_user.otp_secret:
            flash("MFA is not enabled for this account.", "warning")
            return redirect(url_for('dashboard.dashboard'))

        if not mfa_fresh:
            return _require_fresh_mfa('mfa.mfa_disable', action='disable')

        if not form.validate_on_submit():
            flash("Invalid request. Please try again.", "danger")
            return redirect(url_for('mfa.mfa_disable'))

        ip = get_client_ip()
        verification_method = _verify_mfa_code(current_user, form.code.data.strip())
        if not verification_method:
            db.session.rollback()
            if _looks_like_recovery_code(form.code.data) and audit_activity_enabled():
                log_action_isolated(
                    user_id=current_user.id,
                    action="mfa_recovery_code_failed",
                    target=current_route(),
                    extra_data={"ip": ip},
                )

            fail_count = _record_failed_attempt('mfa_disable_fail_count')
            logger.warning(
                f"Invalid MFA disable code attempt {fail_count} for "
                f"user_id={current_user.id} ip={ip}"
            )
            if fail_count >= MFA_MAX_ATTEMPTS:
                return _force_full_login("Too many invalid attempts. Please log in again.")

            flash("Invalid authentication code. MFA was not disabled.", "danger")
            return render_template(
                'mfa/disable.html',
                form=form,
                mfa_fresh=True,
            )

        return _disable_mfa(current_user, ip)

    return render_template(
        'mfa/disable.html',
        form=form,
        mfa_fresh=mfa_fresh,
    )
