# app/core/logger.py
import logging
from flask import request
from app.core.security import get_client_ip

logger = logging.getLogger(__name__)

def extract_request_metadata(sanitize_headers=True):
    """Extracts useful metadata from the current request."""
    headers = dict(request.headers)
    if sanitize_headers:
        for sensitive_key in ["Authorization", "Cookie", "Set-Cookie"]:
            headers.pop(sensitive_key, None)

    ip = get_client_ip()

    return {
        "ip": ip,
        "user_agent": request.headers.get("User-Agent"),
        "referrer": request.referrer,
        "method": request.method,
        "path": request.path,
        "query_string": request.query_string.decode("utf-8"),
        "headers": headers,
    }
