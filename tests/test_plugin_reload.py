import signal
import unittest
from unittest.mock import patch

from app.plugins.reload import AppConfigReloadUnavailable, reload_app_config


class PluginReloadTests(unittest.TestCase):
    def test_reload_signals_gunicorn_pid_one_with_hup(self):
        with patch(
            "pathlib.Path.read_bytes",
            return_value=b"/usr/local/bin/python\x00/usr/local/bin/gunicorn\x00",
        ), patch("app.plugins.reload.os.kill") as kill:
            reload_app_config()

        kill.assert_called_once_with(1, signal.SIGHUP)

    def test_reload_rejects_non_gunicorn_pid_one(self):
        with patch(
            "pathlib.Path.read_bytes",
            return_value=b"python\x00-m\x00flask\x00run\x00",
        ), patch("app.plugins.reload.os.kill") as kill:
            with self.assertRaisesRegex(
                AppConfigReloadUnavailable,
                "only when Gunicorn is PID 1",
            ):
                reload_app_config()

        kill.assert_not_called()

    def test_reload_reports_signal_permission_failure(self):
        with patch(
            "pathlib.Path.read_bytes",
            return_value=b"gunicorn\x00app.wsgi:app\x00",
        ), patch(
            "app.plugins.reload.os.kill",
            side_effect=PermissionError,
        ):
            with self.assertRaisesRegex(
                AppConfigReloadUnavailable,
                "cannot signal the Gunicorn master",
            ):
                reload_app_config()
