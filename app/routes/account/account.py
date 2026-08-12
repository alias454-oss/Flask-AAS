# routes/account/account.py
import logging
from datetime import datetime, timezone

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.core.auth import login_required
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired
from sqlalchemy.exc import SQLAlchemyError
from wtforms import SelectField, StringField, SubmitField
from wtforms.validators import Length, Optional

from app.core.avatar import (
    ProfileImageError,
    delete_profile_image,
    max_upload_request_bytes,
    profile_image_data_uri,
    store_profile_image,
)
from app.core.cache import get_cached_env_settings
from app.core.decorators import log_view_action
from app.core.extensions import db, limiter
from app.core.locations import configure_location_choices
from app.core.meta import page_metadata
from app.core.security import get_client_ip
from app.core.trackers import (
    audit_activity_enabled,
    current_route,
    log_action,
)
from app.models import UserSession

logger = logging.getLogger(__name__)

account_bp = Blueprint('account', __name__)

PROFILE_FIELD_NAMES = (
    'company_name',
    'first_name',
    'last_name',
    'phone',
    'alt_phone',
    'fax',
    'country_code',
    'address',
    'city',
    'zone_code',
    'postal_code',
)

LOCATION_FIELD_NAMES = (
    'country_code',
    'address',
    'city',
    'zone_code',
    'postal_code',
)


def normalize_optional_text(value):
    if value is None:
        return None

    normalized = (
        str(value)
        .replace('\x00', '')
        .replace('\r', ' ')
        .replace('\n', ' ')
        .strip()
    )
    return normalized or None


class ProfileImageForm(FlaskForm):
    image = FileField(
        'Profile Image',
        validators=[FileRequired(message='Select an image to upload.')],
    )


class RemoveProfileImageForm(FlaskForm):
    pass


class ProfileForm(FlaskForm):
    company_name = StringField(
        'Company Name',
        validators=[Optional(), Length(max=255)],
        filters=[normalize_optional_text],
    )
    first_name = StringField(
        'First Name',
        validators=[Optional(), Length(max=100)],
        filters=[normalize_optional_text],
    )
    last_name = StringField(
        'Last Name',
        validators=[Optional(), Length(max=100)],
        filters=[normalize_optional_text],
    )
    phone = StringField(
        'Phone',
        validators=[Optional(), Length(max=50)],
        filters=[normalize_optional_text],
    )
    alt_phone = StringField(
        'Alternate Phone',
        validators=[Optional(), Length(max=50)],
        filters=[normalize_optional_text],
    )
    fax = StringField(
        'Fax',
        validators=[Optional(), Length(max=50)],
        filters=[normalize_optional_text],
    )
    country_code = SelectField(
        'Country',
        choices=[],
        validators=[Optional()],
    )
    address = StringField(
        'Address',
        validators=[Optional(), Length(max=255)],
        filters=[normalize_optional_text],
    )
    city = StringField(
        'City',
        validators=[Optional(), Length(max=100)],
        filters=[normalize_optional_text],
    )
    zone_code = SelectField(
        'Region / Subdivision',
        choices=[],
        validators=[Optional()],
    )
    postal_code = StringField(
        'Postal Code',
        validators=[Optional(), Length(max=20)],
        filters=[normalize_optional_text],
    )
    submit = SubmitField('Save Profile')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        env = get_cached_env_settings()
        if not env or not env.use_user_location:
            for field_name in LOCATION_FIELD_NAMES:
                del self[field_name]
        else:
            configure_location_choices(self)


@account_bp.route('/account', methods=['GET', 'POST'])
@limiter.limit("10 per minute", exempt_when=lambda: not current_user.is_authenticated)
@log_view_action()
@login_required
def account():
    meta = page_metadata.get("account", {})
    form = ProfileForm(obj=current_user)
    image_form = ProfileImageForm()
    remove_image_form = RemoveProfileImageForm()
    profile_image = profile_image_data_uri(current_user.image)
    active_sessions = UserSession.active_for_user(current_user.id)
    current_session_id = current_user.session_record_id
    other_sessions = [
        user_session
        for user_session in active_sessions
        if user_session.id != current_session_id
    ]
    previous_login_at = UserSession.previous_login_at(
        current_user.id,
        current_session_id,
    )

    if form.validate_on_submit():
        changed_fields = []

        for field_name in PROFILE_FIELD_NAMES:
            if field_name not in form or field_name not in request.form:
                continue

            field = form[field_name]
            new_value = field.data
            if getattr(current_user, field_name) == new_value:
                continue

            setattr(current_user, field_name, new_value)
            changed_fields.append(field_name)

        if not changed_fields:
            flash("No profile changes were detected.", "info")
            return redirect(url_for('account.account'))

        if audit_activity_enabled():
            log_action(
                user_id=current_user.id,
                action="profile_updated",
                target=current_route(),
                extra_data={"changed_fields": changed_fields},
            )

        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception(
                "Profile update commit failed for user_id=%s",
                current_user.id,
            )
            flash("Your profile could not be updated. Please try again.", "danger")
            return render_template(
                "account/account.html",
                form=form,
                image_form=image_form,
                remove_image_form=remove_image_form,
                profile_image=profile_image,
                active_sessions=active_sessions,
                current_session_id=current_session_id,
                other_sessions=other_sessions,
                previous_login_at=previous_login_at,
                **meta,
            )

        flash("Your profile has been updated.", "success")
        return redirect(url_for('account.account'))

    return render_template(
        "account/account.html",
        form=form,
        image_form=image_form,
        remove_image_form=remove_image_form,
        profile_image=profile_image,
        active_sessions=active_sessions,
        current_session_id=current_session_id,
        other_sessions=other_sessions,
        previous_login_at=previous_login_at,
        **meta,
    )


@account_bp.route('/account/profile-image', methods=['POST'])
@limiter.limit("5 per minute", exempt_when=lambda: not current_user.is_authenticated)
@login_required
def upload_profile_image():
    request.max_content_length = max_upload_request_bytes()
    form = ProfileImageForm()
    if not form.validate_on_submit():
        message = next(
            (error for errors in form.errors.values() for error in errors),
            "Select an image to upload.",
        )
        flash(message, "danger")
        return redirect(url_for('account.account'))

    old_filename = current_user.image
    try:
        new_filename = store_profile_image(form.image.data)
    except ProfileImageError as exc:
        flash(str(exc), "danger")
        return redirect(url_for('account.account'))
    except (OSError, RuntimeError):
        logger.exception(
            "Profile image storage failed for user_id=%s ip=%s",
            current_user.id,
            get_client_ip(),
        )
        flash("Your profile image could not be stored. Please try again.", "danger")
        return redirect(url_for('account.account'))

    current_user.image = new_filename
    if audit_activity_enabled():
        log_action(
            user_id=current_user.id,
            action="profile_image_updated",
            target=current_route(),
            extra_data={"replaced_existing": bool(old_filename)},
        )

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        delete_profile_image(new_filename)
        logger.exception(
            "Profile image update commit failed for user_id=%s",
            current_user.id,
        )
        flash("Your profile image could not be updated. Please try again.", "danger")
        return redirect(url_for('account.account'))

    if old_filename and old_filename != new_filename:
        delete_profile_image(old_filename)

    flash(
        "Your profile image has been replaced."
        if old_filename
        else "Your profile image has been uploaded.",
        "success",
    )
    return redirect(url_for('account.account'))


@account_bp.route('/account/profile-image/remove', methods=['POST'])
@limiter.limit("5 per minute", exempt_when=lambda: not current_user.is_authenticated)
@login_required
def remove_profile_image():
    form = RemoveProfileImageForm()
    if not form.validate_on_submit():
        flash("The profile image removal request was invalid.", "danger")
        return redirect(url_for('account.account'))

    old_filename = current_user.image
    if not old_filename:
        flash("No profile image is currently set.", "info")
        return redirect(url_for('account.account'))

    current_user.image = None
    if audit_activity_enabled():
        log_action(
            user_id=current_user.id,
            action="profile_image_removed",
            target=current_route(),
        )

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            "Profile image removal commit failed for user_id=%s",
            current_user.id,
        )
        flash("Your profile image could not be removed. Please try again.", "danger")
        return redirect(url_for('account.account'))

    delete_profile_image(old_filename)
    flash("Your profile image has been removed.", "success")
    return redirect(url_for('account.account'))


@account_bp.route('/account/sessions/<int:session_id>/revoke', methods=['POST'])
@limiter.limit("10 per minute", exempt_when=lambda: not current_user.is_authenticated)
@login_required
def revoke_session(session_id):
    current_session_id = current_user.session_record_id
    if current_session_id is None:
        logger.warning(
            'Authenticated user_id=%s has no current session record',
            current_user.id,
        )
        flash('Your current session could not be identified. Please log in again.', 'danger')
        return redirect(url_for('login.login'))

    if session_id == current_session_id:
        flash('Use Log Out to end your current session.', 'warning')
        return redirect(url_for('account.account'))

    user_session = UserSession.query.filter(
        UserSession.id == session_id,
        UserSession.user_id == current_user.id,
        UserSession.revoked_at.is_(None),
        UserSession.ended_at.is_(None),
    ).first()
    if user_session is None:
        flash('That session is no longer active.', 'info')
        return redirect(url_for('account.account'))

    user_session.revoked_at = datetime.now(timezone.utc)
    if audit_activity_enabled():
        log_action(
            user_id=current_user.id,
            action='session_revoked',
            target=current_route(),
            extra_data={'session_id': user_session.id},
        )

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            'Session revocation failed for user_id=%s session_id=%s ip=%s',
            current_user.id,
            session_id,
            get_client_ip(),
        )
        flash('The session could not be revoked. Please try again.', 'danger')
        return redirect(url_for('account.account'))

    flash('The selected session has been revoked.', 'success')
    return redirect(url_for('account.account'))


@account_bp.route('/account/sessions/revoke-others', methods=['POST'])
@limiter.limit('5 per minute', exempt_when=lambda: not current_user.is_authenticated)
@login_required
def revoke_other_sessions():
    current_session_id = current_user.session_record_id
    if current_session_id is None:
        logger.warning(
            'Authenticated user_id=%s has no current session record',
            current_user.id,
        )
        flash('Your current session could not be identified. Please log in again.', 'danger')
        return redirect(url_for('login.login'))

    revoked_at = datetime.now(timezone.utc)
    revoked_session_ids = UserSession.revoke_for_user(
        current_user.id,
        revoked_at=revoked_at,
        exclude_id=current_session_id,
    )
    if not revoked_session_ids:
        flash('No other active sessions were found.', 'info')
        return redirect(url_for('account.account'))

    if audit_activity_enabled():
        log_action(
            user_id=current_user.id,
            action='other_sessions_revoked',
            target=current_route(),
            extra_data={
                'session_count': len(revoked_session_ids),
                'session_ids': revoked_session_ids,
            },
        )

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            'Bulk session revocation failed for user_id=%s ip=%s',
            current_user.id,
            get_client_ip(),
        )
        flash('Other sessions could not be revoked. Please try again.', 'danger')
        return redirect(url_for('account.account'))

    flash('All other active sessions have been revoked.', 'success')
    return redirect(url_for('account.account'))
