"""The project's own app.

It exists for two things that need a home the settings module cannot give them:
`manage.py setup_totp` (Django only discovers management commands inside an
installed app) and the `ready()` hook that points the admin login at the
two-factor form. Doing the latter from settings would run it while the app
registry is still loading, before `admin.site` exists.
"""

from __future__ import annotations

from django.apps import AppConfig


class ConfigAppConfig(AppConfig):
    name = "config"
    verbose_name = "QulaySIM"

    def ready(self) -> None:
        from config import otp

        otp.install()
