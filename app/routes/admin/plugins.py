"""Administration routes for registered Flask-AAS application plugins."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.core.auth import admin_required, login_required
from app.core.decorators import log_view_action
from app.core.extensions import db, limiter
from app.core.meta import page_metadata
from app.core.security import get_client_ip
from app.core.trackers import get_admin_quick_stats, log_action
from app.models import EnvSettings, PluginRegistration
from app.plugins.loader import get_plugin_runtime, resolve_plugin
from app.plugins.registry import disable_plugin, enable_plugin

logger = logging.getLogger(__name__)

plugins_bp = Blueprint("plugins", __name__, url_prefix="/admin/plugins")


@dataclass(frozen=True)
class PluginAdminRow:
    registration: PluginRegistration
    runtime_status: str
    runtime_reason: str | None
    runtime_name: str | None
    runtime_version: str | None
    restart_required: bool


def _admin_rows(registrations, env, runtime):
    rows = []
    global_restart_required = bool(env and env.enable_plugins) != runtime.system_enabled

    for registration in registrations:
        state = runtime.state_for(registration.plugin_id)
        if state is None:
            runtime_status = "NOT_LOADED"
            runtime_reason = None
            runtime_name = None
            runtime_version = None
        else:
            runtime_status = state.status
            runtime_reason = state.reason
            runtime_name = state.name
            runtime_version = state.version

        loaded_enabled = state is not None and state.status != "DISABLED"
        registration_restart_required = registration.enabled != loaded_enabled

        rows.append(
            PluginAdminRow(
                registration=registration,
                runtime_status=runtime_status,
                runtime_reason=runtime_reason,
                runtime_name=runtime_name,
                runtime_version=runtime_version,
                restart_required=(
                    global_restart_required or registration_restart_required
                ),
            )
        )

    return rows


def _registered_plugin(registration):
    plugin = resolve_plugin(registration.import_path)
    return plugin


@plugins_bp.route("/", methods=["GET"])
@limiter.limit("20 per minute", key_func=get_client_ip)
@log_view_action()
@login_required
@admin_required
def list_plugins():
    meta = page_metadata.get("admin_plugins", {})
    env = EnvSettings.get_cached_instance()
    runtime = get_plugin_runtime(current_app)
    registrations = PluginRegistration.query.order_by(
        PluginRegistration.plugin_id
    ).all()

    return render_template(
        "admin/plugins.html",
        rows=_admin_rows(registrations, env, runtime),
        plugin_runtime=runtime,
        plugin_system_requested=bool(env and env.enable_plugins),
        quick_stats=get_admin_quick_stats(),
        **meta,
    )


@plugins_bp.route("/<int:registration_id>/enable", methods=["POST"])
@limiter.limit("10 per minute", key_func=get_client_ip)
@login_required
@admin_required
def enable(registration_id):
    registration = db.session.get(PluginRegistration, registration_id)
    if registration is None:
        return "Plugin registration not found", 404

    if registration.enabled:
        flash(f"Plugin {registration.plugin_id} is already enabled.", "warning")
        return redirect(url_for("plugins.list_plugins"))

    try:
        plugin = _registered_plugin(registration)
        configuration = enable_plugin(registration, plugin)
        log_action(
            action="enable_plugin",
            user_id=current_user.id,
            target=f"/admin/plugins/{registration.id}",
            extra_data={
                "plugin_id": registration.plugin_id,
                "configured": configuration.configured,
                "ip": get_client_ip(),
                "user_agent": request.headers.get("User-Agent"),
            },
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Failed to enable plugin %s", registration.plugin_id)
        flash(
            f"Plugin {registration.plugin_id} could not be enabled. Check the logs.",
            "error",
        )
        return redirect(url_for("plugins.list_plugins"))

    if configuration.configured:
        flash(
            f"Plugin {registration.plugin_id} enabled. Restart Flask-AAS to activate it.",
            "success",
        )
    else:
        reason = configuration.reason or "Required configuration is incomplete."
        flash(
            f"Plugin {registration.plugin_id} enabled but needs configuration: {reason}",
            "warning",
        )

    return redirect(url_for("plugins.list_plugins"))


@plugins_bp.route("/<int:registration_id>/disable", methods=["POST"])
@limiter.limit("10 per minute", key_func=get_client_ip)
@login_required
@admin_required
def disable(registration_id):
    registration = db.session.get(PluginRegistration, registration_id)
    if registration is None:
        return "Plugin registration not found", 404

    if not registration.enabled:
        flash(f"Plugin {registration.plugin_id} is already disabled.", "warning")
        return redirect(url_for("plugins.list_plugins"))

    try:
        plugin = _registered_plugin(registration)
        configuration = disable_plugin(registration, plugin)
        log_action(
            action="disable_plugin",
            user_id=current_user.id,
            target=f"/admin/plugins/{registration.id}",
            extra_data={
                "plugin_id": registration.plugin_id,
                "configured_after_cleanup": configuration.configured,
                "ip": get_client_ip(),
                "user_agent": request.headers.get("User-Agent"),
            },
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Failed to disable plugin %s", registration.plugin_id)
        flash(
            f"Plugin {registration.plugin_id} could not be disabled. "
            "Managed-secret cleanup did not complete.",
            "error",
        )
        return redirect(url_for("plugins.list_plugins"))

    flash(
        f"Plugin {registration.plugin_id} disabled and plugin-managed secrets cleared. "
        "Restart Flask-AAS to unload its runtime surfaces.",
        "success",
    )
    return redirect(url_for("plugins.list_plugins"))
