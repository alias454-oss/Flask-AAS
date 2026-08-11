import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('ADMIN_SECRET', 'test-admin-secret')
os.environ.setdefault('SQLALCHEMY_DATABASE_URI', 'sqlite://')

from flask import Flask, g, request
from flask_login import LoginManager
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.decorators import log_view_action
from app.core.extensions import bcrypt, db
from app.core.logger import extract_request_metadata, redact_route_values
from app.core.security import get_client_ip
from app.core.trackers import (
    CLEAN_ONLINE_USER_MINUTES,
    expire_stale_online_users,
    get_admin_quick_stats,
    get_total_user_count_statistics,
    log_action,
    log_action_isolated,
    log_login,
    track_online_user,
)
from app.models import AuditActivity, AuditLogin, EnvSettings, OnlineUser, User


class AuditTrackingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        default_uri = f"sqlite:///{Path(cls.temp_dir.name) / 'audit-tests.db'}"

        cls.app = Flask(__name__)
        cls.app.config.update(
            TESTING=True,
            SECRET_KEY='audit-test-secret',
            SQLALCHEMY_DATABASE_URI=os.environ.get('AUDIT_TEST_DATABASE_URI', default_uri),
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            PROXY_HOPS=0,
            TRUSTED_PROXIES=[],
        )

        db.init_app(cls.app)
        bcrypt.init_app(cls.app)
        cls.login_manager = LoginManager(cls.app)

        @cls.login_manager.user_loader
        def load_user(session_id):
            return User.load_from_session_id(session_id)

        @cls.app.route('/reset-password/<token>')
        def reset_password_test(token):
            return token

        @cls.app.route('/sensitive/<token>')
        @log_view_action(redact_params={'token'})
        def sensitive_route_test(token):
            return token

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
        g.pop('_env_settings', None)
        if hasattr(self.app, '_trusted_proxies_cache'):
            delattr(self.app, '_trusted_proxies_cache')

    def tearDown(self):
        db.session.remove()
        EnvSettings._cached_instance = None
        g.pop('_env_settings', None)
        self.app_context.pop()

    def _new_user(self, username='audit-user', **overrides):
        values = {
            'username': username,
            'email': f'{username}@example.test',
            'hashed_password': 'not-used-in-audit-tests',
        }
        values.update(overrides)
        return User(**values)

    def _new_settings(self, owner, **overrides):
        values = {
            'user_id': owner.id,
            'site_name': 'Audit Test',
            'site_url': 'https://example.test',
            'site_lang': 'en',
            'site_timezone': 'UTC',
            'description': '',
            'keywords': '',
            'users_per_page': 20,
            'users_stored_path': '/tmp/users',
            'use_verify_email': False,
            'use_user_approval': False,
            'visitor_tracking': True,
            'enable_logging': False,
        }
        values.update(overrides)
        settings = EnvSettings(**values)
        db.session.add(settings)
        db.session.commit()
        EnvSettings._cached_instance = None
        g.pop('_env_settings', None)
        return settings

    @staticmethod
    def _clear_settings_cache():
        EnvSettings._cached_instance = None
        g.pop('_env_settings', None)

    def test_activity_and_business_change_commit_together(self):
        with self.app.test_request_context('/account', environ_base={'REMOTE_ADDR': '192.0.2.10'}):
            user = self._new_user()
            db.session.add(user)
            db.session.flush()

            log_action(
                user_id=user.id,
                action='account_created',
                target='account',
                extra_data={'source': 'test'},
            )
            db.session.commit()

        self.assertEqual(User.query.count(), 1)
        self.assertEqual(AuditActivity.query.count(), 1)

    def test_activity_can_be_queued_without_request_context(self):
        log_action(
            action='scheduled_maintenance',
            target='plugin:openauto',
            extra_data={'source': 'cli'},
        )
        db.session.commit()

        event = AuditActivity.query.one()
        self.assertIsNone(event.user_id)
        self.assertEqual(event.action, 'scheduled_maintenance')
        self.assertEqual(event.target, 'plugin:openauto')
        self.assertEqual(event.ip_address, 'unknown')
        self.assertEqual(event.extra_data, {'source': 'cli'})

    def test_activity_and_business_change_roll_back_together(self):
        with self.app.test_request_context('/account', environ_base={'REMOTE_ADDR': '192.0.2.10'}):
            user = self._new_user()
            db.session.add(user)
            db.session.flush()

            log_action(
                user_id=user.id,
                action='account_created',
                target='account',
            )
            db.session.rollback()

        self.assertEqual(User.query.count(), 0)
        self.assertEqual(AuditActivity.query.count(), 0)

    def test_activity_validation_does_not_rollback_pending_business_data(self):
        with self.app.test_request_context('/account', environ_base={'REMOTE_ADDR': '192.0.2.10'}):
            user = self._new_user()
            db.session.add(user)

            with self.assertRaises(TypeError):
                log_action(
                    action='invalid_metadata',
                    target='account',
                    extra_data='already serialized',
                )

            self.assertIn(user, db.session.new)
            db.session.rollback()

    def test_standalone_activity_does_not_commit_pending_business_data(self):
        with self.app.test_request_context('/contact', environ_base={'REMOTE_ADDR': '192.0.2.11'}):
            db.session.add(self._new_user())

            self.assertTrue(
                log_action_isolated(
                    action='contact_form_invalid',
                    target='contact.contact',
                    extra_data={'fields': ['email']},
                )
            )
            db.session.rollback()

        self.assertEqual(User.query.count(), 0)
        self.assertEqual(AuditActivity.query.count(), 1)

    def test_login_audit_does_not_commit_pending_business_data(self):
        with self.app.test_request_context('/login', environ_base={'REMOTE_ADDR': '192.0.2.12'}):
            db.session.add(self._new_user())

            self.assertTrue(
                log_login(
                    username='submitted-user',
                    ip='192.0.2.12',
                    user_agent='test-agent',
                    referer='https://example.test/reset/secret-token?next=private',
                    success=False,
                    failure_reason='invalid_credentials',
                )
            )
            db.session.rollback()

        self.assertEqual(User.query.count(), 0)
        login_event = AuditLogin.query.one()
        self.assertEqual(login_event.username, 'submitted-user')
        self.assertEqual(login_event.referer, 'https://example.test/reset/secret-token?next=private')
        self.assertFalse(login_event.success)
        self.assertEqual(login_event.failure_reason, 'invalid_credentials')

    def test_login_audit_requires_a_normalized_failure_reason(self):
        with self.app.test_request_context('/login', environ_base={'REMOTE_ADDR': '192.0.2.12'}):
            with self.assertRaises(ValueError):
                log_login(
                    username='submitted-user',
                    ip='192.0.2.12',
                    user_agent='test-agent',
                    referer=None,
                    success=False,
                    failure_reason='password_wrong',
                )

            with self.assertRaises(ValueError):
                log_login(
                    username='submitted-user',
                    ip='192.0.2.12',
                    user_agent='test-agent',
                    referer=None,
                    success=True,
                    failure_reason='invalid_credentials',
                )

        self.assertEqual(AuditLogin.query.count(), 0)

    def test_login_audit_failure_does_not_poison_request_session(self):
        with self.app.test_request_context('/login', environ_base={'REMOTE_ADDR': '192.0.2.12'}):
            db.session.add(self._new_user())

            with self.assertLogs('app.core.trackers', level='ERROR'):
                with patch.object(
                    db.engine,
                    'begin',
                    side_effect=SQLAlchemyError('audit database unavailable'),
                ):
                    self.assertFalse(
                        log_login(
                            username='submitted-user',
                            ip='192.0.2.12',
                            user_agent='test-agent',
                            referer=None,
                            success=False,
                            failure_reason='invalid_credentials',
                        )
                    )

            db.session.commit()

        self.assertEqual(User.query.count(), 1)
        self.assertEqual(AuditLogin.query.count(), 0)

    def test_online_tracking_does_not_commit_pending_business_data(self):
        with self.app.test_request_context('/', environ_base={'REMOTE_ADDR': '192.0.2.13'}):
            db.session.add(self._new_user())
            self.assertTrue(track_online_user())
            db.session.rollback()

        self.assertEqual(User.query.count(), 0)
        online = OnlineUser.query.one()
        self.assertEqual(online.ip_address, '192.0.2.13')
        self.assertEqual(online.user, OnlineUser.GUEST_USER)

    def test_online_tracking_failure_does_not_poison_request_session(self):
        with self.app.test_request_context('/', environ_base={'REMOTE_ADDR': '192.0.2.13'}):
            db.session.add(self._new_user())

            with self.assertLogs('app.core.trackers', level='ERROR'):
                with patch.object(
                    db.engine,
                    'begin',
                    side_effect=SQLAlchemyError('tracking database unavailable'),
                ):
                    self.assertFalse(track_online_user())

            db.session.commit()

        self.assertEqual(User.query.count(), 1)
        self.assertEqual(OnlineUser.query.count(), 0)

    def test_stale_online_cleanup_is_isolated_and_statistics_are_portable(self):
        stale = OnlineUser(
            user=OnlineUser.GUEST_USER,
            ip_address='192.0.2.14',
            last_active=datetime(2000, 1, 1, tzinfo=timezone.utc),
        )
        active = OnlineUser(
            user='active-user',
            ip_address='192.0.2.15',
        )
        db.session.add_all([stale, active])
        db.session.commit()

        with self.app.test_request_context('/', environ_base={'REMOTE_ADDR': '192.0.2.16'}):
            db.session.add(self._new_user())
            self.assertEqual(CLEAN_ONLINE_USER_MINUTES, 10)
            self.assertEqual(expire_stale_online_users(), 1)
            db.session.rollback()

        self.assertEqual(User.query.count(), 0)
        self.assertEqual(get_total_user_count_statistics('guest'), 0)
        self.assertEqual(get_total_user_count_statistics('online'), 1)

    def test_admin_pending_count_follows_enabled_account_requirements(self):
        owner = self._new_user(
            'settings-owner',
            activated=True,
            approved=True,
        )
        db.session.add(owner)
        db.session.flush()
        settings = self._new_settings(owner)

        db.session.add_all(
            [
                self._new_user('ready', activated=True, approved=True),
                self._new_user('unverified', activated=False, approved=True),
                self._new_user('unapproved', activated=True, approved=False),
                self._new_user('blocked-both', activated=False, approved=False),
            ]
        )
        db.session.commit()

        cases = (
            (False, False, 0),
            (True, False, 2),
            (False, True, 2),
            (True, True, 3),
        )
        for verify_email, user_approval, expected in cases:
            with self.subTest(
                verify_email=verify_email,
                user_approval=user_approval,
            ):
                settings.use_verify_email = verify_email
                settings.use_user_approval = user_approval
                db.session.commit()
                self._clear_settings_cache()

                stats = get_admin_quick_stats()

                self.assertEqual(stats['total_users'], 5)
                self.assertEqual(stats['pending_users'], expected)

    def test_admin_online_counts_use_cleanup_window_and_separate_guests(self):
        owner = self._new_user(
            'settings-owner',
            activated=True,
            approved=True,
        )
        db.session.add(owner)
        db.session.flush()
        settings = self._new_settings(owner, visitor_tracking=True)

        now = datetime.now(timezone.utc)
        stale = now - timedelta(minutes=CLEAN_ONLINE_USER_MINUTES + 1)
        db.session.add_all(
            [
                OnlineUser(user='member', ip_address='192.0.2.21', last_active=now),
                OnlineUser(
                    user=OnlineUser.GUEST_USER,
                    ip_address='192.0.2.22',
                    last_active=now,
                ),
                OnlineUser(
                    user='stale-member',
                    ip_address='192.0.2.23',
                    last_active=stale,
                ),
                OnlineUser(
                    user=OnlineUser.GUEST_USER,
                    ip_address='192.0.2.24',
                    last_active=stale,
                ),
            ]
        )
        db.session.commit()

        stats = get_admin_quick_stats()
        self.assertEqual(stats['online_users'], 1)
        self.assertEqual(stats['online_guests'], 1)

        settings.visitor_tracking = False
        db.session.commit()
        self._clear_settings_cache()

        stats = get_admin_quick_stats()
        self.assertFalse(stats['visitor_tracking_enabled'])
        self.assertIsNone(stats['online_users'])
        self.assertIsNone(stats['online_guests'])

    def test_extra_data_is_encoded_once_and_caller_values_are_preserved(self):
        with self.app.test_request_context('/admin', environ_base={'REMOTE_ADDR': '2001:db8::10'}):
            log_action(
                action='settings_updated',
                target='settings.settings',
                extra_data={
                    'token': 'do-not-store',
                    'password_generated': True,
                    'fields_updated': ['site_name'],
                },
            )
            db.session.commit()

        event = AuditActivity.query.one()
        self.assertEqual(event.ip_address, '2001:db8::10')
        self.assertEqual(event.extra_data['token'], 'do-not-store')
        self.assertTrue(event.extra_data['password_generated'])
        self.assertIsInstance(json.loads(event._extra_data), dict)

    def test_legacy_double_encoded_metadata_remains_readable(self):
        metadata = {'method': 'POST'}
        event = AuditActivity(action='legacy')
        event._extra_data = json.dumps(json.dumps(metadata))
        self.assertEqual(event.extra_data, metadata)

    def test_request_metadata_preserves_route_selected_request_details(self):
        headers = {
            'Authorization': 'Bearer secret',
            'Cookie': 'session=secret',
            'Referer': 'https://example.test/reset/token-value?next=private',
            'User-Agent': 'audit-test-agent',
            'X-Audit-Context': 'admin-review',
        }
        with self.app.test_request_context(
            '/reset-password/private-token?token=query-secret&next=dashboard',
            headers=headers,
            environ_base={'REMOTE_ADDR': '192.0.2.40'},
        ):
            self.app.preprocess_request()
            metadata = extract_request_metadata()

        self.assertEqual(metadata['ip'], '192.0.2.40')
        self.assertEqual(metadata['path'], '/reset-password/private-token')
        self.assertEqual(metadata['query_string'], 'token=query-secret&next=dashboard')
        self.assertEqual(
            metadata['referrer'],
            'https://example.test/reset/token-value?next=private',
        )
        self.assertEqual(metadata['headers']['X-Audit-Context'], 'admin-review')
        self.assertNotIn('Authorization', metadata['headers'])
        self.assertNotIn('Cookie', metadata['headers'])

    def test_request_metadata_redacts_declared_route_parameter(self):
        token = 'SUPER-SECRET-RESET-TOKEN-123'
        headers = {
            'Referer': f'https://example.test/reset/{token}?next={token}',
            'User-Agent': 'audit-test-agent',
            'X-Audit-Context': f'token={token}',
        }
        with self.app.test_request_context(
            f'/reset-password/{token}?token={token}&next=dashboard',
            headers=headers,
            environ_base={'REMOTE_ADDR': '192.0.2.41'},
        ):
            self.app.preprocess_request()
            metadata = extract_request_metadata(redact_params={'token'})
            target = redact_route_values(request.path, {'token'})

        serialized = json.dumps(metadata)
        self.assertNotIn(token, serialized)
        self.assertEqual(metadata['path'], '/reset-password/<redacted>')
        self.assertEqual(
            metadata['query_string'],
            'token=<redacted>&next=dashboard',
        )
        self.assertEqual(
            metadata['referrer'],
            'https://example.test/reset/<redacted>?next=<redacted>',
        )
        self.assertEqual(metadata['headers']['X-Audit-Context'], 'token=<redacted>')
        self.assertEqual(target, '/reset-password/<redacted>')

    def test_sensitive_route_prevents_referrer_propagation(self):
        with patch('app.core.decorators.audit_activity_enabled', return_value=False):
            response = self.app.test_client().get('/sensitive/secret-token')

        self.assertEqual(response.headers['Referrer-Policy'], 'no-referrer')

    def test_direct_request_ignores_spoofed_forwarding_header(self):
        self.app.config.update(PROXY_HOPS=0, TRUSTED_PROXIES=[])
        if hasattr(self.app, '_trusted_proxies_cache'):
            delattr(self.app, '_trusted_proxies_cache')

        with self.app.test_request_context(
            '/',
            headers={'X-Forwarded-For': '198.51.100.99'},
            environ_base={'REMOTE_ADDR': '192.0.2.20'},
        ):
            self.assertEqual(get_client_ip(), '192.0.2.20')

    def test_trusted_proxy_chain_returns_nearest_untrusted_client(self):
        self.app.config.update(
            PROXY_HOPS=1,
            TRUSTED_PROXIES=['10.0.0.0/8'],
        )
        if hasattr(self.app, '_trusted_proxies_cache'):
            delattr(self.app, '_trusted_proxies_cache')

        with self.app.test_request_context(
            '/',
            headers={'X-Forwarded-For': '198.51.100.25, 10.0.0.2'},
            environ_base={'REMOTE_ADDR': '10.0.0.3'},
        ):
            self.assertEqual(get_client_ip(), '198.51.100.25')

    def test_isolated_insert_uses_portable_column_names(self):
        with self.app.test_request_context('/', environ_base={'REMOTE_ADDR': '192.0.2.30'}):
            self.assertTrue(
                log_action_isolated(
                    action='portable_insert',
                    target='index.index',
                    extra_data={'method': 'GET'},
                )
            )

        with db.engine.connect() as connection:
            row = connection.execute(
                select(AuditActivity.__table__.c.extra_data)
            ).scalar_one()
        self.assertEqual(json.loads(row), {'method': 'GET'})


if __name__ == '__main__':
    unittest.main()
