"""Regression coverage for administrative account-eligibility invalidation."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Blueprint, Flask, request
from flask_login import LoginManager, login_user
from sqlalchemy.exc import SQLAlchemyError

from app.core.decorators import login_required
from app.core.extensions import csrf, db, limiter
from app.models import EnvSettings, Role, User, UserSession
from app.routes.admin.users import users_bp


class AdminUserEligibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(cls.temp_dir.name) / "admin-user-eligibility-tests.db"

        cls.app = Flask(__name__)
        cls.app.config.update(
            TESTING=True,
            SECRET_KEY="admin-user-eligibility-test-secret",
            SQLALCHEMY_DATABASE_URI=os.environ.get(
                "ADMIN_USER_ELIGIBILITY_TEST_DATABASE_URI",
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

        @login_bp.route("/test-login/<int:user_id>")
        def test_login(user_id):
            user = db.session.get(User, user_id)
            remembered = request.args.get("remember") == "1"
            UserSession.issue_for_user(
                user,
                remembered=remembered,
                ip_address="127.0.0.1",
                user_agent="admin-user-eligibility-test-agent",
            )
            db.session.commit()
            login_user(user, remember=remembered, fresh=True)
            return "logged-in"

        index_bp = Blueprint("index", __name__)

        @index_bp.route("/")
        def index():
            return "index"

        protected_bp = Blueprint("protected", __name__)

        @protected_bp.route("/protected")
        @login_required
        def protected():
            return "protected"

        cls.app.register_blueprint(login_bp)
        cls.app.register_blueprint(index_bp)
        cls.app.register_blueprint(protected_bp)
        cls.app.register_blueprint(users_bp)

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.create_all()
            EnvSettings._cached_instance = None
            Role._cached_instance = None

            admin_role = Role(name="admin")
            user_role = Role(name="user")
            db.session.add_all([admin_role, user_role])
            db.session.flush()

            admin = User(
                username="eligibility-admin",
                email="eligibility-admin@example.com",
                activated=True,
                approved=True,
            )
            admin.set_password("admin-password")
            admin.roles.append(admin_role)
            db.session.add(admin)
            db.session.flush()

            settings = EnvSettings(
                user_id=admin.id,
                site_name="Eligibility Test",
                site_lang="en",
                site_timezone="UTC",
                description="",
                keywords="",
                users_per_page=20,
                users_stored_path="/tmp/users",
                use_mfa=False,
                use_verify_email=True,
                use_user_approval=True,
                use_user_location=False,
                use_captcha=False,
                contact_enabled=False,
                visitor_tracking=False,
                enable_logging=False,
            )
            db.session.add(settings)

            target = User(
                username="eligibility-target",
                email="eligibility-target@example.com",
                activated=True,
                approved=True,
            )
            target.set_password("target-password")
            target.roles.append(user_role)
            db.session.add(target)
            db.session.commit()

            self.admin_id = admin.id
            self.target_id = target.id
            self.user_role_id = user_role.id

            EnvSettings._cached_instance = None
            Role._cached_instance = None

        self.admin_client = self.app.test_client()
        self.normal_client = self.app.test_client()
        self.remembered_client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            EnvSettings._cached_instance = None
            Role._cached_instance = None

    def _login_client(self, client, user_id, *, remember=False):
        response = client.get(
            f"/test-login/{user_id}",
            query_string={"remember": "1" if remember else "0"},
        )
        self.assertEqual(response.status_code, 200)
        with client.session_transaction() as browser_session:
            return browser_session.get("_user_id")

    def _edit_target(self, *, approved, activated=True):
        data = {
            "username": "eligibility-target",
            "email": "eligibility-target@example.com",
            "roles": [str(self.user_role_id)],
        }
        if activated:
            data["activated"] = "y"
        if approved:
            data["approved"] = "y"

        return self.admin_client.post(
            f"/admin/users/{self.target_id}/edit",
            data=data,
            follow_redirects=False,
        )

    def test_loader_rechecks_current_eligibility_without_relying_on_revocation(self):
        with self.app.app_context():
            settings = EnvSettings.get_instance()
            settings.use_user_approval = False
            target = db.session.get(User, self.target_id)
            target.approved = False
            db.session.commit()
            EnvSettings._cached_instance = None

        identity = self._login_client(self.normal_client, self.target_id)

        with self.app.app_context():
            self.assertIsNotNone(User.load_from_session_id(identity))
            settings = EnvSettings.get_instance()
            settings.use_user_approval = True
            db.session.commit()
            EnvSettings._cached_instance = None
            self.assertIsNone(User.load_from_session_id(identity))

    def test_withdrawing_required_approval_revokes_normal_and_remembered_sessions(self):
        normal_identity = self._login_client(self.normal_client, self.target_id)
        remembered_identity = self._login_client(
            self.remembered_client,
            self.target_id,
            remember=True,
        )
        remember_cookie_name = self.app.config.get(
            "REMEMBER_COOKIE_NAME",
            "remember_token",
        )
        self.assertIsNotNone(self.remembered_client.get_cookie(remember_cookie_name))

        with self.app.app_context():
            target = db.session.get(User, self.target_id)
            prior_auth_version = target.auth_version
            session_ids = [
                row.id for row in UserSession.active_for_user(self.target_id)
            ]
            self.assertEqual(len(session_ids), 2)

        self._login_client(self.admin_client, self.admin_id)
        response = self._edit_target(approved=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/admin/users/")

        with self.app.app_context():
            target = db.session.get(User, self.target_id)
            self.assertFalse(target.approved)
            self.assertEqual(target.auth_version, prior_auth_version + 1)
            for session_id in session_ids:
                self.assertIsNotNone(
                    db.session.get(UserSession, session_id).revoked_at
                )
            self.assertIsNone(User.load_from_session_id(normal_identity))
            self.assertIsNone(User.load_from_session_id(remembered_identity))

        normal_response = self.normal_client.get("/protected", follow_redirects=False)
        self.assertEqual(normal_response.status_code, 302)
        self.assertTrue(normal_response.location.startswith("/login"))

        # Force remember-cookie restoration by removing only Flask's session cookie.
        self.remembered_client.delete_cookie(
            self.app.config.get("SESSION_COOKIE_NAME", "session")
        )
        remembered_response = self.remembered_client.get(
            "/protected",
            follow_redirects=False,
        )
        self.assertEqual(remembered_response.status_code, 302)
        self.assertTrue(remembered_response.location.startswith("/login"))

        # Reapproval must not resurrect either pre-withdrawal identity.
        response = self._edit_target(approved=True)
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertTrue(db.session.get(User, self.target_id).approved)
            self.assertIsNone(User.load_from_session_id(normal_identity))
            self.assertIsNone(User.load_from_session_id(remembered_identity))

        self.remembered_client.delete_cookie(
            self.app.config.get("SESSION_COOKIE_NAME", "session")
        )
        replay_response = self.remembered_client.get(
            "/protected",
            follow_redirects=False,
        )
        self.assertEqual(replay_response.status_code, 302)
        self.assertTrue(replay_response.location.startswith("/login"))

    def test_withdrawing_required_activation_revokes_existing_sessions(self):
        identity = self._login_client(self.normal_client, self.target_id)
        with self.app.app_context():
            session_id = UserSession.active_for_user(self.target_id)[0].id
            prior_auth_version = db.session.get(User, self.target_id).auth_version

        self._login_client(self.admin_client, self.admin_id)
        response = self._edit_target(approved=True, activated=False)

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            target = db.session.get(User, self.target_id)
            self.assertFalse(target.activated)
            self.assertEqual(target.auth_version, prior_auth_version + 1)
            self.assertIsNotNone(db.session.get(UserSession, session_id).revoked_at)
            self.assertIsNone(User.load_from_session_id(identity))

    def test_failed_eligibility_change_rolls_back_session_invalidation(self):
        identity = self._login_client(self.normal_client, self.target_id)
        with self.app.app_context():
            session_id = UserSession.active_for_user(self.target_id)[0].id
            prior_auth_version = db.session.get(User, self.target_id).auth_version

        self._login_client(self.admin_client, self.admin_id)
        with (
            patch.object(
                db.session,
                "commit",
                side_effect=SQLAlchemyError("forced eligibility commit failure"),
            ),
            patch(
                "app.routes.admin.users._render_edit_user",
                return_value="edit-user",
            ),
        ):
            response = self._edit_target(approved=False)

        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            target = db.session.get(User, self.target_id)
            session_record = db.session.get(UserSession, session_id)
            self.assertTrue(target.approved)
            self.assertEqual(target.auth_version, prior_auth_version)
            self.assertIsNone(session_record.revoked_at)
            self.assertIsNotNone(User.load_from_session_id(identity))


if __name__ == "__main__":
    unittest.main()
