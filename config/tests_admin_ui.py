"""Admin UI details that must stay the way they were asked for."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

PASSWORD = "Pw-1234-abcd-efgh"


@override_settings(SECURE_SSL_REDIRECT=False, AXES_ENABLED=False)
class ShortcutPanelTests(TestCase):
    """Unfold's keyboard-shortcut panel is removed, and must stay removed.

    It is suppressed by an empty template of ours shadowing Unfold's, which works
    only as long as Unfold keeps including the same path. An upgrade that renames
    it would bring the panel back with no error and no sign — so the absence is
    asserted rather than assumed.
    """

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("sc", "sc@x.uz", PASSWORD)
        self.client.force_login(self.user)

    def test_the_panel_renders_nothing(self):
        from django.template.loader import get_template

        template = get_template("unfold/helpers/shortcuts.html")
        self.assertEqual(template.render({}).strip(), "")
        self.assertIn("/templates/unfold/helpers/", template.origin.name)

    def test_no_admin_page_carries_the_panel(self):
        body = self.client.get(reverse("admin:index")).content.decode()
        for marker in ("Available shortcuts", "Global shortcuts", "Changelist shortcuts"):
            self.assertNotIn(marker, body)

    def test_the_admin_still_works_without_it(self):
        # The panel is included from the base skeleton, so emptying it must not
        # take the page with it.
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)


@override_settings(SECURE_SSL_REDIRECT=False, AXES_ENABLED=False)
class LoginFieldTests(TestCase):
    """The browser must not be invited to draw its own panel over the form.

    A white autofill box kept covering the username field, could not be
    dismissed, and is not part of the document — no stylesheet we ship can move
    it. What summons it is the field's markup, so that is what these assert.
    """

    def setUp(self):
        self.body = self.client.get(reverse("admin:login")).content.decode()

    def _field(self, name: str) -> str:
        import re

        match = re.search(rf'<input[^>]*name="{name}"[^>]*>', self.body)
        assert match, f"{name} field not rendered"
        return match.group(0)

    def test_the_username_field_does_not_grab_focus(self):
        # Focus on load is what opens the suggestion panel before anything is
        # typed.
        self.assertNotIn("autofocus", self._field("username"))

    def test_neither_field_advertises_itself_to_autofill(self):
        for name in ("username", "password"):
            field = self._field(name)
            self.assertIn('autocomplete="off"', field)
            self.assertNotIn('autocomplete="username"', field)
            self.assertNotIn('autocomplete="current-password"', field)

    def test_the_password_manager_opt_outs_are_present(self):
        # 1Password and LastPass ignore autocomplete="off" and need their own.
        for name in ("username", "password"):
            field = self._field(name)
            self.assertIn("data-1p-ignore", field)
            self.assertIn("data-lpignore", field)

    def test_the_form_still_logs_in(self):
        # The point of the change is cosmetic; breaking authentication over it
        # would be a poor trade.
        get_user_model().objects.create_superuser("quiet", "q@x.uz", PASSWORD)
        response = self.client.post(
            reverse("admin:login"), {"username": "quiet", "password": PASSWORD}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)
