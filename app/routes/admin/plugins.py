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
from app.plugins.interface import validate_plugin_contract
from app.plugins.loader import (
    STATUS_ACTIVE,
    STATUS_DISABLED,
    STATUS_NEEDS_CONFIGURATION,
    STATUS_NEEDS_MIGRATION,
    get_plugin_runtime,
    resolve_plugin,
)
from app.plugins.migrations import PluginMigrationManager
from app.plugins.registry import disable_plugin, enable_plugin
from app.plugins.reload import AppConfigReloadUnavailable, reload_app_config

logger = logging.getLogger(__name__)

plugins_bp = Blueprint("plugins", __name__, url_prefix="/admin/plugins")


@dataclass(frozen=True)
class PluginAdminRow:
    registration: PluginRegistration
    runtime_status: str
    runtime_reason: str | None
    runtime_name: str | None
    runtime_version: str | None
    access_status: str
    can_upgrade_schema: bool
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

        access_capable_runtime = (
            state is not None
            and state.status in {STATUS_ACTIVE, STATUS_NEEDS_CONFIGURATION}
        )
        loaded_at_startup = state is not None and state.status != STATUS_DISABLED
        registration_restart_required = (
            registration.enabled != loaded_at_startup
            or (
                registration.enabled
                and state is not None
                and (
                    state.status == STATUS_NEEDS_MIGRATION
                    or (
                        state.status == STATUS_NEEDS_CONFIGURATION
                        and registration.configured
                    )
                    or (
                        state.status == STATUS_ACTIVE
                        and not registration.configured
                    )
                )
            )
        )

        if not runtime.system_enabled:
            access_status = "Unavailable"
        elif state is not None and state.status == STATUS_NEEDS_MIGRATION:
            access_status = "Needs migration"
        elif state is not None and state.status == STATUS_NEEDS_CONFIGURATION:
            access_status = "Needs configuration"
        elif not access_capable_runtime:
            access_status = "Unavailable"
        elif not registration.enabled:
            access_status = "Disabled"
        elif not registration.configured:
            access_status = "Needs configuration"
        else:
            access_status = "Available"

        rows.append(
            PluginAdminRow(
                registration=registration,
                runtime_status=runtime_status,
                runtime_reason=runtime_reason,
                runtime_name=runtime_name,
                runtime_version=runtime_version,
                access_status=access_status,
                can_upgrade_schema=(
                    registration.enabled
                    and state is not None
                    and state.status == STATUS_NEEDS_MIGRATION
                ),
                restart_required=(
                    global_restart_required or registration_restart_required
                ),
            )
        )

    return rows


def _registered_plugin(registration):
    plugin = resolve_plugin(registration.import_path)
    return plugin


def _app_reload_required(env, runtime, rows):
    plugin_system_requested = bool(env and env.enable_plugins)
    return (
        plugin_system_requested != runtime.system_enabled
        or (
            plugin_system_requested
            and any(row.restart_required for row in rows)
        )
    )


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
    rows = _admin_rows(registrations, env, runtime)

    return render_template(
        "admin/plugins.html",
        rows=rows,
        plugin_runtime=runtime,
        plugin_system_requested=bool(env and env.enable_plugins),
        app_reload_required=_app_reload_required(env, runtime, rows),
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
        logger.info(
            "Admin user=%s enabled plugin=%s configured=%s app_config_reload_required=True",
            current_user.username,
            registration.plugin_id,
            configuration.configured,
        )
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
            f"Plugin {registration.plugin_id} enabled. Finish selecting applications, "
            "then use Reload App Config once to activate the requested runtime state.",
            "success",
        )
    else:
        reason = configuration.reason or "Required configuration is incomplete."
        flash(
            f"Plugin {registration.plugin_id} enabled but is not ready: {reason}",
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
        logger.info(
            "Admin user=%s disabled plugin=%s configured=%s "
            "secrets_cleared=True app_config_reload_required=True",
            current_user.username,
            registration.plugin_id,
            configuration.configured,
        )
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
        f"Plugin {registration.plugin_id} disabled; application access is blocked "
        "immediately and plugin-managed secrets were cleared. Use Reload App Config "
        "once after finishing application changes to unload its runtime surfaces.",
        "success",
    )
    return redirect(url_for("plugins.list_plugins"))


@plugins_bp.route("/<int:registration_id>/upgrade-schema", methods=["POST"])
@limiter.limit("3 per minute", key_func=get_client_ip)
@login_required
@admin_required
def upgrade_schema(registration_id):
    registration = db.session.get(PluginRegistration, registration_id)
    if registration is None:
        return "Plugin registration not found", 404

    if not registration.enabled:
        flash(
            f"Plugin {registration.plugin_id} must be enabled before its schema can be upgraded.",
            "warning",
        )
        return redirect(url_for("plugins.list_plugins"))

    previous_revision = None
    target_revision = None
    try:
        plugin = _registered_plugin(registration)
        validate_plugin_contract(plugin)
        if plugin.plugin_id != registration.plugin_id:
            raise ValueError(
                f"Registered plugin ID {registration.plugin_id!r} does not match "
                f"loaded plugin ID {plugin.plugin_id!r}"
            )

        manifest = getattr(plugin, "manifest", None)
        if manifest is None or manifest.migrations is None:
            flash(
                f"Plugin {registration.plugin_id} does not declare database migrations.",
                "warning",
            )
            return redirect(url_for("plugins.list_plugins"))

        manager = PluginMigrationManager(manifest)
        previous_revision = manager.current_revision()
        target_revision = manager.head_revision()
        if target_revision is None:
            raise RuntimeError(
                f"Plugin {registration.plugin_id!r} has no migration head"
            )

        if previous_revision == target_revision:
            flash(
                f"Plugin {registration.plugin_id} database schema is already current "
                f"at revision {target_revision}.",
                "warning",
            )
            return redirect(url_for("plugins.list_plugins"))

        resulting_revision = manager.upgrade("head")
    except Exception as exc:
        db.session.rollback()
        logger.exception(
            "Admin user=%s failed to upgrade plugin=%s schema",
            current_user.username,
            registration.plugin_id,
        )
        try:
            log_action(
                action="upgrade_plugin_schema",
                user_id=current_user.id,
                target=f"/admin/plugins/{registration.id}",
                extra_data={
                    "plugin_id": registration.plugin_id,
                    "outcome": "failed",
                    "previous_revision": previous_revision,
                    "target_revision": target_revision,
                    "error_type": type(exc).__name__,
                    "ip": get_client_ip(),
                    "user_agent": request.headers.get("User-Agent"),
                },
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception(
                "Failed to persist plugin schema-upgrade failure audit for plugin=%s",
                registration.plugin_id,
            )
        flash(
            f"Plugin {registration.plugin_id} database schema upgrade failed. "
            "Check the application logs.",
            "error",
        )
        return redirect(url_for("plugins.list_plugins"))

    registration.configured = False
    try:
        log_action(
            action="upgrade_plugin_schema",
            user_id=current_user.id,
            target=f"/admin/plugins/{registration.id}",
            extra_data={
                "plugin_id": registration.plugin_id,
                "outcome": "success",
                "previous_revision": previous_revision,
                "target_revision": target_revision,
                "resulting_revision": resulting_revision,
                "ip": get_client_ip(),
                "user_agent": request.headers.get("User-Agent"),
            },
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception(
            "Plugin %s schema upgraded successfully but audit/configuration-state persistence failed",
            registration.plugin_id,
        )

    logger.info(
        "Admin user=%s upgraded plugin=%s schema from=%s to=%s app_config_reload_required=True",
        current_user.username,
        registration.plugin_id,
        previous_revision or "<base>",
        resulting_revision or target_revision,
    )
    flash(
        f"Plugin {registration.plugin_id} database schema upgraded from "
        f"{previous_revision or '<base>'} to {resulting_revision or target_revision}. "
        "Use Reload App Config to revalidate configuration and apply the runtime state.",
        "success",
    )
    return redirect(url_for("plugins.list_plugins"))


@plugins_bp.route("/reload", methods=["POST"])
@limiter.limit("3 per minute", key_func=get_client_ip)
@login_required
@admin_required
def reload_config():
    env = EnvSettings.get_cached_instance()
    runtime = get_plugin_runtime(current_app)
    registrations = PluginRegistration.query.order_by(
        PluginRegistration.plugin_id
    ).all()
    rows = _admin_rows(registrations, env, runtime)
    if not _app_reload_required(env, runtime, rows):
        flash(
            "No application configuration changes are waiting to be applied.",
            "warning",
        )
        return redirect(url_for("plugins.list_plugins"))

    try:
        log_action(
            action="reload_app_config",
            user_id=current_user.id,
            target="/admin/plugins/reload",
            extra_data={
                "plugin_system_enabled": bool(env and env.enable_plugins),
                "enabled_plugins": [
                    registration.plugin_id
                    for registration in registrations
                    if registration.enabled
                ],
                "ip": get_client_ip(),
                "user_agent": request.headers.get("User-Agent"),
            },
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception(
            "Failed to persist app config reload audit for admin user=%s",
            current_user.username,
        )
        flash(
            "App config reload was not requested because audit logging failed.",
            "error",
        )
        return redirect(url_for("plugins.list_plugins"))

    try:
        reload_app_config()
    except AppConfigReloadUnavailable as exc:
        logger.warning(
            "Admin user=%s could not reload app config: %s",
            current_user.username,
            exc,
        )
        flash(str(exc), "error")
        return redirect(url_for("plugins.list_plugins"))

    logger.info(
        "Admin user=%s requested app config reload via Gunicorn SIGHUP",
        current_user.username,
    )
    flash(
        "App config reload requested.",
        "success",
    )
    refresh_url = url_for("plugins.list_plugins")
    response = current_app.make_response(
        render_template(
            "admin/plugin_reload.html",
            refresh_url=refresh_url,
        )
    )
    # Do not immediately redirect into the old worker after SIGHUP. Give Gunicorn
    # a short handoff window, then let the browser request the page again.
    response.headers["Refresh"] = f"2; url={refresh_url}"
    response.headers["Cache-Control"] = "no-store"
    return response
