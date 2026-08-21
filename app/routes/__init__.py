# routes/__init__.py
import logging
import traceback
from jinja2 import TemplateNotFound
from flask import jsonify, render_template, request

# Blueprints
from .favicon import favicon_bp
from .about import about_bp
from .index import index_bp
from .tos import tos_bp
from .privacy import privacy_bp
from .login import login_bp
from .logout import logout_bp
from .register import register_bp
from .reset import reset_bp
from .verify import verify_bp

from .account.account import account_bp
from .account.dashboard import dashboard_bp

from .admin.admin import admin_bp
from .admin.settings import settings_bp
from .admin.plugins import plugins_bp
from .admin.users import users_bp

from .mfa.mfa import mfa_bp

from .robots import robots_bp
from .sitemap import sitemap_bp
from .captcha import captcha_bp
from .contact import contact_bp
from .locations import locations_bp

from app.core.security import get_client_ip

# Logging
logger = logging.getLogger(__name__)

# Blueprint list
all_blueprints = [
    favicon_bp,
    mfa_bp,
    captcha_bp,
    contact_bp,
    locations_bp,
    sitemap_bp,
    robots_bp,
    about_bp,
    index_bp,
    tos_bp,
    privacy_bp,
    login_bp,
    logout_bp,
    register_bp,
    reset_bp,
    verify_bp,
    account_bp,
    dashboard_bp,
    admin_bp,
    settings_bp,
    plugins_bp,
    users_bp
]

# Main entry point to register all routes and errors
def register_all_routes(app):
    for bp in all_blueprints:
        app.register_blueprint(bp)
    register_error_handlers(app)

# Error response helper
def error_response(status_code, message, headers=None):
    if 'application/json' in request.headers.get('Accept', ''):
        response = jsonify({
            "status": status_code,
            "error": message,
            "path": request.path
        })
        response.status_code = status_code
        if headers:
            for k, v in headers.items():
                response.headers[k] = v
        return response
    else:
        try:
            rendered = render_template(f"error_pages/{status_code}.html", error=message)
        except TemplateNotFound:
            rendered = f"<h1>{status_code}</h1><p>{message}</p>"
        return rendered, status_code, headers or {}

# Register error handlers
def register_error_handlers(app):
    @app.errorhandler(400)
    def bad_request(e):
        logger.info(f"400 Bad Request - Path: {request.path} - IP: {get_client_ip()}")
        return error_response(400, "Bad request. Please check your input and try again.")

    @app.errorhandler(401)
    def unauthorized(e):
        logger.warning(f"401 Unauthorized - Path: {request.path} - IP: {get_client_ip()}")
        return error_response(401, "Authentication required. Please log in.")

    @app.errorhandler(403)
    def forbidden(e):
        logger.warning(f"403 Forbidden - Path: {request.path} - IP: {get_client_ip()}")
        return error_response(403, "Access denied")

    @app.errorhandler(404)
    def not_found(e):
        logger.info(f"404 Not Found - Path: {request.path} - IP: {get_client_ip()}")
        return error_response(404, "Resource not found")

    @app.errorhandler(405)
    def method_not_allowed(e):
        logger.warning(f"405 Method Not Allowed - Path: {request.path} - Method: {request.method} - IP: {get_client_ip()}")
        return error_response(405, "Method not allowed on this endpoint")

    @app.errorhandler(429)
    def too_many_requests(e):
        retry_after = getattr(e, "retry_after", None)
        headers = {"Retry-After": str(retry_after)} if retry_after else {}
        logger.warning(f"429 Too Many Requests - Path: {request.path} - IP: {get_client_ip()}")
        return error_response(429, "Too many requests. Please try again later.", headers=headers)

    @app.errorhandler(500)
    def internal_error(e):
        logging.error(f"500 Error: {e}\n{traceback.format_exc()}")
        return error_response(500, "An unexpected error occurred. Please try again later.")

    @app.errorhandler(Exception)
    def handle_unexpected_exception(e):
        # Avoid duplicating known error types
        if hasattr(e, "code") and e.code in (400, 401, 403, 404, 405, 429, 500):
            raise e  # Let Flask handle it through the above
        logger.exception(f"Unhandled exception: {e}")
        return error_response(500, "An unexpected error occurred.")