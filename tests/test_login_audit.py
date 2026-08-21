import tempfile
import time
import unittest
import warnings
from contextlib import ExitStack
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pyotp
from flask import Blueprint, Flask
from flask_login import LoginManager
from sqlalchemy.exc import SQLAlchemyError

from app.core.auth import enforce_required_password_change
from app.core.extensions import cache, db, limiter
from app.models import (
    AuditActivity,
    AuditLogin,
    EnvSettings,
    MfaRecoveryCode,
    PasswordResetToken,
    User,
    UserSession,
)
from app.routes.login import login_bp
from app.routes.logout import logout_bp
from app.routes.mfa.mfa import mfa_bp
from app.routes.reset import reset_bp


LEGACY_PASSWORD = 'legacy-correct-password'
LEGACY_FLASK_BCRYPT_HASH = (
    '$2b$12$Xu5MNvTzhYSCFTNiLnky3e08HnFrRrWgt1HyXbGw4GcNaqTUpG77y'
)

# Flask-Login 0.6.3 uses datetime.utcnow() while setting remember-cookie
# expiration. The upstream fix has not yet shipped in a release. Keep this
# filter limited to that dependency, warning category, and exact message.
warnings.filterwarnings(
    'ignore',
    message=r'datetime\.datetime\.utcnow\(\) is deprecated.*',
    category=DeprecationWarning,
    module=r'flask_login\.login_manager',
)


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
        cache.init_app(cls.app)
        limiter.init_app(cls.app)

        cls.login_manager = LoginManager(cls.app)
        cls.login_manager.login_view = 'login.login'

        @cls.login_manager.user_loader
        def load_user(session_id):
            return User.load_from_session_id(session_id)

        admin_bp = Blueprint('admin', __name__)

        @admin_bp.route('/admin/')
        def admin_home():
            return 'admin'

        dashboard_bp = Blueprint('dashboard', __name__)

        @dashboard_bp.route('/dashboard')
        def dashboard():
            return 'dashboard'

        cls.app.register_blueprint(login_bp)
        cls.app.register_blueprint(logout_bp)
        cls.app.register_blueprint(mfa_bp)
        cls.app.register_blueprint(reset_bp)
        cls.app.register_blueprint(admin_bp)
        cls.app.register_blueprint(dashboard_bp)
        cls.app.before_request(enforce_required_password_change)

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
            patch('app.routes.logout.audit_activity_enabled', return_value=False)
        )
        stack.enter_context(
            patch('app.routes.mfa.mfa.audit_activity_enabled', return_value=False)
        )
        stack.enter_context(
            patch('app.routes.mfa.mfa.render_template', return_value='mfa')
        )
        stack.enter_context(
            patch('app.routes.reset.audit_activity_enabled', return_value=False)
        )
        stack.enter_context(
            patch('app.routes.reset.render_template', return_value='reset')
        )
        stack.enter_context(
            patch(
                'app.routes.reset.send_password_changed_email',
                return_value='disabled',
            )
        )
        stack.enter_context(
            patch('app.routes.mfa.mfa.send_mfa_change_email', return_value='disabled')
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

    def test_login_missing_username_flashes_single_validation_summary(self):
        response = self._post_login('', 'provided-password')

        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as login_session:
            self.assertEqual(
                login_session.get('_flashes'),
                [('error', 'Please correct the highlighted fields.')],
            )

    def test_login_missing_password_flashes_single_validation_summary(self):
        response = self._post_login('provided-username', '')

        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as login_session:
            self.assertEqual(
                login_session.get('_flashes'),
                [('error', 'Please correct the highlighted fields.')],
            )

    def test_change_password_rejects_password_below_policy_minimum(self):
        user = self._save_user(username='short-password-change-user')
        self._post_login(
            'short-password-change-user',
            'correct-password',
        )

        with self._request_patches():
            response = self.client.post(
                '/change-password',
                data={
                    'old_password': 'correct-password',
                    'password': 'too-short',
                    'confirm': 'too-short',
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 200)
        db.session.expire_all()
        stored_user = db.session.get(User, user.id)
        self.assertTrue(stored_user.check_password('correct-password'))
        self.assertFalse(stored_user.check_password('too-short'))

    def test_change_password_revokes_sessions_tokens_and_forces_login(self):
        user = self._save_user(username='password-change-user')
        old_session_id = user.get_id()
        reset_token, _ = PasswordResetToken.issue_for_user(user)
        db.session.commit()

        self._post_login(
            'password-change-user',
            'correct-password',
            remember=True,
        )
        remember_cookie_name = self.app.config.get(
            'REMEMBER_COOKIE_NAME',
            'remember_token',
        )
        self.assertIsNotNone(self.client.get_cookie(remember_cookie_name))
        active_session = UserSession.query.filter_by(user_id=user.id).one()

        with self._request_patches(), patch(
            'app.routes.reset.send_password_changed_email',
            return_value='queued',
        ) as send_changed:
            response = self.client.post(
                '/change-password',
                data={
                    'old_password': 'correct-password',
                    'password': 'new-secure-password-ok',
                    'confirm': 'new-secure-password-ok',
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/login'))
        db.session.expire_all()
        stored_user = db.session.get(User, user.id)
        stored_token = db.session.get(PasswordResetToken, reset_token.id)
        self.assertTrue(stored_user.check_password('new-secure-password-ok'))
        self.assertNotEqual(stored_user.get_id(), old_session_id)
        self.assertIsNone(User.load_from_session_id(old_session_id))
        self.assertIsNotNone(stored_token.revoked_at)
        self.assertIsNotNone(
            db.session.get(UserSession, active_session.id).revoked_at
        )
        send_changed.assert_called_once_with(stored_user.email, stored_user.username)

        with self.client.session_transaction() as login_session:
            self.assertNotIn('_user_id', login_session)
        self.assertIsNone(self.client.get_cookie(remember_cookie_name))

    def test_required_password_change_redirects_login_and_blocks_navigation(self):
        user = self._save_user(username='required-change-user')
        user.must_change_password = True
        db.session.commit()

        response = self._post_login('required-change-user', 'correct-password')

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/change-password'))

        blocked = self.client.get('/dashboard', follow_redirects=False)
        self.assertEqual(blocked.status_code, 303)
        self.assertTrue(blocked.location.endswith('/change-password'))

    def test_required_password_change_does_not_require_current_password_again(self):
        user = self._save_user(username='required-change-complete-user')
        user.must_change_password = True
        old_auth_version = user.auth_version
        db.session.commit()

        self._post_login('required-change-complete-user', 'correct-password')

        with self._request_patches():
            response = self.client.post(
                '/change-password',
                data={
                    'password': 'new-private-password-for-user',
                    'confirm': 'new-private-password-for-user',
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/login'))
        db.session.expire_all()
        stored_user = db.session.get(User, user.id)
        self.assertFalse(stored_user.must_change_password)
        self.assertTrue(stored_user.check_password('new-private-password-for-user'))
        self.assertEqual(stored_user.auth_version, old_auth_version + 1)

    def test_change_password_commit_failure_preserves_session_and_token(self):
        user = self._save_user(username='password-change-rollback-user')
        reset_token, _ = PasswordResetToken.issue_for_user(user)
        db.session.commit()

        self._post_login(
            'password-change-rollback-user',
            'correct-password',
            remember=True,
        )
        with self.client.session_transaction() as login_session:
            active_session_identity = login_session.get('_user_id')
        active_session = UserSession.query.filter_by(user_id=user.id).one()
        remember_cookie_name = self.app.config.get(
            'REMEMBER_COOKIE_NAME',
            'remember_token',
        )

        with self._request_patches(), patch.object(
            db.session,
            'commit',
            side_effect=SQLAlchemyError('database unavailable'),
        ), patch(
            'app.routes.reset.send_password_changed_email'
        ) as send_changed:
            response = self.client.post(
                '/change-password',
                data={
                    'old_password': 'correct-password',
                    'password': 'new-secure-password-ok',
                    'confirm': 'new-secure-password-ok',
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 200)
        db.session.expire_all()
        stored_user = db.session.get(User, user.id)
        stored_token = db.session.get(PasswordResetToken, reset_token.id)
        self.assertTrue(stored_user.check_password('correct-password'))
        self.assertEqual(stored_user.get_id(), active_session_identity)
        self.assertIsNone(stored_token.revoked_at)
        self.assertIsNone(
            db.session.get(UserSession, active_session.id).revoked_at
        )
        send_changed.assert_not_called()

        with self.client.session_transaction() as login_session:
            self.assertEqual(
                login_session.get('_user_id'),
                active_session_identity,
            )
        self.assertIsNotNone(self.client.get_cookie(remember_cookie_name))

    def test_nonexistent_user_still_performs_password_hash_verification(self):
        with self._request_patches(), patch(
            'app.routes.login.verify_login_password',
            return_value=False,
        ) as verify_hash:
            response = self.client.post(
                '/login',
                data={
                    'username': 'missing-user',
                    'password': 'wrong-password',
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 200)
        verify_hash.assert_called_once_with(None, 'wrong-password')

    def test_legacy_bcrypt_hash_is_upgraded_after_password_verification(self):
        user = self._save_user(username='legacy-password-user')
        user.hashed_password = LEGACY_FLASK_BCRYPT_HASH
        db.session.commit()

        response = self._post_login('legacy-password-user', LEGACY_PASSWORD)

        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        stored_user = db.session.get(User, user.id)
        self.assertTrue(stored_user.hashed_password.startswith('$argon2id$'))
        self.assertTrue(stored_user.check_password(LEGACY_PASSWORD))

    def test_legacy_bcrypt_rehash_preserves_required_password_change(self):
        user = self._save_user(username='legacy-required-change-user')
        user.hashed_password = LEGACY_FLASK_BCRYPT_HASH
        user.must_change_password = True
        db.session.commit()

        response = self._post_login('legacy-required-change-user', LEGACY_PASSWORD)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/change-password'))
        db.session.expire_all()
        stored_user = db.session.get(User, user.id)
        self.assertTrue(stored_user.hashed_password.startswith('$argon2id$'))
        self.assertTrue(stored_user.must_change_password)

    def test_legacy_bcrypt_hash_upgrade_persists_before_mfa_redirect(self):
        secret = pyotp.random_base32()
        self.settings.use_mfa = True
        db.session.commit()
        EnvSettings._cached_instance = None
        user = self._save_user(
            username='legacy-mfa-user',
            mfa_enabled=True,
            otp_secret=secret,
        )
        user.hashed_password = LEGACY_FLASK_BCRYPT_HASH
        db.session.commit()

        response = self._post_login('legacy-mfa-user', LEGACY_PASSWORD)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/mfa/verify'))
        db.session.expire_all()
        stored_user = db.session.get(User, user.id)
        self.assertTrue(stored_user.hashed_password.startswith('$argon2id$'))
        self.assertTrue(stored_user.check_password(LEGACY_PASSWORD))

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
        with self.client.session_transaction() as login_session:
            self.assertEqual(
                login_session.get('_flashes'),
                [('warning', 'This account is not currently available for sign-in.')],
            )
            self.assertNotIn('_user_id', login_session)
            self.assertNotIn('pre_2fa_user_id', login_session)

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
        with self.client.session_transaction() as login_session:
            self.assertEqual(
                login_session.get('_flashes'),
                [('warning', 'This account is not currently available for sign-in.')],
            )
            self.assertNotIn('_user_id', login_session)
            self.assertNotIn('pre_2fa_user_id', login_session)

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
        self.assertEqual(UserSession.query.count(), 0)

    def test_login_commit_failure_leaves_no_server_or_browser_session(self):
        user = self._save_user(username='login-commit-failure-user')

        with patch.object(
            db.session,
            'commit',
            side_effect=SQLAlchemyError('forced login commit failure'),
        ):
            response = self._post_login(
                'login-commit-failure-user',
                'correct-password',
            )

        self.assertEqual(response.status_code, 200)
        db.session.expire_all()
        self.assertIsNone(db.session.get(User, user.id).last_active)
        self.assertEqual(UserSession.query.count(), 0)
        login_row = AuditLogin.query.one()
        self.assertFalse(login_row.success)
        self.assertEqual(login_row.failure_reason, 'login_rejected')
        with self.client.session_transaction() as login_session:
            self.assertNotIn('_user_id', login_session)

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
        stored_session = UserSession.query.filter_by(user_id=user.id).one()
        self.assertEqual(stored_session.ip_address, '127.0.0.1')
        self.assertEqual(stored_session.user_agent, 'login-audit-test-agent')
        self.assertFalse(stored_session.remembered)
        self.assertIsNone(stored_session.revoked_at)
        self.assertIsNone(stored_session.ended_at)
        with self.client.session_transaction() as login_session:
            identity = login_session.get('_user_id')
            self.assertIsInstance(login_session.get('last_activity_at'), float)
        self.assertEqual(len(identity.split(':', 2)), 3)
        raw_token = identity.split(':', 2)[2]
        self.assertNotEqual(raw_token, stored_session.token_hash)
        self.assertEqual(
            stored_session.token_hash,
            UserSession.hash_token(raw_token),
        )
        loaded = User.load_from_session_id(identity)
        self.assertIsNotNone(loaded)
        self.assertFalse(loaded.session_remembered)

    def test_remembered_login_binds_remembered_session_identity(self):
        user = self._save_user(username='remembered-identity-user')

        response = self._post_login(
            'remembered-identity-user',
            'correct-password',
            remember=True,
        )

        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as login_session:
            identity = login_session.get('_user_id')

        loaded = User.load_from_session_id(identity)
        self.assertIsNotNone(loaded)
        self.assertTrue(loaded.session_remembered)
        self.assertIsNotNone(loaded.session_record_id)
        record = db.session.get(UserSession, loaded.session_record_id)
        self.assertTrue(record.remembered)
        self.assertIsNone(record.ended_at)
        self.assertIsNone(record.revoked_at)

    def test_new_login_ends_the_browser_previous_session(self):
        user = self._save_user(username='relogin-user')
        self._post_login('relogin-user', 'correct-password', remember=True)
        first_session = UserSession.query.filter_by(user_id=user.id).one()
        remember_cookie_name = self.app.config.get(
            'REMEMBER_COOKIE_NAME',
            'remember_token',
        )
        self.assertIsNotNone(self.client.get_cookie(remember_cookie_name))

        response = self._post_login(
            'relogin-user',
            'correct-password',
            remember=False,
        )

        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        sessions = (
            UserSession.query
            .filter_by(user_id=user.id)
            .order_by(UserSession.id)
            .all()
        )
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0].id, first_session.id)
        self.assertIsNotNone(sessions[0].ended_at)
        self.assertIsNone(sessions[1].ended_at)
        self.assertIsNone(sessions[1].revoked_at)
        self.assertIsNone(self.client.get_cookie(remember_cookie_name))

    def test_logout_ends_current_session_and_clears_remember_cookie(self):
        user = self._save_user(username='logout-user')
        self._post_login('logout-user', 'correct-password', remember=True)
        user_session = UserSession.query.filter_by(user_id=user.id).one()
        remember_cookie_name = self.app.config.get(
            'REMEMBER_COOKIE_NAME',
            'remember_token',
        )
        self.assertIsNotNone(self.client.get_cookie(remember_cookie_name))

        with self._request_patches():
            response = self.client.get('/logout', follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/login'))
        db.session.expire_all()
        self.assertIsNotNone(
            db.session.get(UserSession, user_session.id).ended_at
        )
        with self.client.session_transaction() as login_session:
            self.assertNotIn('_user_id', login_session)
        self.assertIsNone(self.client.get_cookie(remember_cookie_name))

    def test_mfa_routes_fail_closed_when_feature_disabled(self):
        self.settings.use_mfa = False
        db.session.commit()
        EnvSettings._cached_instance = None

        for method, path in (
            ("GET", "/mfa/setup"),
            ("POST", "/mfa/setup"),
            ("GET", "/mfa/verify"),
            ("POST", "/mfa/verify"),
            ("GET", "/mfa/reauth"),
            ("POST", "/mfa/reauth"),
            ("GET", "/mfa/replace"),
            ("POST", "/mfa/replace"),
            ("GET", "/mfa/recovery-codes"),
            ("POST", "/mfa/recovery-codes"),
            ("GET", "/mfa/disable"),
            ("POST", "/mfa/disable"),
        ):
            with self.subTest(method=method, path=path), self._request_patches():
                response = self.client.open(path, method=method, follow_redirects=False)
            self.assertEqual(response.status_code, 404)

    def test_mfa_setup_marks_current_session_verified(self):
        self.settings.use_mfa = True
        db.session.commit()
        EnvSettings._cached_instance = None
        user = self._save_user(username='mfa-setup-user')

        login_response = self._post_login('mfa-setup-user', 'correct-password')
        self.assertEqual(login_response.status_code, 302)
        self.assertTrue(login_response.location.endswith('/dashboard'))

        with self._request_patches():
            setup_page = self.client.get('/mfa/setup', follow_redirects=False)
        self.assertEqual(setup_page.status_code, 200)

        db.session.refresh(user)
        secret = user.pending_otp_secret
        self.assertIsNotNone(secret)
        self.assertIsNotNone(user.pending_otp_created_at)
        self.assertIsNone(user.otp_secret)

        with self._request_patches():
            setup_response = self.client.post(
                '/mfa/setup',
                data={'code': pyotp.TOTP(secret).now()},
                follow_redirects=False,
            )

        self.assertEqual(setup_response.status_code, 200)

        db.session.expire_all()
        stored_user = db.session.get(User, user.id)
        self.assertTrue(stored_user.mfa_enabled)
        self.assertEqual(stored_user.otp_secret, secret)
        self.assertIsNone(stored_user.pending_otp_secret)
        self.assertIsNone(stored_user.pending_otp_created_at)
        self.assertIsNotNone(stored_user.last_totp_counter)
        self.assertEqual(MfaRecoveryCode.query.filter_by(user_id=user.id).count(), 10)
        with self.client.session_transaction() as login_session:
            self.assertTrue(login_session.get('mfa_verified'))
            self.assertIsNotNone(login_session.get('mfa_verified_at'))
            self.assertNotIn('pre_2fa_user_id', login_session)
            self.assertNotIn('mfa_recovery_codes', login_session)

    def test_mfa_setup_rejects_non_numeric_code_before_totp_validation(self):
        self.settings.use_mfa = True
        db.session.commit()
        EnvSettings._cached_instance = None
        user = self._save_user(username='mfa-setup-invalid-user')
        self._post_login('mfa-setup-invalid-user', 'correct-password')

        with self._request_patches():
            setup_page = self.client.get('/mfa/setup', follow_redirects=False)
        self.assertEqual(setup_page.status_code, 200)

        with self._request_patches(), patch(
            'app.routes.mfa.mfa._matching_totp_counter'
        ) as match_totp, patch(
            'app.routes.mfa.mfa.render_template', return_value='mfa'
        ) as render_mfa:
            response = self.client.post(
                '/mfa/setup',
                data={'code': 'abc123'},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 200)
        rendered_form = render_mfa.call_args.kwargs['form']
        self.assertIn('The code must be 6 digits.', rendered_form.code.errors)
        match_totp.assert_not_called()
        db.session.refresh(user)
        self.assertFalse(user.mfa_enabled)
        self.assertIsNone(user.otp_secret)

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
        stored_session = UserSession.query.one()
        self.assertEqual(stored_session.user_agent, 'login-audit-test-agent')
        self.assertIsNone(stored_session.revoked_at)
        self.assertIsNone(stored_session.ended_at)
        with self.client.session_transaction() as login_session:
            self.assertIsInstance(login_session.get('last_activity_at'), float)
            self.assertEqual(len(login_session['_user_id'].split(':', 2)), 3)

    def test_required_password_change_occurs_after_mfa_succeeds(self):
        secret = pyotp.random_base32()
        self.settings.use_mfa = True
        db.session.commit()
        EnvSettings._cached_instance = None
        user = self._save_user(
            username='mfa-required-change-user',
            mfa_enabled=True,
            otp_secret=secret,
        )
        user.must_change_password = True
        db.session.commit()

        login_response = self._post_login(
            'mfa-required-change-user',
            'correct-password',
        )
        self.assertEqual(login_response.status_code, 302)
        self.assertTrue(login_response.location.endswith('/mfa/verify'))

        with self._request_patches():
            verify_response = self.client.post(
                '/mfa/verify',
                data={'code': pyotp.TOTP(secret).now()},
                headers={'User-Agent': 'login-audit-test-agent'},
                follow_redirects=False,
            )

        self.assertEqual(verify_response.status_code, 302)
        self.assertTrue(verify_response.location.endswith('/change-password'))

    def test_mfa_rechecks_unverified_account_before_authentication(self):
        secret = pyotp.random_base32()
        self.settings.use_mfa = True
        self.settings.use_verify_email = True
        db.session.commit()
        EnvSettings._cached_instance = None
        user = self._save_user(
            username='mfa-unverified-user',
            mfa_enabled=True,
            otp_secret=secret,
        )

        login_response = self._post_login('mfa-unverified-user', 'correct-password')
        self.assertEqual(login_response.status_code, 302)
        self.assertTrue(login_response.location.endswith('/mfa/verify'))

        user.activated = False
        db.session.commit()

        with self._request_patches():
            verify_response = self.client.post(
                '/mfa/verify',
                data={'code': pyotp.TOTP(secret).now()},
                follow_redirects=False,
            )

        self.assertEqual(verify_response.status_code, 302)
        self.assertTrue(verify_response.location.endswith('/login'))
        row = AuditLogin.query.one()
        self.assertFalse(row.success)
        self.assertEqual(row.failure_reason, 'unverified')
        with self.client.session_transaction() as login_session:
            self.assertNotIn('_user_id', login_session)
            self.assertNotIn('pre_2fa_user_id', login_session)
            self.assertNotIn('mfa_verified', login_session)

    def test_mfa_rechecks_unapproved_account_before_authentication(self):
        secret = pyotp.random_base32()
        self.settings.use_mfa = True
        self.settings.use_user_approval = True
        db.session.commit()
        EnvSettings._cached_instance = None
        user = self._save_user(
            username='mfa-unapproved-user',
            mfa_enabled=True,
            otp_secret=secret,
        )

        login_response = self._post_login('mfa-unapproved-user', 'correct-password')
        self.assertEqual(login_response.status_code, 302)
        self.assertTrue(login_response.location.endswith('/mfa/verify'))

        user.approved = False
        db.session.commit()

        with self._request_patches():
            verify_response = self.client.post(
                '/mfa/verify',
                data={'code': pyotp.TOTP(secret).now()},
                follow_redirects=False,
            )

        self.assertEqual(verify_response.status_code, 302)
        self.assertTrue(verify_response.location.endswith('/login'))
        row = AuditLogin.query.one()
        self.assertFalse(row.success)
        self.assertEqual(row.failure_reason, 'unapproved')
        with self.client.session_transaction() as login_session:
            self.assertNotIn('_user_id', login_session)
            self.assertNotIn('pre_2fa_user_id', login_session)
            self.assertNotIn('mfa_verified', login_session)

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
            'app.routes.mfa.mfa._matching_totp_counter',
            return_value=None,
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


    def test_recovery_code_completes_login_once(self):
        secret = pyotp.random_base32()
        self.settings.use_mfa = True
        db.session.commit()
        EnvSettings._cached_instance = None
        user = self._save_user(
            username='mfa-recovery-user',
            mfa_enabled=True,
            otp_secret=secret,
        )
        recovery_code = MfaRecoveryCode.generate_for_user(user, count=1)[0]
        db.session.commit()

        login_response = self._post_login('mfa-recovery-user', 'correct-password')
        self.assertTrue(login_response.location.endswith('/mfa/verify'))

        with self._request_patches():
            verify_response = self.client.post(
                '/mfa/verify',
                data={'code': recovery_code},
                follow_redirects=False,
            )

        self.assertEqual(verify_response.status_code, 302)
        self.assertTrue(verify_response.location.endswith('/dashboard'))
        stored_code = MfaRecoveryCode.query.filter_by(user_id=user.id).one()
        self.assertIsNotNone(stored_code.consumed_at)

        with self.client.session_transaction() as login_session:
            login_session.clear()

        login_response = self._post_login('mfa-recovery-user', 'correct-password')
        self.assertTrue(login_response.location.endswith('/mfa/verify'))
        with self._request_patches():
            replay_response = self.client.post(
                '/mfa/verify',
                data={'code': recovery_code},
                follow_redirects=False,
            )

        self.assertEqual(replay_response.status_code, 200)
        with self.client.session_transaction() as login_session:
            self.assertEqual(login_session.get('mfa_fail_count'), 1)

    def test_invalid_recovery_code_audit_does_not_lock_sqlite(self):
        secret = pyotp.random_base32()
        self.settings.use_mfa = True
        db.session.commit()
        EnvSettings._cached_instance = None
        user = self._save_user(
            username='mfa-invalid-recovery-user',
            mfa_enabled=True,
            otp_secret=secret,
        )
        MfaRecoveryCode.generate_for_user(user, count=1)
        db.session.commit()

        login_response = self._post_login(
            'mfa-invalid-recovery-user',
            'correct-password',
        )
        self.assertTrue(login_response.location.endswith('/mfa/verify'))

        with ExitStack() as stack:
            stack.enter_context(
                patch('app.core.decorators.audit_activity_enabled', return_value=False)
            )
            stack.enter_context(
                patch('app.routes.mfa.mfa.audit_activity_enabled', return_value=True)
            )
            stack.enter_context(
                patch('app.routes.mfa.mfa.render_template', return_value='mfa')
            )
            response = self.client.post(
                '/mfa/verify',
                data={'code': 'INVALID-RECOVERY-CODE'},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            AuditActivity.query.filter_by(action='mfa_recovery_code_failed').count(),
            1,
        )
        with self.client.session_transaction() as login_session:
            self.assertEqual(login_session.get('mfa_fail_count'), 1)

        with self._request_patches():
            verify_response = self.client.post(
                '/mfa/verify',
                data={'code': pyotp.TOTP(secret).now()},
                follow_redirects=False,
            )

        self.assertEqual(verify_response.status_code, 302)
        self.assertTrue(verify_response.location.endswith('/dashboard'))

    def test_recovery_code_regeneration_invalidates_existing_codes(self):
        secret = pyotp.random_base32()
        user = self._save_user(
            username='mfa-regenerate-user',
            mfa_enabled=True,
            otp_secret=secret,
        )
        old_code = MfaRecoveryCode.generate_for_user(user, count=1)[0]
        db.session.commit()

        self._post_login('mfa-regenerate-user', 'correct-password')
        self.settings.use_mfa = True
        db.session.commit()
        EnvSettings._cached_instance = None
        with self.client.session_transaction() as login_session:
            login_session['mfa_verified'] = True
            login_session['mfa_verified_at'] = __import__('time').time()

        with self._request_patches():
            response = self.client.post('/mfa/recovery-codes', follow_redirects=False)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(MfaRecoveryCode.consume(user.id, old_code))
        db.session.rollback()
        self.assertEqual(MfaRecoveryCode.query.filter_by(user_id=user.id).count(), 10)

    def test_disable_requires_fresh_mfa_and_current_factor(self):
        secret = pyotp.random_base32()
        self.settings.use_mfa = True
        db.session.commit()
        EnvSettings._cached_instance = None
        user = self._save_user(
            username='mfa-disable-user',
            mfa_enabled=True,
            otp_secret=secret,
        )

        self._post_login('mfa-disable-user', 'correct-password')
        with self._request_patches():
            self.client.post(
                '/mfa/verify',
                data={'code': pyotp.TOTP(secret).now()},
                follow_redirects=False,
            )

        with self.client.session_transaction() as login_session:
            login_session['mfa_verified_at'] = 0

        with self._request_patches():
            stale_page = self.client.get('/mfa/disable', follow_redirects=False)
            stale_response = self.client.post('/mfa/disable', follow_redirects=False)

        self.assertEqual(stale_page.status_code, 200)
        self.assertEqual(stale_response.status_code, 302)
        self.assertTrue(stale_response.location.endswith('/mfa/reauth'))
        self.assertTrue(db.session.get(User, user.id).mfa_enabled)

        next_code_time = time.time() + 30
        with self._request_patches(), patch(
            'app.routes.mfa.mfa.time.time',
            return_value=next_code_time,
        ):
            reauth_response = self.client.post(
                '/mfa/reauth',
                data={'code': pyotp.TOTP(secret).at(next_code_time)},
                follow_redirects=False,
            )

        self.assertEqual(reauth_response.status_code, 302)
        self.assertTrue(reauth_response.location.endswith('/dashboard'))
        stored_user = db.session.get(User, user.id)
        self.assertFalse(stored_user.mfa_enabled)
        self.assertIsNone(stored_user.otp_secret)
        self.assertIsNone(stored_user.last_totp_counter)


    def test_authenticator_replacement_requires_fresh_mfa(self):
        secret = pyotp.random_base32()
        user = self._save_user(
            username='mfa-replace-user',
            mfa_enabled=True,
            otp_secret=secret,
        )
        self._post_login('mfa-replace-user', 'correct-password')
        self.settings.use_mfa = True
        db.session.commit()
        EnvSettings._cached_instance = None

        with self.client.session_transaction() as login_session:
            login_session['mfa_verified'] = True
            login_session['mfa_verified_at'] = 0

        with self._request_patches():
            stale_response = self.client.get('/mfa/replace', follow_redirects=False)
        self.assertEqual(stale_response.status_code, 302)
        self.assertTrue(stale_response.location.endswith('/mfa/reauth'))

        reauth_time = time.time()
        with self._request_patches(), patch(
            'app.routes.mfa.mfa.time.time',
            return_value=reauth_time,
        ):
            reauth_response = self.client.post(
                '/mfa/reauth',
                data={'code': pyotp.TOTP(secret).at(reauth_time)},
                follow_redirects=False,
            )
        self.assertTrue(reauth_response.location.endswith('/mfa/replace'))

        with self._request_patches():
            replace_page = self.client.get('/mfa/replace', follow_redirects=False)
        self.assertEqual(replace_page.status_code, 200)

        db.session.refresh(user)
        replacement_secret = user.pending_otp_secret
        self.assertIsNotNone(replacement_secret)
        self.assertIsNotNone(user.pending_otp_created_at)

        with self._request_patches():
            replace_response = self.client.post(
                '/mfa/replace',
                data={'code': pyotp.TOTP(replacement_secret).now()},
                follow_redirects=False,
            )

        self.assertEqual(replace_response.status_code, 200)
        db.session.expire_all()
        stored_user = db.session.get(User, user.id)
        self.assertEqual(stored_user.otp_secret, replacement_secret)
        self.assertIsNone(stored_user.pending_otp_secret)
        self.assertIsNone(stored_user.pending_otp_created_at)
        self.assertEqual(MfaRecoveryCode.query.filter_by(user_id=user.id).count(), 10)

    def test_nonfresh_session_cannot_enable_mfa(self):
        self.settings.use_mfa = True
        db.session.commit()
        EnvSettings._cached_instance = None
        self._save_user(username='mfa-nonfresh-setup-user')
        self._post_login('mfa-nonfresh-setup-user', 'correct-password', remember=True)
        remember_cookie_name = self.app.config.get(
            'REMEMBER_COOKIE_NAME',
            'remember_token',
        )
        self.assertIsNotNone(self.client.get_cookie(remember_cookie_name))

        with self.client.session_transaction() as login_session:
            login_session['_fresh'] = False

        with self._request_patches():
            response = self.client.get('/mfa/setup', follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/login'))
        with self.client.session_transaction() as login_session:
            self.assertNotIn('_user_id', login_session)
        self.assertIsNone(self.client.get_cookie(remember_cookie_name))

    def test_remembered_session_reauth_becomes_fresh(self):
        secret = pyotp.random_base32()
        self._save_user(
            username='mfa-remembered-user',
            mfa_enabled=True,
            otp_secret=secret,
        )
        self._post_login('mfa-remembered-user', 'correct-password', remember=True)
        self.settings.use_mfa = True
        db.session.commit()
        EnvSettings._cached_instance = None

        with self.client.session_transaction() as login_session:
            login_session['_fresh'] = False
            login_session['mfa_verified'] = False
            login_session.pop('mfa_verified_at', None)

        with self._request_patches():
            replace_response = self.client.get('/mfa/replace', follow_redirects=False)
        self.assertTrue(replace_response.location.endswith('/mfa/reauth'))

        with self._request_patches():
            reauth_response = self.client.post(
                '/mfa/reauth',
                data={'code': pyotp.TOTP(secret).now()},
                follow_redirects=False,
            )

        self.assertEqual(reauth_response.status_code, 302)
        self.assertTrue(reauth_response.location.endswith('/mfa/replace'))
        with self.client.session_transaction() as login_session:
            self.assertTrue(login_session.get('_fresh'))
            self.assertTrue(login_session.get('mfa_verified'))
            self.assertIsNotNone(login_session.get('mfa_verified_at'))

    def test_totp_code_cannot_be_replayed(self):
        secret = pyotp.random_base32()
        self.settings.use_mfa = True
        db.session.commit()
        EnvSettings._cached_instance = None
        user = self._save_user(
            username='mfa-totp-replay-user',
            mfa_enabled=True,
            otp_secret=secret,
        )
        totp = pyotp.TOTP(secret)
        fixed_time = int(time.time())
        code = totp.at(fixed_time)
        expected_counter = fixed_time // totp.interval

        self._post_login('mfa-totp-replay-user', 'correct-password')
        with self._request_patches(), patch(
            'app.routes.mfa.mfa._matching_totp_counter',
            return_value=expected_counter,
        ):
            first_response = self.client.post(
                '/mfa/verify',
                data={'code': code},
                follow_redirects=False,
            )

        self.assertEqual(first_response.status_code, 302)
        self.assertTrue(first_response.location.endswith('/dashboard'))
        db.session.refresh(user)
        accepted_counter = user.last_totp_counter
        self.assertEqual(accepted_counter, expected_counter)

        with self.client.session_transaction() as login_session:
            login_session.clear()

        self._post_login('mfa-totp-replay-user', 'correct-password')
        with self._request_patches(), patch(
            'app.routes.mfa.mfa._matching_totp_counter',
            return_value=expected_counter,
        ):
            replay_response = self.client.post(
                '/mfa/verify',
                data={'code': code},
                follow_redirects=False,
            )

        self.assertEqual(replay_response.status_code, 200)
        db.session.refresh(user)
        self.assertEqual(user.last_totp_counter, accepted_counter)
        with self.client.session_transaction() as login_session:
            self.assertEqual(login_session.get('mfa_fail_count'), 1)
            self.assertNotIn('_user_id', login_session)

    def test_mfa_login_commit_failure_leaves_no_authenticated_session(self):
        secret = pyotp.random_base32()
        self.settings.use_mfa = True
        db.session.commit()
        EnvSettings._cached_instance = None
        user = self._save_user(
            username='mfa-commit-failure-user',
            mfa_enabled=True,
            otp_secret=secret,
        )

        self._post_login('mfa-commit-failure-user', 'correct-password')
        with self._request_patches(), patch(
            'app.routes.mfa.mfa.db.session.commit',
            side_effect=SQLAlchemyError('forced commit failure'),
        ):
            response = self.client.post(
                '/mfa/verify',
                data={'code': pyotp.TOTP(secret).now()},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/login'))
        db.session.expire_all()
        stored_user = db.session.get(User, user.id)
        self.assertIsNone(stored_user.last_totp_counter)
        with self.client.session_transaction() as login_session:
            self.assertNotIn('_user_id', login_session)
            self.assertNotIn('pre_2fa_user_id', login_session)
            self.assertNotIn('mfa_verified', login_session)

    def test_mfa_session_commit_failure_leaves_no_authenticated_session(self):
        secret = pyotp.random_base32()
        self.settings.use_mfa = True
        db.session.commit()
        EnvSettings._cached_instance = None
        user = self._save_user(
            username='mfa-session-commit-failure-user',
            mfa_enabled=True,
            otp_secret=secret,
        )

        self._post_login(
            'mfa-session-commit-failure-user',
            'correct-password',
        )
        real_commit = db.session.commit
        commit_count = 0

        def commit_once_then_fail():
            nonlocal commit_count
            commit_count += 1
            if commit_count == 2:
                raise SQLAlchemyError('forced session commit failure')
            return real_commit()

        with self._request_patches(), patch.object(
            db.session,
            'commit',
            side_effect=commit_once_then_fail,
        ):
            response = self.client.post(
                '/mfa/verify',
                data={'code': pyotp.TOTP(secret).now()},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/login'))
        db.session.expire_all()
        stored_user = db.session.get(User, user.id)
        self.assertIsNotNone(stored_user.last_totp_counter)
        self.assertEqual(UserSession.query.count(), 0)
        login_row = AuditLogin.query.one()
        self.assertFalse(login_row.success)
        self.assertEqual(login_row.failure_reason, 'login_rejected')
        with self.client.session_transaction() as login_session:
            self.assertNotIn('_user_id', login_session)
            self.assertNotIn('pre_2fa_user_id', login_session)
            self.assertNotIn('mfa_verified', login_session)

    def test_reauth_lockout_forces_full_login(self):
        secret = pyotp.random_base32()
        self._save_user(
            username='mfa-reauth-lockout-user',
            mfa_enabled=True,
            otp_secret=secret,
        )
        self._post_login('mfa-reauth-lockout-user', 'correct-password', remember=True)
        self.settings.use_mfa = True
        db.session.commit()
        EnvSettings._cached_instance = None
        remember_cookie_name = self.app.config.get(
            'REMEMBER_COOKIE_NAME',
            'remember_token',
        )
        self.assertIsNotNone(self.client.get_cookie(remember_cookie_name))

        with self.client.session_transaction() as login_session:
            login_session['_fresh'] = False

        with self._request_patches():
            self.client.get('/mfa/replace', follow_redirects=False)

        with self._request_patches(), patch(
            'app.routes.mfa.mfa._matching_totp_counter',
            return_value=None,
        ):
            for attempt in range(5):
                response = self.client.post(
                    '/mfa/reauth',
                    data={'code': '000000'},
                    follow_redirects=False,
                )
                if attempt < 4:
                    self.assertEqual(response.status_code, 200)
                else:
                    self.assertEqual(response.status_code, 302)
                    self.assertTrue(response.location.endswith('/login'))

        with self.client.session_transaction() as login_session:
            self.assertNotIn('_user_id', login_session)
        self.assertIsNone(self.client.get_cookie(remember_cookie_name))

    def test_disable_lockout_forces_full_login(self):
        secret = pyotp.random_base32()
        self._save_user(
            username='mfa-disable-lockout-user',
            mfa_enabled=True,
            otp_secret=secret,
        )
        self._post_login('mfa-disable-lockout-user', 'correct-password', remember=True)
        self.settings.use_mfa = True
        db.session.commit()
        EnvSettings._cached_instance = None
        remember_cookie_name = self.app.config.get(
            'REMEMBER_COOKIE_NAME',
            'remember_token',
        )
        self.assertIsNotNone(self.client.get_cookie(remember_cookie_name))

        with self.client.session_transaction() as login_session:
            login_session['mfa_verified'] = True
            login_session['mfa_verified_at'] = time.time()

        with self._request_patches(), patch(
            'app.routes.mfa.mfa._matching_totp_counter',
            return_value=None,
        ):
            for attempt in range(5):
                response = self.client.post(
                    '/mfa/disable',
                    data={'code': '000000'},
                    follow_redirects=False,
                )
                if attempt < 4:
                    self.assertEqual(response.status_code, 200)
                else:
                    self.assertEqual(response.status_code, 302)
                    self.assertTrue(response.location.endswith('/login'))

        with self.client.session_transaction() as login_session:
            self.assertNotIn('_user_id', login_session)
        self.assertIsNone(self.client.get_cookie(remember_cookie_name))

    def test_pending_authenticator_secret_expires(self):
        self.settings.use_mfa = True
        db.session.commit()
        EnvSettings._cached_instance = None
        old_secret = pyotp.random_base32()
        user = self._save_user(username='mfa-expired-setup-secret-user')
        user.pending_otp_secret = old_secret
        user.pending_otp_created_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        db.session.commit()

        self._post_login('mfa-expired-setup-secret-user', 'correct-password')
        with self._request_patches():
            response = self.client.get('/mfa/setup', follow_redirects=False)

        self.assertEqual(response.status_code, 200)
        db.session.refresh(user)
        self.assertNotEqual(user.pending_otp_secret, old_secret)
        self.assertIsNotNone(user.pending_otp_created_at)

    def test_user_delete_removes_recovery_codes_on_sqlite(self):
        user = self._save_user(username='mfa-delete-user')
        MfaRecoveryCode.generate_for_user(user, count=2)
        db.session.commit()
        user_id = user.id

        db.session.expire(user, ['mfa_recovery_codes'])
        db.session.delete(user)
        db.session.commit()

        self.assertIsNone(db.session.get(User, user_id))
        self.assertEqual(MfaRecoveryCode.query.filter_by(user_id=user_id).count(), 0)

    def test_future_mfa_timestamp_is_not_fresh(self):
        secret = pyotp.random_base32()
        self._save_user(
            username='mfa-future-freshness-user',
            mfa_enabled=True,
            otp_secret=secret,
        )
        self._post_login('mfa-future-freshness-user', 'correct-password')
        self.settings.use_mfa = True
        db.session.commit()
        EnvSettings._cached_instance = None

        with self.client.session_transaction() as login_session:
            login_session['mfa_verified'] = True
            login_session['mfa_verified_at'] = time.time() + 60

        with self._request_patches():
            response = self.client.post('/mfa/disable', follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/mfa/reauth'))

    def test_pending_disable_action_expires_before_completion(self):
        secret = pyotp.random_base32()
        user = self._save_user(
            username='mfa-expired-disable-action-user',
            mfa_enabled=True,
            otp_secret=secret,
        )
        self._post_login('mfa-expired-disable-action-user', 'correct-password')
        self.settings.use_mfa = True
        db.session.commit()
        EnvSettings._cached_instance = None

        with self.client.session_transaction() as login_session:
            login_session['mfa_verified'] = False
            login_session['mfa_verified_at'] = 0

        with self._request_patches():
            disable_response = self.client.post('/mfa/disable', follow_redirects=False)
        self.assertTrue(disable_response.location.endswith('/mfa/reauth'))

        with self.client.session_transaction() as login_session:
            login_session['mfa_reauth_requested_at'] = time.time() - 600

        with self._request_patches():
            reauth_response = self.client.post(
                '/mfa/reauth',
                data={'code': pyotp.TOTP(secret).now()},
                follow_redirects=False,
            )

        self.assertEqual(reauth_response.status_code, 302)
        self.assertTrue(reauth_response.location.endswith('/dashboard'))
        db.session.refresh(user)
        self.assertTrue(user.mfa_enabled)
        self.assertIsNotNone(user.otp_secret)


if __name__ == '__main__':
    unittest.main()
