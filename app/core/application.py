# app/core/application.py
"""Flask application-construction helpers."""

import base64
import logging
import os
import time

from flask import current_app, g, redirect, request, session, url_for
from flask_login import current_user
import minify_html
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.core.decorators import enforce_required_password_change
from app.core.cache import get_cached_env_settings
from app.core.content import sanitize_page_html
from app.services.sessions import enforce_inactivity_timeout
from app.core.extensions import table_exists
from app.services.sessions import touch_current_session
from app.core.site import (
    LEGACY_SITE_URL_PLACEHOLDERS,
    normalize_site_url,
    site_url_flask_config,
)
from app.services.trackers import track_online_user, visitor_tracking_enabled
from app.models.plugin import PluginRegistration  # noqa: F401 - register model metadata
from app.models.user import EnvSettings, User
from app.plugins.loader import enforce_plugin_access, initialize_plugins
from app.plugins.navigation import visible_plugin_navigation

logger = logging.getLogger("app")


def safe_get_cached_env_settings():
    """Read cached Site Settings while tolerating bootstrap schema gaps."""
    try:
        return get_cached_env_settings()
    except (OperationalError, ProgrammingError):
        return None


def update_log_level():
    """Apply the persisted log level once the core schema is available."""
    if not table_exists(EnvSettings.__tablename__):
        logger.info("Core schema not initialized; using default log level")
        logger.setLevel(logging.INFO)
        return

    env = safe_get_cached_env_settings()
    if not env:
        logger.info("DB not ready, using default log level")
        logger.setLevel(logging.INFO)
        return

    log_level_str = getattr(env, "log_level", "INFO").upper()

    try:
        logger.setLevel(getattr(logging, log_level_str))
        logger.info("Log level set to %s", log_level_str)
    except AttributeError:
        logger.warning("Invalid log level: %s. Falling back to INFO.", log_level_str)
        logger.setLevel(logging.INFO)


def apply_persisted_site_url(app):
    """Apply persisted Site URL trust settings after extensions are ready."""
    with app.app_context():
        env = (
            safe_get_cached_env_settings()
            if table_exists(EnvSettings.__tablename__)
            else None
        )
        persisted_site_url = getattr(env, "site_url", None) if env else None
        if not persisted_site_url or persisted_site_url in LEGACY_SITE_URL_PLACEHOLDERS:
            return

        try:
            normalized_site_url = normalize_site_url(persisted_site_url)
            app.config.update(site_url_flask_config(normalized_site_url))
        except ValueError as exc:
            logger.error(
                "Ignoring invalid persisted Site URL %r: %s",
                persisted_site_url,
                exc,
            )


def initialize_runtime_state(app):
    """Initialize optional plugins and DB-backed runtime settings."""
    with app.app_context():
        initialize_plugins(app)
        update_log_level()


def register_request_hooks(app):
    """Register host request guards and request-scoped bookkeeping."""
    app.before_request(enforce_inactivity_timeout)
    app.before_request(touch_current_session)

    @app.before_request
    def initialize_request_context():
        g.start_time = time.time()
        g.nonce = base64.b64encode(os.urandom(16)).decode("utf-8")

    @app.before_request
    def before_request_online_tracking():
        if visitor_tracking_enabled():
            track_online_user()

    @app.before_request
    def enforce_mfa():
        exempt_routes = {
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
        if request.endpoint in exempt_routes:
            return None

        if current_user.is_authenticated:
            env = safe_get_cached_env_settings()
            if env.use_mfa and current_user.mfa_enabled:
                if not session.get("mfa_verified", False):
                    return redirect(url_for("mfa.mfa_verify"))
        return None

    app.before_request(enforce_required_password_change)
    app.before_request(enforce_plugin_access)


def register_template_context(app):
    """Register host template context and Jinja integration."""

    @app.context_processor
    def inject_host_context():
        env = safe_get_cached_env_settings()
        if (
            current_user.is_authenticated
            and env
            and getattr(env, "allow_custom_themes", False)
        ):
            template = getattr(current_user, "template", None) or env.template or "default"
        else:
            template = (env.template if env else None) or "default"
        return {
            "tpl_path": f"themes/{template}",
            "sidebar_position": "right",
            "env": env,
            "nonce": getattr(g, "nonce", ""),
        }

    app.jinja_env.globals["plugin_navigation"] = visible_plugin_navigation
    app.jinja_env.filters["sanitize_page_html"] = sanitize_page_html


def register_login_loader(login_manager):
    """Register the Flask-Login durable-session user loader."""

    @login_manager.user_loader
    def load_user(session_id):
        return User.load_from_session_id(session_id, require_session_record=True)


def register_response_hooks(app):
    """Register host security, cache, and HTML response handling."""

    @app.after_request
    def add_security_headers(response):
        nonce = getattr(g, "nonce", "")

        def csp_sources(*sources):
            return " ".join(dict.fromkeys(source for source in sources if source))

        connect_sources = csp_sources(
            "'self'",
            *current_app.config.get("CSP_CONNECT_SRC", []),
        )
        image_sources = csp_sources(
            "'self'",
            "data:",
            *current_app.config.get("CSP_IMG_SRC", []),
        )
        media_sources = csp_sources(
            "'self'",
            *current_app.config.get("CSP_MEDIA_SRC", []),
        )

        response.headers["Content-Security-Policy"] = (
            f"default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}'; "
            f"style-src 'self' 'nonce-{nonce}'; "
            f"img-src {image_sources}; "
            f"media-src {media_sources}; "
            f"connect-src {connect_sources};"
        )
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer-when-downgrade"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=(), payment=()"
        )
        return response

    @app.after_request
    def add_cache_headers(response):
        endpoint = request.endpoint or ""
        if endpoint == "static" or endpoint.endswith(".static"):
            return response

        sensitive_paths = (
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

        if (
            bool(session)
            or current_user.is_authenticated
            or request.path.startswith(sensitive_paths)
        ):
            response.headers["Cache-Control"] = (
                "no-store, no-cache, must-revalidate, private"
            )
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.after_request
    def response_minify(response):
        if response.content_type == "text/html; charset=utf-8":
            html = minify_html.minify(
                response.get_data(as_text=True),
                keep_closing_tags=True,
                keep_html_and_head_opening_tags=True,
            )

            if hasattr(g, "start_time"):
                closing_body = html.rfind("</body>")
                if closing_body != -1:
                    page_gen_time = round((time.time() - g.start_time) * 1000, 2)
                    marker = f"<!-- PageGen in {page_gen_time} ms -->"
                    html = f"{html[:closing_body]}{marker}{html[closing_body:]}"

            response.set_data(html)
        return response
