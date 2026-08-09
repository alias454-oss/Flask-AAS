# tests/test_sitemap.py
import unittest
from functools import wraps
from unittest.mock import patch

from flask import Blueprint, Flask

from app.core.auth import login_required
from app.routes.sitemap import get_all_public_urls, is_protected_view


class SitemapProtectionTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SECRET_KEY="sitemap-test-secret",
            SERVER_NAME="example.com",
            PREFERRED_URL_SCHEME="https",
        )

        pages_bp = Blueprint("pages", __name__)

        @pages_bp.route("/public")
        def public():
            return "public"

        @pages_bp.route("/private")
        @login_required
        def private():
            return "private"

        @pages_bp.route("/wrapped-private")
        @self._passthrough
        @login_required
        def wrapped_private():
            return "wrapped private"

        self.app.register_blueprint(pages_bp)

    @staticmethod
    def _passthrough(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            return view(*args, **kwargs)

        return wrapper

    def test_login_required_marks_view_as_protected(self):
        view = self.app.view_functions["pages.private"]

        self.assertTrue(getattr(view, "login_required", False))
        self.assertTrue(is_protected_view(view))

    def test_protection_marker_survives_wrapping(self):
        view = self.app.view_functions["pages.wrapped_private"]

        self.assertTrue(is_protected_view(view))

    def test_sitemap_includes_public_and_excludes_protected_routes(self):
        with self.app.app_context(), patch(
            "app.routes.sitemap.contact_form_available",
            return_value=True,
        ):
            urls = get_all_public_urls()

        self.assertIn("https://example.com/public", urls)
        self.assertNotIn("https://example.com/private", urls)
        self.assertNotIn("https://example.com/wrapped-private", urls)


if __name__ == "__main__":
    unittest.main()
