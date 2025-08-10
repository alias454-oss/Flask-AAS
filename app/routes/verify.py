# routes/verify.py
import logging
from flask import Blueprint, request, url_for, redirect, flash
from flask_login import current_user
from datetime import datetime, timezone
from itsdangerous import BadSignature, SignatureExpired

from app.core.extensions import db, limiter
from app.core.security import confirm_token, get_client_ip, is_locked_out, track_lockout_attempts, reset_lockout_attempts
from app.core.decorators import nocache, log_view_action
from app.core.trackers import log_action, audit_activity_enabled
from app.models.user import User

logger = logging.getLogger(__name__)

verify_bp = Blueprint('verify', __name__)

# === Token Management ===
RESET_PW_SALT = "app.tokens.password.reset"
EMAIL_VERIFY_SALT = "app.tokens.email.verify"


# Reset token validation used for email verification
@verify_bp.route("/email/<token>")
@nocache
@limiter.limit("10 per hour", key_func=get_client_ip)
@log_view_action()
def verify_email_token(token):
    ip = get_client_ip()

    try:
        email = confirm_token(token, salt=EMAIL_VERIFY_SALT, expiration=86400)  # 1 day default
        if not email:
            raise ValueError("Invalid Token")
    except (SignatureExpired, BadSignature, ValueError) as e:
        logger.warning(f"Invalid or expired token from IP {ip}: {e}")
        flash("The verification link is invalid or has expired.", "danger")
        return redirect(url_for("login.login"))

    user = User.query.filter_by(email=email).first()
    if not user:
        flash("No user found with that email address.", "danger")
        return redirect(url_for("login.login"))

    if user.is_verified:
        logger.info(f"Email {email} already verified. Attempt from IP: {ip}")
        flash("Your email is already verified. Please log in.", "info")
        return redirect(url_for("login.login"))

    if audit_activity_enabled():
        log_action(
            user_id=user.id,
            action="email_verified",
            target=request.path,
            extra_data={"ip": ip}
        )

    user.activated = True
    user.last_active = datetime.now(timezone.utc)
    user.ip_address = ip
    db.session.commit()

    flash("Your email has been verified. You can now log in.", "success")
    return redirect(url_for("login.login"))


# Reset token validation used for password reset verification
@verify_bp.route("/reset/<token>")
@nocache
@limiter.limit("10 per hour", key_func=get_client_ip)
@log_view_action()
def verify_reset_token(token):
    ip = get_client_ip()

    if current_user.is_authenticated:
        logger.info(f"Authenticated user tried to access reset link. IP: {ip}")
        return redirect(url_for("dashboard.dashboard"))

    try:
        email = confirm_token(token, salt=RESET_PW_SALT, expiration=3600)
        if not email:
            raise ValueError("Invalid Token")
    except (SignatureExpired, BadSignature, ValueError) as e:
        track_lockout_attempts("invalid-token", ip)
        logger.warning(f"Invalid or expired token from IP {ip}: {e}")
        flash("The password reset link is invalid or has expired.", "danger")
        return redirect(url_for("reset.forgot_password"))

    if is_locked_out(email, ip):
        flash("Too many attempts. Try again later.", "danger")
        return redirect(url_for("reset.forgot_password"))

    user = User.query.filter_by(email=email).first()
    if not user:
        logger.warning(f"Token matched email {email} but no user found. IP: {ip}")
        flash("Invalid reset token. Please try again.", "danger")
        return redirect(url_for("login.login"))

    # Only after real success
    if audit_activity_enabled():
        log_action(
            user_id=user.id,
            action="password_reset_token_validated",
            target=request.path,
            extra_data={"ip": ip}
        )

    reset_lockout_attempts(email, ip)

    flash("Token verified. You may now reset your password.", "success")
    return redirect(url_for("reset.reset_password", token=token))
