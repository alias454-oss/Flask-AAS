# routes/reset.py
import logging
from datetime import datetime, timedelta, timezone

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_fresh, logout_user

from app.core.auth import login_required
from flask_wtf import FlaskForm
from sqlalchemy.exc import SQLAlchemyError
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo

from app.core.decorators import log_view_action
from app.core.extensions import db, limiter
from app.core.logger import redact_route_values
from app.core.mailer import send_password_changed_email, send_password_reset_email
from app.core.meta import page_metadata
from app.core.passwords import password_policy
from app.core.security import (
    get_client_ip,
    is_locked_out,
    normalize_email,
    old_password_match,
    redact_email,
    reset_lockout_attempts,
    track_lockout_attempts,
)
from app.core.trackers import audit_activity_enabled, log_action, log_action_isolated
from app.models import PasswordResetToken, User, UserSession
from app.models.password_reset_token import TOKEN_PURPOSE_RESET, TOKEN_PURPOSE_SETUP

logger = logging.getLogger(__name__)

reset_bp = Blueprint("reset", __name__)

RESET_TOKEN_LIFETIME = timedelta(hours=1)


class ForgotPasswordForm(FlaskForm):
    email = StringField("Enter Your Email", validators=[DataRequired(), Email()])
    submit = SubmitField("Email Password")


class ResetPasswordForm(FlaskForm):
    password = PasswordField("New Password", validators=[DataRequired(), password_policy])
    confirm = PasswordField(
        "Confirm New Password",
        validators=[DataRequired(), EqualTo("password")],
    )
    submit = SubmitField("Reset Password")


class ChangePasswordForm(FlaskForm):
    old_password = PasswordField("Current Password", validators=[DataRequired()])
    password = PasswordField("New Password", validators=[DataRequired(), password_policy])
    confirm = PasswordField(
        "Confirm New Password",
        validators=[DataRequired(), EqualTo("password")],
    )
    submit = SubmitField("Update Password")


def _force_full_login():
    logout_user()
    session.clear()
    session["_remember"] = "clear"


def _notify_password_changed(user, *, ip, source):
    try:
        status = send_password_changed_email(user.email, user.username)
    except Exception:
        logger.exception(
            "Password-change notification failed for user_id=%s after %s",
            user.id,
            source,
        )
        return

    redacted = redact_email(user.email)
    if status == "queued":
        logger.info(
            "Password-change notification queued for %s after %s from IP %s",
            redacted,
            source,
            ip,
        )
    elif status != "disabled":
        logger.error(
            "Password-change notification could not be queued for %s after %s from IP %s",
            redacted,
            source,
            ip,
        )


@reset_bp.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("10 per hour", key_func=get_client_ip)
@log_view_action(redact_params={"token"})
def reset_password(token):
    return _password_token_form(token, purpose=TOKEN_PURPOSE_RESET)


@reset_bp.route("/set-password/<token>", methods=["GET", "POST"])
@limiter.limit("10 per hour", key_func=get_client_ip)
@log_view_action(redact_params={"token"})
def set_password(token):
    return _password_token_form(token, purpose=TOKEN_PURPOSE_SETUP)


def _password_token_form(token, *, purpose):
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.dashboard"))

    is_setup = purpose == TOKEN_PURPOSE_SETUP
    meta = page_metadata.get("reset", {})
    ip = get_client_ip()
    form = ResetPasswordForm()
    if is_setup:
        form.submit.label.text = "Set Password"
    plaintext_token = token or request.form.get("token")

    token_record = PasswordResetToken.find_active(
        plaintext_token,
        purpose=purpose,
    )
    user = token_record.user if token_record is not None else None
    if token_record is None or user is None:
        logger.warning(
            "Invalid, expired, consumed, or revoked %s token from IP %s",
            "password setup" if is_setup else "password reset",
            ip,
        )
        flash(
            "The password setup link is invalid or has expired."
            if is_setup
            else "The password reset link is invalid or has expired.",
            "danger",
        )
        return redirect(
            url_for("login.login" if is_setup else "reset.forgot_password")
        )

    email = user.email
    if is_locked_out(email, ip):
        flash("Too many attempts. Try again later.", "danger")
        return redirect(
            url_for("login.login" if is_setup else "reset.forgot_password")
        )

    if not form.validate_on_submit():
        return render_template(
            "reset.html",
            form=form,
            reset=True,
            password_heading="Set Your Password" if is_setup else None,
            password_legend="Set Password" if is_setup else None,
            token=plaintext_token,
            **meta,
        )

    password = form.password.data
    if old_password_match(user, password):
        flash("New password cannot be the same as your old password.", "danger")
        track_lockout_attempts(email, ip)
        return render_template(
            "reset.html",
            form=form,
            reset=True,
            password_heading="Set Your Password" if is_setup else None,
            password_legend="Set Password" if is_setup else None,
            token=plaintext_token,
            **meta,
        )

    changed_at = datetime.now(timezone.utc)
    consumed_token = PasswordResetToken.consume(
        plaintext_token,
        purpose=purpose,
        now=changed_at,
    )
    if consumed_token is None:
        db.session.rollback()
        logger.warning(
            "%s token was consumed concurrently from IP %s",
            "Password setup" if is_setup else "Password reset",
            ip,
        )
        flash(
            "The password setup link is invalid or has expired."
            if is_setup
            else "The password reset link is invalid or has expired.",
            "danger",
        )
        return redirect(
            url_for("login.login" if is_setup else "reset.forgot_password")
        )

    user.set_password(password)
    user.must_change_password = False
    user.rotate_authentication_version()
    user.updated_at = changed_at
    PasswordResetToken.revoke_for_user(
        user.id,
        revoked_at=changed_at,
        exclude_id=consumed_token.id,
    )
    UserSession.revoke_for_user(user.id, revoked_at=changed_at)

    if audit_activity_enabled():
        log_action(
            user_id=user.id,
            action="password_setup_success" if is_setup else "password_reset_success",
            target=redact_route_values(request.path, {"token"}),
            extra_data={"ip": ip},
        )

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            "%s commit failed for user_id=%s from IP %s",
            "Password setup" if is_setup else "Password reset",
            user.id,
            ip,
        )
        flash(
            "Your password could not be set. Please try again."
            if is_setup
            else "Your password could not be reset. Please try again.",
            "danger",
        )
        return redirect(
            url_for("login.login" if is_setup else "reset.forgot_password")
        )

    reset_lockout_attempts(email, ip)
    _notify_password_changed(
        user,
        ip=ip,
        source="initial password setup" if is_setup else "password reset",
    )
    _force_full_login()
    flash(
        "Password set successfully. Please log in."
        if is_setup
        else "Password reset successfully. Please log in.",
        "success",
    )
    return redirect(url_for("login.login"))


@reset_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per minute", key_func=get_client_ip)
@log_view_action()
def forgot_password():
    meta = page_metadata.get("reset", {})
    form = ForgotPasswordForm()

    if not form.validate_on_submit():
        return render_template("reset.html", form=form, reset=True, **meta)

    email = normalize_email(form.email.data)
    ip = get_client_ip()

    if is_locked_out(email, ip):
        flash("Too many attempts. Try again later.", "danger")
        return redirect(url_for("reset.forgot_password"))

    user = User.query.filter_by(email=email).first()
    if user:
        if audit_activity_enabled():
            log_action_isolated(
                user_id=user.id,
                action="password_reset_requested",
                target=request.path,
                extra_data={"ip": ip},
            )

        try:
            token_record, plaintext_token = PasswordResetToken.issue_for_user(
                user,
                purpose=TOKEN_PURPOSE_RESET,
                lifetime=RESET_TOKEN_LIFETIME,
            )
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception(
                "Password reset token could not be issued for %s from IP %s",
                redact_email(user.email),
                ip,
            )
        else:
            try:
                mail_status = send_password_reset_email(
                    user.email,
                    plaintext_token,
                )
            except Exception:
                mail_status = "failed"
                logger.exception(
                    "Unexpected password reset email error for %s from IP %s",
                    redact_email(user.email),
                    ip,
                )

            redacted = redact_email(user.email)
            if mail_status == "queued":
                logger.info(
                    "Password reset email queued for %s from IP %s",
                    redacted,
                    ip,
                )
            else:
                token_record.revoke()
                try:
                    db.session.commit()
                except SQLAlchemyError:
                    db.session.rollback()
                    logger.exception(
                        "Failed to revoke undelivered password reset token id=%s",
                        token_record.id,
                    )

                if mail_status == "disabled":
                    logger.info(
                        "Password reset email delivery disabled for %s from IP %s",
                        redacted,
                        ip,
                    )
                else:
                    logger.error(
                        "Password reset email could not be queued for %s from IP %s",
                        redacted,
                        ip,
                    )

    track_lockout_attempts(email, ip)
    flash("If your email is registered, you’ll receive a password reset link.", "info")
    return redirect(url_for("login.login"))


@reset_bp.route("/change-password", methods=["GET", "POST"])
@log_view_action()
@login_required
def change_password():
    meta = page_metadata.get("reset", {})
    ip = get_client_ip()
    required_change = bool(current_user.must_change_password)

    if required_change and not login_fresh():
        _force_full_login()
        flash("Please log in again before choosing a new password.", "warning")
        return redirect(url_for("login.login"))

    form = ResetPasswordForm() if required_change else ChangePasswordForm()
    if required_change:
        form.submit.label.text = "Set Password"

    render_kwargs = {
        "form": form,
        "required_change": required_change,
        **meta,
    }
    if required_change:
        render_kwargs.update(
            password_heading="Choose Your Password",
            password_legend="Set Password",
        )

    if not form.validate_on_submit():
        return render_template("reset.html", **render_kwargs)

    old_password = form.old_password.data if not required_change else None
    password = form.password.data

    if not required_change and not current_user.check_password(old_password):
        logger.info(
            "Incorrect current password for user %s from IP %s",
            current_user.id,
            ip,
        )
        flash("Current password is incorrect.", "danger")
        return render_template("reset.html", **render_kwargs)

    if old_password_match(current_user, password):
        flash("New password must be different from your current password.", "danger")
        return render_template("reset.html", **render_kwargs)

    user = current_user._get_current_object()
    changed_at = datetime.now(timezone.utc)
    user.set_password(password)
    user.must_change_password = False
    user.rotate_authentication_version()
    user.last_active = changed_at
    user.ip_address = ip
    PasswordResetToken.revoke_for_user(user.id, revoked_at=changed_at)
    UserSession.revoke_for_user(user.id, revoked_at=changed_at)

    if audit_activity_enabled():
        log_action(
            user_id=user.id,
            action="password_changed",
            target=request.path,
            extra_data={"ip": ip, "required": required_change},
        )

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            "Password change commit failed for user_id=%s from IP %s",
            user.id,
            ip,
        )
        flash("Your password could not be changed. Please try again.", "danger")
        return render_template("reset.html", **render_kwargs)

    _notify_password_changed(user, ip=ip, source="authenticated password change")
    _force_full_login()
    logger.info("Password successfully changed for user %s from IP %s", user.id, ip)
    flash("Your password has been updated. Please log in again.", "success")
    return redirect(url_for("login.login"))
