from django.apps import AppConfig


class ContentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "content"

    def ready(self):
        # Registers the cache-invalidation signal handlers.
        from content import signals  # noqa: F401
