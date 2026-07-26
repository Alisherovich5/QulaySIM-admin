"""Container health endpoint.

The load balancer terminates TLS and checks this over plain HTTP from inside
the network, so it is exempt from SECURE_SSL_REDIRECT — otherwise the check
follows a redirect to https on an internal address with no certificate and
times out, marking a perfectly healthy container as failed.
"""

from __future__ import annotations

from django.db import connection
from django.http import HttpRequest, JsonResponse


def healthz(_request: HttpRequest) -> JsonResponse:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        database = "ok"
        status = 200
    except Exception:  # noqa: BLE001
        database = "down"
        status = 503
    return JsonResponse({"status": "ok" if status == 200 else "degraded",
                         "database": database}, status=status)
