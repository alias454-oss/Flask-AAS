# tests/test_maintenance_cli.py
from unittest.mock import patch

from click.testing import CliRunner
from sqlalchemy.exc import SQLAlchemyError

from app.core.trackers import CLEAN_ONLINE_USER_MINUTES
from manage import cleanup_online_users


def test_cleanup_online_users_uses_active_window_by_default():
    runner = CliRunner()

    with patch(
        "manage.expire_stale_online_users",
        return_value=3,
    ) as cleanup:
        result = runner.invoke(cleanup_online_users)

    assert result.exit_code == 0, result.output
    cleanup.assert_called_once_with(
        minutes=CLEAN_ONLINE_USER_MINUTES,
        suppress_errors=False,
    )


def test_cleanup_online_users_accepts_explicit_window():
    runner = CliRunner()

    with patch(
        "manage.expire_stale_online_users",
        return_value=2,
    ) as cleanup:
        result = runner.invoke(cleanup_online_users, ["--minutes", "30"])

    assert result.exit_code == 0, result.output
    cleanup.assert_called_once_with(minutes=30, suppress_errors=False)


def test_cleanup_online_users_fails_closed_on_database_error():
    runner = CliRunner()

    with patch(
        "manage.expire_stale_online_users",
        side_effect=SQLAlchemyError("database unavailable"),
    ):
        result = runner.invoke(cleanup_online_users)

    assert result.exit_code != 0
    assert "Online-presence cleanup failed" in result.output
