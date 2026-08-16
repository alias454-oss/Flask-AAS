# app/core/trackers.py
import logging
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address

from flask import has_request_context, request
from flask_login import current_user
from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import SQLAlchemyError

from app.core.cache import get_cached_env_settings
from app.core.extensions import db
from app.core.security import get_client_ip
from app.models import AuditActivity, AuditLogin, OnlineUser, User
from app.models.audit_activity import serialize_extra_data

logger = logging.getLogger(__name__)

CLEAN_ONLINE_USER_MINUTES = 10

LOGIN_FAILURE_INVALID_CREDENTIALS = 'invalid_credentials'
LOGIN_FAILURE_LOCKED_OUT = 'locked_out'
LOGIN_FAILURE_UNVERIFIED = 'unverified'
LOGIN_FAILURE_UNAPPROVED = 'unapproved'
LOGIN_FAILURE_REJECTED = 'login_rejected'
LOGIN_FAILURE_MFA_FAILED = 'mfa_failed'
LOGIN_FAILURE_MFA_EXPIRED = 'mfa_expired'

LOGIN_FAILURE_REASONS = frozenset({
    LOGIN_FAILURE_INVALID_CREDENTIALS,
    LOGIN_FAILURE_LOCKED_OUT,
    LOGIN_FAILURE_UNVERIFIED,
    LOGIN_FAILURE_UNAPPROVED,
    LOGIN_FAILURE_REJECTED,
    LOGIN_FAILURE_MFA_FAILED,
    LOGIN_FAILURE_MFA_EXPIRED,
})


def visitor_tracking_enabled():
    return bool(get_cached_env_settings().visitor_tracking)


def audit_activity_enabled():
    return bool(get_cached_env_settings().enable_logging)


def audit_login_enabled():
    return bool(get_cached_env_settings().enable_logging)


def current_route():
    return request.endpoint or request.path


def _truncate(value, max_length):
    if value is None:
        return None
    cleaned = str(value).replace('\r', ' ').replace('\n', ' ').replace('\x00', '')
    return cleaned[:max_length]


def _normalize_ip(value, fallback='unknown'):
    try:
        candidate = str(value).split('%', 1)[0]
        return str(ip_address(candidate))
    except (TypeError, ValueError):
        return fallback


def _normalize_user_id(user_id):
    if user_id is None:
        return None
    try:
        return int(user_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("user_id must be an integer if provided.") from exc


def _prepare_extra_data(extra_data):
    if extra_data is None:
        return None
    if not isinstance(extra_data, dict):
        raise TypeError("extra_data must be a dictionary if provided.")
    return extra_data


def _prepare_activity_values(user_id, action, target, extra_data):
    if not action:
        raise ValueError("Missing required 'action' parameter.")
    if len(str(action)) > 255:
        raise ValueError("action must not exceed 255 characters.")
    if target is not None and len(str(target)) > 255:
        raise ValueError("target must not exceed 255 characters.")

    if (
        user_id is None
        and has_request_context()
        and current_user.is_authenticated
    ):
        user_id = current_user.id

    source_ip = get_client_ip() if has_request_context() else 'unknown'

    return {
        'user_id': _normalize_user_id(user_id),
        'action': str(action),
        'target': str(target) if target is not None else None,
        'ip_address': _normalize_ip(source_ip),
        'timestamp': datetime.now(timezone.utc),
        'extra_data': _prepare_extra_data(extra_data),
    }


def _normalize_login_failure_reason(success, failure_reason):
    if success:
        if failure_reason is not None:
            raise ValueError(
                "failure_reason must be None when a login attempt succeeds."
            )
        return None

    if failure_reason not in LOGIN_FAILURE_REASONS:
        allowed = ', '.join(sorted(LOGIN_FAILURE_REASONS))
        raise ValueError(
            f"failure_reason must be one of: {allowed}."
        )
    return failure_reason


def log_login(
    username,
    ip,
    user_agent,
    referer,
    success,
    failure_reason=None,
):
    """Persist a finalized authentication attempt outside the request session."""
    normalized_success = bool(success)
    values = {
        'username': _truncate(username or 'unknown', 60),
        'ip_address': _normalize_ip(ip),
        'user_agent': _truncate(user_agent, 255),
        'referer': _truncate(referer, 255),
        'success': normalized_success,
        'failure_reason': _normalize_login_failure_reason(
            normalized_success,
            failure_reason,
        ),
        'timestamp': datetime.now(timezone.utc),
    }

    try:
        with db.engine.begin() as connection:
            connection.execute(AuditLogin.__table__.insert().values(**values))
        return True
    except SQLAlchemyError:
        logger.exception("Database error while recording login audit event")
        return False


def log_action(user_id=None, action=None, target=None, extra_data=None):
    """Queue an activity event in the caller-owned transaction."""
    values = _prepare_activity_values(user_id, action, target, extra_data)
    log = AuditActivity(
        user_id=values['user_id'],
        action=values['action'],
        target=values['target'],
        ip_address=values['ip_address'],
        timestamp=values['timestamp'],
        extra_data=values['extra_data'],
    )
    db.session.add(log)
    logger.debug(
        "Queued action audit event action=%s user_id=%s ip=%s",
        values['action'],
        values['user_id'],
        values['ip_address'],
    )
    return log


def log_action_isolated(user_id=None, action=None, target=None, extra_data=None):
    """Persist a standalone activity event without touching the request session."""
    values = _prepare_activity_values(user_id, action, target, extra_data)
    values['extra_data'] = serialize_extra_data(values['extra_data'])

    try:
        with db.engine.begin() as connection:
            connection.execute(AuditActivity.__table__.insert().values(**values))
        return True
    except SQLAlchemyError:
        logger.exception(
            "Database error while recording standalone activity action=%s",
            values['action'],
        )
        return False


def track_online_user():
    """Update online presence in an isolated, best-effort transaction."""
    try:
        ip_str = _normalize_ip(get_client_ip())
        username = (
            _truncate(current_user.username, 60)
            if current_user.is_authenticated
            else OnlineUser.GUEST_USER
        )
        now = datetime.now(timezone.utc)
        table = OnlineUser.__table__

        with db.engine.begin() as connection:
            existing = connection.execute(
                select(table.c.id, table.c.user)
                .where(table.c.ip_address == ip_str)
                .order_by(table.c.id)
                .limit(1)
            ).mappings().first()

            if existing:
                values = {'last_active': now}
                if existing['user'] == OnlineUser.GUEST_USER and username != OnlineUser.GUEST_USER:
                    values['user'] = username
                connection.execute(
                    update(table).where(table.c.id == existing['id']).values(**values)
                )
            else:
                connection.execute(
                    table.insert().values(
                        user=username,
                        ip_address=ip_str,
                        last_active=now,
                    )
                )
        return True
    except SQLAlchemyError:
        logger.exception("Error tracking online user")
        return False
    except Exception:
        logger.exception("Unexpected error tracking online user")
        return False


def expire_stale_online_users(
    minutes=CLEAN_ONLINE_USER_MINUTES,
    *,
    suppress_errors=True,
):
    """Delete stale online-presence rows outside normal request processing."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)

    try:
        with db.engine.begin() as connection:
            result = connection.execute(
                delete(OnlineUser.__table__).where(OnlineUser.last_active < cutoff)
            )
        return result.rowcount or 0
    except SQLAlchemyError:
        if not suppress_errors:
            raise
        logger.exception("Error expiring stale online users")
        return 0


def get_total_user_count_statistics(
    stat_type='online',
    minutes=CLEAN_ONLINE_USER_MINUTES,
):
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    query = OnlineUser.query.filter(OnlineUser.last_active >= cutoff)

    if stat_type == 'guest':
        query = query.filter(OnlineUser.user == OnlineUser.GUEST_USER)
    elif stat_type == 'online':
        query = query.filter(OnlineUser.user != OnlineUser.GUEST_USER)

    return query.count()


def get_admin_quick_stats():
    """Return the shared account and online-presence counts for admin pages."""
    settings = get_cached_env_settings()
    pending_conditions = []

    if settings.use_verify_email:
        pending_conditions.append(User.activated.is_(False))

    if settings.use_user_approval:
        pending_conditions.append(User.approved.is_(False))

    pending_users = (
        User.query.filter(or_(*pending_conditions)).count()
        if pending_conditions
        else 0
    )
    tracking_enabled = bool(settings.visitor_tracking)

    return {
        'total_users': User.query.count(),
        'pending_users': pending_users,
        'visitor_tracking_enabled': tracking_enabled,
        'online_users': (
            get_total_user_count_statistics('online')
            if tracking_enabled
            else None
        ),
        'online_guests': (
            get_total_user_count_statistics('guest')
            if tracking_enabled
            else None
        ),
    }
