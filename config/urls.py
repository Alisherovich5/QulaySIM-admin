"""URL configuration.

The admin path is configurable: leaving it on /admin/ hands every scanner a
known login form to hammer.
"""

from django.conf import settings
from django.contrib import admin
from django.urls import path

from config.health import healthz

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path(f"{settings.ADMIN_URL_PATH}/", admin.site.urls),
]
