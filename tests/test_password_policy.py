import tempfile
import unittest
from pathlib import Path

from flask import Flask

from app.core.extensions import bcrypt, db
from app.core.passwords import generate_random_password, password_policy_errors
from app.models import EnvSettings, User


class PasswordPolicyTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            BCRYPT_HANDLE_LONG_PASSWORDS=True,
        )
        bcrypt.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()

    def tearDown(self):
        self.context.pop()

    def test_default_policy_requires_twenty_characters_and_allows_passphrases(self):
        self.assertIn(
            "Password must be at least 20 characters long.",
            password_policy_errors("x" * 19),
        )
        self.assertEqual(password_policy_errors("this is a long phrase"), [])

    def test_complexity_requirements_are_configurable(self):
        self.app.config.update(
            PASSWORD_MIN_LENGTH=8,
            PASSWORD_REQUIRE_UPPERCASE=True,
            PASSWORD_REQUIRE_LOWERCASE=True,
            PASSWORD_REQUIRE_NUMBER=True,
            PASSWORD_REQUIRE_SPECIAL=True,
        )

        self.assertEqual(password_policy_errors("Abcdef1!"), [])
        errors = password_policy_errors("abcdefgh")
        self.assertIn("Password must contain at least one uppercase letter.", errors)
        self.assertIn("Password must contain at least one number.", errors)
        self.assertIn(
            "Password must contain at least one non-alphanumeric character.",
            errors,
        )

    def test_policy_can_be_explicitly_disabled(self):
        self.app.config.update(
            PASSWORD_POLICY_ENABLED=False,
            PASSWORD_MIN_LENGTH=20,
            PASSWORD_REQUIRE_UPPERCASE=True,
        )

        self.assertEqual(password_policy_errors("short"), [])

    def test_generator_never_undershoots_active_policy(self):
        self.app.config.update(
            PASSWORD_MIN_LENGTH=24,
            PASSWORD_REQUIRE_UPPERCASE=True,
            PASSWORD_REQUIRE_LOWERCASE=True,
            PASSWORD_REQUIRE_NUMBER=True,
            PASSWORD_REQUIRE_SPECIAL=True,
        )

        password = generate_random_password(length=8)

        self.assertGreaterEqual(len(password), 24)
        self.assertEqual(password_policy_errors(password), [])

    def test_generator_honors_requested_length_above_policy_minimum(self):
        password = generate_random_password(length=32)

        self.assertEqual(len(password), 32)
        self.assertEqual(password_policy_errors(password), [])

    def test_long_password_hash_uses_bytes_beyond_bcrypt_native_limit(self):
        password = "a" * 100 + "X"
        changed_tail = "a" * 100 + "Y"
        user = User(
            username="long-password-user",
            email="long-password-user@example.test",
        )

        user.set_password(password)

        self.assertTrue(user.check_password(password))
        self.assertFalse(user.check_password(changed_tail))

    def test_demo_admin_password_can_still_be_hashed_and_verified(self):
        user = User(
            username="admin",
            email="admin@example.test",
        )

        user.set_password("adminpass")

        self.assertTrue(user.check_password("adminpass"))


class PersistedPasswordPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "password-policy.db"
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            BCRYPT_HANDLE_LONG_PASSWORDS=True,
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path}",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            PASSWORD_MIN_LENGTH=8,
        )
        db.init_app(self.app)
        bcrypt.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

        owner = User(
            username="password-policy-owner",
            email="password-policy-owner@example.test",
            hashed_password="not-used",
            activated=True,
            approved=True,
        )
        db.session.add(owner)
        db.session.flush()
        db.session.add(
            EnvSettings(
                user_id=owner.id,
                site_name="Password Policy Test",
                site_lang="en",
                site_timezone="UTC",
                description="",
                keywords="",
                users_per_page=20,
                users_stored_path="/tmp/users",
                password_policy_enabled=True,
                password_min_length=28,
                password_require_uppercase=True,
            )
        )
        db.session.commit()
        EnvSettings._cached_instance = None

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        EnvSettings._cached_instance = None
        self.context.pop()
        self.temp_dir.cleanup()

    def test_persisted_policy_overrides_deployment_seed_defaults(self):
        errors = password_policy_errors("a" * 27)

        self.assertIn("Password must be at least 28 characters long.", errors)
        self.assertIn("Password must contain at least one uppercase letter.", errors)

        password = generate_random_password(length=20)
        self.assertGreaterEqual(len(password), 28)
        self.assertEqual(password_policy_errors(password), [])


if __name__ == "__main__":
    unittest.main()
