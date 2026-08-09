# app/core/site.py
import ipaddress
from urllib.parse import urlsplit

DEFAULT_SITE_URL = "http://127.0.0.1:5000"
LEGACY_SITE_URL_PLACEHOLDERS = {"https://yoursite.com"}


def _normalize_hostname(hostname: str) -> str:
    """Normalize and validate a hostname, IPv4 address, or IPv6 address."""
    value = hostname.strip().rstrip(".").lower()
    if not value:
        raise ValueError("Site URL must include a host.")

    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass

    try:
        ascii_host = value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Site URL contains an invalid host.") from exc

    labels = ascii_host.split(".")
    for label in labels:
        if not label or len(label) > 63:
            raise ValueError("Site URL contains an invalid host.")
        if label.startswith("-") or label.endswith("-"):
            raise ValueError("Site URL contains an invalid host.")
        if not all(character.isalnum() or character == "-" for character in label):
            raise ValueError("Site URL contains an invalid host.")

    return ascii_host


def normalize_site_url(value: str) -> str:
    """Return one canonical HTTP(S) origin for externally visible URLs."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Site URL is required.")

    parsed = urlsplit(raw)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("Site URL must use http or https.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Site URL must not include credentials.")
    if not parsed.hostname:
        raise ValueError("Site URL must include a host.")
    if parsed.query or parsed.fragment:
        raise ValueError("Site URL must not include a query string or fragment.")
    if parsed.path not in {"", "/"}:
        raise ValueError("Site URL must not include an application path.")

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Site URL contains an invalid port.") from exc

    hostname = _normalize_hostname(parsed.hostname)
    host_for_url = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        host_for_url = f"{host_for_url}:{port}"

    return f"{scheme}://{host_for_url}"


def site_url_flask_config(site_url: str) -> dict[str, object]:
    """Derive Flask's native external-URL and Host trust settings."""
    normalized = normalize_site_url(site_url)
    parsed = urlsplit(normalized)
    hostname = parsed.hostname

    trusted_hosts = [hostname]
    if hostname in {
        "127.0.0.1",
        "::1",
        "localhost",
    }:
        for loopback_host in ("127.0.0.1", "::1", "localhost"):
            if loopback_host not in trusted_hosts:
                trusted_hosts.append(loopback_host)

    return {
        "SITE_URL": normalized,
        "SERVER_NAME": parsed.netloc,
        "PREFERRED_URL_SCHEME": parsed.scheme,
        "TRUSTED_HOSTS": trusted_hosts,
    }
