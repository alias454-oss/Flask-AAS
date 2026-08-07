import os
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

from flask import Blueprint, Flask
from flask_login import LoginManager
from sqlalchemy.exc import SQLAlchemyError

from app.core.extensions import bcrypt, cache, db, limiter
from app.core.security import generate_token
from app.models import (
    AuditActivity,
    EnvSettings,
    PasswordResetToken,
    User,
    UserSession,
)
from app.routes.register import EMAIL_VERIFY_SALT, register_bp
from app.routes.reset import reset_bp
from app.routes.verify import verify_bp


class EmailLifecycleRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(cls.temp_dir.name) / "email-lifecycle-tests.db"
        default_uri = f"sqlite:///{database_path}"

        cls.app = Flask(__name__)
        cls.app.config.update(
            TESTING=True,
            SECRET_KEY="email-lifecycle-test-secret",
            SQLALCHEMY_DATABASE_URI=os.environ.get(
                "ACCOUNT_TEST_DATABASE_URI",
                default_uri,
            ),
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            RATELIMIT_ENABLED=False,
            CACHE_TYPE="SimpleCache",
            SERVER_NAME="example.test",
        )

        db.init_app(cls.app)
        bcrypt.init_app(cls.app)
        cache.init_app(cls.app)
        limiter.init_app(cls.app)

        cls.login_manager = LoginManager(cls.app)

        @cls.login_manager.user_loader
        def load_user(session_id):
            return User.load_from_session_id(session_id)

        login_bp = Blueprint("login", __name__)

        @login_bp.route("/login")
        def login():
            return "login"

        dashboard_bp = Blueprint("dashboard", __name__)

        @dashboard_bp.route("/dashboard")
        def dashboard():
            return "dashboard"

        cls.app.register_blueprint(register_bp)
        cls.app.register_blueprint(reset_bp)
        cls.app.register_blueprint(verify_bp)
        cls.app.register_blueprint(login_bp)
        cls.app.register_blueprint(dashboard_bp)

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
        cache.clear()
        EnvSettings._cached_instance = None

        owner = User(
            username="settings-owner",
            email="settings-owner@example.test",
            activated=True,
            approved=True,
        )
        owner.set_password("owner-password")
        db.session.add(owner)
        db.session.flush()

        self.settings = EnvSettings(
            user_id=owner.id,
            site_name="Email Lifecycle Test",
            site_lang="en",
            site_timezone="UTC",
            description="",
            keywords="",
            users_per_page=20,
            users_stored_path="/tmp/users",
            use_captcha=False,
            use_mfa=False,
            use_verify_email=True,
            use_user_approval=False,
            use_user_location=False,
            use_smtp=True,
            visitor_tracking=False,
            enable_logging=True,
        )
        db.session.add(self.settings)
        db.session.commit()
        EnvSettings._cached_instance = None

        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        EnvSettings._cached_instance = None
        self.app_context.pop()

    def _request_patches(self, *, route_audit=False):
        stack = ExitStack()
        stack.enter_context(
            patch("app.core.decorators.audit_activity_enabled", return_value=False)
        )
        stack.enter_context(
            patch("app.routes.register.audit_activity_enabled", return_value=False)
        )
        stack.enter_context(
            patch("app.routes.reset.audit_activity_enabled", return_value=False)
        )
        stack.enter_context(
            patch("app.routes.register.render_template", return_value="register")
        )
        stack.enter_context(
            patch("app.routes.reset.render_template", return_value="reset")
        )
        stack.enter_context(
            patch(
                "app.routes.reset.send_password_changed_email",
                return_value="disabled",
            )
        )
        if not route_audit:
            stack.enter_context(
                patch("app.routes.verify.audit_activity_enabled", return_value=False)
            )
        return stack

    def _register(self, mail_status):
        with self._request_patches(), patch(
            "app.routes.register.send_verification_email",
            return_value=mail_status,
        ) as send_verification:
            response = self.client.post(
                "/register",
                data={
                    "username": f"user-{mail_status}",
                    "email": f"user-{mail_status}@example.com",
                    "password": "correct-horse-battery-staple",
                    "agree": "y",
                    "nobot_check": "",
                },
                follow_redirects=False,
            )
        return response, send_verification

    def _flash_text(self):
        with self.client.session_transaction() as session:
            flashes = session.get("_flashes", [])
        return " ".join(message for _, message in flashes)

    def _save_user(self, email, *, activated=False):
        user = User(
            username=email.split("@", 1)[0],
            email=email,
            activated=activated,
            approved=True,
        )
        user.set_password("test-password")
        db.session.add(user)
        db.session.commit()
        return user

    def test_registration_queues_verification_using_registered_endpoint(self):
        response, send_verification = self._register("queued")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlparse(response.location).path, "/login")
        user = User.query.filter_by(email="user-queued@example.com").one()
        self.assertFalse(user.activated)

        send_verification.assert_called_once()
        verify_url = send_verification.call_args.args[2]
        self.assertTrue(urlparse(verify_url).path.startswith("/email/"))
        self.assertIn("Check your email shortly", self._flash_text())

    def test_required_verification_blocks_registration_without_outbound_email(self):
        self.settings.use_smtp = False
        db.session.commit()
        EnvSettings._cached_instance = None

        response, send_verification = self._register("queued")

        self.assertEqual(response.status_code, 200)
        send_verification.assert_not_called()
        self.assertIsNone(
            User.query.filter_by(email="user-queued@example.com").first()
        )
        self.assertIn("temporarily unavailable", self._flash_text())

    def test_registration_reports_disabled_or_failed_dispatch_without_removing_user(self):
        expectations = {
            "disabled": "email verification is currently unavailable",
            "failed": "verification email could not be queued",
        }

        for status, message in expectations.items():
            with self.subTest(status=status):
                response, _ = self._register(status)
                self.assertEqual(response.status_code, 302)
                self.assertIsNotNone(
                    User.query.filter_by(
                        email=f"user-{status}@example.com"
                    ).first()
                )
                self.assertIn(message, self._flash_text())

    def test_forgot_password_preserves_generic_response_for_all_dispatch_results(self):
        user = self._save_user("reset-user@example.com")
        expected_public_message = (
            "If your email is registered, you’ll receive a password reset link."
        )

        for status in ("queued", "disabled", "failed"):
            with self.subTest(status=status):
                with self._request_patches(), patch(
                    "app.routes.reset.send_password_reset_email",
                    return_value=status,
                ) as send_reset, patch(
                    "app.routes.reset.logger"
                ) as route_logger:
                    response = self.client.post(
                        "/forgot-password",
                        data={"email": user.email},
                        follow_redirects=False,
                    )

                self.assertEqual(response.status_code, 302)
                self.assertEqual(urlparse(response.location).path, "/login")
                send_reset.assert_called_once()
                plaintext_token = send_reset.call_args.args[1]
                stored_token = PasswordResetToken.query.filter_by(
                    token_hash=PasswordResetToken.hash_token(plaintext_token)
                ).one()
                self.assertNotEqual(stored_token.token_hash, plaintext_token)
                self.assertIn(expected_public_message, self._flash_text())

                if status == "queued":
                    self.assertIsNone(stored_token.revoked_at)
                    route_logger.info.assert_called_once()
                elif status == "disabled":
                    self.assertIsNotNone(stored_token.revoked_at)
                    route_logger.warning.assert_called_once()
                else:
                    self.assertIsNotNone(stored_token.revoked_at)
                    route_logger.error.assert_called_once()
                cache.clear()

    def test_new_reset_request_does_not_revoke_older_active_token(self):
        user = self._save_user("new-reset@example.com")

        with self._request_patches(), patch(
            "app.routes.reset.send_password_reset_email",
            return_value="queued",
        ) as first_send:
            self.client.post("/forgot-password", data={"email": user.email})
        first_plaintext = first_send.call_args.args[1]

        with self._request_patches(), patch(
            "app.routes.reset.send_password_reset_email",
            return_value="queued",
        ) as second_send:
            self.client.post("/forgot-password", data={"email": user.email})
        second_plaintext = second_send.call_args.args[1]

        first = PasswordResetToken.query.filter_by(
            token_hash=PasswordResetToken.hash_token(first_plaintext)
        ).one()
        second = PasswordResetToken.query.filter_by(
            token_hash=PasswordResetToken.hash_token(second_plaintext)
        ).one()
        self.assertIsNone(first.revoked_at)
        self.assertIsNone(first.consumed_at)
        self.assertIsNone(second.revoked_at)
        self.assertIsNone(second.consumed_at)

    def test_reset_token_is_single_use_and_rotates_session_identity(self):
        user = self._save_user("single-use-reset@example.com")
        old_session_id = user.get_id()
        token_record, plaintext_token = PasswordResetToken.issue_for_user(user)
        other_token, _ = PasswordResetToken.issue_for_user(user)
        active_session = UserSession.issue_for_user(
            user,
            ip_address='192.0.2.50',
            user_agent='reset-session-agent',
            remembered=True,
        )
        db.session.commit()

        with self._request_patches(), patch(
            "app.routes.reset.send_password_changed_email",
            return_value="queued",
        ) as send_changed:
            response = self.client.post(
                f"/reset-password/{plaintext_token}",
                data={
                    "password": "new-secure-password",
                    "confirm": "new-secure-password",
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlparse(response.location).path, "/login")
        db.session.expire_all()
        stored_user = db.session.get(User, user.id)
        stored_token = db.session.get(PasswordResetToken, token_record.id)
        stored_other_token = db.session.get(PasswordResetToken, other_token.id)
        self.assertTrue(stored_user.check_password("new-secure-password"))
        self.assertNotEqual(stored_user.get_id(), old_session_id)
        self.assertIsNone(User.load_from_session_id(old_session_id))
        self.assertIsNotNone(stored_token.consumed_at)
        self.assertIsNotNone(stored_other_token.revoked_at)
        self.assertIsNotNone(
            db.session.get(UserSession, active_session.id).revoked_at
        )
        send_changed.assert_called_once_with(stored_user.email, stored_user.username)

        with self._request_patches():
            replay = self.client.get(
                f"/reset-password/{plaintext_token}",
                follow_redirects=False,
            )
        self.assertEqual(replay.status_code, 302)
        self.assertEqual(urlparse(replay.location).path, "/forgot-password")

    def test_expired_and_revoked_reset_tokens_fail_safely(self):
        user = self._save_user("invalid-reset@example.com")
        now = datetime.now(timezone.utc)
        expired_plaintext = "expired-reset-token"
        revoked_plaintext = "revoked-reset-token"
        db.session.add_all([
            PasswordResetToken(
                user_id=user.id,
                token_hash=PasswordResetToken.hash_token(expired_plaintext),
                expires_at=now - timedelta(minutes=1),
            ),
            PasswordResetToken(
                user_id=user.id,
                token_hash=PasswordResetToken.hash_token(revoked_plaintext),
                expires_at=now + timedelta(hours=1),
                revoked_at=now,
            ),
        ])
        db.session.commit()

        for plaintext_token in (expired_plaintext, revoked_plaintext):
            with self.subTest(token=plaintext_token), self._request_patches():
                response = self.client.get(
                    f"/reset-password/{plaintext_token}",
                    follow_redirects=False,
                )
            self.assertEqual(response.status_code, 302)
            self.assertEqual(urlparse(response.location).path, "/forgot-password")

    def test_reset_commit_failure_preserves_password_and_token(self):
        user = self._save_user("reset-rollback@example.com")
        old_auth_version = user.auth_version
        token_record, plaintext_token = PasswordResetToken.issue_for_user(user)
        active_session = UserSession.issue_for_user(
            user,
            ip_address='192.0.2.51',
            user_agent='reset-rollback-agent',
        )
        db.session.commit()

        with self._request_patches(), patch.object(
            db.session,
            "commit",
            side_effect=SQLAlchemyError("database unavailable"),
        ), patch(
            "app.routes.reset.send_password_changed_email"
        ) as send_changed:
            response = self.client.post(
                f"/reset-password/{plaintext_token}",
                data={
                    "password": "new-secure-password",
                    "confirm": "new-secure-password",
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlparse(response.location).path, "/forgot-password")
        db.session.expire_all()
        stored_user = db.session.get(User, user.id)
        stored_token = db.session.get(PasswordResetToken, token_record.id)
        self.assertTrue(stored_user.check_password("test-password"))
        self.assertEqual(stored_user.auth_version, old_auth_version)
        self.assertIsNone(stored_token.consumed_at)
        self.assertIsNone(stored_token.revoked_at)
        self.assertIsNone(
            db.session.get(UserSession, active_session.id).revoked_at
        )
        send_changed.assert_not_called()

    def test_user_delete_removes_reset_tokens_on_sqlite(self):
        user = self._save_user("delete-reset-user@example.com")
        PasswordResetToken.issue_for_user(user)
        UserSession.issue_for_user(
            user,
            ip_address='192.0.2.52',
            user_agent='delete-session-agent',
        )
        db.session.commit()
        user_id = user.id

        db.session.expire(user, ["password_reset_tokens"])
        db.session.delete(user)
        db.session.commit()

        self.assertIsNone(db.session.get(User, user_id))
        self.assertEqual(
            PasswordResetToken.query.filter_by(user_id=user_id).count(),
            0,
        )
        self.assertEqual(
            UserSession.query.filter_by(user_id=user_id).count(),
            0,
        )

    def test_valid_verification_activates_user_and_commits_audit_event(self):
        user = self._save_user("verify-user@example.test")
        token = generate_token(user.email, EMAIL_VERIFY_SALT)

        with self._request_patches(route_audit=True):
            response = self.client.get(f"/email/{token}", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        db.session.refresh(user)
        self.assertTrue(user.activated)
        event = AuditActivity.query.filter_by(action="email_verified").one()
        self.assertEqual(event.user_id, user.id)
        self.assertNotIn(token, event.target)

    def test_repeated_verification_is_idempotent(self):
        user = self._save_user("already-verified@example.test", activated=True)
        token = generate_token(user.email, EMAIL_VERIFY_SALT)

        with self._request_patches():
            response = self.client.get(f"/email/{token}", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            AuditActivity.query.filter_by(action="email_verified").count(),
            0,
        )
        self.assertIn("already verified", self._flash_text())

    def test_missing_user_verification_uses_generic_failure(self):
        token = generate_token("missing@example.test", EMAIL_VERIFY_SALT)

        with self._request_patches():
            response = self.client.get(f"/email/{token}", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        flash_text = self._flash_text()
        self.assertIn("invalid or has expired", flash_text)
        self.assertNotIn("No user found", flash_text)

    def test_invalid_verification_token_fails_safely(self):
        with self._request_patches(), patch(
            "app.routes.verify.confirm_token",
            return_value=None,
        ):
            response = self.client.get(
                "/email/not-a-valid-token",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("invalid or has expired", self._flash_text())

    def test_verification_commit_failure_rolls_back_activation(self):
        user = self._save_user("rollback@example.test")
        token = generate_token(user.email, EMAIL_VERIFY_SALT)

        with self._request_patches(), patch.object(
            db.session,
            "commit",
            side_effect=SQLAlchemyError("database unavailable"),
        ):
            response = self.client.get(f"/email/{token}", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        user = db.session.get(User, user.id)
        self.assertFalse(user.activated)
        self.assertIn("could not verify your email", self._flash_text())


if __name__ == "__main__":
    unittest.main()
