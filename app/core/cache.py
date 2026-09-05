# app/core/cache.py
import logging
from flask import g, request, session
from flask_login import current_user
from sqlalchemy import event
from sqlalchemy.exc import OperationalError, ProgrammingError
from app.models import EnvSettings, Role

logger = logging.getLogger(__name__)

@event.listens_for(EnvSettings, 'after_insert')
@event.listens_for(EnvSettings, 'after_update')
@event.listens_for(EnvSettings, 'after_delete')
def invalidate_env_settings_cache(mapper, connection, target):
    EnvSettings._cached_instance = None
    logger.debug("EnvSettings cache invalidated")

def get_cached_env_settings():
    if not hasattr(g, '_env_settings'):
        g._env_settings = EnvSettings.get_cached_instance()
    return g._env_settings


def safe_get_cached_env_settings():
    """Read cached Site Settings while tolerating bootstrap schema gaps."""
    try:
        return get_cached_env_settings()
    except (OperationalError, ProgrammingError):
        return None


SENSITIVE_CACHE_PATHS = (
    "/admin",
    "/account",
    "/member",
    "/dashboard",
    "/login",
    "/logout",
    "/register",
    "/change-password",
    "/forgot-password",
    "/reset-password",
    "/set-password",
    "/email",
    "/mfa",
    "/captcha",
    "/internal",
    "/debug",
    "/test",
)


def add_cache_headers(response):
    """Prevent storage of session-bearing and security-sensitive responses."""
    endpoint = request.endpoint or ""
    if endpoint == "static" or endpoint.endswith(".static"):
        return response

    if (
        bool(session)
        or current_user.is_authenticated
        or request.path.startswith(SENSITIVE_CACHE_PATHS)
    ):
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, private"
        )
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@event.listens_for(Role, 'after_insert')
@event.listens_for(Role, 'after_update')
@event.listens_for(Role, 'after_delete')
def invalidate_role_cache(mapper, connection, target):
    Role._cached_instance = None
    logger.debug("Role cache invalidated")

def get_cached_roles():
    if not hasattr(g, '_roles'):
        g._roles = Role.query.all()
    return g._roles