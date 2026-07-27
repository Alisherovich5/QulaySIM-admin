"""URL configuration.

The admin path stays configurable — it keeps automated scanners that probe
/admin/ and /wp-admin/ from finding anything. The root now redirects to it,
which does reveal the path to anyone who asks for it; that is an acceptable
trade because the login itself is rate-limited by django-axes, so security no
longer rests on the path being unguessable.
"""

from django.conf import settings
from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

from config.health import healthz

urlpatterns = [
    # Exempt from SECURE_SSL_REDIRECT — see settings.
    path("healthz", healthz, name="healthz"),
    path(
        "",
        RedirectView.as_view(url=f"/{settings.ADMIN_URL_PATH}/", permanent=False),
        name="root",
    ),
    path(f"{settings.ADMIN_URL_PATH}/", admin.site.urls),
]
