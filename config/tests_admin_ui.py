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


@override_settings(SECURE_SSL_REDIRECT=False, AXES_ENABLED=False)
class ModalOverlayTests(TestCase):
    """Unfold's empty modal must not cover the page.

    It ships on every admin page as an empty white box plus a full-screen
    backdrop-blur overlay, hidden only by Alpine. When Alpine does not finish
    initialising, both render — a blurred page with an undismissable white
    rectangle over the form, which is indistinguishable from the admin being
    broken. The stylesheet now hides them on :empty, which does not depend on
    the attribute that goes missing in that failure.
    """

    def test_the_stylesheet_hides_the_empty_modal(self):
        from django.conf import settings

        css = (settings.BASE_DIR / "static" / "admin" / "qulaysim-admin.css").read_text()
        self.assertIn("#modal-content:empty", css)
        self.assertIn("#modal-overlay", css)

    def test_the_rule_does_not_depend_on_x_cloak(self):
        # x-cloak is precisely what is missing when this fails, so a rule keyed
        # on it would fail in the same case.
        from django.conf import settings

        css = (settings.BASE_DIR / "static" / "admin" / "qulaysim-admin.css").read_text()
        block = css[css.index("#modal-content:empty"):]
        self.assertNotIn("x-cloak", block)

    def test_the_full_screen_wrapper_is_hidden_too(self):
        """Hiding only the white box leaves an invisible sheet over the page.

        The box sits inside a `fixed inset-0 z-70` wrapper. Hide the box alone
        and the page looks correct while nothing can be clicked or typed into —
        reported, accurately, as the admin having turned into a picture.
        """
        from django.conf import settings

        css = (settings.BASE_DIR / "static" / "admin" / "qulaysim-admin.css").read_text()
        self.assertIn("div:has(> #modal-content:empty)", css)

    def test_every_full_screen_overlay_on_the_page_is_covered_by_a_rule(self):
        # If Unfold adds a third overlay, this catches it before an operator
        # discovers it as an unclickable page.
        import re

        user = get_user_model().objects.create_superuser("ovl", "o@x.uz", PASSWORD)
        self.client.force_login(user)
        body = self.client.get(reverse("admin:index")).content.decode()
        overlays = [
            m.group(1)
            for m in re.finditer(r'<div[^>]*class="([^"]*)"[^>]*>', body)
            if "fixed" in m.group(1) and "bottom-0" in m.group(1) and "top-0" in m.group(1)
        ]
        from django.conf import settings

        css = (settings.BASE_DIR / "static" / "admin" / "qulaysim-admin.css").read_text()
        # Every full-screen overlay on the page must be answered by a rule, or it
        # sits invisibly on top and swallows clicks.
        self.assertIn("div:has(> #modal-content:empty)", css)
        self.assertIn("#modal-overlay", css)
        self.assertLessEqual(
            len(overlays), 4, f"a new full-screen overlay appeared: {overlays}"
        )

    def test_the_admin_page_still_carries_the_modal_markup(self):
        # Hidden, not deleted: a real modal must still be able to open.
        user = get_user_model().objects.create_superuser("modal", "m@x.uz", PASSWORD)
        self.client.force_login(user)
        body = self.client.get(reverse("admin:index")).content.decode()
        self.assertIn('id="modal-content"', body)

    def test_the_inputs_keep_their_styling(self):
        """The login form must not strip Unfold's classes off the inputs.

        Subclassing Django's AdminAuthenticationForm instead of Unfold's did
        exactly that: the fields rendered with no class, which on a white page
        means invisible white boxes under visible labels. It reads as the fields
        having vanished, and no error is raised anywhere.
        """
        import re

        body = self.client.get(reverse("admin:login")).content.decode()
        for name in ("username", "password"):
            tag = re.search(rf'<input[^>]*name="{name}"[^>]*>', body).group(0)
            cls = re.search(r'class="([^"]*)"', tag)
            self.assertIsNotNone(cls, f"{name} rendered with no class attribute")
            self.assertIn("border", cls.group(1))
            self.assertIn("bg-white", cls.group(1))
