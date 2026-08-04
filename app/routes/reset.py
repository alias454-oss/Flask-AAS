# routes/reset.py
import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length
from datetime import datetime, timezone
from itsdangerous import BadSignature, SignatureExpired

from app.core.extensions import db, limiter
from app.core.security import generate_token, confirm_token, old_password_match,  normalize_email, redact_email, get_client_ip, is_locked_out, track_lockout_attempts, reset_lockout_attempts
from app.core.logger import redact_route_values
from app.core.meta import page_metadata
from app.core.decorators import nocache, log_view_action
from app.core.trackers import log_action, log_action_isolated, audit_activity_enabled
from app.core.mailer import send_password_reset_email
from app.models import User

logger = logging.getLogger(__name__)

reset_bp = Blueprint("reset", __name__)

# === Token Management ===
RESET_PW_SALT = "app.tokens.password.reset"

# === Forms ===
class ForgotPasswordForm(FlaskForm):
    email = StringField("Enter Your Email", validators=[DataRequired(), Email()])
    submit = SubmitField("Email Password")

class ResetPasswordForm(FlaskForm):
    password = PasswordField("New Password", validators=[DataRequired(), Length(min=8)])
    confirm = PasswordField("Confirm New Password", validators=[DataRequired(), EqualTo("password")])
    submit = SubmitField("Reset Password")

class ChangePasswordForm(FlaskForm):
    old_password = PasswordField("Current Password", validators=[DataRequired()])
    password = PasswordField("New Password", validators=[DataRequired(), Length(min=8)])
    confirm = PasswordField("Confirm New Password", validators=[DataRequired(), EqualTo("password")])
    submit = SubmitField("Update Password")


# === Routes ===
# Reset token validation used for email verification
@reset_bp.route("/reset-password/<token>", methods=["GET", "POST"])
@nocache
@limiter.limit("10 per hour", key_func=get_client_ip)
@log_view_action(redact_params={"token"})
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.dashboard"))

    meta = page_metadata.get("reset", {})
    ip = get_client_ip()

    form = ResetPasswordForm()
    token = token or request.form.get("token")

    try:
        email = confirm_token(token, salt=RESET_PW_SALT, expiration=3600)
        if not email:
            raise ValueError("Invalid Token")
    except (SignatureExpired, BadSignature, ValueError) as e:
        logger.warning(f"Invalid or expired token from IP {ip}: {e}")
        flash("The password reset link is invalid or has expired.", "danger")
        return redirect(url_for("reset.forgot_password"))

    if is_locked_out(email, ip):
        flash("Too many attempts. Try again later.", "danger")
        return redirect(url_for("reset.forgot_password"))

    if form.validate_on_submit():
        password = form.password.data # new password
        confirm = form.confirm.data   # confirm new password

        if not password or not confirm:
            flash("Please fill out all fields.", "danger")
            return render_template("reset.html", reset=True, token=token, **meta)

        if password != confirm:
            flash("Passwords do not match.", "danger")
            return render_template("reset.html", reset=True, token=token, **meta)

        user = User.query.filter_by(email=email).first()
        if not user:
            track_lockout_attempts(email, ip)
            logger.warning(f"Password reset attempt for unknown user {redact_email(email)} from IP {ip}")
            flash("There was a problem resetting your password. Please try again.", "danger")
            return redirect(url_for("login.login"))

        if old_password_match(user, password):
            flash("New password cannot be the same as your old password.", "danger")
            track_lockout_attempts(email, ip)
            return render_template("reset.html", reset=True, token=token, **meta)

        # Only after real success
        user.set_password(password)  # Ensure this hashes the password!
        user.updated_at = datetime.now(timezone.utc)
        if audit_activity_enabled():
            log_action(
                user_id=user.id,
                action="password_reset_success",
                target=redact_route_values(request.path, {"token"}),
                extra_data={"ip": ip}
            )

        db.session.commit()

        reset_lockout_attempts(email, ip)
        flash("Password reset successfully. Please log in.", "success")
        return redirect(url_for("login.login"))

    return render_template("reset.html", reset=True, token=token, **meta)


# Forgot password route is used for non-logged in users
@reset_bp.route("/forgot-password", methods=["GET", "POST"])
@nocache
@limiter.limit("5 per minute", key_func=get_client_ip)
@log_view_action()
def forgot_password():
    meta = page_metadata.get("reset", {})
    form = ForgotPasswordForm()

    if form.validate_on_submit():
        # add username to reduce enumeration potential
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
                    extra_data={"ip": ip}
                )

            try:
                logger.info(f"Password reset email sent to {redact_email(user.email)} from IP {ip}")
                token = generate_token(user.email, RESET_PW_SALT)
                send_password_reset_email(user.email, token)
            except Exception as e:
                logger.error(f"Failed to send password reset email to {redact_email(user.email)}: {e}", exc_info=True)
                # Optional: flash user-friendly message or log to monitoring tool

        # In *both* success and failure cases:
        track_lockout_attempts(email, ip)

        flash("If your email is registered, you’ll receive a password reset link.", "info")
        return redirect(url_for("login.login"))

    return render_template("reset.html", form=form, reset=True, **meta)


# Change password route is used for logged-in users
@reset_bp.route("/change-password", methods=["GET", "POST"])
@nocache
@log_view_action()
@login_required
def change_password():
    meta = page_metadata.get("reset", {})
    ip = get_client_ip()

    form = ChangePasswordForm()
    if form.validate_on_submit():
        old_password = form.old_password.data # old password
        password = form.password.data         # new password
        confirm = form.confirm.data           # confirm new password

        if not password or not confirm:
            flash("Please fill out all fields.", "danger")
            return render_template("reset.html", form=form, **meta)

        if password != confirm:
            flash("Passwords do not match.", "danger")
            return render_template("reset.html", form=form, **meta)

        if not current_user.check_password(old_password):
            logger.info(f"Incorrect current password for user {current_user.id} from IP {ip}")
            flash("Current password is incorrect.", "danger")
        else:
            if old_password_match(current_user, password):
                flash("New password must be different from your current password.", "danger")
                return render_template("reset.html", form=form, **meta)

            current_user.set_password(password)
            current_user.last_active = datetime.now(timezone.utc)
            current_user.ip_address = ip
            if audit_activity_enabled():
                log_action(
                    user_id=current_user.id,
                    action="password_changed",
                    target=request.path,
                    extra_data={"ip": ip}
                )

            db.session.commit()

            logger.info(f"Password successfully changed for user {current_user.id} from IP {ip}")
            flash("Your password has been updated.", "success")
            return redirect(url_for("dashboard.dashboard"))
    return render_template("reset.html", form=form, **meta)