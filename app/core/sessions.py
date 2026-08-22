# app/core/sessions.py
import logging
from datetime import datetime, timedelta, timezone

from flask import current_app, request
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

from app.models import UserSession

logger = logging.getLogger(__name__)

SESSION_ACTIVITY_TOUCH_INTERVAL = timedelta(seconds=10)
_SESSION_ACTIVITY_EXEMPT_ATTR = "_flask_aas_session_activity_exempt"


def _utc_now():
    return datetime.now(timezone.utc)


def session_activity_exempt(view):
    """Mark a route as authenticated traffic that does not advance activity."""
    setattr(view, _SESSION_ACTIVITY_EXEMPT_ATTR, True)
    return view


def request_advances_session_activity():
    """Return whether the current request represents user session activity."""
    if request.endpoint == "static":
        return False

    endpoint = request.endpoint
    if not endpoint:
        return True

    view = current_app.view_functions.get(endpoint)
    return not bool(getattr(view, _SESSION_ACTIVITY_EXEMPT_ATTR, False))


def create_login_session(user, *, remembered=False, ip_address=None, user_agent=None):
    """Queue one durable browser-session record in the caller transaction."""
    return UserSession.issue_for_user(
        user,
        remembered=remembered,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def touch_current_session():
    """Refresh the active server-side session record without owning route work."""
    if request.endpoint == 'static' or not current_user.is_authenticated:
        return None
    if not request_advances_session_activity():
        return None

    session_id = getattr(current_user, 'session_record_id', None)
    if session_id is None:
        return None

    now = _utc_now()
    last_active_at = getattr(current_user, 'session_last_active_at', None)
    if last_active_at is not None:
        if last_active_at.tzinfo is None:
            last_active_at = last_active_at.replace(tzinfo=timezone.utc)
        else:
            last_active_at = last_active_at.astimezone(timezone.utc)

        if (
            last_active_at <= now
            and now - last_active_at < SESSION_ACTIVITY_TOUCH_INTERVAL
        ):
            return None

    try:
        UserSession.touch_isolated(session_id, current_user.id, now=now)
    except SQLAlchemyError:
        logger.exception(
            'Failed to update session activity for session_id=%s user_id=%s',
            session_id,
            current_user.id,
        )
    return None


def close_current_session():
    """Best-effort closure of the authenticated browser's durable session row."""
    if not current_user.is_authenticated:
        return False

    session_id = getattr(current_user, 'session_record_id', None)
    if session_id is None:
        return False

    try:
        return UserSession.end_isolated(session_id, current_user.id)
    except SQLAlchemyError:
        logger.exception(
            'Failed to close session_id=%s for user_id=%s',
            session_id,
            current_user.id,
        )
        return False
