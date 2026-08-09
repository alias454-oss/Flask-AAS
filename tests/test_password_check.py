import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.core.extensions import bcrypt, db
from app.core.passwords import password_policy_errors, password_validation_errors
from app.core.pwcheck import (
    LocalPasswordCheckProvider,
    PasswordCheckProvider,
    PasswordCheckResult,
    check_password,
    password_check_provider_choices,
    register_password_check_provider,
)
from app.core.seeder import seed_env_settings
from app.models import EnvSettings, Role, User


class PasswordCheckProviderTests(unittest.TestCase):
    def test_local_provider_rejects_common_password_case_insensitively(self):
        provider = LocalPasswordCheckProvider()

        result = provider.check("PASSWORD")

        self.assertFalse(result.passed)
        self.assertIn("too common", result.message)

    def test_local_provider_does_not_strip_password(self):
        provider = LocalPasswordCheckProvider()

        self.assertTrue(provider.check(" password ").passed)

    def test_builtin_provider_is_exposed_as_a_selectable_choice(self):
        self.assertIn(
            ("local", "Built-in Local Blocklist"),
            password_check_provider_choices(),
        )

    def test_registered_provider_uses_the_same_contract(self):
        class ExampleProvider(PasswordCheckProvider):
            key = "test-example"
            label = "Test Example"

            def check(self, password):
                return PasswordCheckResult(password != "reject-me", "Rejected by test")

        with patch.dict("app.core.pwcheck._PASSWORD_CHECK_PROVIDERS", {}, clear=False):
            register_password_check_provider(ExampleProvider)

            self.assertIn(
                ("test-example", "Test Example"),
                password_check_provider_choices(),
            )
            self.assertTrue(check_password("allowed", "test-example").passed)
            self.assertFalse(check_password("reject-me", "test-example").passed)

    def test_unknown_provider_fails_closed(self):
        result = check_password("anything", "missing-provider")

        self.assertFalse(result.passed)
        self.assertIn("unavailable", result.message)


class PasswordCheckWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            PASSWORD_POLICY_ENABLED=False,
            PASSWORD_CHECK_ENABLED=False,
            PASSWORD_CHECK_PROVIDER="local",
        )
        self.context = self.app.app_context()
        self.context.push()

    def tearDown(self):
        self.context.pop()

    def test_disabled_check_does_not_resolve_provider(self):
        self.app.config["PASSWORD_CHECK_PROVIDER"] = "missing-provider"

        self.assertEqual(password_validation_errors("password"), [])

    def test_enabled_check_runs_even_when_length_policy_is_disabled(self):
        self.app.config["PASSWORD_CHECK_ENABLED"] = True

        errors = password_validation_errors("password")

        self.assertIn(
            "This password is too common. Please choose a different password.",
            errors,
        )

    def test_enabled_check_rejects_invalid_provider_configuration(self):
        self.app.config.update(
            PASSWORD_CHECK_ENABLED=True,
            PASSWORD_CHECK_PROVIDER="missing-provider",
        )

        errors = password_validation_errors("otherwise acceptable password")

        self.assertIn(
            "Password checking is unavailable. Please contact the site administrator.",
            errors,
        )


class PasswordCheckSeederTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "password-check-seeder.db"
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path}",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            BCRYPT_HANDLE_LONG_PASSWORDS=True,
            MAIL_DEBUG=False,
            ADMIN_SECRET="adminpass",
        )
        db.init_app(self.app)
        bcrypt.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

        admin_role = Role(name="admin", description="Administrator")
        user_role = Role(name="user", description="User")
        db.session.add_all([admin_role, user_role])
        db.session.flush()

        admin = User(
            username="admin",
            email="admin@example.test",
            activated=True,
            approved=True,
        )
        admin.set_password("adminpass")
        admin.roles.append(admin_role)
        db.session.add(admin)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        EnvSettings._cached_instance = None
        self.context.pop()
        self.temp_dir.cleanup()

    def test_seeder_defaults_password_check_off_with_local_provider_selected(self):
        seed_env_settings()

        env = db.session.query(EnvSettings).one()
        self.assertFalse(env.password_check_enabled)
        self.assertEqual(env.password_check_provider, "local")


if __name__ == "__main__":
    unittest.main()
