"""Whose address the admin judges a failed login by.

django-axes locks out on (username, ip_address). Get the address wrong and the
lockout counts against something that is not the person: on the day Cloudflare
went in front, every failed admin login was recorded against a Cloudflare edge
that rotates between requests, so six wrong passwords from one attacker never
added up to six anywhere. That was found by an audit, not by a test — hence this
file.

The twin of tests/unit/test_ratelimit.py in the API repo. Both assert the same
rule for the same reason; see docs/adr/0001-client-address-behind-cloudflare.md.
"""

from __future__ import annotations

from django.test import SimpleTestCase, override_settings

from config.client_address import TrustedClientIpMiddleware, is_cloudflare, peer


class _Request:
    def __init__(self, meta):
        self.META = meta


def _run(meta):
    """Push a request through the middleware and hand back its META."""
    middleware = TrustedClientIpMiddleware(lambda request: request)
    return middleware(_Request(meta)).META


class TrustedClientIpTests(SimpleTestCase):
    # 162.158.172.93 is the edge that delivered the payment callback we refused.
    CF_EDGE = "162.158.172.93"
    VISITOR = "203.0.113.9"
    DIRECT = "185.213.229.124"

    @override_settings(TRUST_CLOUDFLARE_CLIENT_IP=True)
    def test_the_header_survives_when_cloudflare_delivered_the_request(self):
        meta = _run(
            {
                "HTTP_CF_CONNECTING_IP": self.VISITOR,
                "HTTP_X_FORWARDED_FOR": self.CF_EDGE,
                "REMOTE_ADDR": "172.18.0.4",
            }
        )
        self.assertEqual(meta["HTTP_CF_CONNECTING_IP"], self.VISITOR)

    @override_settings(TRUST_CLOUDFLARE_CLIENT_IP=True)
    def test_a_direct_caller_cannot_name_its_own_address(self):
        """The hole this closes. Without the middleware, anyone reaching the
        origin directly could have their failed logins counted against 8.8.8.8."""
        meta = _run(
            {
                "HTTP_CF_CONNECTING_IP": "8.8.8.8",
                "HTTP_X_FORWARDED_FOR": self.DIRECT,
                "REMOTE_ADDR": "172.18.0.4",
            }
        )
        self.assertNotIn("HTTP_CF_CONNECTING_IP", meta)

    @override_settings(TRUST_CLOUDFLARE_CLIENT_IP=False)
    def test_the_header_is_dropped_entirely_when_the_switch_is_off(self):
        """Turning Cloudflare off in one service must not leave the other
        believing it — which is exactly what the hard-coded header order did."""
        meta = _run(
            {
                "HTTP_CF_CONNECTING_IP": self.VISITOR,
                "HTTP_X_FORWARDED_FOR": self.CF_EDGE,
            }
        )
        self.assertNotIn("HTTP_CF_CONNECTING_IP", meta)

    def test_a_request_without_the_header_is_left_alone(self):
        meta = _run({"HTTP_X_FORWARDED_FOR": self.CF_EDGE, "REMOTE_ADDR": "172.18.0.4"})
        self.assertEqual(meta["HTTP_X_FORWARDED_FOR"], self.CF_EDGE)


class PeerTests(SimpleTestCase):
    def test_the_peer_is_the_right_most_hop(self):
        """Caddy replaces X-Forwarded-For with the address it heard from rather
        than appending, so the right-most entry is the peer. A caller that seeds
        its own header ends up to the left of it and cannot be mistaken for it."""
        self.assertEqual(peer({"HTTP_X_FORWARDED_FOR": "1.1.1.1, 162.158.172.93"}), "162.158.172.93")

    def test_it_falls_back_to_the_socket(self):
        self.assertEqual(peer({"REMOTE_ADDR": "10.0.0.5"}), "10.0.0.5")
        self.assertEqual(peer({}), "")


class CloudflareRangeTests(SimpleTestCase):
    def test_it_recognises_the_edges_this_deployment_has_seen(self):
        for address in ("162.158.172.93", "172.64.198.64"):
            self.assertTrue(is_cloudflare(address), address)

    def test_it_refuses_everything_else(self):
        for address in ("8.8.8.8", "185.213.229.124", "77.37.54.14", "not-an-ip", ""):
            self.assertFalse(is_cloudflare(address), address)
