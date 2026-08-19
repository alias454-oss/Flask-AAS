"""Bootstrap administrator credential-state regression coverage."""

from flask import Flask
import pytest

from app.core.extensions import db
from app.core.seeder import seed_admin_user, seed_roles
from app.models import User


def _app(flask_env="testing"):
    app = Flask(__name__)
    app.config.update(
        TESTING=flask_env == "testing",
        FLASK_ENV=flask_env,
        SECRET_KEY="bootstrap-test-secret",
        ADMIN_SECRET="generated-bootstrap-password",
        ADMIN_EMAIL="bootstrap@example.test",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    return app


def test_seeded_admin_requires_password_change_in_production():
    app = _app("production")

    with app.app_context():
        db.create_all()
        seed_roles()
        seed_admin_user()

        admin = User.query.filter_by(username="admin").one()
        assert admin.email == "bootstrap@example.test"
        assert admin.check_password("generated-bootstrap-password")
        assert admin.must_change_password is True

        db.session.remove()
        db.drop_all()


@pytest.mark.parametrize("flask_env", ["development", "testing"])
def test_seeded_admin_does_not_require_password_change_outside_production(flask_env):
    app = _app(flask_env)

    with app.app_context():
        db.create_all()
        seed_roles()
        seed_admin_user()

        admin = User.query.filter_by(username="admin").one()
        assert admin.check_password("generated-bootstrap-password")
        assert admin.must_change_password is False

        db.session.remove()
        db.drop_all()


def test_seed_admin_is_idempotent_after_bootstrap_secret_is_removed():
    app = _app("production")

    with app.app_context():
        db.create_all()
        seed_roles()
        seed_admin_user()

        admin = User.query.filter_by(username="admin").one()
        admin.set_password("administrator-private-password")
        admin.must_change_password = False
        db.session.commit()

        app.config["ADMIN_SECRET"] = None
        seed_admin_user()

        stored_admin = User.query.filter_by(username="admin").one()
        assert stored_admin.check_password("administrator-private-password")
        assert stored_admin.must_change_password is False

        db.session.remove()
        db.drop_all()
