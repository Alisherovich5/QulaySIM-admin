"""The cost-and-price sheet: hand-typed prices must survive being typed.

The trap this page exists inside: Plan.save() recalculates the price from cost on
every write. So a hand-typed price that is not locked is overwritten before the
page has finished reloading — the operator types 12.34, hits save, and sees the
old number come back with no error and no explanation.

Locking on edit is therefore not a convenience. It is the difference between the
feature working and appearing to be broken.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import Country, Plan, PricingRule


# Production redirects to HTTPS, which turns every admin GET in a test into a
# 301. Same override the other admin tests use.
@override_settings(SECURE_SSL_REDIRECT=False)
class PriceSheetTests(TestCase):
    def setUp(self):
        PricingRule.objects.create(scope=PricingRule.Scope.GLOBAL, markup_percent=Decimal("50"))
        self.country = Country.objects.create(
            name="Turkey", name_uz="Turkiya", slug="turkey", iso2="TR"
        )
        self.plan = Plan.objects.create(
            country=self.country,
            title="Turkey 3 GB · 15 days",
            data_amount_mb=3072,
            validity_days=15,
            cost_usd=Decimal("1.39"),
            price_usd=Decimal("0"),
            provider="esimaccess",
            provider_package_code="TR_3_15",
        )
        self.other = Plan.objects.create(
            country=self.country,
            title="Turkey 5 GB · 30 days",
            data_amount_mb=5120,
            validity_days=30,
            cost_usd=Decimal("2.30"),
            price_usd=Decimal("0"),
            provider="esimaccess",
            provider_package_code="TR_5_30",
        )
        user = get_user_model().objects.create_superuser("staff", "s@x.uz", "Pw-1234-abcd")
        self.client.force_login(user)
        self.url = reverse("admin:catalog_plan_price_sheet")

    def test_the_rule_prices_it_before_anyone_touches_it(self):
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.price_usd, Decimal("2.09"))
        self.assertFalse(self.plan.price_locked)

    def test_the_page_shows_cost_grouped_by_destination(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # Cost is the whole point of the page — it was previously spread across a
        # changelist column and an inline. Django localises decimals, so under
        # the Uzbek locale this renders "1,39"; both spellings count.
        self.assertTrue("1.39" in body or "1,39" in body, "supplier cost must be shown")
        self.assertIn("Turkiya", body)
        self.assertIn(f'name="price-{self.plan.pk}"', body)

    def test_the_price_field_is_prefilled_unlocalised(self):
        # The value has to come back the way it went in. Localised, the field
        # would arrive as "2,09" and its round-trip would depend on the locale.
        self.plan.refresh_from_db()
        body = self.client.get(self.url).content.decode()
        self.assertIn(f'name="price-{self.plan.pk}"\n                           value="2.09"', body)

    def test_a_typed_price_is_saved_and_locked(self):
        self.client.post(self.url, {f"price-{self.plan.pk}": "12.34"})
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.price_usd, Decimal("12.34"))
        # Without the lock, save() would have recalculated it back to 2.09.
        self.assertTrue(self.plan.price_locked)

    def test_a_locked_price_survives_a_cost_change(self):
        self.client.post(self.url, {f"price-{self.plan.pk}": "12.34"})
        # What the nightly sync does: a new wholesale cost lands.
        self.plan.refresh_from_db()
        self.plan.cost_usd = Decimal("1.90")
        self.plan.save()
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.price_usd, Decimal("12.34"), "the lock must hold")

    def test_ticking_automatic_hands_it_back_to_the_rules(self):
        self.client.post(self.url, {f"price-{self.plan.pk}": "12.34"})
        self.client.post(
            self.url, {f"price-{self.plan.pk}": "12.34", f"auto-{self.plan.pk}": "on"}
        )
        self.plan.refresh_from_db()
        self.assertFalse(self.plan.price_locked)
        self.assertEqual(self.plan.price_usd, Decimal("2.09"), "repriced from cost")

    def test_a_comma_is_accepted_as_the_decimal_separator(self):
        # The local keyboard types a comma by default; refusing it would read as
        # the page being broken.
        self.client.post(self.url, {f"price-{self.plan.pk}": "3,75"})
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.price_usd, Decimal("3.75"))

    def test_rubbish_is_refused_and_reported_rather_than_written(self):
        for bad in ("abc", "-5", "0", "", "  "):
            with self.subTest(bad=bad):
                response = self.client.post(
                    self.url, {f"price-{self.plan.pk}": bad}, follow=True
                )
                self.plan.refresh_from_db()
                self.assertEqual(self.plan.price_usd, Decimal("2.09"))
                self.assertFalse(self.plan.price_locked)
                messages = [str(m) for m in response.context["messages"]]
                self.assertTrue(messages, "a refused value must say so")

    def test_an_untouched_row_is_left_exactly_as_it_was(self):
        # The form posts every visible tariff. Writing them all would fire
        # sourcing and repricing across the catalogue and stamp updated_at on
        # rows nobody edited.
        before = self.other.price_usd
        self.client.post(
            self.url,
            {
                f"price-{self.plan.pk}": "9.99",
                f"price-{self.other.pk}": str(before),
                f"auto-{self.other.pk}": "on",
            },
        )
        self.other.refresh_from_db()
        self.assertEqual(self.other.price_usd, before)
        self.assertFalse(self.other.price_locked)

    def test_a_price_equal_to_the_current_one_still_locks_when_asked(self):
        # Typing the same number with "automatic" unticked is a deliberate
        # instruction to freeze it there, not a no-op.
        self.plan.refresh_from_db()
        self.client.post(self.url, {f"price-{self.plan.pk}": str(self.plan.price_usd)})
        self.plan.refresh_from_db()
        self.assertTrue(self.plan.price_locked)

    def test_search_narrows_to_one_destination(self):
        Country.objects.create(name="Japan", name_uz="Yaponiya", slug="japan", iso2="JP")
        response = self.client.get(self.url, {"q": "yapon"})
        body = response.content.decode()
        self.assertIn("Yaponiya", body)
        self.assertNotIn("Turkiya", body)

    def test_a_reader_without_change_permission_is_turned_away(self):
        viewer = get_user_model().objects.create_user(
            "viewer", "v@x.uz", "Pw-1234-abcd", is_staff=True
        )
        self.client.force_login(viewer)
        response = self.client.post(self.url, {f"price-{self.plan.pk}": "1.00"})
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.price_usd, Decimal("2.09"))
        self.assertIn(response.status_code, (302, 403))
