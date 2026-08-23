# app/__init__.py
import logging

from flask import Flask
from flask_login import LoginManager

from app.core.cache import add_cache_headers
from app.core.config import apply_persisted_log_level, settings
from app.core.content import host_template_context, minify_response, sanitize_page_html, start_page_timer
from app.core.decorators import enforce_mfa, enforce_required_password_change
from app.core.extensions import cache, csrf, db, limiter, mail, migrate
from app.core.security import TrustedProxyFix, add_security_headers, initialize_request_security
from app.core.site import apply_persisted_site_url, site_url_flask_config
from app.plugins.cli import plugin_cli
from app.plugins.loader import enforce_plugin_access, initialize_plugins
from app.plugins.navigation import visible_plugin_navigation
from app.routes import register_all_routes
from app.services.sessions import enforce_inactivity_timeout, load_user, touch_current_session
from app.services.trackers import track_online_request

logging.basicConfig(level=logging.INFO)

login_manager = LoginManager()
login_manager.login_view = "login.login"


def create_app():
    app = Flask(__name__)

    # Load config before applying topology-dependent middleware.
    app.config.from_object(settings)
    app.config.update(site_url_flask_config(app.config["SITE_URL"]))

    proxy_hops = app.config.get("PROXY_HOPS", 0)
    if proxy_hops:
        app.wsgi_app = TrustedProxyFix(
            app.wsgi_app,
            trusted_proxies=app.config.get("TRUSTED_PROXIES", []),
            proxy_hops=proxy_hops,
        )

    app.permanent_session_lifetime = settings.PERMANENT_SESSION_LIFETIME

    cache.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    mail.init_app(app)
    login_manager.init_app(app)

    # Keep application-specific commands out of the Flask-AAS CLI. Plugins may
    # expose their own command groups through this single generic dispatcher.
    app.cli.add_command(plugin_cli)

    db.init_app(app)
    migrate.init_app(app, db)

    # Existing Site Settings is authoritative after clean-install bootstrap.
    apply_persisted_site_url(app)

    # Blueprints include their error-handler registration.
    register_all_routes(app)

    # Optional plugins and DB-backed runtime settings fail closed while a fresh
    # database is being created or migrated.
    with app.app_context():
        initialize_plugins(app)
        apply_persisted_log_level()

    # Request hook order is deliberate. Session validity/activity enforcement
    # runs before request bookkeeping and the remaining authentication guards.
    app.before_request(enforce_inactivity_timeout)
    app.before_request(touch_current_session)
    app.before_request(start_page_timer)
    app.before_request(initialize_request_security)
    app.before_request(track_online_request)
    app.before_request(enforce_mfa)
    app.before_request(enforce_required_password_change)
    app.before_request(enforce_plugin_access)

    app.context_processor(host_template_context)
    app.jinja_env.globals["plugin_navigation"] = visible_plugin_navigation
    app.jinja_env.filters["sanitize_page_html"] = sanitize_page_html

    login_manager.user_loader(load_user)

    # Flask executes after-request callbacks in reverse registration order. Keep
    # this sequence aligned with the established minify -> cache -> security flow.
    app.after_request(add_security_headers)
    app.after_request(add_cache_headers)
    app.after_request(minify_response)

    return app
