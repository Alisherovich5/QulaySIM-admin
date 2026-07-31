from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ContentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "content"
    verbose_name = _("Content")

    def ready(self):
        # Registers the cache-invalidation signal handlers.
        from content import signals  # noqa: F401
