# tests/test_page_content.py
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Blueprint, Flask
from flask_login import LoginManager

from app.core.content import sanitize_page_html
from app.core.extensions import csrf, db, limiter
from app.models import AuditActivity, EnvSettings, Role, User, UserSession
from app.routes.admin.settings import settings_bp


ROOT = Path(__file__).resolve().parents[1]


class PageContentSanitizerTests(unittest.TestCase):
    def test_allows_basic_markup_and_safe_links(self):
        value = (
            '<h2>Heading</h2><p>Hello <strong>there</strong>.</p>'
            '<a href="https://example.test/path?q=1&amp;x=2" title="Example">Link</a>'
        )

        self.assertEqual(
            sanitize_page_html(value),
            '<h2>Heading</h2><p>Hello <strong>there</strong>.</p>'
            '<a href="https://example.test/path?q=1&amp;x=2" title="Example">Link</a>',
        )

    def test_removes_active_content_attributes_and_unsafe_links(self):
        value = (
            '<p onclick="alert(1)">Safe</p>'
            '<script>alert(1)</script>'
            '<iframe src="https://example.test">hidden</iframe>'
            '<a href="jav&#x61;script:alert(1)" style="color:red">Bad</a>'
            '<img src=x onerror="alert(1)">'
        )

        self.assertEqual(
            sanitize_page_html(value),
            '<p>Safe</p><a>Bad</a>',
        )

    def test_rejects_control_character_scheme_obfuscation(self):
        self.assertEqual(
            sanitize_page_html('<a href="java\nscript:alert(1)">Bad</a>'),
            '<a>Bad</a>',
        )


class PageContentTemplateContractTests(unittest.TestCase):
    def test_core_pages_keep_static_defaults_and_opt_into_overrides(self):
        expectations = {
            "index.html": ("page_home_html", "Flask-Based Login & User Management System"),
            "about.html": ("page_about_html", "About {{ env.site_name }}"),
            "privacy.html": ("page_privacy_html", "Privacy Statement for {{ env.site_name }}"),
            "tos.html": ("page_terms_html", "Website User Agreement for {{ env.site_name }}"),
        }

        page_keys = {
            "index.html": "home",
            "about.html": "about",
            "privacy.html": "privacy",
            "tos.html": "terms",
        }

        for filename, (field_name, default_marker) in expectations.items():
            source = (ROOT / "app" / "templates" / filename).read_text(encoding="utf-8")
            self.assertIn(f"env.{field_name}", source)
            self.assertIn("| sanitize_page_html | safe", source)
            self.assertIn("{% else %}", source)
            self.assertIn(default_marker, source)
            self.assertIn(
                f'data-page-content="{page_keys[filename]}"',
                source,
            )

    def test_admin_menu_exposes_page_content_editor(self):
        menu = (
            ROOT / "app" / "templates" / "admin" / "includes" / "menu.html"
        ).read_text(encoding="utf-8")
        self.assertIn("url_for('settings.page_content')", menu)
        self.assertIn(">Page Content</a>", menu)

    def test_page_content_overview_template_uses_manage_links(self):
        source = (
            ROOT / "app" / "templates" / "admin" / "page_content.html"
        ).read_text(encoding="utf-8")
        self.assertIn("{% for page in pages %}", source)
        self.assertIn(
            "url_for('settings.page_content_manage', page_key=page.key)",
            source,
        )
        self.assertIn("Customized", source)
        self.assertIn("Theme default", source)

    def test_manage_template_loads_current_theme_content_without_editor_dependency(self):
        template = (
            ROOT / "app" / "templates" / "admin" / "page_content_manage.html"
        ).read_text(encoding="utf-8")
        script = (
            ROOT / "app" / "static" / "js" / "page_content.js"
        ).read_text(encoding="utf-8")

        self.assertIn("data_page_content_editor", template)
        self.assertIn("data_page_key=page.key", template)
        self.assertIn("data_source_url=page.view_url", template)
        self.assertIn("js/page_content.js", template)
        self.assertIn("fetch(sourceUrl", script)
        self.assertIn("new DOMParser()", script)
        self.assertIn("data-page-content=", script)
        self.assertIn("page-content-editor", template)
        self.assertIn("data_has_errors", template)
        self.assertIn("formatPageContent", script)
        self.assertIn(r'join("\n\n")', script)
        self.assertNotIn("quill", script.lower())
        self.assertNotIn("trix", script.lower())
        self.assertNotIn("tinymce", script.lower())


class PageContentRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(cls.temp_dir.name) / "page-content-tests.db"

        cls.app = Flask(__name__)
        cls.app.config.update(
            TESTING=True,
            SECRET_KEY="page-content-test-secret",
            SQLALCHEMY_DATABASE_URI=os.environ.get(
                "PAGE_CONTENT_TEST_DATABASE_URI",
                f"sqlite:///{database_path}",
            ),
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            RATELIMIT_ENABLED=False,
            PROXY_HOPS=0,
            TRUSTED_PROXIES=[],
        )

        db.init_app(cls.app)
        csrf.init_app(cls.app)
        limiter.init_app(cls.app)

        login_manager = LoginManager(cls.app)
        login_manager.login_view = "login.login"

        @login_manager.user_loader
        def load_user(session_id):
            return User.load_from_session_id(session_id)

        login_bp = Blueprint("login", __name__)

        @login_bp.route("/login")
        def login():
            return "login"

        index_bp = Blueprint("index", __name__)
        about_bp = Blueprint("about", __name__)
        privacy_bp = Blueprint("privacy", __name__)
        tos_bp = Blueprint("tos", __name__)

        @index_bp.route("/")
        def index():
            return "index"

        @about_bp.route("/about")
        def about():
            return "about"

        @privacy_bp.route("/privacy")
        def privacy():
            return "privacy"

        @tos_bp.route("/tos")
        def tos():
            return "tos"

        cls.app.register_blueprint(login_bp)
        cls.app.register_blueprint(index_bp)
        cls.app.register_blueprint(about_bp)
        cls.app.register_blueprint(privacy_bp)
        cls.app.register_blueprint(tos_bp)
        cls.app.register_blueprint(settings_bp)

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        cls.temp_dir.cleanup()

    def setUp(self):
        self.app_context = self.app.app_context()
        self.app_context.push()

        db.session.remove()
        db.drop_all()
        db.create_all()
        EnvSettings._cached_instance = None

        admin_role = Role(name="admin")
        db.session.add(admin_role)
        db.session.flush()

        admin = User(
            username="page-admin",
            email="page-admin@example.test",
            activated=True,
            approved=True,
        )
        admin.set_password("page-admin-password")
        admin.roles.append(admin_role)
        db.session.add(admin)
        db.session.flush()

        settings = EnvSettings(
            user_id=admin.id,
            site_name="Page Content Test",
            site_lang="en",
            site_timezone="UTC",
            description="",
            keywords="",
            users_per_page=20,
            users_stored_path="uploads/users",
            use_mfa=False,
            use_verify_email=False,
            use_user_approval=False,
            use_user_location=False,
            use_captcha=False,
            contact_enabled=False,
            visitor_tracking=False,
            enable_logging=False,
        )
        db.session.add(settings)
        db.session.commit()

        self.admin_id = admin.id
        self.client = self.app.test_client()
        self._login()

    def tearDown(self):
        db.session.remove()
        EnvSettings._cached_instance = None
        self.app_context.pop()

    def _login(self):
        admin = db.session.get(User, self.admin_id)
        UserSession.issue_for_user(
            admin,
            ip_address="127.0.0.1",
            user_agent="page-content-test-agent",
        )
        db.session.commit()
        with self.client.session_transaction() as session:
            session["_user_id"] = admin.get_id()
            session["_fresh"] = True

    def test_overview_lists_fixed_pages_with_manage_links(self):
        with patch(
            "app.routes.admin.settings.render_template",
            return_value="page-content-overview",
        ) as render:
            response = self.client.get("/admin/settings/page-content")

        self.assertEqual(response.status_code, 200)
        render.assert_called_once()
        args, kwargs = render.call_args
        self.assertEqual(args[0], "admin/page_content.html")
        self.assertEqual(
            [(page["key"], page["label"]) for page in kwargs["pages"]],
            [
                ("home", "Homepage"),
                ("about", "About"),
                ("privacy", "Privacy Policy"),
                ("terms", "Terms of Service"),
            ],
        )

    def test_admin_save_sanitizes_one_page_and_audits_without_content(self):
        response = self.client.post(
            "/admin/settings/page-content/home",
            data={
                "content_html": (
                    '<h2>Hello</h2><script>alert(1)</script>'
                    '<p onclick="alert(2)">Body</p>'
                    '<a href="javascript:alert(3)">Bad link</a>'
                ),
                "submit": "Save Changes",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/admin/settings/page-content/home")

        db.session.expire_all()
        settings = EnvSettings.query.one()
        self.assertEqual(
            settings.page_home_html,
            "<h2>Hello</h2><p>Body</p><a>Bad link</a>",
        )
        self.assertIsNone(settings.page_about_html)
        self.assertIsNone(settings.page_privacy_html)
        self.assertIsNone(settings.page_terms_html)

        event = AuditActivity.query.filter_by(action="update_page_content").one()
        self.assertEqual(event.extra_data["page"], "Homepage")
        self.assertEqual(event.extra_data["page_key"], "home")
        self.assertNotIn("Hello", event._extra_data)

    def test_reset_restores_only_managed_page_to_theme_default(self):
        settings = EnvSettings.query.one()
        settings.page_home_html = "<p>Custom home</p>"
        settings.page_about_html = "<p>Keep about</p>"
        db.session.commit()

        response = self.client.post(
            "/admin/settings/page-content/home",
            data={
                "content_html": "<p>Custom home</p>",
                "reset": "Reset to Theme Default",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/admin/settings/page-content/home")

        db.session.expire_all()
        settings = EnvSettings.query.one()
        self.assertIsNone(settings.page_home_html)
        self.assertEqual(settings.page_about_html, "<p>Keep about</p>")

        event = AuditActivity.query.filter_by(action="reset_page_content").one()
        self.assertEqual(event.extra_data["page"], "Homepage")
        self.assertEqual(event.extra_data["page_key"], "home")

    def test_unknown_page_key_returns_not_found(self):
        response = self.client.get("/admin/settings/page-content/not-a-page")
        self.assertEqual(response.status_code, 404)
