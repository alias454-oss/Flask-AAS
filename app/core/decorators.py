# app/core/decorators.py
from functools import wraps
from flask import make_response, request
from flask_login import current_user
from app.core.logger import extract_request_metadata
from app.core.trackers import current_route, log_action, audit_activity_enabled

def log_view_action(action="view"):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if audit_activity_enabled():
                user_id = current_user.id if current_user.is_authenticated else None
                route = current_route() or request.path
                route_metadata = extract_request_metadata()
                log_action(user_id=user_id, action=action, target=route, extra_data=route_metadata)
            return f(*args, **kwargs)
        return wrapper
    return decorator

def nocache(view):
    @wraps(view)
    def no_cache_wrapper(*args, **kwargs):
        response = make_response(view(*args, **kwargs))
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    return no_cache_wrapper