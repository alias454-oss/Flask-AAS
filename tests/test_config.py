"""Tests for deployment configuration and startup contracts."""

# tests/test_config.py
from pathlib import Path

from app.core.config import Settings
from app.routes.admin.settings import get_timezones
from sqlalchemy import create_engine


def _settings(database_uri):
    return Settings(
        _env_file=None,
        ADMIN_SECRET="not-the-example-admin-secret",
        SECRET_KEY="test-secret-key",
        SQLALCHEMY_DATABASE_URI=database_uri,
        FLASK_ENV="testing",
    )


def test_generic_postgresql_uri_selects_psycopg3_driver():
    settings = _settings("postgresql://user:pass@db.example/testdb")

    assert (
        settings.SQLALCHEMY_DATABASE_URI
        == "postgresql+psycopg://user:pass@db.example/testdb"
    )


def test_explicit_psycopg3_uri_is_preserved():
    uri = "postgresql+psycopg://user:pass@db.example/testdb"

    assert _settings(uri).SQLALCHEMY_DATABASE_URI == uri


def test_sqlite_uri_is_preserved():
    uri = "sqlite:///./dev.db"

    assert _settings(uri).SQLALCHEMY_DATABASE_URI == uri


def test_psycopg3_sqlalchemy_dialect_is_loadable():
    engine = create_engine("postgresql+psycopg://user:pass@localhost/testdb")
    try:
        assert engine.dialect.driver == "psycopg"
    finally:
        engine.dispose()


def test_admin_timezone_choices_are_sorted_iana_keys():
    choices = get_timezones()

    assert ("UTC", "UTC") in choices
    assert choices == sorted(choices)


def test_admin_email_is_available_as_explicit_deployment_config():
    settings = Settings(
        _env_file=None,
        ADMIN_SECRET="not-the-example-admin-secret",
        ADMIN_EMAIL="bootstrap@example.test",
        SECRET_KEY="test-secret-key",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        FLASK_ENV="testing",
    )

    assert settings.ADMIN_EMAIL == "bootstrap@example.test"


def test_entrypoint_debug_does_not_enable_shell_xtrace():
    entrypoint = (Path(__file__).resolve().parents[1] / "entrypoint.sh").read_text()

    assert "set -o xtrace" not in entrypoint
    assert "set -x" not in entrypoint
