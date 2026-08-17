"""Tests for administrator-managed user field validation."""

from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from werkzeug.datastructures import MultiDict

from app.routes.admin.users import AdminUserForm, _audit_failure_metadata


def _env():
    """Return settings that keep optional approval fields and hide locations."""
    return SimpleNamespace(
        use_user_approval=True,
        use_verify_email=True,
        use_user_location=False,
    )


def _form(**overrides):
    """Construct an admin-user form with CSRF disabled for validation tests."""
    app = Flask(__name__)
    app.config.update(SECRET_KEY="admin-user-form-test", WTF_CSRF_ENABLED=False)
    values = {
        "username": "example-user",
        "email": "user@example.com",
    }
    values.update(overrides)

    with app.test_request_context(method="POST"), patch(
        "app.routes.admin.users.get_cached_env_settings",
        return_value=_env(),
    ):
        form = AdminUserForm(formdata=MultiDict(values))
        valid = form.validate()
        return form, valid


def test_admin_user_form_canonicalizes_identity_and_profile_text():
    form, valid = _form(
        username="  ExAmPle-User\n",
        email="  USER@EXAMPLE.COM  ",
        company_name="  Example\nCompany\x00  ",
    )

    assert valid is True
    assert form.username.data == "example-user"
    assert form.email.data == "user@example.com"
    assert form.company_name.data == "Example Company"


def test_admin_user_form_rejects_database_overlength_values():
    form, valid = _form(username="u" * 61, email=f"{'e' * 244}@example.com")

    assert valid is False
    assert form.username.errors
    assert form.email.errors


def test_admin_audit_failure_metadata_does_not_persist_exception_text():
    exc = RuntimeError("database-password=do-not-store")

    metadata = _audit_failure_metadata(exc)

    assert metadata == {"outcome": "failed", "error_type": "RuntimeError"}
    assert "do-not-store" not in repr(metadata)
