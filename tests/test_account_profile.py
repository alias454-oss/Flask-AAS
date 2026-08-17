import io
import os
import shutil
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from flask import Blueprint, Flask, g
from flask_login import LoginManager
from sqlalchemy.exc import SQLAlchemyError
from PIL import Image

from app.core.avatar import profile_image_data_uri, profile_image_root
from app.core.extensions import csrf, db, limiter
from app.models import AuditActivity, Country, EnvSettings, User, UserSession, Zone
from app.routes.account.account import account_bp
from app.routes.locations import locations_bp


class AccountProfileRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(cls.temp_dir.name) / 'account-profile-tests.db'
        default_uri = f'sqlite:///{database_path}'
        template_path = Path(__file__).resolve().parents[1] / 'app' / 'templates'
        static_path = Path(__file__).resolve().parents[1] / 'app' / 'static'

        cls.app = Flask(
            __name__,
            template_folder=str(template_path),
            static_folder=str(static_path),
        )
        cls.app.config.update(
            TESTING=True,
            SECRET_KEY='account-profile-test-secret',
            SQLALCHEMY_DATABASE_URI=os.environ.get(
                'ACCOUNT_TEST_DATABASE_URI',
                default_uri,
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

        cls.login_manager = LoginManager(cls.app)
        cls.login_manager.login_view = 'login.login'

        @cls.login_manager.user_loader
        def load_user(session_id):
            return User.load_from_session_id(session_id)

        login_bp = Blueprint('login', __name__)

        @login_bp.route('/login')
        def login():
            return 'login'

        index_bp = Blueprint('index', __name__)

        @index_bp.route('/')
        def index():
            return 'index'

        about_bp = Blueprint('about', __name__)

        @about_bp.route('/about')
        def about():
            return 'about'

        dashboard_bp = Blueprint('dashboard', __name__)

        @dashboard_bp.route('/dashboard')
        def dashboard():
            return 'dashboard'

        logout_bp = Blueprint('logout', __name__)

        @logout_bp.route('/logout')
        def logout():
            return 'logout'

        reset_bp = Blueprint('reset', __name__)

        @reset_bp.route('/change-password')
        def change_password():
            return 'change-password'

        mfa_bp = Blueprint('mfa', __name__)

        @mfa_bp.route('/mfa/setup')
        def mfa_setup():
            return 'mfa-setup'

        @mfa_bp.route('/mfa/replace')
        def mfa_replace():
            return 'mfa-replace'

        @mfa_bp.route('/mfa/recovery-codes')
        def mfa_recovery_codes():
            return 'mfa-recovery-codes'

        @mfa_bp.route('/mfa/disable')
        def mfa_disable():
            return 'mfa-disable'

        cls.app.register_blueprint(account_bp)
        cls.app.register_blueprint(locations_bp)
        cls.app.register_blueprint(login_bp)
        cls.app.register_blueprint(index_bp)
        cls.app.register_blueprint(about_bp)
        cls.app.register_blueprint(dashboard_bp)
        cls.app.register_blueprint(logout_bp)
        cls.app.register_blueprint(reset_bp)
        cls.app.register_blueprint(mfa_bp)

        @cls.app.context_processor
        def inject_template_context():
            return {
                'tpl_path': 'themes/default',
                'env': EnvSettings.get_cached_instance(),
                'nonce': '',
                'sidebar_position': 'right',
                'page_gen_time': 0,
                'current_year': 2026,
            }

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
        self.image_root = Path(self.temp_dir.name) / 'profile-images'
        shutil.rmtree(self.image_root, ignore_errors=True)
        self.image_root.mkdir(parents=True, exist_ok=True)

        owner = User(
            username='settings-owner',
            email='settings-owner@example.test',
            activated=True,
            approved=True,
        )
        owner.set_password('owner-password')
        db.session.add(owner)
        db.session.flush()

        self.settings = EnvSettings(
            user_id=owner.id,
            site_name='Account Profile Test',
            site_lang='en',
            site_timezone='UTC',
            description='',
            keywords='',
            users_per_page=20,
            users_stored_path=str(self.image_root),
            use_captcha=True,
            use_mfa=False,
            use_verify_email=True,
            use_user_approval=True,
            use_user_location=True,
            contact_enabled=False,
            visitor_tracking=False,
            enable_logging=False,
        )
        db.session.add(self.settings)

        us = Country(name='United States', iso_code_2='US', iso_code_3='USA')
        ca = Country(name='Canada', iso_code_2='CA', iso_code_3='CAN')
        db.session.add_all([us, ca])
        db.session.flush()
        db.session.add_all([
            Zone(country_id=us.country_id, code='US-IL', name='Illinois', type='State'),
            Zone(country_id=ca.country_id, code='CA-ON', name='Ontario', type='Province'),
        ])

        user = User(
            username='profile-user',
            email='profile-user@example.test',
            company_name='Existing Company',
            first_name='Existing',
            last_name='User',
            phone='555-0100',
            alt_phone='555-0101',
            fax='555-0102',
            country_code='US',
            address='100 Existing Street',
            city='Existing City',
            zone_code='US-IL',
            postal_code='61032',
            activated=True,
            approved=True,
            admin_notes='Administrator-only note',
            reg_date=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        user.set_password('correct-password')
        db.session.add(user)
        db.session.commit()
        self.user_id = user.id
        self.original_auth_version = user.auth_version
        EnvSettings._cached_instance = None

        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        EnvSettings._cached_instance = None
        self.app_context.pop()

    def _login(
        self,
        *,
        ip_address='127.0.0.1',
        user_agent='account-profile-test-agent',
        remembered=False,
        now=None,
    ):
        user = db.session.get(User, self.user_id)
        user_session = UserSession.issue_for_user(
            user,
            ip_address=ip_address,
            user_agent=user_agent,
            remembered=remembered,
            now=now,
        )
        db.session.commit()
        with self.client.session_transaction() as session:
            session['_user_id'] = user.get_id()
            session['_fresh'] = True
        return user_session

    def _request_patches(self, *, route_audit=False, render=True):
        stack = ExitStack()
        stack.enter_context(
            patch('app.core.decorators.audit_activity_enabled', return_value=False)
        )
        if not route_audit:
            stack.enter_context(
                patch(
                    'app.routes.account.account.audit_activity_enabled',
                    return_value=False,
                )
            )
        if render:
            stack.enter_context(
                patch(
                    'app.routes.account.account.render_template',
                    return_value='account',
                )
            )
        return stack

    def _post(self, data, *, route_audit=False, render=True):
        self._login()
        with self._request_patches(route_audit=route_audit, render=render):
            return self.client.post(
                '/account',
                data=data,
                follow_redirects=False,
            )

    def _profile_image_payload(self, *, format='JPEG', size=(480, 320), exif=False):
        image = Image.new('RGB', size, color=(40, 90, 140))
        payload = io.BytesIO()
        save_kwargs = {}
        if exif and format == 'JPEG':
            metadata = Image.Exif()
            metadata[0x010E] = 'private profile metadata'
            save_kwargs['exif'] = metadata
        image.save(payload, format=format, **save_kwargs)
        image.close()
        payload.seek(0)
        return payload

    def _upload_profile_image(self, payload=None, filename='avatar.jpg', *, route_audit=False):
        if payload is None:
            payload = self._profile_image_payload()
        self._login()
        with self._request_patches(route_audit=route_audit):
            return self.client.post(
                '/account/profile-image',
                data={'image': (payload, filename)},
                content_type='multipart/form-data',
                follow_redirects=False,
            )

    def test_user_storage_path_uses_complete_configured_directory(self):
        self.assertEqual(profile_image_root(), self.image_root.resolve())

        self.settings.users_stored_path = 'uploads/users'
        db.session.commit()
        EnvSettings._cached_instance = None
        if hasattr(g, '_env_settings'):
            delattr(g, '_env_settings')

        self.assertEqual(
            profile_image_root(),
            (Path(self.app.root_path).parent / 'uploads/users').resolve(),
        )

    def test_login_is_required(self):
        with self._request_patches():
            response = self.client.get('/account', follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.location)

    def test_version_only_identity_is_not_an_active_browser_session(self):
        user = db.session.get(User, self.user_id)
        version_only_identity = f'{user.id}:{user.auth_version}'

        self.assertIsNone(User.load_from_session_id(version_only_identity))
        self.assertEqual(
            User.load_from_session_id(
                version_only_identity,
                require_session_record=False,
            ).id,
            user.id,
        )

    def test_get_populates_profile_form_from_current_user(self):
        self._login()

        with self._request_patches(render=False), patch(
            'app.routes.account.account.render_template',
            return_value='account',
        ) as render:
            response = self.client.get('/account')

        self.assertEqual(response.status_code, 200)
        form = render.call_args.kwargs['form']
        self.assertEqual(form.company_name.data, 'Existing Company')
        self.assertEqual(form.first_name.data, 'Existing')
        self.assertEqual(form.alt_phone.data, '555-0101')
        self.assertEqual(form.address.data, '100 Existing Street')

    def test_profile_page_renders_read_only_identity_without_captcha(self):
        self._login()

        with self._request_patches(render=False):
            response = self.client.get('/account')

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('<div class="account-summary-grid">', html)
        self.assertIn('<legend>Account Details</legend>', html)
        self.assertIn('<legend>Active Sessions</legend>', html)
        self.assertIn('<legend>Update Account Details</legend>', html)
        self.assertLess(
            html.index('<legend>Account Details</legend>'),
            html.index('<legend>Update Account Details</legend>'),
        )
        self.assertLess(
            html.index('<legend>Active Sessions</legend>'),
            html.index('<legend>Update Account Details</legend>'),
        )
        self.assertIn('<legend>Location</legend>', html)
        self.assertIn('name="country_code"', html)
        self.assertIn('name="zone_code"', html)
        self.assertIn('name="postal_code"', html)
        self.assertNotIn('<legend>Account Security</legend>', html)
        self.assertIn('If you need to change your password,', html)
        self.assertIn('change it here</a>.', html)
        self.assertIn(
            'href="/change-password">change it here</a>.',
            html,
        )
        self.assertIn('profile-user@example.test', html)
        self.assertIn('id="account-identity-name"', html)
        self.assertIn('Existing User', html)
        self.assertIn('class="account-identity__username">profile-user</div>', html)
        self.assertIn('class="account-identity__email">profile-user@example.test</div>', html)
        self.assertIn('<dl class="account-details-list">', html)
        self.assertIn('<dt>Email Status</dt>', html)
        self.assertIn('Email changes are not currently available.', html)
        self.assertIn('<dt>Previous Login</dt>', html)
        self.assertIn('<dd>None</dd>', html)
        self.assertIn('Current Session', html)
        self.assertIn('account-profile-test-agent', html)
        self.assertNotIn('name="email"', html)
        self.assertNotIn('name="username"', html)
        self.assertNotIn('<dt>MFA</dt>', html)
        self.assertNotIn('CAPTCHA', html)

    def test_mfa_controls_are_part_of_account_details_when_enabled(self):
        self.settings.use_mfa = True
        db.session.commit()
        EnvSettings._cached_instance = None
        self._login()

        with self._request_patches(render=False):
            disabled_response = self.client.get('/account')

        disabled_html = disabled_response.get_data(as_text=True)
        self.assertIn('<dt>MFA</dt>', disabled_html)
        self.assertIn('Disabled', disabled_html)
        self.assertIn('/mfa/setup', disabled_html)
        self.assertNotIn('<legend>Account Security</legend>', disabled_html)

        user = db.session.get(User, self.user_id)
        user.mfa_enabled = True
        user.otp_secret = 'JBSWY3DPEHPK3PXP'
        db.session.commit()

        with self._request_patches(render=False):
            enabled_response = self.client.get('/account')

        enabled_html = enabled_response.get_data(as_text=True)
        self.assertIn('Enabled', enabled_html)
        self.assertIn('/mfa/replace', enabled_html)
        self.assertIn('/mfa/recovery-codes', enabled_html)
        self.assertIn('/mfa/disable', enabled_html)
        self.assertNotIn('<legend>Account Security</legend>', enabled_html)

    def test_profile_page_renders_profile_image_controls(self):
        self._login()

        with self._request_patches(render=False):
            response = self.client.get('/account')

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('class="profile-image-preview"', html)
        self.assertIn('images/no_user.jpg', html)
        identity_section = html.split('<section class="account-identity"', 1)[1].split('</section>', 1)[0]
        update_section = html.split('<legend>Update Account Details</legend>', 1)[1]
        self.assertIn('class="profile-image-form"', identity_section)
        self.assertIn('class="profile-image-help"', identity_section)
        self.assertNotIn('class="profile-image-form"', update_section)
        self.assertIn('action="/account/profile-image"', html)
        self.assertIn('accept="image/jpeg,image/png,image/webp"', html)
        self.assertIn('value="Upload Image"', html)
        self.assertNotIn('/account/profile-image/remove', html)

    def test_valid_profile_image_is_normalized_and_rendered_as_data_uri(self):
        response = self._upload_profile_image(
            self._profile_image_payload(size=(640, 320), exif=True),
            'camera-original.jpg',
        )

        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        user = db.session.get(User, self.user_id)
        self.assertRegex(user.image, r'^[0-9a-f]{32}\.webp$')
        self.assertNotIn('camera-original', user.image)

        stored_path = self.image_root / user.image
        self.assertTrue(stored_path.is_file())
        with Image.open(stored_path) as stored:
            self.assertEqual(stored.format, 'WEBP')
            self.assertEqual(stored.size, (256, 256))
            self.assertEqual(len(stored.getexif()), 0)

        data_uri = profile_image_data_uri(user.image)
        self.assertTrue(data_uri.startswith('data:image/webp;base64,'))

        with self._request_patches(render=False):
            rendered = self.client.get('/account')
        self.assertIn('data:image/webp;base64,', rendered.get_data(as_text=True))

    def test_png_and_webp_sources_are_accepted(self):
        for image_format, filename in (('PNG', 'avatar.png'), ('WEBP', 'avatar.webp')):
            with self.subTest(image_format=image_format):
                response = self._upload_profile_image(
                    self._profile_image_payload(format=image_format),
                    filename,
                )
                self.assertEqual(response.status_code, 302)
                db.session.expire_all()
                user = db.session.get(User, self.user_id)
                self.assertTrue(user.image.endswith('.webp'))
                (self.image_root / user.image).unlink()
                user.image = None
                db.session.commit()

    def test_invalid_image_and_invalid_stored_name_are_not_exposed(self):
        response = self._upload_profile_image(
            io.BytesIO(b'not an image'),
            'avatar.png',
        )
        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        self.assertIsNone(db.session.get(User, self.user_id).image)
        self.assertEqual(list(self.image_root.iterdir()), [])
        self.assertIsNone(profile_image_data_uri('../../etc/passwd'))

    def test_profile_image_source_size_limit_is_enforced(self):
        original_limit = self.app.config.get('USER_IMAGE_MAX_BYTES')
        self.app.config['USER_IMAGE_MAX_BYTES'] = 32
        try:
            response = self._upload_profile_image(
                self._profile_image_payload(),
                'avatar.jpg',
            )
        finally:
            if original_limit is None:
                self.app.config.pop('USER_IMAGE_MAX_BYTES', None)
            else:
                self.app.config['USER_IMAGE_MAX_BYTES'] = original_limit

        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        self.assertIsNone(db.session.get(User, self.user_id).image)
        self.assertEqual(list(self.image_root.iterdir()), [])

    def test_profile_image_upload_has_route_specific_request_size_limit(self):
        original_limit = self.app.config.get('USER_IMAGE_MAX_BYTES')
        self.app.config['USER_IMAGE_MAX_BYTES'] = 32
        self._login()
        try:
            response = self.client.post(
                '/account/profile-image',
                data={'image': (io.BytesIO(b'x' * 300_000), 'oversized.jpg')},
                content_type='multipart/form-data',
                follow_redirects=False,
            )
        finally:
            if original_limit is None:
                self.app.config.pop('USER_IMAGE_MAX_BYTES', None)
            else:
                self.app.config['USER_IMAGE_MAX_BYTES'] = original_limit

        self.assertEqual(response.status_code, 413)
        db.session.expire_all()
        self.assertIsNone(db.session.get(User, self.user_id).image)
        self.assertEqual(list(self.image_root.iterdir()), [])

    def test_replacing_and_removing_profile_image_cleans_generated_files(self):
        first_response = self._upload_profile_image()
        self.assertEqual(first_response.status_code, 302)
        db.session.expire_all()
        first_filename = db.session.get(User, self.user_id).image
        first_path = self.image_root / first_filename
        self.assertTrue(first_path.is_file())

        second_response = self._upload_profile_image(
            self._profile_image_payload(format='PNG'),
            'replacement.png',
        )
        self.assertEqual(second_response.status_code, 302)
        db.session.expire_all()
        second_filename = db.session.get(User, self.user_id).image
        second_path = self.image_root / second_filename
        self.assertNotEqual(first_filename, second_filename)
        self.assertFalse(first_path.exists())
        self.assertTrue(second_path.is_file())

        self._login()
        with self._request_patches():
            removed = self.client.post(
                '/account/profile-image/remove',
                follow_redirects=False,
            )
        self.assertEqual(removed.status_code, 302)
        db.session.expire_all()
        self.assertIsNone(db.session.get(User, self.user_id).image)
        self.assertFalse(second_path.exists())

    def test_profile_image_removal_commit_failure_keeps_database_and_file(self):
        upload_response = self._upload_profile_image()
        self.assertEqual(upload_response.status_code, 302)
        db.session.expire_all()
        filename = db.session.get(User, self.user_id).image
        stored_path = self.image_root / filename
        self.assertTrue(stored_path.is_file())

        self._login()
        with self._request_patches(), patch.object(
            db.session,
            'commit',
            side_effect=SQLAlchemyError('database unavailable'),
        ):
            response = self.client.post(
                '/account/profile-image/remove',
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        self.assertEqual(db.session.get(User, self.user_id).image, filename)
        self.assertTrue(stored_path.is_file())

    def test_profile_image_commit_failure_removes_new_file_and_keeps_database_state(self):
        self._login()
        with self._request_patches(), patch.object(
            db.session,
            'commit',
            side_effect=SQLAlchemyError('database unavailable'),
        ):
            response = self.client.post(
                '/account/profile-image',
                data={'image': (self._profile_image_payload(), 'avatar.jpg')},
                content_type='multipart/form-data',
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        self.assertIsNone(db.session.get(User, self.user_id).image)
        self.assertEqual(list(self.image_root.iterdir()), [])

    def test_profile_image_update_audit_does_not_store_filenames(self):
        self.settings.enable_logging = True
        db.session.commit()
        EnvSettings._cached_instance = None

        response = self._upload_profile_image(
            self._profile_image_payload(),
            'sensitive-original-name.jpg',
            route_audit=True,
        )

        self.assertEqual(response.status_code, 302)
        event = AuditActivity.query.filter_by(action='profile_image_updated').one()
        self.assertEqual(event.user_id, self.user_id)
        self.assertEqual(event.extra_data, {'replaced_existing': False})
        self.assertNotIn('sensitive-original-name', event._extra_data)
        db.session.expire_all()
        filename = db.session.get(User, self.user_id).image
        self.assertNotIn(filename, event._extra_data)

    def test_valid_profile_update_normalizes_values_and_protects_identity_fields(self):
        response = self._post(
            {
                'company_name': '  Updated Company  ',
                'first_name': 'Updated',
                'last_name': 'Person',
                'phone': '   ',
                'alt_phone': '555-0201',
                'fax': '555-0202',
                'country_code': 'US',
                'address': '200 Updated Street',
                'city': 'Freeport',
                'zone_code': 'US-IL',
                'postal_code': '61032',
                'username': 'attacker-selected-name',
                'email': 'attacker@example.test',
                'approved': '',
                'admin_notes': 'changed by profile request',
                'auth_version': '999',
            }
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/account'))

        db.session.expire_all()
        user = db.session.get(User, self.user_id)
        self.assertEqual(user.company_name, 'Updated Company')
        self.assertEqual(user.first_name, 'Updated')
        self.assertEqual(user.last_name, 'Person')
        self.assertIsNone(user.phone)
        self.assertEqual(user.alt_phone, '555-0201')
        self.assertEqual(user.fax, '555-0202')
        self.assertEqual(user.country_code, 'US')
        self.assertEqual(user.address, '200 Updated Street')
        self.assertEqual(user.city, 'Freeport')
        self.assertEqual(user.zone_code, 'US-IL')
        self.assertEqual(user.postal_code, '61032')
        self.assertEqual(user.username, 'profile-user')
        self.assertEqual(user.email, 'profile-user@example.test')
        self.assertTrue(user.approved)
        self.assertEqual(user.admin_notes, 'Administrator-only note')
        self.assertEqual(user.auth_version, self.original_auth_version)

    def test_zone_must_belong_to_selected_country(self):
        response = self._post(
            {
                'country_code': 'CA',
                'zone_code': 'US-IL',
                'city': 'Toronto',
                'postal_code': 'M5V',
            }
        )

        self.assertEqual(response.status_code, 200)
        db.session.expire_all()
        user = db.session.get(User, self.user_id)
        self.assertEqual(user.country_code, 'US')
        self.assertEqual(user.zone_code, 'US-IL')
        self.assertEqual(user.city, 'Existing City')

    def test_disabled_location_fields_cannot_be_changed_by_posting_them(self):
        self.settings.use_user_location = False
        db.session.commit()
        EnvSettings._cached_instance = None

        response = self._post(
            {
                'first_name': 'Updated',
                'country_code': 'CA',
                'address': 'Maliciously changed address',
                'city': 'Toronto',
                'zone_code': 'CA-ON',
                'postal_code': 'M5V',
            }
        )

        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        user = db.session.get(User, self.user_id)
        self.assertEqual(user.first_name, 'Updated')
        self.assertEqual(user.country_code, 'US')
        self.assertEqual(user.address, '100 Existing Street')
        self.assertEqual(user.city, 'Existing City')
        self.assertEqual(user.zone_code, 'US-IL')
        self.assertEqual(user.postal_code, '61032')

    def test_invalid_field_length_does_not_modify_profile(self):
        response = self._post({'company_name': 'x' * 256})

        self.assertEqual(response.status_code, 200)
        db.session.expire_all()
        user = db.session.get(User, self.user_id)
        self.assertEqual(user.company_name, 'Existing Company')
        self.assertEqual(AuditActivity.query.count(), 0)

    def test_normalized_no_op_does_not_write_audit_event(self):
        self.settings.enable_logging = True
        db.session.commit()
        EnvSettings._cached_instance = None

        self._login()
        with self._request_patches(route_audit=True), patch(
            'app.routes.account.account.log_action'
        ) as log_action:
            response = self.client.post(
                '/account',
                data={
                    'company_name': ' Existing Company ',
                    'first_name': ' Existing ',
                    'last_name': ' User ',
                    'phone': '555-0100',
                    'alt_phone': '555-0101',
                    'fax': '555-0102',
                    'country_code': 'US',
                    'address': '100 Existing Street',
                    'city': 'Existing City',
                    'zone_code': 'US-IL',
                    'postal_code': '61032',
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        log_action.assert_not_called()
        self.assertEqual(AuditActivity.query.count(), 0)

    def test_successful_update_and_safe_audit_event_commit_together(self):
        self.settings.enable_logging = True
        db.session.commit()
        EnvSettings._cached_instance = None

        response = self._post(
            {
                'first_name': 'Audited',
                'phone': '555-0300',
            },
            route_audit=True,
        )

        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        user = db.session.get(User, self.user_id)
        self.assertEqual(user.first_name, 'Audited')
        self.assertEqual(user.phone, '555-0300')

        event = AuditActivity.query.filter_by(action='profile_updated').one()
        self.assertEqual(event.user_id, self.user_id)
        self.assertEqual(event.target, 'account.account')
        self.assertEqual(
            event.extra_data,
            {'changed_fields': ['first_name', 'phone']},
        )
        self.assertNotIn('Audited', event._extra_data)
        self.assertNotIn('555-0300', event._extra_data)

    def test_commit_failure_rolls_back_profile_and_audit_event(self):
        self.settings.enable_logging = True
        db.session.commit()
        EnvSettings._cached_instance = None

        self._login()
        with self._request_patches(route_audit=True), patch.object(
            db.session,
            'commit',
            side_effect=SQLAlchemyError('database unavailable'),
        ):
            response = self.client.post(
                '/account',
                data={'first_name': 'Should Roll Back'},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 200)
        db.session.expire_all()
        user = db.session.get(User, self.user_id)
        self.assertEqual(user.first_name, 'Existing')
        self.assertEqual(
            AuditActivity.query.filter_by(action='profile_updated').count(),
            0,
        )

    def test_previous_login_is_relative_to_the_current_session(self):
        previous_time = datetime(2026, 8, 6, 14, 0, tzinfo=timezone.utc)
        current_time = previous_time + timedelta(hours=1)
        later_time = current_time + timedelta(hours=1)
        self._login(now=previous_time)
        current_record = self._login(now=current_time)

        user = db.session.get(User, self.user_id)
        UserSession.issue_for_user(
            user,
            ip_address='192.0.2.10',
            user_agent='later-session-agent',
            now=later_time,
        )
        db.session.commit()

        with self._request_patches(render=False), patch(
            'app.routes.account.account.render_template',
            return_value='account',
        ) as render:
            response = self.client.get('/account')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            render.call_args.kwargs['current_session_id'],
            current_record.id,
        )
        rendered_previous_login = render.call_args.kwargs['previous_login_at']
        self.assertEqual(
            rendered_previous_login.strftime('%Y-%m-%d %H:%M:%S'),
            previous_time.strftime('%Y-%m-%d %H:%M:%S'),
        )

    def test_first_session_has_no_previous_login(self):
        self._login()

        with self._request_patches(render=False), patch(
            'app.routes.account.account.render_template',
            return_value='account',
        ) as render:
            response = self.client.get('/account')

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(render.call_args.kwargs['previous_login_at'])

    def test_user_can_revoke_another_session_but_not_the_current_session(self):
        self.settings.enable_logging = True
        db.session.commit()
        EnvSettings._cached_instance = None

        current_record = self._login()
        user = db.session.get(User, self.user_id)
        other_record = UserSession.issue_for_user(
            user,
            ip_address='192.0.2.20',
            user_agent='other-session-agent',
            remembered=True,
        )
        other_identity = user.get_id()
        db.session.commit()

        response = self.client.post(
            f'/account/sessions/{other_record.id}/revoke',
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        stored_current = db.session.get(UserSession, current_record.id)
        stored_other = db.session.get(UserSession, other_record.id)
        self.assertIsNone(stored_current.revoked_at)
        self.assertIsNotNone(stored_other.revoked_at)
        self.assertIsNone(User.load_from_session_id(other_identity))

        event = AuditActivity.query.filter_by(action='session_revoked').one()
        self.assertEqual(event.extra_data, {'session_id': other_record.id})

        current_response = self.client.post(
            f'/account/sessions/{current_record.id}/revoke',
            follow_redirects=False,
        )
        self.assertEqual(current_response.status_code, 302)
        db.session.refresh(stored_current)
        self.assertIsNone(stored_current.revoked_at)

    def test_user_can_revoke_all_other_sessions(self):
        self.settings.enable_logging = True
        db.session.commit()
        EnvSettings._cached_instance = None

        current_record = self._login()
        user = db.session.get(User, self.user_id)
        other_records = [
            UserSession.issue_for_user(
                user,
                ip_address=f'192.0.2.{index}',
                user_agent=f'other-agent-{index}',
            )
            for index in (30, 31)
        ]
        db.session.commit()

        response = self.client.post(
            '/account/sessions/revoke-others',
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        self.assertIsNone(db.session.get(UserSession, current_record.id).revoked_at)
        for record in other_records:
            self.assertIsNotNone(db.session.get(UserSession, record.id).revoked_at)

        event = AuditActivity.query.filter_by(
            action='other_sessions_revoked'
        ).one()
        self.assertEqual(event.extra_data['session_count'], 2)
        self.assertEqual(
            event.extra_data['session_ids'],
            sorted(record.id for record in other_records),
        )

    def test_session_revocation_cannot_target_another_user(self):
        self._login()
        other_user = User(
            username='other-user',
            email='other-user@example.test',
            activated=True,
            approved=True,
        )
        other_user.set_password('other-password')
        db.session.add(other_user)
        db.session.flush()
        other_record = UserSession.issue_for_user(
            other_user,
            ip_address='192.0.2.40',
            user_agent='foreign-session-agent',
        )
        db.session.commit()

        response = self.client.post(
            f'/account/sessions/{other_record.id}/revoke',
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        self.assertIsNone(db.session.get(UserSession, other_record.id).revoked_at)


if __name__ == '__main__':
    unittest.main()
