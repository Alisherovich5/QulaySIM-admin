"""Whose word to take about a visitor's address, in the admin.

The twin of `app/core/cloudflare_ips.py` and `app/core/ratelimit.py` in the API
repo. Both runtimes have to answer the same question — which address is the
person, given the proxies in front of us — and they answer it for different
consumers: the API for its rate limits and the ATMOS callback's source-range
check, this for django-axes' lockout counter.

Why it is written twice: the two services are separate repositories and separate
processes, so there is no module to share. What is shared is the switch —
`TRUST_CLOUDFLARE_CLIENT_IP` in the environment — and the rule, recorded in
docs/adr/0001-client-address-behind-cloudflare.md. Change one, change both, and
refresh the ranges below together.

The list is https://api.cloudflare.com/client/v4/ips, fetched 2026-08-20
(etag 38f79d050aa027e3be3865e495dcc9bc). It changes a handful of times a decade.
"""

from __future__ import annotations

from ipaddress import ip_address, ip_network

#: https://api.cloudflare.com/client/v4/ips — ipv4_cidrs
CLOUDFLARE_IPV4 = (
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
)

#: https://api.cloudflare.com/client/v4/ips — ipv6_cidrs
CLOUDFLARE_IPV6 = (
    "2400:cb00::/32",
    "2606:4700::/32",
    "2803:f800::/32",
    "2405:b500::/32",
    "2405:8100::/32",
    "2a06:98c0::/29",
    "2c0f:f248::/32",
)

_NETWORKS = tuple(ip_network(cidr) for cidr in CLOUDFLARE_IPV4 + CLOUDFLARE_IPV6)


def is_cloudflare(address: str) -> bool:
    """Whether an address belongs to Cloudflare's edge."""
    try:
        parsed = ip_address(address.strip())
    except ValueError:
        return False
    return any(parsed in network for network in _NETWORKS)


def peer(meta: dict) -> str:
    """The address our own proxy heard from — the last hop before us.

    Caddy replaces X-Forwarded-For with the address it received the connection
    from rather than appending to it, measured on this deployment. So the
    right-most entry is the peer, and with Cloudflare in front that peer is one
    of its edges.
    """
    forwarded = meta.get("HTTP_X_FORWARDED_FOR", "")
    hops = [part.strip() for part in forwarded.split(",") if part.strip()]
    if hops:
        return hops[-1]
    return meta.get("REMOTE_ADDR", "")


class TrustedClientIpMiddleware:
    """Remove `CF-Connecting-IP` unless Cloudflare actually delivered the request.

    django-axes reads headers in the order settings give it and does not check
    who wrote them. On its own that is exactly as forgeable as the connection:
    anyone reaching the origin directly could send `CF-Connecting-IP: 8.8.8.8`
    and have their failed admin logins counted against Google — which is the
    lockout counter handed over for the cost of one header.

    This strips the header before anything reads it, so the fallback
    (X-Forwarded-For, right-most) applies instead. Placed first in the
    middleware list, ahead of axes.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings

        trusted = getattr(settings, "TRUST_CLOUDFLARE_CLIENT_IP", False)
        if "HTTP_CF_CONNECTING_IP" in request.META and not (
            trusted and is_cloudflare(peer(request.META))
        ):
            del request.META["HTTP_CF_CONNECTING_IP"]
        return self.get_response(request)
