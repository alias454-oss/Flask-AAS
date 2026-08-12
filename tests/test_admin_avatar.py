# tests/test_admin_avatar.py
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Blueprint, Flask
from flask_login import LoginManager
from sqlalchemy.exc import SQLAlchemyError

from app.core.avatar import profile_image_root
from app.core.extensions import bcrypt, csrf, db, limiter
from app.models import AuditActivity, EnvSettings, Role, User, UserSession
from app.routes.admin.users import users_bp


class AdminProfileImageRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(cls.temp_dir.name) / "admin-avatar-tests.db"

        cls.app = Flask(__name__)
        cls.app.config.update(
            TESTING=True,
            SECRET_KEY="admin-avatar-test-secret",
            SQLALCHEMY_DATABASE_URI=os.environ.get(
                "ADMIN_AVATAR_TEST_DATABASE_URI",
                f"sqlite:///{database_path}",
            ),
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            RATELIMIT_ENABLED=False,
            PROXY_HOPS=0,
            TRUSTED_PROXIES=[],
        )

        db.init_app(cls.app)
        bcrypt.init_app(cls.app)
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

        @index_bp.route("/")
        def index():
            return "index"

        cls.app.register_blueprint(login_bp)
        cls.app.register_blueprint(index_bp)
        cls.app.register_blueprint(users_bp)

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

        self.image_root = Path(self.temp_dir.name) / "profile-images"
        self.image_root.mkdir(parents=True, exist_ok=True)

        admin_role = Role(name="admin")
        user_role = Role(name="user")
        db.session.add_all([admin_role, user_role])
        db.session.flush()

        admin = User(
            username="admin-avatar",
            email="admin-avatar@example.test",
            activated=True,
            approved=True,
        )
        admin.set_password("admin-password")
        admin.roles.append(admin_role)
        db.session.add(admin)
        db.session.flush()

        settings = EnvSettings(
            user_id=admin.id,
            site_name="Admin Avatar Test",
            site_lang="en",
            site_timezone="UTC",
            description="",
            keywords="",
            users_per_page=20,
            users_stored_path=str(self.image_root),
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
            username="avatar-target",
            email="avatar-target@example.test",
            activated=True,
            approved=True,
            image="0123456789abcdef0123456789abcdef.webp",
        )
        target.set_password("target-password")
        target.roles.append(user_role)
        db.session.add(target)
        db.session.commit()

        self.admin_id = admin.id
        self.target_id = target.id
        self.filename = target.image
        self.image_path = profile_image_root() / self.filename
        self.image_path.write_bytes(b"stored-webp-placeholder")
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        EnvSettings._cached_instance = None
        self.app_context.pop()

    def _login(self, user_id):
        user = db.session.get(User, user_id)
        UserSession.issue_for_user(
            user,
            ip_address="127.0.0.1",
            user_agent="admin-avatar-test-agent",
        )
        db.session.commit()
        with self.client.session_transaction() as session:
            session["_user_id"] = user.get_id()
            session["_fresh"] = True

    def test_admin_can_remove_user_profile_image(self):
        self._login(self.admin_id)

        with patch("app.core.decorators.audit_activity_enabled", return_value=False):
            response = self.client.post(
                f"/admin/users/{self.target_id}/profile-image/remove",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/admin/users/")
        self.assertIsNone(db.session.get(User, self.target_id).image)
        self.assertFalse(self.image_path.exists())

        event = AuditActivity.query.filter_by(
            action="admin_profile_image_removed",
            user_id=self.admin_id,
            target=f"user:{self.target_id}",
        ).one()
        self.assertIsNotNone(event)

    def test_non_admin_cannot_remove_user_profile_image(self):
        self._login(self.target_id)

        with patch("app.core.decorators.audit_activity_enabled", return_value=False):
            response = self.client.post(
                f"/admin/users/{self.target_id}/profile-image/remove",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/")
        self.assertEqual(db.session.get(User, self.target_id).image, self.filename)
        self.assertTrue(self.image_path.exists())

    def test_admin_remove_without_image_is_a_noop(self):
        target = db.session.get(User, self.target_id)
        target.image = None
        db.session.commit()
        self.image_path.unlink()
        self._login(self.admin_id)

        with patch("app.core.decorators.audit_activity_enabled", return_value=False):
            response = self.client.post(
                f"/admin/users/{self.target_id}/profile-image/remove",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertIsNone(db.session.get(User, self.target_id).image)
        self.assertEqual(
            AuditActivity.query.filter_by(action="admin_profile_image_removed").count(),
            0,
        )

    def test_commit_failure_preserves_database_reference_and_file(self):
        self._login(self.admin_id)

        with (
            patch("app.core.decorators.audit_activity_enabled", return_value=False),
            patch.object(db.session, "commit", side_effect=SQLAlchemyError("boom")),
        ):
            response = self.client.post(
                f"/admin/users/{self.target_id}/profile-image/remove",
                follow_redirects=False,
            )

        db.session.expire_all()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(db.session.get(User, self.target_id).image, self.filename)
        self.assertTrue(self.image_path.exists())
