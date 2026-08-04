import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from jinja2 import TemplateNotFound

from app.core.mailer import (
    render_email,
    send_email,
    send_password_reset_email,
)


class MailerTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            MAIL_DEBUG=False,
            TESTING=False,
            MAIL_DEFAULT_SENDER="noreply@example.test",
            REPLY_TO_EMAIL="support@example.test",
        )

    def test_missing_recipient_fails_before_policy_lookup(self):
        with self.app.app_context(), patch(
            "app.core.mailer.get_cached_env_settings"
        ) as get_settings:
            status = send_email("Subject", None, "Body")

        self.assertEqual(status, "failed")
        get_settings.assert_not_called()

    def test_empty_message_fails_before_policy_lookup(self):
        with self.app.app_context(), patch(
            "app.core.mailer.get_cached_env_settings"
        ) as get_settings:
            status = send_email("Subject", "user@example.test", "", "")

        self.assertEqual(status, "failed")
        get_settings.assert_not_called()

    def test_testing_mode_reports_queued_without_starting_a_thread(self):
        self.app.config["TESTING"] = True

        with self.app.app_context(), patch(
            "app.core.mailer.get_cached_env_settings"
        ) as get_settings, patch("app.core.mailer.threading.Thread") as thread:
            status = send_email("Subject", "user@example.test", "Body")

        self.assertEqual(status, "queued")
        get_settings.assert_not_called()
        thread.assert_not_called()

    def test_disabled_smtp_reports_disabled(self):
        settings = SimpleNamespace(use_smtp=False)

        with self.app.app_context(), patch(
            "app.core.mailer.get_cached_env_settings",
            return_value=settings,
        ), patch("app.core.mailer.threading.Thread") as thread:
            status = send_email("Subject", "user@example.test", "Body")

        self.assertEqual(status, "disabled")
        thread.assert_not_called()

    def test_policy_lookup_failure_reports_failed(self):
        with self.app.app_context(), patch(
            "app.core.mailer.get_cached_env_settings",
            side_effect=RuntimeError("database unavailable"),
        ), patch("app.core.mailer.threading.Thread") as thread:
            status = send_email("Subject", "user@example.test", "Body")

        self.assertEqual(status, "failed")
        thread.assert_not_called()

    def test_enabled_smtp_queues_background_delivery(self):
        settings = SimpleNamespace(use_smtp=True)

        with self.app.app_context(), patch(
            "app.core.mailer.get_cached_env_settings",
            return_value=settings,
        ), patch("app.core.mailer.EmailMessage") as email_message, patch(
            "app.core.mailer.threading.Thread"
        ) as thread:
            status = send_email(
                "Subject",
                "user@example.test",
                "Plain body",
                "<p>HTML body</p>",
            )

        self.assertEqual(status, "queued")
        email_message.assert_called_once_with(
            subject="Subject",
            body="Plain body",
            to=["user@example.test"],
            from_email="noreply@example.test",
            reply_to=["support@example.test"],
        )
        email_message.return_value.attach_alternative.assert_called_once_with(
            "<p>HTML body</p>",
            "text/html",
        )
        thread.return_value.start.assert_called_once_with()

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

    def test_password_reset_wrapper_returns_dispatch_status(self):
        with (
            patch(
                "app.core.mailer.url_for",
                return_value=(
                        "https://example.test/"
                        "reset-password/test-token"
                ),
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
            reset_url=(
                "https://example.test/"
                "reset-password/test-token"
            ),
        )

        mock_send.assert_called_once_with(
            "Password Reset Request",
            "user@example.test",
            "Text body",
            "<p>HTML body</p>",
        )


if __name__ == "__main__":
    unittest.main()
