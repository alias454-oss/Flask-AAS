import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.core.extensions import db
from app.core.seeder import seed_env_settings
from app.core.spam import (
    LocalSpamCheckProvider,
    SpamCheckProvider,
    SpamCheckResult,
    check_spam,
    register_spam_check_provider,
    spam_check_provider_choices,
)
from app.models import EnvSettings, Role, User


class SpamCheckProviderTests(unittest.TestCase):
    def test_local_provider_rejects_phrase_case_insensitively(self):
        result = LocalSpamCheckProvider().check("Please CLICK HERE for details")

        self.assertFalse(result.passed)
        self.assertEqual(result.message, "Your message appears to be spam.")

    def test_local_provider_allows_clean_message(self):
        result = LocalSpamCheckProvider().check("Please help with my account.")

        self.assertTrue(result.passed)

    def test_builtin_provider_is_exposed_as_a_selectable_choice(self):
        self.assertIn(
            ("local", "Built-in Local Phrase List"),
            spam_check_provider_choices(),
        )

    def test_registered_provider_uses_the_same_contract(self):
        class ExampleProvider(SpamCheckProvider):
            key = "test-example"
            label = "Test Example"

            def check(self, message):
                return SpamCheckResult(message != "reject-me", "Rejected by test")

        with patch.dict("app.core.spam._SPAM_CHECK_PROVIDERS", {}, clear=False):
            register_spam_check_provider(ExampleProvider)

            self.assertIn(
                ("test-example", "Test Example"),
                spam_check_provider_choices(),
            )
            self.assertTrue(check_spam("allowed", "test-example").passed)
            self.assertFalse(check_spam("reject-me", "test-example").passed)

    def test_unknown_provider_fails_open(self):
        with self.assertLogs("app.core.spam", level="ERROR"):
            result = check_spam("anything", "missing-provider")

        self.assertTrue(result.passed)

    def test_provider_runtime_failure_fails_open(self):
        class BrokenProvider(SpamCheckProvider):
            key = "test-broken"
            label = "Test Broken"

            def check(self, message):
                raise RuntimeError("provider failed")

        with patch.dict("app.core.spam._SPAM_CHECK_PROVIDERS", {}, clear=False):
            register_spam_check_provider(BrokenProvider)
            with self.assertLogs("app.core.spam", level="ERROR"):
                result = check_spam("anything", "test-broken")

        self.assertTrue(result.passed)


class SpamCheckSeederTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "spam-check-seeder.db"
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path}",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            MAIL_DEBUG=False,
            ADMIN_SECRET="adminpass",
        )
        db.init_app(self.app)
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

    def test_seeder_preserves_local_spam_check_by_default(self):
        seed_env_settings()

        env = db.session.query(EnvSettings).one()
        self.assertTrue(env.spam_check_enabled)
        self.assertEqual(env.spam_check_provider, "local")


if __name__ == "__main__":
    unittest.main()
