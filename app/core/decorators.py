# app/core/decorators.py
"""Route access-control and request-audit decorators."""

from functools import wraps

from flask import flash, make_response, redirect, request, session, url_for
from flask_login import current_user

from app.core.cache import safe_get_cached_env_settings
from app.core.security import extract_request_metadata
from app.services.trackers import audit_activity_enabled, current_route, log_action_isolated


REQUIRED_PASSWORD_CHANGE_EXEMPT_ENDPOINTS = {
    "favicon.favicon",
    "logout.logout",
    "reset.change_password",
    "static",
}

MFA_EXEMPT_ENDPOINTS = {
    "favicon.favicon",
    "robots.robots",
    "login.login",
    "logout.logout",
    "mfa.mfa_verify",
    "mfa.mfa_setup",
    "mfa.mfa_disable",
    "mfa.mfa_reauth",
    "mfa.mfa_replace",
    "mfa.mfa_recovery_codes",
    "register.register",
    "reset.forgot_password",
    "reset.reset_password",
    "static",
}


def enforce_mfa():
    """Require configured MFA completion before ordinary route handling."""
    if request.endpoint in MFA_EXEMPT_ENDPOINTS:
        return None

    if current_user.is_authenticated:
        env = safe_get_cached_env_settings()
        if env.use_mfa and current_user.mfa_enabled:
            if not session.get("mfa_verified", False):
                return redirect(url_for("mfa.mfa_verify"))
    return None


def enforce_required_password_change():
    """Confine authenticated temporary-credential sessions to password setup."""
    if not current_user.is_authenticated:
        return None

    if not getattr(current_user, "must_change_password", False):
        return None

    if request.endpoint in REQUIRED_PASSWORD_CHANGE_EXEMPT_ENDPOINTS:
        return None

    return redirect(url_for("reset.change_password"), code=303)


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("login.login", next=request.url))
        return f(*args, **kwargs)

    # Preserve the route's authentication requirement as explicit metadata.
    # Consumers such as the sitemap can classify protected views without
    # maintaining a separate route allow/deny list.
    decorated_function.login_required = True
    return decorated_function


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            flash("You do not have permission to view this page.", "danger")
            return redirect(url_for("index.index"))
        return f(*args, **kwargs)
    return decorated_function


def log_view_action(action="view", redact_params=None):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if audit_activity_enabled():
                user_id = current_user.id if current_user.is_authenticated else None
                route = current_route()
                route_metadata = extract_request_metadata(redact_params=redact_params)
                log_action_isolated(
                    user_id=user_id,
                    action=action,
                    target=route,
                    extra_data=route_metadata,
                )

            response = f(*args, **kwargs)
            if redact_params:
                response = make_response(response)
                response.headers["Referrer-Policy"] = "no-referrer"
            return response
        return wrapper
    return decorator
