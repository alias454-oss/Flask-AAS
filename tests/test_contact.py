import unittest
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner
from flask import Flask

from app import create_app
from app.core.config import settings
from app.core.extensions import limiter
from app.core.mailer import send_contact_email
from app.routes.captcha import captcha_bp
from app.routes.contact import contact_bp
from manage import mail_test


class ContactRegistrationTests(unittest.TestCase):
    def test_application_factory_registers_contact_route(self):
        with patch.object(
            settings,
            "SQLALCHEMY_DATABASE_URI",
            "sqlite://",
        ):
            app = create_app()

        rules = {
            (rule.endpoint, rule.rule, frozenset(rule.methods))
            for rule in app.url_map.iter_rules()
        }

        self.assertTrue(
            any(
                endpoint == "contact.contact"
                and rule == "/contact"
                and {"GET", "POST"}.issubset(methods)
                for endpoint, rule, methods in rules
            )
        )


class ContactRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SECRET_KEY="contact-route-test-secret",
            TESTING=True,
            WTF_CSRF_ENABLED=False,
            RATELIMIT_ENABLED=False,
        )
        limiter.init_app(self.app)
        self.app.register_blueprint(captcha_bp)
        self.app.register_blueprint(contact_bp)
        self.client = self.app.test_client()
        self.env = SimpleNamespace(use_captcha=False)

    @staticmethod
    def _form_data(**overrides):
        data = {
            "name": "Example User",
            "email": "User@Example.com",
            "subject": "Account question",
            "message": "Please help with my account.",
            "nobot_check": "",
        }
        data.update(overrides)
        return data

    def _post(self, mail_status):
        with patch(
            "app.routes.contact.get_cached_env_settings",
            return_value=self.env,
        ), patch(
            "app.routes.contact.audit_activity_enabled",
            return_value=False,
        ), patch(
            "app.core.decorators.audit_activity_enabled",
            return_value=False,
        ), patch(
            "app.routes.contact.send_contact_email",
            return_value=mail_status,
        ) as send_contact:
            response = self.client.post(
                "/contact",
                data=self._form_data(),
            )

        return response, send_contact

    def _flashes(self):
        with self.client.session_transaction() as session:
            return list(session.get("_flashes", []))

    def test_get_contact_route_is_available(self):
        with patch(
            "app.routes.contact.get_cached_env_settings",
            return_value=self.env,
        ), patch(
            "app.core.decorators.audit_activity_enabled",
            return_value=False,
        ), patch(
            "app.routes.contact.render_template",
            return_value="contact form",
        ):
            response = self.client.get("/contact")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), "contact form")

    def test_queued_contact_submission_reports_acceptance(self):
        response, send_contact = self._post("queued")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/contact")
        self.assertIn(
            ("success", "Your message was accepted for delivery."),
            self._flashes(),
        )
        send_contact.assert_called_once_with(
            name="Example User",
            email="user@example.com",
            message="Please help with my account.",
            subject="Account question",
        )

    def test_unavailable_contact_delivery_does_not_report_success(self):
        for mail_status in ("disabled", "failed"):
            with self.subTest(mail_status=mail_status):
                response, _ = self._post(mail_status)
                flashes = self._flashes()

                self.assertEqual(response.status_code, 302)
                self.assertIn(
                    (
                        "danger",
                        "Message delivery is currently unavailable. "
                        "Please try again later.",
                    ),
                    flashes,
                )
                self.assertNotIn(
                    ("success", "Your message was accepted for delivery."),
                    flashes,
                )

                with self.client.session_transaction() as session:
                    session.pop("_flashes", None)

    def test_contact_audit_records_dispatch_status_without_raw_email(self):
        with patch(
            "app.routes.contact.get_cached_env_settings",
            return_value=self.env,
        ), patch(
            "app.core.decorators.audit_activity_enabled",
            return_value=False,
        ), patch(
            "app.routes.contact.audit_activity_enabled",
            return_value=True,
        ), patch(
            "app.routes.contact.log_action_isolated",
        ) as log_action, patch(
            "app.routes.contact.send_contact_email",
            return_value="failed",
        ):
            response = self.client.post(
                "/contact",
                data=self._form_data(),
            )

        self.assertEqual(response.status_code, 302)
        contact_event = next(
            call
            for call in log_action.call_args_list
            if call.kwargs.get("action") == "contact_attempt"
        )
        extra_data = contact_event.kwargs["extra_data"]
        self.assertEqual(extra_data["status"], "failed")
        self.assertEqual(extra_data["email"], "u***r@example.com")
        self.assertNotIn("User@Example.com", str(extra_data))


class ContactMailerTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    def test_contact_email_uses_admin_recipient_and_complete_templates(self):
        env = SimpleNamespace(admin_email="admin@example.com")

        with self.app.app_context(), patch(
            "app.core.mailer.get_mail_env_settings",
            return_value=env,
        ), patch(
            "app.core.mailer.render_email",
            return_value=("text body", "html body"),
        ) as render, patch(
            "app.core.mailer.send_email",
            return_value="queued",
        ) as send:
            status = send_contact_email(
                name="Example User",
                email="user@example.com",
                subject="  Account\r\nquestion  ",
                message="Please help.",
            )

        self.assertEqual(status, "queued")
        render.assert_called_once_with(
            "contact",
            name="Example User",
            email="user@example.com",
            subject="Account question",
            message="Please help.",
        )
        send.assert_called_once_with(
            "Contact Form: Account question",
            "admin@example.com",
            "text body",
            "html body",
        )

    def test_contact_email_fails_closed_without_admin_recipient(self):
        env = SimpleNamespace(admin_email=None)

        with self.app.app_context(), patch(
            "app.core.mailer.get_mail_env_settings",
            return_value=env,
        ), patch("app.core.mailer.render_email") as render, patch(
            "app.core.mailer.send_email",
        ) as send:
            status = send_contact_email(
                name="Example User",
                email="user@example.com",
                message="Please help.",
            )

        self.assertEqual(status, "failed")
        render.assert_not_called()
        send.assert_not_called()


class MailCliTests(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    def test_mail_test_defaults_to_configured_admin_email(self):
        env = SimpleNamespace(admin_email="admin@example.com")

        with patch(
            "manage.get_mail_env_settings",
            return_value=env,
        ), patch(
            "manage.send_email",
            return_value="queued",
        ) as send:
            result = self.runner.invoke(mail_test)

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Test email queued for admin@example.com.", result.output)
        self.assertEqual(send.call_args.kwargs["recipient"], "admin@example.com")

    def test_mail_test_accepts_recipient_override(self):
        with patch(
            "manage.get_mail_env_settings",
        ) as get_settings, patch(
            "manage.send_email",
            return_value="queued",
        ) as send:
            result = self.runner.invoke(
                mail_test,
                ["--to", "operator@example.com"],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        get_settings.assert_not_called()
        self.assertEqual(send.call_args.kwargs["recipient"], "operator@example.com")

    def test_mail_test_rejects_invalid_recipient(self):
        with patch("manage.send_email") as send:
            result = self.runner.invoke(
                mail_test,
                ["--to", "not-an-email"],
            )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Recipient email address is invalid.", result.output)
        send.assert_not_called()

    def test_mail_test_returns_failure_for_unavailable_delivery(self):
        for status in ("disabled", "failed"):
            with self.subTest(status=status), patch(
                "manage.send_email",
                return_value=status,
            ):
                result = self.runner.invoke(
                    mail_test,
                    ["--to", "operator@example.com"],
                )

            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("Error:", result.output)

    def test_mail_test_requires_default_or_override_recipient(self):
        env = SimpleNamespace(admin_email=None)

        with patch(
            "manage.get_mail_env_settings",
            return_value=env,
        ), patch("manage.send_email") as send:
            result = self.runner.invoke(mail_test)

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Admin Email is not configured", result.output)
        send.assert_not_called()
