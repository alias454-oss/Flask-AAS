# app/core/proxy.py
"""Trusted reverse-proxy middleware and address helpers."""

import ipaddress
import logging

from werkzeug.middleware.proxy_fix import ProxyFix

logger = logging.getLogger(__name__)


def normalize_ip(value):
    """Normalize an IP-like forwarding value to an ipaddress object."""
    if value is None:
        return None

    candidate = str(value).strip().split('%', 1)[0]
    if not candidate or candidate.lower() == 'unknown' or candidate.startswith('_'):
        return None

    if candidate.startswith('['):
        closing = candidate.find(']')
        if closing == -1:
            return None
        candidate = candidate[1:closing]
    elif candidate.count(':') == 1:
        host, port = candidate.rsplit(':', 1)
        if port.isdigit():
            candidate = host

    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


def parse_trusted_proxy_networks(values):
    """Parse configured trusted proxy addresses/CIDRs, skipping malformed entries."""
    trusted = []
    for value in values or []:
        try:
            trusted.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            logger.warning("Malformed trusted proxy network entry skipped: %s", value)
    return tuple(trusted)


def address_is_trusted(address, networks):
    return address is not None and any(address in network for network in networks)


class TrustedProxyFix:
    """Apply ProxyFix only when the immediate network peer is explicitly trusted.

    Client IP identity is intentionally left to ``get_client_ip()`` so variable
    proxy chains do not rewrite ``REMOTE_ADDR`` to an intermediate proxy.
    """

    def __init__(self, app, *, trusted_proxies, proxy_hops):
        self.app = app
        self.trusted_networks = parse_trusted_proxy_networks(trusted_proxies)
        self.proxy_app = ProxyFix(
            app,
            x_for=0,
            x_proto=proxy_hops,
            x_host=proxy_hops,
            x_prefix=proxy_hops,
        )

    def __call__(self, environ, start_response):
        peer = normalize_ip(environ.get('REMOTE_ADDR'))
        if not address_is_trusted(peer, self.trusted_networks):
            return self.app(environ, start_response)
        return self.proxy_app(environ, start_response)
