"""Startup loader and runtime state for Flask-AAS application plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
import importlib
import logging
from typing import Any

from flask import abort, current_app, request
from sqlalchemy.exc import SQLAlchemyError

from app.core.extensions import db
from app.models.env_settings import EnvSettings
from app.models.plugin import PluginRegistration
from app.plugins.interface import (
    ApplicationPlugin,
    PluginCompatibilityError,
    validate_plugin_contract,
)
from app.plugins.registry import refresh_configuration

logger = logging.getLogger(__name__)

PLUGIN_RUNTIME_EXTENSION = "flask_aas_plugins"

STATUS_DISABLED = "DISABLED"
STATUS_NEEDS_CONFIGURATION = "NEEDS_CONFIGURATION"
STATUS_INCOMPATIBLE = "INCOMPATIBLE"
STATUS_ACTIVE = "ACTIVE"
STATUS_ERROR = "ERROR"


@dataclass(frozen=True)
class PluginRuntimeState:
    """Host-visible state for one registered plugin in this process."""

    plugin_id: str
    status: str
    reason: str | None = None
    name: str | None = None
    version: str | None = None
    api_version: int | None = None


@dataclass
class PluginRuntime:
    """Plugin-system state captured during application startup."""

    system_enabled: bool = False
    plugins: dict[str, PluginRuntimeState] = field(default_factory=dict)
    endpoints: dict[str, str] = field(default_factory=dict)

    def state_for(self, plugin_id: str) -> PluginRuntimeState | None:
        return self.plugins.get(plugin_id)

    def plugin_for_endpoint(self, endpoint: str | None) -> str | None:
        if endpoint is None:
            return None
        return self.endpoints.get(endpoint)


def resolve_plugin(import_path: str) -> ApplicationPlugin:
    """Import a plugin object from a persisted ``package.module:attribute`` path."""

    if not isinstance(import_path, str):
        raise ValueError("Plugin import path must be a string")

    module_name, separator, attribute_name = import_path.strip().partition(":")
    if (
        not separator
        or not module_name
        or not attribute_name
        or ":" in attribute_name
    ):
        raise ValueError(
            "Plugin import path must use the form 'package.module:attribute'"
        )

    module = importlib.import_module(module_name)
    return getattr(module, attribute_name)


def get_plugin_runtime(app: Any) -> PluginRuntime:
    """Return the runtime snapshot for the current Flask process."""

    runtime = app.extensions.get(PLUGIN_RUNTIME_EXTENSION)
    if isinstance(runtime, PluginRuntime):
        return runtime

    runtime = PluginRuntime()
    app.extensions[PLUGIN_RUNTIME_EXTENSION] = runtime
    return runtime


def _record_plugin_endpoints(
    app: Any,
    runtime: PluginRuntime,
    plugin_id: str,
    endpoints_before: set[str],
) -> None:
    """Associate endpoints added by one plugin with its host registration."""

    for endpoint in set(app.view_functions) - endpoints_before:
        runtime.endpoints[endpoint] = plugin_id


def enforce_plugin_access() -> None:
    """Fail closed for plugin routes that are not currently usable.

    Structural Flask routes are installed only at startup. Persisted plugin
    activation and configuration state can change later, so plugin requests are
    gated against current database state. This lets disabling take effect
    immediately and lets an already-loaded plugin become usable after pending
    configuration is completed, without mutating Flask's route map.

    The global plugin-system switch remains a startup boundary: changing it in
    Site Settings still requires a restart, matching the loader contract.
    """

    runtime = get_plugin_runtime(current_app)
    plugin_id = runtime.plugin_for_endpoint(request.endpoint)
    if plugin_id is None:
        return

    state = runtime.state_for(plugin_id)
    if (
        not runtime.system_enabled
        or state is None
        or state.status not in {STATUS_ACTIVE, STATUS_NEEDS_CONFIGURATION}
    ):
        abort(404)

    try:
        registration = PluginRegistration.query.filter_by(plugin_id=plugin_id).first()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Failed to read access state for plugin %s", plugin_id)
        abort(404)

    if (
        registration is None
        or not registration.enabled
        or not registration.configured
    ):
        abort(404)


def _read_plugin_system_enabled() -> bool:
    """Read the persisted global feature toggle, failing closed during bootstrap."""

    try:
        env = EnvSettings.query.first()
    except SQLAlchemyError:
        logger.info("Plugin settings unavailable; plugin loader remains disabled")
        db.session.rollback()
        return False

    if env is None:
        logger.info("Application plugin loader disabled; site settings are unavailable")
        return False

    if not env.enable_plugins:
        logger.info("Application plugin loader disabled by site settings")
        return False

    return True


def _runtime_state(
    registration: PluginRegistration,
    status: str,
    *,
    plugin: ApplicationPlugin | None = None,
    reason: str | None = None,
) -> PluginRuntimeState:
    return PluginRuntimeState(
        plugin_id=registration.plugin_id,
        status=status,
        reason=reason,
        name=getattr(plugin, "name", None) if plugin is not None else None,
        version=getattr(plugin, "version", None) if plugin is not None else None,
        api_version=getattr(plugin, "api_version", None) if plugin is not None else None,
    )


def initialize_plugins(app: Any) -> PluginRuntime:
    """Load enabled registered plugins at the application startup boundary.

    The global feature toggle and registry both live in the database. Fresh
    database initialization and migrations therefore fail closed: if either
    table is not ready, Flask-AAS continues without application plugins.

    Plugin failures are isolated per registration. An incompatible, broken, or
    unconfigured optional plugin must not prevent the Flask-AAS core or another
    plugin from starting.
    """

    runtime = PluginRuntime(system_enabled=False)
    app.extensions[PLUGIN_RUNTIME_EXTENSION] = runtime

    if not _read_plugin_system_enabled():
        return runtime

    runtime.system_enabled = True

    try:
        registrations = PluginRegistration.query.order_by(
            PluginRegistration.plugin_id
        ).all()
    except SQLAlchemyError:
        logger.info("Plugin registry unavailable; no application plugins loaded")
        db.session.rollback()
        return runtime

    registration_count = len(registrations)
    logger.info(
        "Application plugin loader enabled; %d plugin%s registered",
        registration_count,
        "" if registration_count == 1 else "s",
    )

    configuration_changed = False

    for registration in registrations:
        if not registration.enabled:
            runtime.plugins[registration.plugin_id] = _runtime_state(
                registration,
                STATUS_DISABLED,
            )
            logger.info(
                "Plugin %s disabled; runtime loading skipped",
                registration.plugin_id,
            )
            continue

        plugin: ApplicationPlugin | None = None
        logger.info("Loading application plugin %s", registration.plugin_id)

        try:
            plugin = resolve_plugin(registration.import_path)
            validate_plugin_contract(plugin)
            if plugin.plugin_id != registration.plugin_id:
                raise ValueError(
                    f"Registered plugin ID {registration.plugin_id!r} does not "
                    f"match imported plugin ID {plugin.plugin_id!r}"
                )
        except PluginCompatibilityError as exc:
            runtime.plugins[registration.plugin_id] = _runtime_state(
                registration,
                STATUS_INCOMPATIBLE,
                plugin=plugin,
                reason=str(exc),
            )
            logger.warning(
                "Plugin %s is incompatible: %s",
                registration.plugin_id,
                exc,
            )
            continue
        except Exception as exc:
            runtime.plugins[registration.plugin_id] = _runtime_state(
                registration,
                STATUS_ERROR,
                plugin=plugin,
                reason="Plugin import or contract validation failed. Check application logs.",
            )
            logger.exception(
                "Failed to import or validate plugin %s",
                registration.plugin_id,
            )
            continue

        try:
            configuration = refresh_configuration(registration, plugin)
            configuration_changed = True
        except Exception as exc:
            registration.configured = False
            configuration_changed = True
            runtime.plugins[registration.plugin_id] = _runtime_state(
                registration,
                STATUS_ERROR,
                plugin=plugin,
                reason="Plugin configuration validation failed. Check application logs.",
            )
            logger.exception(
                "Plugin %s configuration validation failed",
                registration.plugin_id,
            )
            continue

        endpoints_before = set(app.view_functions)
        try:
            # Structural registration is intentionally independent of current
            # configuration viability. The request guard keeps these routes
            # unavailable until the persisted plugin state is both enabled and
            # configured, without attempting to mutate Flask's route map live.
            plugin.register(app)
        except Exception as exc:
            _record_plugin_endpoints(
                app, runtime, registration.plugin_id, endpoints_before
            )
            runtime.plugins[registration.plugin_id] = _runtime_state(
                registration,
                STATUS_ERROR,
                plugin=plugin,
                reason="Plugin runtime registration failed. Check application logs.",
            )
            logger.exception(
                "Plugin %s failed during runtime registration",
                registration.plugin_id,
            )
            continue

        _record_plugin_endpoints(
            app, runtime, registration.plugin_id, endpoints_before
        )

        if not configuration.configured:
            runtime.plugins[registration.plugin_id] = _runtime_state(
                registration,
                STATUS_NEEDS_CONFIGURATION,
                plugin=plugin,
                reason=configuration.reason,
            )
            logger.info(
                "Loaded application plugin %s version=%s api=%s with access gated pending configuration",
                registration.plugin_id,
                plugin.version,
                plugin.api_version,
            )
            continue

        runtime.plugins[registration.plugin_id] = _runtime_state(
            registration,
            STATUS_ACTIVE,
            plugin=plugin,
        )
        logger.info(
            "Activated application plugin %s version=%s api=%s",
            registration.plugin_id,
            plugin.version,
            plugin.api_version,
        )

    if configuration_changed:
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to persist plugin configuration status")

    status_counts = {
        STATUS_ACTIVE: 0,
        STATUS_DISABLED: 0,
        STATUS_NEEDS_CONFIGURATION: 0,
        STATUS_INCOMPATIBLE: 0,
        STATUS_ERROR: 0,
    }
    for state in runtime.plugins.values():
        if state.status in status_counts:
            status_counts[state.status] += 1

    logger.info(
        "Application plugin startup complete: active=%d disabled=%d "
        "pending_config=%d incompatible=%d errors=%d",
        status_counts[STATUS_ACTIVE],
        status_counts[STATUS_DISABLED],
        status_counts[STATUS_NEEDS_CONFIGURATION],
        status_counts[STATUS_INCOMPATIBLE],
        status_counts[STATUS_ERROR],
    )

    return runtime
