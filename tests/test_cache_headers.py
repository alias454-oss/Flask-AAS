# tests/test_cache_headers.py
from unittest.mock import patch

import pytest
from flask import session
from flask_login import UserMixin, login_user

from app import create_app
from app.core.config import settings


_NO_STORE = "no-store, no-cache, must-revalidate, private"


class _AuthenticatedUser(UserMixin):
    id = "cache-policy-user"


@pytest.fixture
def app():
    with patch.object(settings, "SQLALCHEMY_DATABASE_URI", "sqlite://"):
        application = create_app()

    application.config.update(TESTING=True, RATELIMIT_ENABLED=False)

    @application.get("/_cache/public")
    def cache_public():
        return "public"

    @application.get("/_cache/session")
    def cache_session():
        session["cache_policy_test"] = True
        return "session"

    @application.get("/_cache/authenticated")
    def cache_authenticated():
        login_user(_AuthenticatedUser())
        # Keep this branch independent of bool(session): Flask-Login retains the
        # current request user after the backing session data is cleared.
        session.clear()
        return "authenticated"

    @application.get("/internal/cache-policy-test")
    def cache_sensitive():
        return "sensitive"

    return application


@pytest.fixture
def client(app):
    return app.test_client()


def _get(client, path):
    with patch("app.visitor_tracking_enabled", return_value=False):
        return client.get(path)


def _assert_no_store(response):
    assert response.headers["Cache-Control"] == _NO_STORE
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Expires"] == "0"


def test_anonymous_public_response_remains_normally_cacheable(client):
    response = _get(client, "/_cache/public")

    assert response.status_code == 200
    assert "Cache-Control" not in response.headers
    assert "Pragma" not in response.headers
    assert "Expires" not in response.headers


def test_session_bearing_response_is_not_cacheable(client):
    response = _get(client, "/_cache/session")

    assert response.status_code == 200
    _assert_no_store(response)


def test_authenticated_response_is_not_cacheable(client):
    response = _get(client, "/_cache/authenticated")

    assert response.status_code == 200
    _assert_no_store(response)


def test_sensitive_pre_auth_route_is_not_cacheable(client):
    response = _get(client, "/internal/cache-policy-test")

    assert response.status_code == 200
    _assert_no_store(response)


def test_static_asset_is_not_forced_to_no_store(client):
    response = _get(client, "/static/favicon.ico")

    assert response.status_code == 200
    assert "no-store" not in response.headers.get("Cache-Control", "")
    assert response.headers.get("Pragma") != "no-cache"
    assert response.headers.get("Expires") != "0"
