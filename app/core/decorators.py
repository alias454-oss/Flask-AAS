# app/core/decorators.py
from functools import wraps
from flask import make_response
from flask_login import current_user
from app.core.logger import extract_request_metadata
from app.core.trackers import current_route, log_action_isolated, audit_activity_enabled


def log_view_action(action="view", redact_params=None):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if audit_activity_enabled():
                user_id = current_user.id if current_user.is_authenticated else None
                route = current_route()
                route_metadata = extract_request_metadata(redact_params=redact_params)
                log_action_isolated(
                    user_id=user_id,
                    action=action,
                    target=route,
                    extra_data=route_metadata,
                )

            response = f(*args, **kwargs)
            if redact_params:
                response = make_response(response)
                response.headers["Referrer-Policy"] = "no-referrer"
            return response
        return wrapper
    return decorator