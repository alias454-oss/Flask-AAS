import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.fernet import Fernet
from flask import Flask
from jinja2 import TemplateNotFound

from app.core.mailer import (
    MailConfigurationError,
    decrypt_smtp_password,
    encrypt_smtp_password,
    get_mail_configuration_state,
    render_email,
    send_email,
    send_password_changed_email,
    send_password_reset_email,
)


class MailerTests(unittest.TestCase):
    def setUp(self):
        self.encryption_key = Fernet.generate_key().decode("utf-8")
        self.app = Flask(__name__)
        self.app.config.update(
            MAIL_DEBUG=False,
            TESTING=False,
            MAIL_CONFIG_UI_ENABLED=False,
            MAIL_CONFIG_ENCRYPTION_KEY=self.encryption_key,
            MAIL_SERVER="smtp.environment.test",
            MAIL_PORT=587,
            MAIL_USE_TLS=True,
            MAIL_USE_SSL=False,
            MAIL_USERNAME="environment-user",
            MAIL_PASSWORD="environment-password",
            MAIL_DEFAULT_SENDER="noreply@example.test",
            REPLY_TO_EMAIL="support@example.test",
        )

    @staticmethod
    def _settings(**overrides):
        values = {
            "use_smtp": True,
            "smtp_host": None,
            "smtp_port": 587,
            "smtp_security": "starttls",
            "smtp_user": None,
            "smtp_pass": None,
            "smtp_default_sender": None,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_missing_recipient_fails_before_policy_lookup(self):
        with self.app.app_context(), patch(
            "app.core.mailer.get_mail_env_settings"
        ) as get_settings:
            status = send_email("Subject", None, "Body")

        self.assertEqual(status, "failed")
        get_settings.assert_not_called()

    def test_empty_message_fails_before_policy_lookup(self):
        with self.app.app_context(), patch(
            "app.core.mailer.get_mail_env_settings"
        ) as get_settings:
            status = send_email("Subject", "user@example.test", "", "")

        self.assertEqual(status, "failed")
        get_settings.assert_not_called()

    def test_master_switch_disables_debug_delivery(self):
        self.app.config["MAIL_DEBUG"] = True

        with self.app.app_context(), patch(
            "app.core.mailer.get_mail_env_settings",
            return_value=self._settings(use_smtp=False),
        ), patch("app.core.mailer.threading.Thread") as thread:
            status = send_email("Subject", "user@example.test", "Body")

        self.assertEqual(status, "disabled")
        thread.assert_not_called()

    def test_debug_mode_reports_queued_without_starting_a_thread(self):
        self.app.config["MAIL_DEBUG"] = True

        with self.app.app_context(), patch(
            "app.core.mailer.get_mail_env_settings",
            return_value=self._settings(),
        ), patch("app.core.mailer.threading.Thread") as thread:
            status = send_email("Subject", "user@example.test", "Body")

        self.assertEqual(status, "queued")
        thread.assert_not_called()

    def test_disabled_outbound_email_reports_disabled(self):
        with self.app.app_context(), patch(
            "app.core.mailer.get_mail_env_settings",
            return_value=self._settings(use_smtp=False),
        ), patch("app.core.mailer.threading.Thread") as thread:
            status = send_email("Subject", "user@example.test", "Body")

        self.assertEqual(status, "disabled")
        thread.assert_not_called()

    def test_policy_lookup_failure_reports_failed(self):
        with self.app.app_context(), patch(
            "app.core.mailer.get_mail_env_settings",
            side_effect=RuntimeError("database unavailable"),
        ), patch("app.core.mailer.threading.Thread") as thread:
            status = send_email("Subject", "user@example.test", "Body")

        self.assertEqual(status, "failed")
        thread.assert_not_called()

    def test_environment_configuration_queues_background_delivery(self):
        with self.app.app_context(), patch(
            "app.core.mailer.get_mail_env_settings",
            return_value=self._settings(),
        ), patch("app.core.mailer.EmailBackend") as backend, patch(
            "app.core.mailer.EmailMultiAlternatives"
        ) as email_message, patch("app.core.mailer.threading.Thread") as thread:
            status = send_email(
                "Subject",
                "user@example.test",
                "Plain body",
                "<p>HTML body</p>",
            )

        self.assertEqual(status, "queued")
        backend.assert_called_once_with(
            host="smtp.environment.test",
            port=587,
            username="environment-user",
            password="environment-password",
            use_tls=True,
            use_ssl=False,
            fail_silently=False,
        )
        email_message.assert_called_once_with(
            subject="Subject",
            body="Plain body",
            to=["user@example.test"],
            from_email="noreply@example.test",
            reply_to=["support@example.test"],
            connection=backend.return_value,
        )
        email_message.return_value.attach_alternative.assert_called_once_with(
            "<p>HTML body</p>",
            "text/html",
        )
        thread.return_value.start.assert_called_once_with()

    def test_complete_database_override_supersedes_environment(self):
        self.app.config["MAIL_CONFIG_UI_ENABLED"] = True

        with self.app.app_context():
            encrypted_password = encrypt_smtp_password("database-password")

        settings = self._settings(
            smtp_host="smtp.database.test",
            smtp_port=465,
            smtp_security="ssl",
            smtp_user="database-user",
            smtp_pass=encrypted_password,
            smtp_default_sender="database-sender@example.test",
        )

        with self.app.app_context(), patch(
            "app.core.mailer.get_mail_env_settings",
            return_value=settings,
        ), patch("app.core.mailer.EmailBackend") as backend, patch(
            "app.core.mailer.EmailMultiAlternatives"
        ), patch("app.core.mailer.threading.Thread") as thread:
            status = send_email("Subject", "user@example.test", "Body")

        self.assertEqual(status, "queued")
        backend.assert_called_once_with(
            host="smtp.database.test",
            port=465,
            username="database-user",
            password="database-password",
            use_tls=False,
            use_ssl=True,
            fail_silently=False,
        )
        thread.return_value.start.assert_called_once_with()

    def test_database_override_supports_unauthenticated_relay(self):
        self.app.config["MAIL_CONFIG_UI_ENABLED"] = True
        settings = self._settings(
            smtp_host="smtp.relay.test",
            smtp_port=25,
            smtp_security="none",
            smtp_default_sender="relay-sender@example.test",
        )

        with self.app.app_context(), patch(
            "app.core.mailer.get_mail_env_settings",
            return_value=settings,
        ), patch("app.core.mailer.EmailBackend") as backend, patch(
            "app.core.mailer.EmailMultiAlternatives"
        ), patch("app.core.mailer.threading.Thread"):
            status = send_email("Subject", "user@example.test", "Body")

        self.assertEqual(status, "queued")
        backend.assert_called_once_with(
            host="smtp.relay.test",
            port=25,
            username=None,
            password=None,
            use_tls=False,
            use_ssl=False,
            fail_silently=False,
        )

    def test_ui_disabled_ignores_saved_database_override(self):
        settings = self._settings(
            smtp_host="smtp.database.test",
            smtp_port=465,
            smtp_security="ssl",
            smtp_user="database-user",
            smtp_pass="not-used-while-ui-is-disabled",
            smtp_default_sender="database-sender@example.test",
        )

        with self.app.app_context(), patch(
            "app.core.mailer.get_mail_env_settings",
            return_value=settings,
        ), patch("app.core.mailer.EmailBackend") as backend, patch(
            "app.core.mailer.EmailMultiAlternatives"
        ), patch("app.core.mailer.threading.Thread"):
            status = send_email("Subject", "user@example.test", "Body")

        self.assertEqual(status, "queued")
        self.assertEqual(backend.call_args.kwargs["host"], "smtp.environment.test")

    def test_invalid_database_override_falls_back_to_environment(self):
        self.app.config["MAIL_CONFIG_UI_ENABLED"] = True
        settings = self._settings(smtp_host="smtp.partial.test")

        with self.app.app_context(), patch(
            "app.core.mailer.get_mail_env_settings",
            return_value=settings,
        ), patch("app.core.mailer.EmailBackend") as backend, patch(
            "app.core.mailer.EmailMultiAlternatives"
        ), patch("app.core.mailer.threading.Thread"):
            status = send_email("Subject", "user@example.test", "Body")

        self.assertEqual(status, "queued")
        self.assertEqual(backend.call_args.kwargs["host"], "smtp.environment.test")

    def test_no_complete_source_reports_disabled(self):
        self.app.config.update(
            MAIL_SERVER=None,
            MAIL_USERNAME=None,
            MAIL_PASSWORD=None,
            MAIL_DEFAULT_SENDER=None,
        )

        with self.app.app_context(), patch(
            "app.core.mailer.get_mail_env_settings",
            return_value=self._settings(),
        ), patch("app.core.mailer.threading.Thread") as thread:
            status = send_email("Subject", "user@example.test", "Body")

        self.assertEqual(status, "disabled")
        thread.assert_not_called()

    def test_mail_configuration_state_reports_database_source(self):
        self.app.config["MAIL_CONFIG_UI_ENABLED"] = True
        with self.app.app_context():
            encrypted_password = encrypt_smtp_password("database-password")
            state = get_mail_configuration_state(
                self._settings(
                    smtp_host="smtp.database.test",
                    smtp_port=587,
                    smtp_security="starttls",
                    smtp_user="database-user",
                    smtp_pass=encrypted_password,
                    smtp_default_sender="sender@example.test",
                )
            )

        self.assertTrue(state.available)
        self.assertEqual(state.source, "database")
        self.assertEqual(state.source_label, "Site Settings")

    def test_smtp_password_round_trip_uses_environment_key(self):
        with self.app.app_context():
            encrypted = encrypt_smtp_password("secret-password")
            decrypted = decrypt_smtp_password(encrypted)

        self.assertNotEqual(encrypted, "secret-password")
        self.assertEqual(decrypted, "secret-password")

    def test_invalid_encryption_key_rejects_password_storage(self):
        self.app.config["MAIL_CONFIG_ENCRYPTION_KEY"] = "not-a-fernet-key"

        with self.app.app_context(), self.assertRaises(MailConfigurationError):
            encrypt_smtp_password("secret-password")

    def test_render_email_uses_html_and_text_directories(self):
        with self.app.app_context(), patch(
            "app.core.mailer.render_template",
            side_effect=["<p>HTML</p>", "Plain text"],
        ) as render:
            text, html = render_email("welcome", username="example")

        self.assertEqual(text, "Plain text")
        self.assertEqual(html, "<p>HTML</p>")
        self.assertEqual(
            render.call_args_list[0].args[0],
            "emails/html/welcome.html",
        )
        self.assertEqual(
            render.call_args_list[1].args[0],
            "emails/txt/welcome.txt",
        )

    def test_missing_html_template_returns_empty_bodies(self):
        with self.app.app_context(), patch(
            "app.core.mailer.render_template",
            side_effect=TemplateNotFound("missing"),
        ):
            text, html = render_email("missing")

        self.assertEqual((text, html), ("", ""))

    def test_missing_text_template_uses_fallback(self):
        with self.app.app_context(), patch(
            "app.core.mailer.render_template",
            side_effect=["<p>HTML</p>", TemplateNotFound("missing")],
        ):
            text, html = render_email("welcome")

        self.assertEqual(
            text,
            "Please view this email in an HTML-compatible client.",
        )
        self.assertEqual(html, "<p>HTML</p>")

    def test_password_changed_wrapper_returns_dispatch_status(self):
        self.app.config["SITE_NAME"] = "Example Site"
        with self.app.app_context(), patch(
            "app.core.mailer.render_email",
            return_value=("Text body", "<p>HTML body</p>"),
        ) as mock_render, patch(
            "app.core.mailer.send_email",
            return_value="queued",
        ) as mock_send:
            status = send_password_changed_email(
                "user@example.test",
                "example-user",
            )

        self.assertEqual(status, "queued")
        mock_render.assert_called_once_with(
            "password_changed",
            username="example-user",
        )
        mock_send.assert_called_once_with(
            "Password changed for Example Site",
            "user@example.test",
            "Text body",
            "<p>HTML body</p>",
        )

    def test_password_reset_wrapper_returns_dispatch_status(self):
        with (
            patch(
                "app.core.mailer.url_for",
                return_value="https://example.test/reset-password/test-token",
            ) as mock_url_for,
            patch(
                "app.core.mailer.render_email",
                return_value=("Text body", "<p>HTML body</p>"),
            ) as mock_render,
            patch(
                "app.core.mailer.send_email",
                return_value="queued",
            ) as mock_send,
        ):
            status = send_password_reset_email(
                "user@example.test",
                "test-token",
            )

        self.assertEqual(status, "queued")
        mock_url_for.assert_called_once_with(
            "reset.reset_password",
            token="test-token",
            _external=True,
        )
        mock_render.assert_called_once_with(
            "reset_password",
            reset_url="https://example.test/reset-password/test-token",
        )
        mock_send.assert_called_once_with(
            "Password Reset Request",
            "user@example.test",
            "Text body",
            "<p>HTML body</p>",
        )


if __name__ == "__main__":
    unittest.main()
