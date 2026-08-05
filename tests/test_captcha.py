# tests/test_captcha.py
import unittest
from unittest.mock import patch

from flask import Flask, jsonify, request

from app.core.extensions import cache, limiter
from app.routes import captcha as captcha_module


class CaptchaStateTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="captcha-test-secret",
            CACHE_TYPE="SimpleCache",
            CACHE_DEFAULT_TIMEOUT=300,
            RATELIMIT_ENABLED=False,
        )
        cache.init_app(self.app)
        limiter.init_app(self.app)
        self.app.register_blueprint(captcha_module.captcha_bp)

        @self.app.post("/test-captcha-validation")
        def test_captcha_validation():
            valid, message = captcha_module.validate_captcha(
                request.form.get("answer")
            )
            return jsonify(valid=valid, message=message)

        self.client = self.app.test_client()
        self.enabled_patch = patch.object(
            captcha_module,
            "is_captcha_enabled",
            return_value=True,
        )
        self.enabled_patch.start()
        self.audit_patch = patch(
            "app.core.decorators.audit_activity_enabled",
            return_value=False,
        )
        self.audit_patch.start()

    def tearDown(self):
        self.audit_patch.stop()
        self.enabled_patch.stop()
        with self.app.app_context():
            cache.clear()

    def _generate(self, answer="AbC234", timestamp=100.0):
        with (
            patch.object(
                captcha_module,
                "generate_captcha_text",
                return_value=answer,
            ),
            patch.object(
                captcha_module,
                "_current_timestamp",
                return_value=timestamp,
            ),
        ):
            response = self.client.get("/captcha_image")
        self.assertEqual(response.status_code, 200)
        return response

    def _session_payload(self):
        cookie_name = self.app.config.get("SESSION_COOKIE_NAME", "session")
        cookie = self.client.get_cookie(cookie_name)
        self.assertIsNotNone(cookie)
        serializer = self.app.session_interface.get_signing_serializer(self.app)
        return serializer.loads(cookie.value)

    def _challenge_id(self):
        with self.client.session_transaction() as captcha_session:
            return captcha_session.get(captcha_module.CAPTCHA_SESSION_KEY)

    def _challenge(self, challenge_id=None):
        challenge_id = challenge_id or self._challenge_id()
        with self.app.app_context():
            return cache.get(captcha_module._captcha_cache_key(challenge_id))

    def _validate(self, answer, timestamp=101.0):
        with patch.object(
            captcha_module,
            "_current_timestamp",
            return_value=timestamp,
        ):
            return self.client.post(
                "/test-captcha-validation",
                data={"answer": answer},
            ).get_json()

    def test_image_uses_packaged_font_and_returns_png(self):
        response = self._generate()

        self.assertEqual(response.mimetype, "image/png")
        self.assertTrue(response.data.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_session_contains_only_opaque_challenge_id(self):
        answer = "Readable7"
        self._generate(answer=answer)

        payload = self._session_payload()

        self.assertIn(captcha_module.CAPTCHA_SESSION_KEY, payload)
        self.assertNotIn("captcha_code", payload)
        self.assertNotIn("captcha_expiry", payload)
        self.assertNotIn("captcha_attempts", payload)
        self.assertNotIn(answer, repr(payload))

        challenge = self._challenge(payload[captcha_module.CAPTCHA_SESSION_KEY])
        self.assertIsInstance(challenge, dict)
        self.assertNotEqual(challenge["answer_hash"], answer)
        self.assertNotIn(answer, repr(challenge))
        self.assertEqual(challenge["attempts"], 0)

    def test_correct_answer_is_case_insensitive_and_single_use(self):
        self._generate(answer="AbC234")
        challenge_id = self._challenge_id()

        accepted = self._validate("aBc234")
        replayed = self._validate("aBc234")

        self.assertTrue(accepted["valid"])
        self.assertEqual(accepted["message"], "")
        self.assertFalse(replayed["valid"])
        self.assertIn("missing", replayed["message"].lower())
        self.assertIsNone(self._challenge(challenge_id))
        self.assertIsNone(self._challenge_id())

    def test_incorrect_answers_are_bounded_and_invalidate_challenge(self):
        self._generate(answer="AbC234")
        challenge_id = self._challenge_id()

        first = self._validate("wrong1")
        second = self._validate("wrong2")
        third = self._validate("wrong3")

        self.assertFalse(first["valid"])
        self.assertIn("incorrect", first["message"].lower())
        self.assertFalse(second["valid"])
        self.assertIn("incorrect", second["message"].lower())
        self.assertFalse(third["valid"])
        self.assertIn("too many", third["message"].lower())
        self.assertIsNone(self._challenge(challenge_id))
        self.assertIsNone(self._challenge_id())

    def test_expired_challenge_is_deleted(self):
        self._generate(answer="AbC234", timestamp=100.0)
        challenge_id = self._challenge_id()

        result = self._validate("AbC234", timestamp=400.0)

        self.assertFalse(result["valid"])
        self.assertIn("expired", result["message"].lower())
        self.assertIsNone(self._challenge(challenge_id))
        self.assertIsNone(self._challenge_id())

    def test_reloading_replaces_and_deletes_previous_challenge(self):
        with patch.object(
            captcha_module.secrets,
            "token_urlsafe",
            side_effect=("first-challenge", "second-challenge"),
        ):
            self._generate(answer="First1")
            first_id = self._challenge_id()
            self._generate(answer="Second2")
            second_id = self._challenge_id()

        self.assertEqual(first_id, "first-challenge")
        self.assertEqual(second_id, "second-challenge")
        self.assertIsNone(self._challenge(first_id))
        self.assertIsNotNone(self._challenge(second_id))

    def test_disabled_captcha_creates_no_state(self):
        with patch.object(
            captcha_module,
            "is_captcha_enabled",
            return_value=False,
        ):
            response = self.client.get("/captcha_image")

        self.assertEqual(response.status_code, 404)
        self.assertIsNone(self._challenge_id())

    def test_cache_write_failure_does_not_issue_challenge(self):
        with (
            patch.object(
                captcha_module,
                "generate_captcha_text",
                return_value="AbC234",
            ),
            patch.object(
                captcha_module,
                "generate_captcha_image",
                return_value=b"png",
            ),
            patch.object(captcha_module.cache, "set", return_value=False),
        ):
            response = self.client.get("/captcha_image")

        self.assertEqual(response.status_code, 503)
        self.assertIsNone(self._challenge_id())


if __name__ == "__main__":
    unittest.main()
