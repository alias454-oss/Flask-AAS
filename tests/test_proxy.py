# tests/test_proxy.py
import unittest

from flask import Flask, jsonify, request

from app.core.extensions import _client_ip_key
from app.core.proxy import TrustedProxyFix
from app.core.security import get_client_ip


class TrustedProxyTests(unittest.TestCase):
    def _app(self, trusted_proxies=None):
        app = Flask(__name__)
        app.config.update(
            PROXY_HOPS=1,
            TRUSTED_PROXIES=(
                trusted_proxies
                if trusted_proxies is not None
                else ['10.0.0.0/8']
            ),
        )

        @app.get('/')
        def index():
            return jsonify(
                client_ip=get_client_ip(),
                host=request.host,
                remote_addr=request.remote_addr,
                scheme=request.scheme,
            )

        app.wsgi_app = TrustedProxyFix(
            app.wsgi_app,
            trusted_proxies=app.config['TRUSTED_PROXIES'],
            proxy_hops=app.config['PROXY_HOPS'],
        )
        return app

    def test_untrusted_peer_cannot_apply_forwarding_headers(self):
        app = self._app()
        response = app.test_client().get(
            '/',
            base_url='http://internal.test',
            headers={
                'X-Forwarded-For': '198.51.100.25',
                'X-Real-IP': '203.0.113.44',
                'X-Forwarded-Host': 'public.example',
                'X-Forwarded-Proto': 'https',
            },
            environ_overrides={'REMOTE_ADDR': '192.0.2.20'},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['client_ip'], '192.0.2.20')
        self.assertEqual(payload['remote_addr'], '192.0.2.20')
        self.assertEqual(payload['host'], 'internal.test')
        self.assertEqual(payload['scheme'], 'http')

    def test_trusted_peer_applies_origin_headers_and_resolves_client_ip(self):
        app = self._app()
        response = app.test_client().get(
            '/',
            base_url='http://internal.test',
            headers={
                'X-Forwarded-For': '198.51.100.25, 10.0.0.2',
                'X-Forwarded-Host': 'public.example',
                'X-Forwarded-Proto': 'https',
            },
            environ_overrides={'REMOTE_ADDR': '10.0.0.3'},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['client_ip'], '198.51.100.25')
        self.assertEqual(payload['remote_addr'], '10.0.0.3')
        self.assertEqual(payload['host'], 'public.example')
        self.assertEqual(payload['scheme'], 'https')

    def test_client_ip_ignores_spoofed_leftmost_forwarded_for_value(self):
        app = self._app()
        response = app.test_client().get(
            '/',
            headers={
                'X-Forwarded-For': (
                    '203.0.113.99, 198.51.100.25, 10.0.0.2'
                ),
            },
            environ_overrides={'REMOTE_ADDR': '10.0.0.3'},
        )

        self.assertEqual(response.get_json()['client_ip'], '198.51.100.25')

    def test_client_ip_ignores_unrelated_forwarded_header_family(self):
        app = self._app()
        response = app.test_client().get(
            '/',
            headers={
                'Forwarded': 'for=203.0.113.99;proto=https',
                'X-Forwarded-For': '198.51.100.25, 10.0.0.2',
            },
            environ_overrides={'REMOTE_ADDR': '10.0.0.3'},
        )

        self.assertEqual(response.get_json()['client_ip'], '198.51.100.25')

    def test_client_ip_prefers_x_real_ip_from_trusted_peer(self):
        app = self._app()
        response = app.test_client().get(
            '/',
            headers={
                'X-Real-IP': '198.51.100.44',
                'X-Forwarded-For': '198.51.100.99, 203.0.113.10',
            },
            environ_overrides={'REMOTE_ADDR': '10.0.0.3'},
        )

        self.assertEqual(response.get_json()['client_ip'], '198.51.100.44')

    def test_client_ip_prefers_railway_x_real_ip_over_cdn_hop(self):
        app = self._app(trusted_proxies=['100.0.0.0/8'])
        response = app.test_client().get(
            '/',
            headers={
                'X-Real-IP': '73.210.23.125',
                'X-Forwarded-For': '73.210.23.125, 152.233.40.1',
            },
            environ_overrides={'REMOTE_ADDR': '100.64.0.7'},
        )

        self.assertEqual(response.get_json()['client_ip'], '73.210.23.125')

    def test_invalid_x_real_ip_falls_back_to_forwarded_chain(self):
        app = self._app()
        response = app.test_client().get(
            '/',
            headers={
                'X-Real-IP': 'not-an-ip',
                'X-Forwarded-For': '198.51.100.25, 10.0.0.2',
            },
            environ_overrides={'REMOTE_ADDR': '10.0.0.3'},
        )

        self.assertEqual(response.get_json()['client_ip'], '198.51.100.25')

    def test_default_limiter_key_uses_effective_client_ip(self):
        app = Flask(__name__)
        app.config.update(
            PROXY_HOPS=1,
            TRUSTED_PROXIES=['10.0.0.0/8'],
        )

        with app.test_request_context(
            '/',
            headers={
                'X-Real-IP': '198.51.100.44',
                'X-Forwarded-For': '198.51.100.25, 10.0.0.2',
            },
            environ_base={'REMOTE_ADDR': '10.0.0.3'},
        ):
            self.assertEqual(_client_ip_key(), '198.51.100.44')


if __name__ == '__main__':
    unittest.main()
