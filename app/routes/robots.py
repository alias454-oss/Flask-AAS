# routes/robots.py
import logging
from flask import Blueprint, Response, url_for

from app.core.cache import get_cached_env_settings
from app.core.extensions import cache
from app.core.decorators import log_view_action

logger = logging.getLogger(__name__)

robots_bp = Blueprint('robots', __name__)

@robots_bp.route('/robots.txt')
@log_view_action()
@cache.cached(timeout=0)
def robots():
    env = get_cached_env_settings()
    if not env.site_url:
        return Response("User-agent: *\nDisallow:", mimetype="text/plain")

    site_url = env.site_url.rstrip("/")
    lines = [
        "User-agent: *",
        "Disallow:",
        f"Sitemap: {site_url}{url_for('sitemap.sitemap')}"
    ]
    return Response("\n".join(lines), mimetype="text/plain")

