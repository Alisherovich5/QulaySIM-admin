from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "catalog"

    def ready(self):
        # Registers the cache-invalidation signal handlers.
        from catalog import signals  # noqa: F401
