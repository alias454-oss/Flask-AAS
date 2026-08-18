# app/__init__.py
import logging
import os
import base64
import time
from flask import Flask, g, current_app, request, session, redirect, url_for
from flask_login import LoginManager, current_user
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy.exc import OperationalError, ProgrammingError
import minify_html

from app.core.cache import get_cached_env_settings
from app.core.content import sanitize_page_html
from app.core.extensions import db, migrate, csrf, cache, limiter, mail
from app.routes import register_error_handlers, register_all_routes
from app.core.config import settings
from app.core.inactivity import enforce_inactivity_timeout
from app.core.sessions import touch_current_session
from app.core.schema import table_exists
from app.core.site import (
    LEGACY_SITE_URL_PLACEHOLDERS,
    normalize_site_url,
    site_url_flask_config,
)
from app.core.trackers import track_online_user, visitor_tracking_enabled
from app.models.user import EnvSettings, User
from app.models.plugin import PluginRegistration  # noqa: F401 - register model metadata
from app.plugins.cli import plugin_cli
from app.plugins.loader import enforce_plugin_access, initialize_plugins
from app.plugins.navigation import visible_plugin_navigation

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def safe_get_cached_env_settings():
    try:
        return get_cached_env_settings()
    except (OperationalError, ProgrammingError):
        # DB not ready, return default or None
        return None

def update_log_level():
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
        logger.info(f"Log level set to {log_level_str}")
    except AttributeError:
        logger.warning(f"Invalid log level: {log_level_str}. Falling back to INFO.")
        logger.setLevel(logging.INFO)

login_manager = LoginManager()
login_manager.login_view = 'login.login'

def create_app():
    app = Flask(__name__)

    # Load config before applying topology-dependent middleware.
    app.config.from_object(settings)
    app.config.update(
        site_url_flask_config(app.config["SITE_URL"])
    )

    proxy_hops = app.config.get('PROXY_HOPS', 0)
    if proxy_hops:
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=proxy_hops,
            x_proto=proxy_hops,
            x_host=proxy_hops,
            x_prefix=proxy_hops,
        )

    # Set the session lifetime from your settings
    app.permanent_session_lifetime = settings.PERMANENT_SESSION_LIFETIME

    # Init extensions
    cache.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    mail.init_app(app)
    login_manager.init_app(app)

    # Keep application-specific commands out of the Flask-AAS CLI. Plugins may
    # expose their own command groups through this single generic dispatcher.
    app.cli.add_command(plugin_cli)

    # Initialize DB layer
    db.init_app(app)
    migrate.init_app(app, db)

    # Existing Site Settings is authoritative after the clean-install bootstrap.
    # Derive Flask's native SERVER_NAME, PREFERRED_URL_SCHEME, and TRUSTED_HOSTS
    # before the application begins accepting requests.
    with app.app_context():
        env = (
            safe_get_cached_env_settings()
            if table_exists(EnvSettings.__tablename__)
            else None
        )
        persisted_site_url = getattr(env, "site_url", None) if env else None
        if persisted_site_url and persisted_site_url not in LEGACY_SITE_URL_PLACEHOLDERS:
            try:
                normalized_site_url = normalize_site_url(persisted_site_url)
                app.config.update(
                    site_url_flask_config(normalized_site_url)
                )
            except ValueError as exc:
                logger.error(
                    "Ignoring invalid persisted Site URL %r: %s",
                    persisted_site_url,
                    exc,
                )

    # Register all blueprints
    register_all_routes(app)

    # Initialize optional application plugins and DB-backed runtime settings.
    # Both fail closed while a fresh database is being created or migrated.
    with app.app_context():
        initialize_plugins(app)
        update_log_level()

    # Plugin routes are structural startup state, but current enablement and
    # configuration are persisted state. Gate plugin application surfaces before
    # ordinary request processing so disable/configuration changes take effect
    # without mutating Flask's live route map.
    app.before_request(enforce_plugin_access)

    # Apply a sliding inactivity window to authenticated browser sessions.
    # Pre-authentication MFA state remains governed by its own expiry controls.
    app.before_request(enforce_inactivity_timeout)
    app.before_request(touch_current_session)

    @app.before_request
    def start_timer():
        g.start_time = time.time()

    @app.before_request
    def generate_nonce():
        nonce = base64.b64encode(os.urandom(16)).decode('utf-8')
        g.nonce = nonce

    @app.before_request
    def before_request_online_tracking():
        if visitor_tracking_enabled():
            track_online_user()

    @app.before_request
    def enforce_mfa():
        # Skip for static files, login, MFA verification route
        exempt_routes = {
            "favicon.favicon",
            'robots.robots',
            'login.login',
            'logout.logout',
            'mfa.mfa_verify',
            'mfa.mfa_setup',
            'mfa.mfa_disable',
            'mfa.mfa_reauth',
            'mfa.mfa_replace',
            'mfa.mfa_recovery_codes',
            'register.register',
            'reset.forgot_password',
            'reset.reset_password',
            'static'
        }
        if request.endpoint in exempt_routes:
            return

        if current_user.is_authenticated:
            env = safe_get_cached_env_settings()
            if env.use_mfa and current_user.mfa_enabled:
                if not session.get("mfa_verified", False):
                    return redirect(url_for('mfa.mfa_verify'))

    @app.context_processor
    def inject_tpl_path():
        env = safe_get_cached_env_settings()
        if current_user.is_authenticated and env and getattr(env, 'allow_custom_themes', False):
            template = getattr(current_user, "template", None) or env.template or "default"
        else:
            template = (env.template if env else None) or "default"
        return dict(tpl_path=f"themes/{template}")

    @app.context_processor
    def inject_sidebar_position():
        return dict(sidebar_position='right')  # or 'left'

    @app.context_processor
    def inject_env_settings():
        env = safe_get_cached_env_settings()
        return dict(env=env)

    @app.context_processor
    def inject_nonce():
        return dict(nonce=getattr(g, 'nonce', ''))

    # Plugins contribute navigation structurally, while visibility follows the
    # same current enabled/configured access state as plugin application routes.
    app.jinja_env.globals["plugin_navigation"] = visible_plugin_navigation
    app.jinja_env.filters["sanitize_page_html"] = sanitize_page_html

    # User loader for Flask-Login
    @login_manager.user_loader
    def load_user(session_id):
        return User.load_from_session_id(session_id, require_session_record=True)

    # Helper for building error responses
    register_error_handlers(app)

    @app.after_request
    def add_security_headers(response):
        nonce = getattr(g, 'nonce', '')

        def csp_sources(*sources):
            return " ".join(dict.fromkeys(source for source in sources if source))

        connect_sources = csp_sources(
            "'self'",
            *current_app.config.get('CSP_CONNECT_SRC', []),
        )
        image_sources = csp_sources(
            "'self'",
            'data:',
            *current_app.config.get('CSP_IMG_SRC', []),
        )
        media_sources = csp_sources(
            "'self'",
            *current_app.config.get('CSP_MEDIA_SRC', []),
        )

        response.headers['Content-Security-Policy'] = (
            f"default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}'; "
            f"style-src 'self' 'nonce-{nonce}'; "
            f"img-src {image_sources}; "
            f"media-src {media_sources}; "
            f"connect-src {connect_sources};"
        )
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer-when-downgrade'
        response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=(), payment=()'
        return response

    @app.after_request
    def add_cache_headers(response):
        # Apply cache control globally to admin and captcha paths
        ignored_paths=("/admin", "/account", "/member", "/dashboard", "/captcha", "/internal", "/debug", "/test")
        path = request.path
        if path.startswith(ignored_paths):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.after_request
    def response_minify(response):
        if response.content_type == u'text/html; charset=utf-8':
            html = minify_html.minify(
                response.get_data(as_text=True),
                keep_closing_tags=True,
                keep_html_and_head_opening_tags=True,
            )

            # Ordinary template comments remain stripped by the minifier. Add only
            # the intentional request timing marker after minification so it survives.
            if hasattr(g, "start_time"):
                closing_body = html.rfind("</body>")
                if closing_body != -1:
                    page_gen_time = round((time.time() - g.start_time) * 1000, 2)
                    marker = f"<!-- PageGen in {page_gen_time} ms -->"
                    html = f"{html[:closing_body]}{marker}{html[closing_body:]}"

            response.set_data(html)
        return response

    return app
