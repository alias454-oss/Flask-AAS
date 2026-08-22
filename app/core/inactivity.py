"""Sliding inactivity enforcement for authenticated browser sessions."""

import logging
import math
import time

from flask import current_app, flash, redirect, request, session, url_for
from flask_login import current_user, logout_user

from app.core.sessions import close_current_session, request_advances_session_activity

logger = logging.getLogger(__name__)

SESSION_ACTIVITY_KEY = 'last_activity_at'
LEGACY_SESSION_ACTIVITY_KEY = 'last_active'


def _current_timestamp():
    """Return the current Unix timestamp through a module-local test seam."""
    return time.time()


def _normalized_timestamp(value):
    """Return a finite numeric timestamp, or None for unusable session state."""
    if isinstance(value, bool):
        return None

    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(timestamp):
        return None
    return timestamp


def mark_session_activity(timestamp=None):
    """Start or refresh the authenticated browser session's activity window."""
    if timestamp is None:
        timestamp = _current_timestamp()

    normalized = _normalized_timestamp(timestamp)
    if normalized is None:
        raise ValueError('Session activity timestamp must be a finite number')

    session.pop(LEGACY_SESSION_ACTIVITY_KEY, None)
    session[SESSION_ACTIVITY_KEY] = normalized
    return normalized


def _remembered_timeout_redirect_target():
    """Return a safe GET target after a remembered-session timeout.

    GET requests can resume at their original URL. State-changing requests must
    not be redirected back to a mutation endpoint because a 303 changes the next
    request to GET and many mutation routes intentionally do not support GET.

    :return: Same-page URL for GET requests, otherwise the application index URL.
    """
    if request.method == "GET":
        return request.full_path.rstrip("?") or request.path or "/"
    return url_for("index.index")


def enforce_inactivity_timeout():
    """Expire or downgrade an authenticated session after its idle period."""
    # Static asset requests are not user activity and should not keep a login alive.
    if request.endpoint == 'static':
        return None

    # Pre-authentication MFA state has its own bounded lifetime. It must not be
    # treated as an authenticated session or receive an inactivity timestamp.
    if not current_user.is_authenticated:
        return None

    advances_activity = request_advances_session_activity()

    timeout = current_app.config.get('SESSION_INACTIVITY_TIMEOUT_SECONDS')
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        logger.error(
            'Invalid SESSION_INACTIVITY_TIMEOUT_SECONDS=%r; timeout disabled',
            timeout,
        )
        return None

    if not math.isfinite(timeout) or timeout <= 0:
        session.pop(SESSION_ACTIVITY_KEY, None)
        session.pop(LEGACY_SESSION_ACTIVITY_KEY, None)
        return None

    now = _current_timestamp()
    last_activity = _normalized_timestamp(session.get(SESSION_ACTIVITY_KEY))

    # A missing, malformed, or future timestamp can occur after deployment
    # upgrades or wall-clock correction. Start a new bounded window rather than
    # creating an indefinite session or forcing an unexplained logout.
    if last_activity is None or last_activity > now:
        mark_session_activity(now)
        return None

    if now - last_activity >= timeout:
        user_id = current_user.id
        endpoint = request.endpoint or request.path

        if current_user.session_remembered:
            # Remembered browsers retain their durable session and remember cookie,
            # but inactivity still clears application session state and downgrades
            # the authenticated identity to non-fresh. Stop the triggering request
            # so POST/other state-changing work is never executed across the timeout
            # boundary; the redirected request continues under the non-fresh session.
            user_identity = current_user.get_id()
            session_identifier = session.get('_id')
            session.clear()
            session['_user_id'] = user_identity
            if session_identifier is not None:
                session['_id'] = session_identifier
            session['_fresh'] = False
            session.permanent = True
            mark_session_activity(now)
            flash(
                'Your remembered sign-in was restored after inactivity. '
                'Reauthentication may be required for sensitive actions.',
                'info',
            )
            logger.info(
                'Remembered session downgraded after inactivity for '
                'user_id=%s endpoint=%s',
                user_id,
                endpoint,
            )
            return redirect(_remembered_timeout_redirect_target(), code=303)

        close_current_session()
        logout_user()
        session.clear()
        # logout_user() requests remember-cookie deletion, but session.clear()
        # removes Flask-Login's marker. Restore it for the after_request hook.
        session['_remember'] = 'clear'
        flash('You have been logged out due to inactivity.', 'warning')
        logger.info(
            'Session expired due to inactivity for user_id=%s endpoint=%s',
            user_id,
            endpoint,
        )
        return redirect(url_for('login.login'))

    if advances_activity:
        mark_session_activity(now)
    return None
