# routes/verify.py
import logging
from datetime import datetime, timezone

from flask import Blueprint, flash, redirect, request, url_for
from itsdangerous import BadSignature, SignatureExpired
from sqlalchemy.exc import SQLAlchemyError

from app.core.decorators import log_view_action
from app.core.extensions import db, limiter
from app.core.logger import redact_route_values
from app.core.security import confirm_token, get_client_ip, redact_email
from app.core.trackers import audit_activity_enabled, log_action
from app.models.user import User

logger = logging.getLogger(__name__)

verify_bp = Blueprint('verify', __name__)

# === Token Management ===
EMAIL_VERIFY_SALT = "app.tokens.email.verify"


@verify_bp.route("/email/<token>")
@limiter.limit("10 per hour", key_func=get_client_ip)
@log_view_action(redact_params={"token"})
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
        logger.warning(
            "Verification token resolved to a missing account for %s from IP %s",
            redact_email(email),
            ip,
        )
        flash("The verification link is invalid or has expired.", "danger")
        return redirect(url_for("login.login"))

    if user.activated:
        logger.info(
            "Email already verified for %s; repeat attempt from IP %s",
            redact_email(email),
            ip,
        )
        flash("Your email is already verified. Please log in.", "info")
        return redirect(url_for("login.login"))

    try:
        user.activated = True
        user.last_active = datetime.now(timezone.utc)
        user.ip_address = ip

        if audit_activity_enabled():
            log_action(
                user_id=user.id,
                action="email_verified",
                target=redact_route_values(request.path, {"token"}),
                extra_data={"ip": ip},
            )

        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            "Email verification commit failed for user_id=%s from IP %s",
            user.id,
            ip,
        )
        flash(
            "We could not verify your email right now. Please try again.",
            "danger",
        )
        return redirect(url_for("login.login"))

    flash("Your email has been verified. You can now log in.", "success")
    return redirect(url_for("login.login"))
