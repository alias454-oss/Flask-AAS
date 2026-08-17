"""Tests for authenticated browser-session inactivity handling."""

import unittest
from datetime import timedelta
from unittest.mock import patch

from flask import Blueprint, Flask, request, session
from flask_login import (
    LoginManager,
    UserMixin,
    login_fresh,
    login_required,
    login_user,
)

from app.core.inactivity import (
    SESSION_ACTIVITY_KEY,
    enforce_inactivity_timeout,
    mark_session_activity,
)


class _SessionUser(UserMixin):
    id = 1

    def __init__(self):
        self.session_remembered = False

    def get_id(self):
        return '1:0'


class InactivityTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.user = _SessionUser()
        self.mutations = 0
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY='inactivity-test-secret',
            SESSION_INACTIVITY_TIMEOUT_SECONDS=10,
            REMEMBER_COOKIE_DURATION=timedelta(days=30),
        )

        login_manager = LoginManager(self.app)
        login_manager.login_view = 'login.login'

        @login_manager.user_loader
        def load_user(session_id):
            if session_id == self.user.get_id():
                return self.user
            return None

        login_bp = Blueprint('login', __name__)
        index_bp = Blueprint('index', __name__)

        @index_bp.route('/')
        def index():
            return 'index'

        @login_bp.route('/login')
        def login():
            remember = request.args.get('remember') == '1'
            self.user.session_remembered = remember
            login_user(self.user, remember=remember, fresh=True)
            session.permanent = remember
            mark_session_activity()
            return 'logged in'

        @self.app.route('/protected')
        @login_required
        def protected():
            return 'fresh' if login_fresh() else 'nonfresh'

        @self.app.route('/public')
        def public():
            return 'public'

        @self.app.route('/mutate', methods=['POST'])
        @login_required
        def mutate():
            self.mutations += 1
            return 'mutated'

        self.app.register_blueprint(login_bp)
        self.app.register_blueprint(index_bp)
        self.app.before_request(enforce_inactivity_timeout)
        self.client = self.app.test_client()

    def _login(self, timestamp=100.0, remember=False):
        with patch(
            'app.core.inactivity._current_timestamp',
            return_value=timestamp,
        ):
            response = self.client.get(
                '/login',
                query_string={'remember': '1' if remember else '0'},
            )
        self.assertEqual(response.status_code, 200)
        return response

    def _request_at(self, timestamp, path='/protected', method='GET'):
        with patch(
            'app.core.inactivity._current_timestamp',
            return_value=timestamp,
        ):
            return self.client.open(
                path,
                method=method,
                follow_redirects=False,
            )

    def test_authenticated_activity_refreshes_sliding_window(self):
        self._login(timestamp=100.0)

        first = self._request_at(105.0)
        second = self._request_at(114.0)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        with self.client.session_transaction() as login_session:
            self.assertEqual(login_session[SESSION_ACTIVITY_KEY], 114.0)

    def test_exact_timeout_without_remember_forces_full_login(self):
        self._login(timestamp=100.0, remember=False)

        with self.client.session_transaction() as login_session:
            login_session['mfa_verified'] = True
            login_session['mfa_verified_at'] = 100.0
            login_session['mfa_reauth_next'] = 'mfa.mfa_disable'

        response = self._request_at(110.0)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/login'))
        with self.client.session_transaction() as login_session:
            self.assertNotIn('_user_id', login_session)
            self.assertNotIn(SESSION_ACTIVITY_KEY, login_session)
            self.assertNotIn('mfa_verified', login_session)
            self.assertNotIn('mfa_verified_at', login_session)
            self.assertNotIn('mfa_reauth_next', login_session)
            self.assertEqual(
                login_session.get('_flashes'),
                [('warning', 'You have been logged out due to inactivity.')],
            )

    def test_exact_timeout_with_remember_downgrades_to_nonfresh(self):
        self._login(timestamp=100.0, remember=True)
        remember_cookie_name = self.app.config.get(
            'REMEMBER_COOKIE_NAME',
            'remember_token',
        )
        remember_cookie = self.client.get_cookie(remember_cookie_name)
        self.assertIsNotNone(remember_cookie)

        with self.client.session_transaction() as login_session:
            login_session['mfa_verified'] = True
            login_session['mfa_verified_at'] = 100.0
            login_session['mfa_reauth_next'] = 'mfa.mfa_disable'

        response = self._request_at(110.0)

        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.location.endswith('/protected'))
        self.assertEqual(
            self.client.get_cookie(remember_cookie_name).value,
            remember_cookie.value,
        )
        with self.client.session_transaction() as login_session:
            self.assertEqual(login_session.get('_user_id'), self.user.get_id())
            self.assertFalse(login_session.get('_fresh'))
            self.assertTrue(login_session.permanent)
            self.assertEqual(login_session[SESSION_ACTIVITY_KEY], 110.0)
            self.assertNotIn('mfa_verified', login_session)
            self.assertNotIn('mfa_verified_at', login_session)
            self.assertNotIn('mfa_reauth_next', login_session)
            self.assertEqual(
                login_session.get('_flashes'),
                [(
                    'info',
                    'Your remembered sign-in was restored after inactivity. '
                    'Reauthentication may be required for sensitive actions.',
                )],
            )

        restored = self._request_at(111.0)
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.get_data(as_text=True), 'nonfresh')

    def test_remembered_timeout_stops_state_changing_request(self):
        self._login(timestamp=100.0, remember=True)

        response = self._request_at(110.0, path='/mutate', method='POST')

        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.location.endswith('/'))
        self.assertEqual(self.mutations, 0)
        with self.client.session_transaction() as login_session:
            self.assertFalse(login_session.get('_fresh'))
            self.assertEqual(login_session[SESSION_ACTIVITY_KEY], 110.0)

    def test_missing_timestamp_starts_new_window(self):
        self._login(timestamp=100.0)
        with self.client.session_transaction() as login_session:
            login_session.pop(SESSION_ACTIVITY_KEY, None)
            login_session['last_active'] = '2026-08-05 05:00:00'

        response = self._request_at(500.0)

        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as login_session:
            self.assertEqual(login_session[SESSION_ACTIVITY_KEY], 500.0)
            self.assertNotIn('last_active', login_session)

    def test_malformed_timestamp_starts_new_window(self):
        self._login(timestamp=100.0)
        with self.client.session_transaction() as login_session:
            login_session[SESSION_ACTIVITY_KEY] = 'not-a-timestamp'

        response = self._request_at(500.0)

        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as login_session:
            self.assertEqual(login_session[SESSION_ACTIVITY_KEY], 500.0)

    def test_future_timestamp_starts_new_window(self):
        self._login(timestamp=100.0)
        with self.client.session_transaction() as login_session:
            login_session[SESSION_ACTIVITY_KEY] = 900.0

        response = self._request_at(500.0)

        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as login_session:
            self.assertEqual(login_session[SESSION_ACTIVITY_KEY], 500.0)

    def test_remember_cookie_restoration_starts_nonfresh_window(self):
        self._login(timestamp=100.0, remember=True)
        session_cookie_name = self.app.config.get('SESSION_COOKIE_NAME', 'session')
        remember_cookie_name = self.app.config.get(
            'REMEMBER_COOKIE_NAME',
            'remember_token',
        )
        self.client.delete_cookie(session_cookie_name)
        self.assertIsNotNone(self.client.get_cookie(remember_cookie_name))

        restored = self._request_at(500.0)

        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.get_data(as_text=True), 'nonfresh')
        with self.client.session_transaction() as login_session:
            self.assertEqual(login_session[SESSION_ACTIVITY_KEY], 500.0)
            self.assertFalse(login_session.get('_fresh'))

        expired = self._request_at(510.0)
        self.assertEqual(expired.status_code, 303)
        self.assertTrue(expired.location.endswith('/protected'))
        self.assertIsNotNone(self.client.get_cookie(remember_cookie_name))

        restored_again = self._request_at(511.0)
        self.assertEqual(restored_again.status_code, 200)
        self.assertEqual(restored_again.get_data(as_text=True), 'nonfresh')

    def test_unauthenticated_pending_mfa_state_is_ignored(self):
        with self.client.session_transaction() as login_session:
            login_session['pre_2fa_user_id'] = 42
            login_session['pre_2fa_time'] = 100.0

        response = self._request_at(500.0, path='/public')

        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as login_session:
            self.assertEqual(login_session['pre_2fa_user_id'], 42)
            self.assertEqual(login_session['pre_2fa_time'], 100.0)
            self.assertNotIn(SESSION_ACTIVITY_KEY, login_session)

    def test_static_requests_do_not_refresh_activity(self):
        self._login(timestamp=100.0)

        static_response = self._request_at(105.0, path='/static/missing.css')

        self.assertEqual(static_response.status_code, 404)
        with self.client.session_transaction() as login_session:
            self.assertEqual(login_session[SESSION_ACTIVITY_KEY], 100.0)

        expired = self._request_at(110.0)
        self.assertEqual(expired.status_code, 302)
        self.assertTrue(expired.location.endswith('/login'))

    def test_zero_timeout_disables_and_clears_activity_state(self):
        self._login(timestamp=100.0)
        self.app.config['SESSION_INACTIVITY_TIMEOUT_SECONDS'] = 0

        response = self._request_at(500.0)

        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as login_session:
            self.assertNotIn(SESSION_ACTIVITY_KEY, login_session)


if __name__ == '__main__':
    unittest.main()
