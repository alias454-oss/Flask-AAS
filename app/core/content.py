# app/core/content.py
"""Host HTML, template-context, and response-content helpers."""

import time
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

from flask import g
from flask_login import current_user
import minify_html

from app.core.cache import safe_get_cached_env_settings


ALLOWED_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "code",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "hr",
    "i",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "ul",
}
ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
}
ALLOWED_URL_SCHEMES = {"", "http", "https", "mailto", "tel"}
VOID_TAGS = {"br", "hr"}
DROP_CONTENT_TAGS = {
    "embed",
    "form",
    "iframe",
    "math",
    "object",
    "script",
    "style",
    "svg",
    "template",
}


def _safe_href(value: str) -> bool:
    """Return True for relative or explicitly allowlisted link targets."""
    compact = "".join(ch for ch in str(value) if ord(ch) > 32 and ord(ch) != 127)
    if not compact:
        return False
    return urlsplit(compact).scheme.lower() in ALLOWED_URL_SCHEMES


class _PageHTMLSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self._drop_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if self._drop_depth:
            if tag not in VOID_TAGS:
                self._drop_depth += 1
            return

        if tag in DROP_CONTENT_TAGS:
            self._drop_depth = 1
            return

        if tag not in ALLOWED_TAGS:
            return

        clean_attrs = []
        allowed_attrs = ALLOWED_ATTRIBUTES.get(tag, set())
        for name, value in attrs:
            name = name.lower()
            if name not in allowed_attrs or value is None:
                continue
            if name == "href" and not _safe_href(value):
                continue
            clean_attrs.append(
                f' {name}="{escape(str(value), quote=True)}"'
            )

        self.output.append(f"<{tag}{''.join(clean_attrs)}>")

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self._drop_depth or tag in DROP_CONTENT_TAGS or tag not in ALLOWED_TAGS:
            return
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()

        if self._drop_depth:
            self._drop_depth -= 1
            return

        if tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            self.output.append(f"</{tag}>")

    def handle_data(self, data):
        if not self._drop_depth:
            self.output.append(escape(data, quote=False))


def sanitize_page_html(value) -> str:
    """Sanitize administrator-authored HTML for the fixed core page overrides."""
    if value is None:
        return ""

    parser = _PageHTMLSanitizer()
    parser.feed(str(value))
    parser.close()
    return "".join(parser.output)


def host_template_context():
    """Return the common host template context for the current request."""
    env = safe_get_cached_env_settings()
    if (
        current_user.is_authenticated
        and env
        and getattr(env, "allow_custom_themes", False)
    ):
        template = (
            getattr(current_user, "template", None)
            or env.template
            or "default"
        )
    else:
        template = (env.template if env else None) or "default"
    return {
        "tpl_path": f"themes/{template}",
        "sidebar_position": "right",
        "env": env,
        "nonce": getattr(g, "nonce", ""),
    }


def start_page_timer():
    """Start request timing used by the HTML response marker."""
    g.start_time = time.time()


def minify_response(response):
    """Minify HTML responses and append the page-generation timing marker."""
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
