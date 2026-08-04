import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pyotp
from flask import Blueprint, Flask
from flask_login import LoginManager

from app.core.extensions import bcrypt, cache, db, limiter
from app.models import AuditActivity, AuditLogin, EnvSettings, User
from app.routes.login import login_bp
from app.routes.mfa.mfa import mfa_bp


class LoginAuditRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(cls.temp_dir.name) / 'login-audit-tests.db'

        cls.app = Flask(__name__)
        cls.app.config.update(
            TESTING=True,
            SECRET_KEY='login-audit-test-secret',
            SQLALCHEMY_DATABASE_URI=f'sqlite:///{database_path}',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            RATELIMIT_ENABLED=False,
            CACHE_TYPE='SimpleCache',
            PROXY_HOPS=0,
            TRUSTED_PROXIES=[],
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

        admin_bp = Blueprint('admin', __name__)

        @admin_bp.route('/admin/')
        def admin_home():
            return 'admin'

        dashboard_bp = Blueprint('dashboard', __name__)

        @dashboard_bp.route('/dashboard')
        def dashboard():
            return 'dashboard'

        cls.app.register_blueprint(login_bp)
        cls.app.register_blueprint(mfa_bp)
        cls.app.register_blueprint(admin_bp)
        cls.app.register_blueprint(dashboard_bp)

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.drop_all()
        cls.temp_dir.cleanup()

    def setUp(self):
        self.app_context = self.app.app_context()
        self.app_context.push()

        db.session.remove()
        db.drop_all()
        db.create_all()
        cache.clear()
        EnvSettings._cached_instance = None

        owner = self._new_user(
            username='settings-owner',
            password='owner-password',
            activated=True,
            approved=True,
        )
        db.session.add(owner)
        db.session.flush()

        self.settings = EnvSettings(
            user_id=owner.id,
            site_name='Audit Test',
            site_lang='en',
            site_timezone='UTC',
            description='',
            keywords='',
            users_per_page=20,
            users_stored_path='/tmp/users',
            use_captcha=False,
            use_mfa=False,
            use_verify_email=False,
            use_user_approval=False,
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

    def _new_user(
        self,
        username='login-user',
        password='correct-password',
        activated=True,
        approved=True,
        mfa_enabled=False,
        otp_secret=None,
    ):
        user = User(
            username=username,
            email=f'{username}@example.test',
            activated=activated,
            approved=approved,
            mfa_enabled=mfa_enabled,
            otp_secret=otp_secret,
        )
        user.set_password(password)
        return user

    def _save_user(self, **kwargs):
        user = self._new_user(**kwargs)
        db.session.add(user)
        db.session.commit()
        EnvSettings._cached_instance = None
        return user

    def _request_patches(self):
        stack = ExitStack()
        stack.enter_context(
            patch('app.core.decorators.audit_activity_enabled', return_value=False)
        )
        stack.enter_context(
            patch('app.routes.login.audit_activity_enabled', return_value=False)
        )
        stack.enter_context(
            patch('app.routes.login.render_template', return_value='login')
        )
        stack.enter_context(
            patch('app.routes.mfa.mfa.audit_activity_enabled', return_value=False)
        )
        stack.enter_context(
            patch('app.routes.mfa.mfa.render_template', return_value='mfa')
        )
        return stack

    def _post_login(self, username, password, remember=False):
        with self._request_patches():
            return self.client.post(
                '/login',
                data={
                    'username': username,
                    'password': password,
                    'remember_me': 'y' if remember else '',
                },
                headers={
                    'User-Agent': 'login-audit-test-agent',
                    'Referer': 'https://example.test/login',
                },
                follow_redirects=False,
            )

    def test_nonexistent_user_and_wrong_password_share_failure_reason(self):
        self._save_user(username='known-user')

        nonexistent_response = self._post_login('missing-user', 'wrong-password')
        wrong_password_response = self._post_login('known-user', 'wrong-password')

        self.assertEqual(nonexistent_response.status_code, 200)
        self.assertEqual(wrong_password_response.status_code, 200)

        rows = AuditLogin.query.order_by(AuditLogin.id).all()
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            [row.failure_reason for row in rows],
            ['invalid_credentials', 'invalid_credentials'],
        )
        self.assertTrue(all(not row.success for row in rows))

    def test_locked_out_attempt_is_recorded(self):
        with patch('app.routes.login.is_locked_out', return_value=True):
            response = self._post_login('locked-user', 'any-password')

        self.assertEqual(response.status_code, 200)
        row = AuditLogin.query.one()
        self.assertFalse(row.success)
        self.assertEqual(row.failure_reason, 'locked_out')

    def test_unverified_account_is_not_recorded_as_success(self):
        self.settings.use_verify_email = True
        db.session.commit()
        EnvSettings._cached_instance = None
        self._save_user(username='unverified-user', activated=False)

        response = self._post_login('unverified-user', 'correct-password')

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/login'))
        row = AuditLogin.query.one()
        self.assertFalse(row.success)
        self.assertEqual(row.failure_reason, 'unverified')

    def test_unapproved_account_is_not_recorded_as_success(self):
        self.settings.use_user_approval = True
        db.session.commit()
        EnvSettings._cached_instance = None
        self._save_user(username='unapproved-user', approved=False)

        response = self._post_login('unapproved-user', 'correct-password')

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/login'))
        row = AuditLogin.query.one()
        self.assertFalse(row.success)
        self.assertEqual(row.failure_reason, 'unapproved')

    def test_flask_login_rejection_is_recorded_as_failure(self):
        user = self._save_user(username='rejected-user')

        with patch('app.routes.login.login_user', return_value=False):
            response = self._post_login('rejected-user', 'correct-password')

        self.assertEqual(response.status_code, 200)
        row = AuditLogin.query.one()
        self.assertFalse(row.success)
        self.assertEqual(row.failure_reason, 'login_rejected')

        db.session.expire_all()
        stored_user = db.session.get(User, user.id)
        self.assertIsNone(stored_user.last_active)
        self.assertEqual(AuditActivity.query.filter_by(action='login').count(), 0)

    def test_success_is_recorded_only_after_flask_login_accepts(self):
        user = self._save_user(username='accepted-user')

        response = self._post_login('accepted-user', 'correct-password')

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/dashboard'))

        row = AuditLogin.query.one()
        self.assertTrue(row.success)
        self.assertIsNone(row.failure_reason)

        db.session.expire_all()
        stored_user = db.session.get(User, user.id)
        self.assertIsNotNone(stored_user.last_active)

    def test_mfa_login_is_finalized_after_second_factor_succeeds(self):
        secret = pyotp.random_base32()
        self.settings.use_mfa = True
        db.session.commit()
        EnvSettings._cached_instance = None
        self._save_user(
            username='mfa-user',
            mfa_enabled=True,
            otp_secret=secret,
        )

        login_response = self._post_login('mfa-user', 'correct-password')
        self.assertEqual(login_response.status_code, 302)
        self.assertTrue(login_response.location.endswith('/mfa/verify'))
        self.assertEqual(AuditLogin.query.count(), 0)

        with self._request_patches():
            verify_response = self.client.post(
                '/mfa/verify',
                data={'code': pyotp.TOTP(secret).now()},
                headers={'User-Agent': 'login-audit-test-agent'},
                follow_redirects=False,
            )

        self.assertEqual(verify_response.status_code, 302)
        self.assertTrue(verify_response.location.endswith('/dashboard'))
        row = AuditLogin.query.one()
        self.assertTrue(row.success)
        self.assertIsNone(row.failure_reason)

    def test_expired_mfa_attempt_is_recorded_as_failure(self):
        secret = pyotp.random_base32()
        self.settings.use_mfa = True
        db.session.commit()
        EnvSettings._cached_instance = None
        self._save_user(
            username='mfa-expired-user',
            mfa_enabled=True,
            otp_secret=secret,
        )

        login_response = self._post_login('mfa-expired-user', 'correct-password')
        self.assertEqual(login_response.status_code, 302)
        self.assertEqual(AuditLogin.query.count(), 0)

        with self.client.session_transaction() as login_session:
            login_session['pre_2fa_time'] = 0

        with self._request_patches():
            response = self.client.get('/mfa/verify', follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/login'))
        row = AuditLogin.query.one()
        self.assertFalse(row.success)
        self.assertEqual(row.failure_reason, 'mfa_expired')

    def test_terminal_mfa_failure_creates_one_failed_login_row(self):
        secret = pyotp.random_base32()
        self.settings.use_mfa = True
        db.session.commit()
        EnvSettings._cached_instance = None
        self._save_user(
            username='mfa-failure-user',
            mfa_enabled=True,
            otp_secret=secret,
        )

        login_response = self._post_login('mfa-failure-user', 'correct-password')
        self.assertEqual(login_response.status_code, 302)
        self.assertEqual(AuditLogin.query.count(), 0)

        with self._request_patches(), patch(
            'app.routes.mfa.mfa.pyotp.TOTP.verify',
            return_value=False,
        ):
            for attempt in range(5):
                response = self.client.post(
                    '/mfa/verify',
                    data={'code': '000000'},
                    follow_redirects=False,
                )

                if attempt < 4:
                    self.assertEqual(response.status_code, 200)
                else:
                    self.assertEqual(response.status_code, 302)
                    self.assertTrue(response.location.endswith('/login'))

        row = AuditLogin.query.one()
        self.assertFalse(row.success)
        self.assertEqual(row.failure_reason, 'mfa_failed')


if __name__ == '__main__':
    unittest.main()
