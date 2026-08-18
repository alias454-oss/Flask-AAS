# app/core/content.py
"""Small, allowlisted HTML sanitizer for editable core page content."""

from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit


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
