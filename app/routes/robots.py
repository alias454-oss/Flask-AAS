# routes/robots.py
import logging
from flask import Blueprint, current_app, Response, url_for

from app.core.extensions import cache
from app.core.decorators import log_view_action

logger = logging.getLogger(__name__)

robots_bp = Blueprint('robots', __name__)

@robots_bp.route('/robots.txt')
@log_view_action()
@cache.cached(timeout=0)
def robots():
    sitemap_url = url_for(
        "sitemap.sitemap",
        _external=True,
        _scheme=current_app.config["PREFERRED_URL_SCHEME"],
    )
    lines = [
        "User-agent: *",
        "Disallow:",
        f"Sitemap: {sitemap_url}",
    ]
    return Response("\n".join(lines), mimetype="text/plain")

