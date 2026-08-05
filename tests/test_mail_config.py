import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from flask import Blueprint, Flask
from flask_login import LoginManager

from app.core.extensions import bcrypt, cache, db, limiter
from app.core.mailer import decrypt_smtp_password, encrypt_smtp_password
from app.core.seeder import initial_outbound_email_enabled
from app.models import EnvSettings, Role, User
from app.routes.admin.settings import settings_bp


class MailConfigurationRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(cls.temp_dir.name) / "mail-config-tests.db"

        cls.encryption_key = Fernet.generate_key().decode("utf-8")
        cls.app = Flask(__name__)
        cls.app.config.update(
            TESTING=False,
            PROPAGATE_EXCEPTIONS=True,
            SECRET_KEY="mail-config-test-secret",
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path}",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            RATELIMIT_ENABLED=False,
            CACHE_TYPE="SimpleCache",
            PROXY_HOPS=0,
            TRUSTED_PROXIES=[],
            MAIL_DEBUG=False,
            MAIL_CONFIG_UI_ENABLED=True,
            MAIL_CONFIG_ENCRYPTION_KEY=cls.encryption_key,
            MAIL_SERVER="smtp.environment.test",
            MAIL_PORT=587,
            MAIL_USE_TLS=True,
            MAIL_USE_SSL=False,
            MAIL_USERNAME="environment-user",
            MAIL_PASSWORD="environment-password",
            MAIL_DEFAULT_SENDER="environment-sender@example.com",
        )

        db.init_app(cls.app)
        bcrypt.init_app(cls.app)
        cache.init_app(cls.app)
        limiter.init_app(cls.app)

        cls.login_manager = LoginManager(cls.app)

        @cls.login_manager.user_loader
        def load_user(user_id):
            try:
                return db.session.get(User, int(user_id))
            except (TypeError, ValueError):
                return None

        login_bp = Blueprint("login", __name__)

        @login_bp.route("/login")
        def login():
            return "login"

        index_bp = Blueprint("index", __name__)

        @index_bp.route("/")
        def index():
            return "index"

        cls.app.register_blueprint(settings_bp)
        cls.app.register_blueprint(login_bp)
        cls.app.register_blueprint(index_bp)

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        cls.temp_dir.cleanup()

    def setUp(self):
        self.app.config.update(
            MAIL_DEBUG=False,
            TESTING=False,
            MAIL_CONFIG_UI_ENABLED=True,
            MAIL_CONFIG_ENCRYPTION_KEY=self.encryption_key,
            MAIL_SERVER="smtp.environment.test",
            MAIL_PORT=587,
            MAIL_USE_TLS=True,
            MAIL_USE_SSL=False,
            MAIL_USERNAME="environment-user",
            MAIL_PASSWORD="environment-password",
            MAIL_DEFAULT_SENDER="environment-sender@example.com",
        )
        self.app_context = self.app.app_context()
        self.app_context.push()

        db.session.remove()
        db.drop_all()
        db.create_all()
        cache.clear()
        EnvSettings._cached_instance = None

        admin_role = Role(name="admin", description="Administrator")
        user_role = Role(name="user", description="User")
        db.session.add_all([admin_role, user_role])
        db.session.flush()

        self.admin = User(
            username="admin",
            email="admin@example.com",
            activated=True,
            approved=True,
        )
        self.admin.set_password("admin-password")
        self.admin.roles.append(admin_role)
        db.session.add(self.admin)
        db.session.flush()

        self.settings = EnvSettings(
            user_id=self.admin.id,
            site_name="Mail Configuration Test",
            site_url="https://example.com",
            site_lang="en",
            site_timezone="UTC",
            description="",
            keywords="",
            admin_name="Admin",
            admin_email="admin@example.com",
            site_mode=0,
            default_role_id=user_role.id,
            users_per_page=20,
            users_stored_path="/tmp/users",
            template="default",
            use_mfa=False,
            use_verify_email=False,
            use_user_approval=False,
            use_user_location=False,
            use_captcha=False,
            maint_mode=False,
            visitor_tracking=False,
            use_fancy_urls=False,
            enable_delete_old_users=False,
            users_delete_after_days=15,
            email_after_days=45,
            use_smtp=True,
            smtp_host=None,
            smtp_port=587,
            smtp_security="starttls",
            smtp_user=None,
            smtp_pass=None,
            smtp_default_sender=None,
            enable_analytics=False,
            allow_custom_themes=False,
            max_failed_attempts=5,
            lockout_duration_seconds=900,
            enable_logging=True,
            log_level="INFO",
        )
        db.session.add(self.settings)
        db.session.commit()
        EnvSettings._cached_instance = None

        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True

    def tearDown(self):
        db.session.remove()
        EnvSettings._cached_instance = None
        self.app_context.pop()

    def _form_data(self, **overrides):
        data = {
            "site_name": self.settings.site_name,
            "site_url": self.settings.site_url,
            "site_lang": "en",
            "site_timezone": "UTC",
            "description": self.settings.description,
            "keywords": self.settings.keywords,
            "admin_name": self.settings.admin_name,
            "admin_email": self.settings.admin_email,
            "site_mode": str(self.settings.site_mode),
            "default_role_id": str(self.settings.default_role_id),
            "users_per_page": str(self.settings.users_per_page),
            "users_stored_path": self.settings.users_stored_path,
            "template": "default",
            "users_delete_after_days": str(self.settings.users_delete_after_days),
            "email_after_days": str(self.settings.email_after_days),
            "smtp_host": self.settings.smtp_host or "",
            "smtp_port": str(self.settings.smtp_port or 587),
            "smtp_security": self.settings.smtp_security or "starttls",
            "smtp_user": self.settings.smtp_user or "",
            "smtp_pass": "",
            "smtp_default_sender": self.settings.smtp_default_sender or "",
            "max_failed_attempts": str(self.settings.max_failed_attempts),
            "lockout_duration_seconds": str(
                self.settings.lockout_duration_seconds
            ),
            "log_level": self.settings.log_level,
        }
        if self.settings.use_smtp:
            data["use_smtp"] = "y"
        if self.settings.use_verify_email:
            data["use_verify_email"] = "y"
        data.update(overrides)
        return data

    def _post(self, data):
        with patch(
            "app.core.decorators.audit_activity_enabled",
            return_value=False,
        ), patch(
            "app.routes.admin.settings.render_template",
            return_value="settings",
        ) as render, patch(
            "app.routes.admin.settings.log_action"
        ) as log_action:
            response = self.client.post(
                "/admin/settings/",
                data=data,
                follow_redirects=False,
            )
        return response, render, log_action

    def test_clean_install_enables_outbound_email_for_complete_environment(self):
        self.assertTrue(initial_outbound_email_enabled())

    def test_clean_install_disables_outbound_email_without_any_transport(self):
        self.app.config.update(
            MAIL_DEBUG=False,
            MAIL_SERVER=None,
            MAIL_USERNAME=None,
            MAIL_PASSWORD=None,
            MAIL_DEFAULT_SENDER=None,
        )
        self.assertFalse(initial_outbound_email_enabled())

    def test_get_never_populates_the_saved_password(self):
        self.settings.smtp_host = "smtp.database.test"
        self.settings.smtp_user = "database-user"
        self.settings.smtp_default_sender = "sender@example.com"
        self.settings.smtp_pass = encrypt_smtp_password("database-password")
        db.session.commit()
        EnvSettings._cached_instance = None

        with patch(
            "app.core.decorators.audit_activity_enabled",
            return_value=False,
        ), patch(
            "app.routes.admin.settings.render_template",
            return_value="settings",
        ) as render:
            response = self.client.get("/admin/settings/")

        self.assertEqual(response.status_code, 200)
        form = render.call_args.kwargs["form"]
        self.assertEqual(form.smtp_pass.data, "")
        self.assertNotIn(self.settings.smtp_pass, str(form.smtp_pass()))
        self.assertTrue(render.call_args.kwargs["mail_config_ui_enabled"])

    def test_complete_ui_override_is_encrypted_and_audited_without_secret(self):
        response, _, log_action = self._post(
            self._form_data(
                smtp_host="smtp.database.test",
                smtp_port="465",
                smtp_security="ssl",
                smtp_user="database-user",
                smtp_pass="database-password",
                smtp_default_sender="sender@example.com",
            )
        )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(self.settings)
        self.assertEqual(self.settings.smtp_host, "smtp.database.test")
        self.assertNotEqual(self.settings.smtp_pass, "database-password")
        self.assertEqual(
            decrypt_smtp_password(self.settings.smtp_pass),
            "database-password",
        )

        audit_data = log_action.call_args.kwargs["extra_data"]
        self.assertIn("smtp_password_changed", audit_data["fields_updated"])
        self.assertNotIn("database-password", repr(log_action.call_args))

    def test_blank_password_preserves_existing_encrypted_value(self):
        self.settings.smtp_host = "smtp.database.test"
        self.settings.smtp_port = 587
        self.settings.smtp_security = "starttls"
        self.settings.smtp_user = "database-user"
        self.settings.smtp_pass = encrypt_smtp_password("existing-password")
        self.settings.smtp_default_sender = "sender@example.com"
        db.session.commit()
        encrypted_before = self.settings.smtp_pass
        EnvSettings._cached_instance = None

        response, _, _ = self._post(
            self._form_data(
                smtp_host="smtp.database.test",
                smtp_port="587",
                smtp_security="starttls",
                smtp_user="database-user",
                smtp_pass="",
                smtp_default_sender="sender@example.com",
            )
        )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(self.settings)
        self.assertEqual(self.settings.smtp_pass, encrypted_before)

    def test_unauthenticated_ui_relay_is_accepted(self):
        response, _, _ = self._post(
            self._form_data(
                smtp_host="smtp.relay.test",
                smtp_port="25",
                smtp_security="none",
                smtp_user="",
                smtp_pass="",
                smtp_default_sender="sender@example.com",
            )
        )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(self.settings)
        self.assertEqual(self.settings.smtp_host, "smtp.relay.test")
        self.assertIsNone(self.settings.smtp_user)
        self.assertIsNone(self.settings.smtp_pass)

    def test_username_without_password_is_rejected(self):
        response, render, _ = self._post(
            self._form_data(
                smtp_host="smtp.database.test",
                smtp_user="database-user",
                smtp_pass="",
                smtp_default_sender="sender@example.com",
            )
        )

        self.assertEqual(response.status_code, 200)
        form = render.call_args.kwargs["form"]
        self.assertTrue(form.smtp_host.errors)
        self.assertIn(
            "both be supplied",
            " ".join(form.smtp_host.errors),
        )

    def test_authenticated_override_requires_valid_encryption_key(self):
        self.app.config["MAIL_CONFIG_ENCRYPTION_KEY"] = None

        response, render, _ = self._post(
            self._form_data(
                smtp_host="smtp.database.test",
                smtp_user="database-user",
                smtp_pass="database-password",
                smtp_default_sender="sender@example.com",
            )
        )

        self.assertEqual(response.status_code, 200)
        form = render.call_args.kwargs["form"]
        self.assertIn(
            "MAIL_CONFIG_ENCRYPTION_KEY",
            " ".join(form.smtp_host.errors),
        )
        db.session.refresh(self.settings)
        self.assertIsNone(self.settings.smtp_pass)

    def test_partial_ui_override_is_rejected_even_with_environment_fallback(self):
        response, render, _ = self._post(
            self._form_data(
                smtp_host="smtp.partial.test",
                smtp_default_sender="",
            )
        )

        self.assertEqual(response.status_code, 200)
        form = render.call_args.kwargs["form"]
        self.assertTrue(form.smtp_host.errors)
        db.session.refresh(self.settings)
        self.assertIsNone(self.settings.smtp_host)

    def test_clear_override_returns_to_environment_fallback(self):
        self.settings.smtp_host = "smtp.database.test"
        self.settings.smtp_port = 465
        self.settings.smtp_security = "ssl"
        self.settings.smtp_user = "database-user"
        self.settings.smtp_pass = encrypt_smtp_password("database-password")
        self.settings.smtp_default_sender = "sender@example.com"
        db.session.commit()
        EnvSettings._cached_instance = None

        response, _, log_action = self._post(
            self._form_data(clear_smtp_override="y")
        )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(self.settings)
        self.assertIsNone(self.settings.smtp_host)
        self.assertIsNone(self.settings.smtp_user)
        self.assertIsNone(self.settings.smtp_pass)
        self.assertIsNone(self.settings.smtp_default_sender)
        fields = log_action.call_args.kwargs["extra_data"]["fields_updated"]
        self.assertIn("smtp_override_cleared", fields)

    def test_clear_override_bypasses_invalid_saved_field_validation(self):
        self.settings.smtp_host = "smtp.database.test"
        self.settings.smtp_port = 465
        self.settings.smtp_security = "ssl"
        self.settings.smtp_user = "database-user"
        self.settings.smtp_pass = encrypt_smtp_password("database-password")
        self.settings.smtp_default_sender = "not-an-email-address"
        db.session.commit()
        EnvSettings._cached_instance = None

        response, _, _ = self._post(
            self._form_data(
                clear_smtp_override="y",
                use_smtp="",
                use_verify_email="",
            )
        )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(self.settings)
        self.assertIsNone(self.settings.smtp_host)
        self.assertIsNone(self.settings.smtp_pass)
        self.assertIsNone(self.settings.smtp_default_sender)

    def test_verification_cannot_be_required_when_outbound_email_is_disabled(self):
        data = self._form_data(use_verify_email="y")
        data.pop("use_smtp", None)

        response, render, _ = self._post(data)

        self.assertEqual(response.status_code, 200)
        form = render.call_args.kwargs["form"]
        self.assertTrue(form.use_verify_email.errors)
        db.session.refresh(self.settings)
        self.assertFalse(self.settings.use_verify_email)

    def test_outbound_email_cannot_be_enabled_without_a_transport(self):
        self.app.config.update(
            MAIL_SERVER=None,
            MAIL_USERNAME=None,
            MAIL_PASSWORD=None,
            MAIL_DEFAULT_SENDER=None,
        )

        response, render, _ = self._post(self._form_data())

        self.assertEqual(response.status_code, 200)
        form = render.call_args.kwargs["form"]
        self.assertTrue(form.use_smtp.errors)

    def test_ui_disabled_ignores_submitted_smtp_override_fields(self):
        self.app.config["MAIL_CONFIG_UI_ENABLED"] = False

        response, _, _ = self._post(
            self._form_data(
                smtp_host="smtp.attacker.test",
                smtp_port="465",
                smtp_security="ssl",
                smtp_user="attacker",
                smtp_pass="attacker-password",
                smtp_default_sender="attacker@example.com",
            )
        )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(self.settings)
        self.assertIsNone(self.settings.smtp_host)
        self.assertIsNone(self.settings.smtp_pass)


if __name__ == "__main__":
    unittest.main()
