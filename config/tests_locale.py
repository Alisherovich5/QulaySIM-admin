"""The admin's language belongs to the panel, not to the browser.

The same admin rendered Uzbek in one browser and Russian in another, with nothing
on screen to explain it and nothing the operator had done to cause it — Django
was honouring Accept-Language, which is a preference the browser's installer
expressed, not one the operator did.

What must hold: no explicit choice means Uzbek, whatever the browser says; an
explicit choice still wins, or the language switcher would be a decoration.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(SECURE_SSL_REDIRECT=False, AXES_ENABLED=False)
class LanguageDefaultTests(TestCase):
    def setUp(self):
        self.url = reverse("admin:login")

    def _lang(self, response) -> str:
        return response.headers.get("Content-Language", "")

    def test_a_russian_browser_still_gets_uzbek(self):
        response = self.client.get(self.url, HTTP_ACCEPT_LANGUAGE="ru-RU,ru;q=0.9,en;q=0.8")
        self.assertEqual(self._lang(response), "uz")

    def test_an_english_browser_still_gets_uzbek(self):
        response = self.client.get(self.url, HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9")
        self.assertEqual(self._lang(response), "uz")

    def test_no_header_gets_uzbek(self):
        self.assertEqual(self._lang(self.client.get(self.url)), "uz")

    def test_an_explicit_choice_wins(self):
        # The switcher writes this cookie; ignoring it would make the switcher
        # a decoration.
        self.client.cookies["django_language"] = "ru"
        response = self.client.get(self.url, HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9")
        self.assertEqual(self._lang(response), "ru")

    def test_the_switcher_still_changes_the_language(self):
        response = self.client.post(
            reverse("set_language"),
            {"language": "ru", "next": self.url},
            HTTP_ACCEPT_LANGUAGE="uz",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._lang(self.client.get(self.url)), "ru")

    def test_the_storefront_side_is_not_affected(self):
        # The middleware is global; healthz must not start 500ing because of it.
        self.assertEqual(self.client.get("/healthz").status_code, 200)


@override_settings(SECURE_SSL_REDIRECT=False, AXES_ENABLED=False)
class RenderedLanguageTests(TestCase):
    def test_the_login_page_reads_uzbek_to_a_russian_browser(self):
        response = self.client.get(
            reverse("admin:login"), HTTP_ACCEPT_LANGUAGE="ru-RU,ru;q=0.9"
        )
        body = response.content.decode().lower()
        self.assertIn("foydalanuvchi nomi", body)
        self.assertNotIn("имя пользователя", body)

    def test_an_admin_page_reads_uzbek_to_a_russian_browser(self):
        user = get_user_model().objects.create_superuser("lang", "l@x.uz", "Pw-1234-abcd")
        self.client.force_login(user)
        response = self.client.get(reverse("admin:index"), HTTP_ACCEPT_LANGUAGE="ru,en;q=0.9")
        self.assertEqual(response.headers.get("Content-Language"), "uz")
