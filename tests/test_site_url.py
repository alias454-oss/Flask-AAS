# tests/test_site_url.py
import os
import unittest

os.environ.setdefault("ADMIN_SECRET", "test-admin-secret")
os.environ.setdefault("SQLALCHEMY_DATABASE_URI", "sqlite://")

from flask import Flask, url_for

from app.core.site import normalize_site_url, site_url_flask_config


class SiteUrlTests(unittest.TestCase):
    def test_normalizes_domain_and_trailing_slash(self):
        self.assertEqual(
            normalize_site_url("HTTPS://Example.COM/"),
            "https://example.com",
        )

    def test_accepts_ip_addresses_and_ports(self):
        self.assertEqual(
            normalize_site_url("http://192.168.1.50:8000"),
            "http://192.168.1.50:8000",
        )
        self.assertEqual(
            normalize_site_url("https://[2001:db8::1]:8443/"),
            "https://[2001:db8::1]:8443",
        )

    def test_rejects_non_origin_values(self):
        invalid_values = (
            "ftp://example.com",
            "https://user:pass@example.com",
            "https://example.com/application",
            "https://example.com?next=/admin",
            "https://example.com/#fragment",
        )

        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_site_url(value)

    def test_derives_native_flask_url_and_host_settings(self):
        config = site_url_flask_config("https://example.com:8443")

        self.assertEqual(config["SITE_URL"], "https://example.com:8443")
        self.assertEqual(config["SERVER_NAME"], "example.com:8443")
        self.assertEqual(config["PREFERRED_URL_SCHEME"], "https")
        self.assertEqual(config["TRUSTED_HOSTS"], ["example.com"])

    def test_loopback_site_accepts_common_localhost_names(self):
        config = site_url_flask_config("http://127.0.0.1:5000")

        self.assertEqual(
            config["TRUSTED_HOSTS"],
            ["127.0.0.1", "::1", "localhost"],
        )

    def test_flask_rejects_untrusted_host(self):
        app = Flask(__name__)
        app.config.update(site_url_flask_config("https://example.com"))
        app.add_url_rule("/", "index", lambda: "ok")

        response = app.test_client().get(
            "/",
            base_url="https://attacker.example",
        )

        self.assertEqual(response.status_code, 400)

    def test_external_url_uses_trusted_host_and_configured_scheme(self):
        app = Flask(__name__)
        app.config.update(site_url_flask_config("https://example.com"))
        app.add_url_rule("/token/<token>", "token", lambda token: token)

        with app.test_request_context("/", base_url="http://example.com"):
            generated = url_for(
                "token",
                token="abc123",
                _external=True,
                _scheme=app.config["PREFERRED_URL_SCHEME"],
            )

        self.assertEqual(generated, "https://example.com/token/abc123")


if __name__ == "__main__":
    unittest.main()
