# app/core/logger.py
import logging
from urllib.parse import quote, quote_plus

from flask import request

from app.core.security import get_client_ip

logger = logging.getLogger(__name__)

REDACTED_ROUTE_VALUE = "<redacted>"


def _route_redaction_values(redact_params):
    if not redact_params:
        return []

    route_values = request.view_args or {}
    return [
        str(route_values[param])
        for param in redact_params
        if route_values.get(param) is not None
    ]


def redact_route_values(value, redact_params=None):
    """Redact explicitly declared route parameter values from request-derived text."""
    if value is None:
        return None

    redacted = str(value)
    for route_value in _route_redaction_values(redact_params):
        encoded_values = {
            route_value,
            quote(route_value, safe=""),
            quote_plus(route_value, safe=""),
        }
        for encoded_value in sorted(encoded_values, key=len, reverse=True):
            if encoded_value:
                redacted = redacted.replace(encoded_value, REDACTED_ROUTE_VALUE)

    return redacted


def extract_request_metadata(sanitize_headers=True, redact_params=None):
    """Extract useful request metadata for routes that opt into view auditing."""
    headers = dict(request.headers)
    if sanitize_headers:
        for sensitive_key in ["Authorization", "Cookie", "Set-Cookie"]:
            headers.pop(sensitive_key, None)

    headers = {
        key: redact_route_values(value, redact_params)
        for key, value in headers.items()
    }

    return {
        "ip": get_client_ip(),
        "user_agent": redact_route_values(
            request.headers.get("User-Agent"),
            redact_params,
        ),
        "referrer": redact_route_values(request.referrer, redact_params),
        "method": request.method,
        "path": redact_route_values(request.path, redact_params),
        "query_string": redact_route_values(
            request.query_string.decode("utf-8"),
            redact_params,
        ),
        "headers": headers,
    }
