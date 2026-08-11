"""The admin sidebar stays short, and the removals stay reversible.

Twenty-nine pages had accumulated. A sidebar that long is not a cosmetic problem:
the pages that matter get harder to find, and every extra row is another thing to
click by mistake on a live database.

What these tests hold down is the distinction that makes the cleanup safe — the
page is gone, the data is not. A future change that drops a model or a table would
pass a "page is absent" assertion while quietly destroying rows, so each one is
checked from both sides.
"""

from __future__ import annotations

from django.apps import apps
from django.contrib import admin
from django.test import TestCase

from config.admin_cleanup import UNREGISTER


class HiddenPagesTests(TestCase):
    def test_none_of_them_has_a_page(self):
        registered = {(m._meta.app_label, m.__name__) for m in admin.site._registry}
        still_there = sorted(k for k in UNREGISTER if k in registered)
        self.assertEqual(still_there, [], f"still in the sidebar: {still_there}")

    def test_all_of_them_still_have_a_model_and_a_table(self):
        """The page was removed, not the data.

        Deleting a model would also make `test_none_of_them_has_a_page` pass, so
        that assertion alone cannot tell a tidy sidebar from lost rows.
        """
        from django.db import connection

        tables = set(connection.introspection.table_names())
        for app_label, model_name in UNREGISTER:
            with self.subTest(model=f"{app_label}.{model_name}"):
                model = apps.get_model(app_label, model_name)
                self.assertIn(model._meta.db_table, tables)

    def test_every_removal_carries_a_reason(self):
        """A line with no reason is a line nobody can review later."""
        for key, reason in UNREGISTER.items():
            with self.subTest(model=key):
                self.assertTrue(reason and len(reason) > 12, key)


class KeptPagesTests(TestCase):
    """The pages that must survive a tidy-up.

    Empty today is not the same as unused: testimonials arrive from the account
    panel, complimentary grants from the giveaway page, and both would look like
    dead weight to anyone counting rows.
    """

    KEEP = [
        ("orders", "Order"),
        ("orders", "ESIM"),
        ("orders", "AtmosTransaction"),
        ("orders", "PromoCode"),
        ("orders", "ComplimentaryGrant"),
        ("orders", "TelegramRecipient"),
        ("catalog", "Plan"),
        ("catalog", "Country"),
        ("catalog", "PricingRule"),
        ("catalog", "SupplierOffer"),
        ("customers", "Customer"),
        ("content", "Testimonial"),
        # The one axes page worth having: it is what an operator reads when
        # somebody cannot log in, and clears to let them back in.
        ("axes", "AccessAttempt"),
    ]

    def test_they_are_all_still_reachable(self):
        registered = {(m._meta.app_label, m.__name__) for m in admin.site._registry}
        missing = [k for k in self.KEEP if k not in registered]
        self.assertEqual(missing, [], f"lost a page that is used: {missing}")
