"""Uzbek unless someone asked for something else.

Django's LocaleMiddleware resolves the language in this order: URL prefix,
session, cookie, `Accept-Language`, then LANGUAGE_CODE. The `Accept-Language`
step is the problem here. It is the *browser's* preference, not the operator's,
and it made the same admin render in three languages depending on which browser
was open — Uzbek in one, Russian in another, English in a third, with nothing on
screen explaining why and nothing the operator had done to cause it.

This is a shop run by Uzbek-speaking staff, so the default belongs to the panel
rather than to whatever Chrome was installed with. The middleware drops the
header before LocaleMiddleware reads it, which collapses the chain to: explicit
choice, else Uzbek.

The switcher keeps working. Choosing Russian writes the `django_language`
cookie, and a request carrying that cookie is left alone — so a preference a
person actually expressed still wins, while one they never expressed no longer
does.
"""

from __future__ import annotations

from django.conf import settings


class PreferSiteLanguageMiddleware:
    """Ignore Accept-Language unless the operator has chosen a language."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.cookie = settings.LANGUAGE_COOKIE_NAME

    def _chosen(self, request) -> str | None:
        """The language the operator picked, from wherever Django stored it."""
        session = getattr(request, "session", None)
        if session is not None and session.get("_language"):
            return session["_language"]
        return request.COOKIES.get(self.cookie)

    def __call__(self, request):
        if not self._chosen(request):
            # Removed rather than rewritten: LocaleMiddleware falls through to
            # LANGUAGE_CODE when the header is absent, which is exactly the
            # behaviour wanted, and rewriting it to "uz" would also override an
            # explicit ?language= form post on the same request.
            request.META.pop("HTTP_ACCEPT_LANGUAGE", None)
        return self.get_response(request)
