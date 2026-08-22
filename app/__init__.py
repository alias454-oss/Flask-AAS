# app/__init__.py
import logging

from flask import Flask
from flask_login import LoginManager

from app.core.application import (
    apply_persisted_site_url,
    initialize_runtime_state,
    register_login_loader,
    register_request_hooks,
    register_response_hooks,
    register_template_context,
)
from app.core.config import settings
from app.core.extensions import cache, csrf, db, limiter, mail, migrate
from app.core.security import TrustedProxyFix
from app.core.site import site_url_flask_config
from app.plugins.cli import plugin_cli
from app.routes import register_all_routes

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
    initialize_runtime_state(app)

    register_request_hooks(app)
    register_template_context(app)
    register_login_loader(login_manager)
    register_response_hooks(app)

    return app
