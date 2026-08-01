"""URL configuration.

The admin path stays configurable — it keeps automated scanners that probe
/admin/ and /wp-admin/ from finding anything. The root now redirects to it,
which does reveal the path to anyone who asks for it; that is an acceptable
trade because the login itself is rate-limited by django-axes, so security no
longer rests on the path being unguessable.
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path
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
    # Backs the language switcher in the header. Without this the switcher
    # renders and posts into a 404, so the language never changes.
    #
    # Under the admin prefix rather than at the site root, so it sits behind
    # whatever protects the rest of the admin. The admin is served from its own
    # hostname today, where either would work; this way it does not become a
    # publicly reachable endpoint if it is ever mounted on the main host, where
    # only this prefix is proxied to Django.
    #
    # `set_language` only accepts a redirect target on this host, and only a
    # code from LANGUAGES, so it is not an open redirect.
    path(f"{settings.ADMIN_URL_PATH}/i18n/", include("django.conf.urls.i18n")),
    path(f"{settings.ADMIN_URL_PATH}/", admin.site.urls),
]
