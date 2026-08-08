# plugins/reload.py
from __future__ import annotations

import os
import signal
from pathlib import Path


class AppConfigReloadUnavailable(RuntimeError):
    """Raised when Flask-AAS cannot safely request an application reload."""


def _pid_one_cmdline() -> str:
    try:
        raw = Path("/proc/1/cmdline").read_bytes()
    except OSError as exc:
        raise AppConfigReloadUnavailable(
            "Automatic app config reload is unavailable on this deployment."
        ) from exc

    return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()


def reload_app_config() -> None:
    """Request a graceful Gunicorn worker reload through the container master."""
    cmdline = _pid_one_cmdline()
    if "gunicorn" not in cmdline.lower():
        raise AppConfigReloadUnavailable(
            "Automatic app config reload is available only when Gunicorn is PID 1."
        )

    try:
        os.kill(1, signal.SIGHUP)
    except PermissionError as exc:
        raise AppConfigReloadUnavailable(
            "The application worker cannot signal the Gunicorn master."
        ) from exc
    except ProcessLookupError as exc:
        raise AppConfigReloadUnavailable(
            "The Gunicorn master process is no longer available."
        ) from exc
