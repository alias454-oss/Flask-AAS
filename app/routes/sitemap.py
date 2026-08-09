# routes/sitemap.py
import logging
import threading
import urllib.parse
import urllib.request
from datetime import datetime
from flask import Blueprint, current_app, url_for, Response

from app.core.decorators import log_view_action
from app.core.mailer import contact_form_available

logger = logging.getLogger(__name__)

sitemap_bp = Blueprint('sitemap', __name__)

def ping_search_engines():
    with current_app.app_context():
        sitemap_url = url_for(
            'sitemap.sitemap',
            _external=True,
            _scheme=current_app.config['PREFERRED_URL_SCHEME'],
        )

        targets = [
            f"https://www.google.com/ping?sitemap={urllib.parse.quote(sitemap_url)}",
            f"https://www.bing.com/ping?sitemap={urllib.parse.quote(sitemap_url)}",
        ]

        for url in targets:
            try:
                with urllib.request.urlopen(url) as response:
                    logger.info(f"Pinged: {url} - {response.status}")
            except Exception as e:
                logger.warning(f"Ping failed: {url} - {e}")

def is_protected_view(view_func):
    # Unwrap decorated functions
    while hasattr(view_func, "__wrapped__"):
        view_func = view_func.__wrapped__

    # Flask-Login wraps views and adds _login_disabled = False
    return getattr(view_func, "_login_disabled", True) is False

def get_all_public_urls():
    ignored_prefixes = [
        "/login",
        "/logout",
        "/register",
        "/forgot-password",
        "/change-password",
        "/dashboard",
        "/captcha_image",
        "/mfa",
        "/admin",
        "/internal",
        "/debug",
        "/test",
        "/sitemap.xml"
    ]

    ignored_substrings = ["static"]

    with current_app.app_context():
        output = []
        contact_available = contact_form_available()
        for rule in current_app.url_map.iter_rules():
            if (
                "GET" in rule.methods
                and len(rule.arguments) == 0
                and not any(rule.rule.startswith(prefix) for prefix in ignored_prefixes)
                and not any(substr in rule.endpoint for substr in ignored_substrings)
                and (rule.endpoint != "contact.contact" or contact_available)
            ):
                view_func = current_app.view_functions.get(rule.endpoint)
                if view_func and not getattr(view_func, "login_required", False):
                    try:
                        url = url_for(
                            rule.endpoint,
                            _external=True,
                            _scheme=current_app.config["PREFERRED_URL_SCHEME"],
                        )
                        output.append(url)
                    except Exception as e:
                        logger.warning(f"Skipping rule {rule} ({rule.endpoint}): {e}")
        return sorted(set(output))

@sitemap_bp.route("/sitemap.xml")
@log_view_action()
def sitemap():
    urls = get_all_public_urls()
    # urls += [
    #     url_for('index.index', _external=True),
    # ]

    logger.info(f"Sitemap generated with {len(urls)} URLs")

    xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')

    for url in urls:
        xml.append("<url>")
        xml.append(f"<loc>{url}</loc>")
        xml.append(f"<lastmod>{datetime.utcnow().date()}</lastmod>")
        xml.append("<changefreq>weekly</changefreq>")
        xml.append("<priority>0.5</priority>")
        xml.append("</url>")

    xml.append("</urlset>")
    sitemap_xml = "\n".join(xml)

    # Asynchronously ping search engines
    # threading.Thread(target=ping_search_engines).start()

    return Response(sitemap_xml, mimetype="application/xml")
