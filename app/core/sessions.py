# app/core/sessions.py
import logging

from flask import request
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

from app.models import UserSession

logger = logging.getLogger(__name__)


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

    session_id = getattr(current_user, 'session_record_id', None)
    if session_id is None:
        return None

    try:
        UserSession.touch_isolated(session_id, current_user.id)
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
