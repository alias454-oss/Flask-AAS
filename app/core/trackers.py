# app/core/trackers.py
import logging
import json
from flask import request
from flask_login import current_user
from datetime import datetime, timezone, timedelta
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.security import get_client_ip
from app.models import OnlineUser, AuditActivity, AuditLogin
from app.core.cache import get_cached_env_settings
from app.core.extensions import db

logger = logging.getLogger(__name__)

def visitor_tracking_enabled():
    return bool(get_cached_env_settings().visitor_tracking)

def audit_activity_enabled():
    return bool(get_cached_env_settings().enable_logging)

def audit_login_enabled():
    return bool(get_cached_env_settings().enable_logging)

def user_location_enabled():
    return bool(get_cached_env_settings().use_user_location)

def current_route():
    return request.endpoint or request.path

def log_login(username, ip, user_agent, referer, success):
    audit_entry = AuditLogin(
        username=username,
        ip_address=ip,
        user_agent=user_agent,
        referer=referer,
        success=success,
        timestamp=datetime.now(timezone.utc)
    )
    try:
        db.session.add(audit_entry)
        db.session.commit()
    except IntegrityError as e:
        db.session.rollback()
        logger.warning(f"Audit log DB integrity error: {e}")
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Audit log DB error: {e}")
    except Exception as e:
        db.session.rollback()
        logger.critical(f"Unexpected audit log error: {e}")

def log_action(user_id=None, action=None, target=None, extra_data=None):
    if not action:
        raise ValueError("Missing required 'action' parameter.")

    # IP resolution
    ip_str = get_client_ip()

    # Default user_id from session
    if user_id is None and current_user.is_authenticated:
        user_id = current_user.get_id()

    if extra_data:
        if not isinstance(extra_data, dict):
            raise TypeError("extra_data must be a dictionary if provided.")
        extra_data = json.dumps(extra_data)
    else:
        extra_data = None

    log = AuditActivity(
        user_id=user_id,
        action=action,
        target=target,
        ip_address=ip_str,
        timestamp=datetime.now(timezone.utc),
        extra_data=extra_data
    )

    try:
        db.session.add(log)
        db.session.commit()
        logger.debug(f"Action logged: {action} by user {user_id} from {ip_str}")
    except TypeError as e:
        logger.warning(f"Bad extra_data: {e}")
    except ValueError as e:
        logger.warning(f"Validation error in log_action: {e}")
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.exception(f"Database error during log_action {e}")
    except Exception as e:
        logger.exception(f"Unexpected error during log_action {e}")

def track_online_user():
    # TODO: Distinguish multiple guests behind the same IP by using session or unique visitor IDs.
    try:
        # IP resolution
        ip_str = get_client_ip()

        username = current_user.username if current_user.is_authenticated else OnlineUser.GUEST_USER
        now = datetime.now(timezone.utc)

        existing = OnlineUser.query.filter_by(ip_address=ip_str).first()
        if existing:
            existing.last_active = now
            if existing.user == OnlineUser.GUEST_USER and username != OnlineUser.GUEST_USER:
                existing.user = username  # Promote guest to user
        else:
            db.session.add(OnlineUser(user=username, ip_address=ip_str, last_active=now))

        # Expire stale sessions
        cutoff = now - timedelta(minutes=30)
        OnlineUser.query.filter(OnlineUser.last_active < cutoff).delete()

        db.session.commit()
    except Exception as e:
        logger.exception(f"Error tracking online user {e}")

def expire_stale_online_users(minutes=30):
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    try:
        stale = OnlineUser.query.filter(OnlineUser.last_active < cutoff).delete()
        db.session.commit()
        return stale
    except Exception as e:
        logger.exception(f"Error expiring stale online users: {e}")
        # Maybe return 0 or None explicitly if failure
        return 0

def get_total_user_count_statistics(stat_type='online'):
    query = OnlineUser.query
    if stat_type == 'guest':
        query = query.filter_by(is_guest=True)
    elif stat_type == 'online':
        query = query.filter_by(is_guest=False)
    return query.count()
