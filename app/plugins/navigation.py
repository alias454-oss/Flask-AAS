# plugins/navigation.py
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from app.core.extensions import db
from app.models.plugin import PluginRegistration
from app.plugins.loader import (
    STATUS_ACTIVE,
    STATUS_NEEDS_CONFIGURATION,
    get_plugin_runtime,
)

logger = logging.getLogger(__name__)

PLUGIN_NAVIGATION_EXTENSION = "flask_aas_plugin_navigation"


@dataclass(frozen=True)
class PluginNavigationItem:
    """One top-level navigation contribution from an application plugin."""

    plugin_id: str
    label: str
    endpoint: str


def _registry(app: Any) -> dict[tuple[str, str], PluginNavigationItem]:
    return app.extensions.setdefault(PLUGIN_NAVIGATION_EXTENSION, {})


def register_plugin_navigation(
    app: Any,
    *,
    plugin_id: str,
    label: str,
    endpoint: str,
) -> PluginNavigationItem:
    """Register one plugin-owned top-level navigation link.

    The contribution is structural startup state. Visibility is resolved from
    current persisted plugin enablement/configuration so a loaded plugin can
    disappear from navigation immediately without changing Flask's route map.
    """

    for field_name, value in (
        ("plugin_id", plugin_id),
        ("label", label),
        ("endpoint", endpoint),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")

    if endpoint not in app.view_functions:
        raise ValueError(
            f"Plugin navigation endpoint {endpoint!r} is not registered on the app"
        )

    item = PluginNavigationItem(
        plugin_id=plugin_id,
        label=label,
        endpoint=endpoint,
    )
    registry = _registry(app)
    key = (plugin_id, endpoint)
    if key in registry:
        raise ValueError(
            f"Plugin navigation entry already registered for {plugin_id!r} "
            f"endpoint {endpoint!r}"
        )
    registry[key] = item
    return item


def get_plugin_navigation(app: Any) -> list[PluginNavigationItem]:
    """Return structurally registered navigation contributions."""

    return list(_registry(app).values())


def visible_plugin_navigation() -> list[PluginNavigationItem]:
    """Return plugin links whose application surface is currently usable."""

    app = current_app._get_current_object()
    runtime = get_plugin_runtime(app)
    items = get_plugin_navigation(app)

    if not runtime.system_enabled or not items:
        return []

    plugin_ids = {item.plugin_id for item in items}
    try:
        registrations = {
            registration.plugin_id: registration
            for registration in PluginRegistration.query.filter(
                PluginRegistration.plugin_id.in_(plugin_ids)
            ).all()
        }
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Failed to read plugin navigation access state")
        return []

    visible = []
    for item in items:
        state = runtime.state_for(item.plugin_id)
        registration = registrations.get(item.plugin_id)
        if (
            state is not None
            and state.status in {STATUS_ACTIVE, STATUS_NEEDS_CONFIGURATION}
            and registration is not None
            and registration.enabled
            and registration.configured
            and runtime.plugin_for_endpoint(item.endpoint) == item.plugin_id
        ):
            visible.append(item)

    return visible
