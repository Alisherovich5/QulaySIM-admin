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
