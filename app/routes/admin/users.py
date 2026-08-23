"""Administrative user-management routes."""

import logging
from flask import Blueprint, render_template, redirect, request, url_for, flash
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

from app.services.avatar import delete_profile_image, profile_image_data_uri
from app.core.cache import get_cached_env_settings, get_cached_roles
from app.core.extensions import db, limiter
from app.forms.profile import PROFILE_FIELD_NAMES, apply_form_fields
from app.forms.users import AdminUserForm
from app.core.security import get_client_ip
from app.core.decorators import login_required, admin_required
from app.core.meta import page_metadata
from app.core.decorators import log_view_action
from app.services.trackers import (
    audit_failure_metadata,
    get_admin_quick_stats,
    log_action,
    log_action_isolated,
)
from app.models import User, Role

logger = logging.getLogger(__name__)

users_bp = Blueprint('users', __name__, url_prefix='/admin/users')

ADMIN_EDITABLE_FIELD_NAMES = (
    "username",
    "email",
    *PROFILE_FIELD_NAMES,
    "activated",
    "approved",
    "notes",
    "admin_notes",
)


def _render_edit_user(form, user, meta):
    return render_template(
        "admin/edit_user.html",
        form=form,
        user=user,
        quick_stats=get_admin_quick_stats(),
        **meta,
    )


@users_bp.route("/", methods=["GET"])
@limiter.limit("20 per minute", key_func=get_client_ip)
@log_view_action()
@login_required
@admin_required
def list_users():
    meta = page_metadata.get("admin_users", {})
    env = get_cached_env_settings()
    page = request.args.get("page", 1, type=int)
    paginate = User.query.order_by(User.id.desc()).paginate(page=page, per_page=env.users_per_page)
    quick_stats = get_admin_quick_stats()
    profile_images = {
        user.id: profile_image_data_uri(user.image)
        for user in paginate.items
    }

    return render_template(
        "admin/list_users.html",
        paginate=paginate,
        users=paginate.items,
        profile_images=profile_images,
        quick_stats=quick_stats,
        **meta,
    )


@users_bp.route("/<int:user_id>/profile-image/remove", methods=["POST"])
@limiter.limit("5 per minute", key_func=get_client_ip)
@log_view_action(action="remove_user_profile_image")
@login_required
@admin_required
def remove_profile_image(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        flash("User not found", "error")
        return redirect(url_for("users.list_users"))

    old_filename = user.image
    if not old_filename:
        flash("No profile image is currently set for this user.", "info")
        return redirect(url_for("users.list_users"))

    user.image = None
    log_action(
        action="admin_profile_image_removed",
        user_id=current_user.id,
        target=f"user:{user_id}",
    )

    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.exception("Failed to remove profile image for user %s", user_id)
        log_action_isolated(
            action="admin_profile_image_remove_failed",
            user_id=current_user.id,
            target=f"user:{user_id}",
            extra_data=audit_failure_metadata(exc),
        )
        flash("The profile image could not be removed.", "error")
        return redirect(url_for("users.list_users"))

    delete_profile_image(old_filename)
    flash("Profile image removed.", "success")
    return redirect(url_for("users.list_users"))


@users_bp.route("/<int:user_id>/delete", methods=["POST"])
@limiter.limit("5 per minute", key_func=get_client_ip)
@log_view_action(action="delete_user")
@login_required
@admin_required
def delete_user(user_id):
    if user_id == 1:
        flash("Cannot delete the first admin user.", "error")

        # Always log Admin actions
        log_action_isolated(
            action="delete_user_denied",
            user_id=current_user.id,
            target=f"user:{user_id}",
            extra_data={"reason": "attempt to delete primary admin"}
        )

        return redirect(url_for("users.list_users"))

    user = db.session.get(User, user_id)
    if not user:
        flash("User not found", "error")

        # Always log Admin actions
        log_action_isolated(
            action="delete_user_failed",
            user_id=current_user.id,
            target=f"user:{user_id}",
            extra_data={"reason": "user not found"}
        )

        return redirect(url_for("users.list_users"))

    profile_image_filename = user.image

    try:
        actor_user_id = current_user.id
        audit_user_id = actor_user_id if actor_user_id != user_id else None
        audit_metadata = (
            {"actor_user_id": actor_user_id}
            if audit_user_id is None
            else None
        )
        log_action(
            action="delete_user_success",
            user_id=audit_user_id,
            target=f"user:{user_id}",
            extra_data=audit_metadata,
        )
        db.session.delete(user)
        db.session.commit()
        delete_profile_image(profile_image_filename)
        flash("User successfully deleted.", "success")

    except Exception as e:
        db.session.rollback()
        flash("An error occurred deleting the user.", "error")
        logger.exception(f"Failed to delete user {user_id}: {e}")

        # Always log Admin actions
        log_action_isolated(
            action="delete_user_failed",
            user_id=current_user.id,
            target=f"user:{user_id}",
            extra_data=audit_failure_metadata(e)
        )

    return redirect(url_for("users.list_users"))


@users_bp.route("/<int:user_id>/edit", methods=["GET", "POST"])
@limiter.limit("10 per minute", key_func=get_client_ip)
@log_view_action(action="edit_user")
@login_required
@admin_required
def edit_user(user_id):
    meta = page_metadata.get("register", {})
    user = User.query.get_or_404(user_id)
    form = AdminUserForm(obj=user)

    # Populate role choices dynamically
    all_roles = get_cached_roles()
    form.roles.choices = [(role.id, role.name) for role in all_roles]

    if request.method == "GET":
        # Pre-fill roles with current user roles
        form.roles.data = [role.id for role in user.roles]

    if request.method == "POST":
        if not form.validate():
            # Log validation failures
            logger.warning(f"User update validation failed for user {user_id}: {form.errors}")

            # Always log Admin actions
            log_action_isolated(
                action="edit_user_validation_failed",
                user_id=current_user.id,
                target=f"user:{user_id}",
                extra_data={"errors": form.errors}
            )

        else:
            selected_role_ids = form.roles.data

            admin_role = next((role for role in all_roles if role.name == 'admin'), None)
            if user.id == 1 and admin_role.id not in selected_role_ids:
                flash("You cannot remove the admin role from the primary admin user.", "danger")
                return _render_edit_user(form, user, meta)

            changed_fields = apply_form_fields(
                user,
                form,
                ADMIN_EDITABLE_FIELD_NAMES,
            )

            # Update roles relationship explicitly with Role objects
            selected_roles = Role.query.filter(Role.id.in_(selected_role_ids)).all()
            user.roles = selected_roles

            try:
                log_action(
                    action="edit_user_success",
                    user_id=current_user.id,
                    target=f"user:{user_id}",
                    extra_data={
                        "fields_changed": changed_fields,
                        "roles_updated": [r.name for r in user.roles]
                    }
                )

                db.session.commit()
                flash("User updated successfully.", "success")

                return redirect(url_for('users.list_users'))
            except Exception as e:
                db.session.rollback()
                flash("An error occurred while updating the user.", "error")
                logger.exception(f"Error updating user {user_id}: {e}")

                # Always log Admin actions
                log_action_isolated(
                    action="edit_user_failed",
                    user_id=current_user.id,
                    target=f"user:{user_id}",
                    extra_data=audit_failure_metadata(e)
                )

    # Render form with errors or initial data
    return _render_edit_user(form, user, meta)
