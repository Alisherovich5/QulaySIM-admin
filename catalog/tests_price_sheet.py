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

from catalog.models import Country, Plan, PricingRule, Region


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


@override_settings(SECURE_SSL_REDIRECT=False)
class PriceSheetCoversEverythingTests(TestCase):
    """A paginated sheet reads as a partial one, so the totals are stated and
    the whole catalogue is reachable in one page if that is what someone wants.
    """

    def setUp(self):
        PricingRule.objects.create(scope=PricingRule.Scope.GLOBAL, markup_percent=Decimal("50"))
        self.europe = Region.objects.create(name="Europe", name_uz="Yevropa", slug="europe")
        self.asia = Region.objects.create(name="Asia", name_uz="Osiyo", slug="asia", sort_order=2)
        for index in range(22):
            region = self.europe if index % 2 == 0 else self.asia
            country = Country.objects.create(
                name=f"Country {index:02d}",
                name_uz=f"Davlat {index:02d}",
                slug=f"country-{index:02d}",
                iso2=f"C{index:X}"[:2],
                region=region,
            )
            Plan.objects.create(
                country=country,
                title=f"Country {index:02d} 3 GB",
                data_amount_mb=3072,
                validity_days=15,
                cost_usd=Decimal("2.00"),
                price_usd=Decimal("0"),
                provider="esimaccess",
                provider_package_code=f"C{index}",
            )
        # A multi-country tariff, which belongs to a region and no country.
        Plan.objects.create(
            region=self.europe,
            title="Europe 5 GB · 30 days",
            scope=Plan.Scope.REGIONAL,
            data_amount_mb=5120,
            validity_days=30,
            cost_usd=Decimal("11.00"),
            price_usd=Decimal("0"),
            provider="esimaccess",
            provider_package_code="EU_5_30",
        )
        user = get_user_model().objects.create_superuser("staff2", "s2@x.uz", "Pw-1234-abcd")
        self.client.force_login(user)
        self.url = reverse("admin:catalog_plan_price_sheet")

    def _blocks(self, **params):
        """Destination blocks only. Counted on a data attribute rather than the
        class name, which also appears in the page's own stylesheet."""
        body = self.client.get(self.url, params).content.decode()
        return body.count("data-country="), body

    def test_the_default_page_is_readable_not_complete(self):
        blocks, body = self._blocks()
        self.assertEqual(blocks, 15)
        # And it says so, so nobody mistakes the page for the catalogue.
        self.assertIn("22", body)

    def test_everything_fits_on_one_page_when_asked(self):
        blocks, body = self._blocks(show="all")
        self.assertEqual(blocks, 22)
        # The multi-country tariffs come along, in their own block.
        self.assertIn('data-regional="1"', body)

    def test_fifty_per_page_holds_them_all_here(self):
        blocks, _ = self._blocks(show="50")
        self.assertEqual(blocks, 22)

    def test_a_region_narrows_both_the_destinations_and_the_bundles(self):
        blocks, body = self._blocks(region="europe", show="all")
        self.assertEqual(blocks, 11)
        self.assertIn("Europe 5 GB", body)

    def test_a_region_with_no_bundle_shows_only_its_destinations(self):
        blocks, body = self._blocks(region="asia", show="all")
        self.assertEqual(blocks, 11)
        self.assertNotIn("Europe 5 GB", body)
        self.assertNotIn('data-regional="1"', body)

    def test_paging_keeps_the_filters(self):
        # 22 destinations at 15 a page means a second page exists, and its link
        # has to carry the region or the filter is lost on the way there.
        body = self.client.get(self.url, {"region": "europe", "show": "15"}).content.decode()
        self.assertIn("data-country=", body)
        body_all = self.client.get(self.url).content.decode()
        self.assertIn("page=2", body_all)
        self.assertIn("show=15", body_all)

    def test_an_unknown_page_size_falls_back_rather_than_erroring(self):
        blocks, _ = self._blocks(show="99999")
        self.assertEqual(blocks, 15)

    def test_the_page_says_how_much_of_the_catalogue_it_is_showing(self):
        _, body = self._blocks()
        # 15 of 22 — stated, because a paginated sheet reads as a partial one.
        self.assertIn("15", body)
        self.assertIn("22", body)


@override_settings(SECURE_SSL_REDIRECT=False)
class PriceSheetRendersNoCommentaryTests(TestCase):
    """Nothing meant for a developer may reach the screen.

    Two {# … #} blocks spanning several lines shipped their own reasoning to the
    page: Django only treats {# … #} as a comment when it closes on the same
    line, so the explanation was printed above the numbers it explained.
    """

    def setUp(self):
        PricingRule.objects.create(scope=PricingRule.Scope.GLOBAL, markup_percent=Decimal("50"))
        country = Country.objects.create(name="Turkey", name_uz="Turkiya", slug="turkey", iso2="TR")
        Plan.objects.create(
            country=country,
            title="Turkey 3 GB",
            data_amount_mb=3072,
            validity_days=15,
            cost_usd=Decimal("1.39"),
            price_usd=Decimal("0"),
            provider="esimaccess",
            provider_package_code="TR",
        )
        self.client.force_login(
            get_user_model().objects.create_superuser("staff3", "s3@x.uz", "Pw-1234-abcd")
        )

    def test_no_template_commentary_is_rendered(self):
        for url_name in (
            "admin:catalog_plan_price_sheet",
            "admin:catalog_country_board",
            "admin:catalog_plan_import_prices",
        ):
            with self.subTest(page=url_name):
                body = self.client.get(reverse(url_name)).content.decode()
                self.assertNotIn("{#", body)
                self.assertNotIn("#}", body)
                # Phrases from the comments, in case the markers were stripped
                # but the prose survived.
                self.assertNotIn("paginated sheet reads as a partial", body)
                self.assertNotIn("must not submit", body)
                self.assertNotIn("Django", body)

    def test_the_header_is_numbers_not_paragraphs(self):
        body = self.client.get(reverse("admin:catalog_plan_price_sheet")).content.decode()
        # The stat strip replaced three paragraphs of prose.
        self.assertIn("qs-ps__stats", body)
        self.assertEqual(body.count('class="qs-ps__stat"'), 3)
        # The locking rule survives as one short note, because getting it wrong
        # costs an operator their edit.
        self.assertIn("qs-ps__note", body)
        self.assertIn("qulflanadi", body)


@override_settings(SECURE_SSL_REDIRECT=False)
class PerRowSaveTests(TestCase):
    """One button per price, and the bug that made the bulk save look broken.

    With "all on one page" the sheet posts two fields per tariff. At 1377 tariffs
    that is 2755, and Django's default DATA_UPLOAD_MAX_NUMBER_FIELDS is 1000 — so
    every save was refused with a bare 400 and no hint that the page size was the
    reason. The operator typed a price, pressed save, and nothing happened.
    """

    def setUp(self):
        PricingRule.objects.create(scope=PricingRule.Scope.GLOBAL, markup_percent=Decimal("50"))
        country = Country.objects.create(name="Turkey", name_uz="Turkiya", slug="turkey", iso2="TR")
        self.a = Plan.objects.create(
            country=country, title="Turkey 3 GB", data_amount_mb=3072, validity_days=15,
            cost_usd=Decimal("2.00"), price_usd=Decimal("0"),
            provider="esimaccess", provider_package_code="A",
        )
        self.b = Plan.objects.create(
            country=country, title="Turkey 5 GB", data_amount_mb=5120, validity_days=30,
            cost_usd=Decimal("4.00"), price_usd=Decimal("0"),
            provider="esimaccess", provider_package_code="B",
        )
        self.client.force_login(
            get_user_model().objects.create_superuser("staff4", "s4@x.uz", "Pw-1234-abcd")
        )
        self.url = reverse("admin:catalog_plan_price_sheet")

    def test_every_row_has_its_own_save_button(self):
        body = self.client.get(self.url).content.decode()
        self.assertIn(f'name="row" value="{self.a.pk}"', body)
        self.assertIn(f'name="row" value="{self.b.pk}"', body)

    def test_a_row_save_touches_only_that_row(self):
        # The browser posts the whole form; the button says which row it meant.
        self.client.post(
            self.url,
            {
                "row": str(self.a.pk),
                f"price-{self.a.pk}": "9.99",
                f"price-{self.b.pk}": "1.11",
                f"auto-{self.b.pk}": "on",
            },
        )
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual(self.a.price_usd, Decimal("9.99"))
        self.assertTrue(self.a.price_locked)
        # The other row was in the payload and must be untouched.
        self.assertEqual(self.b.price_usd, Decimal("6.00"))
        self.assertFalse(self.b.price_locked)

    def test_a_row_save_can_also_unlock_just_that_row(self):
        self.client.post(self.url, {"row": str(self.a.pk), f"price-{self.a.pk}": "9.99"})
        self.client.post(
            self.url,
            {
                "row": str(self.a.pk),
                f"price-{self.a.pk}": "9.99",
                f"auto-{self.a.pk}": "on",
                f"price-{self.b.pk}": "1.11",
            },
        )
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertFalse(self.a.price_locked)
        self.assertEqual(self.a.price_usd, Decimal("3.00"), "back to the rule")
        self.assertEqual(self.b.price_usd, Decimal("6.00"), "still untouched")

    def test_the_bulk_save_still_works_without_a_row(self):
        self.client.post(
            self.url,
            {f"price-{self.a.pk}": "9.99", f"price-{self.b.pk}": "8.88"},
        )
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual((self.a.price_usd, self.b.price_usd), (Decimal("9.99"), Decimal("8.88")))

    def test_the_field_ceiling_clears_the_whole_catalogue_twice_over(self):
        from django.conf import settings as dj

        # Two fields per tariff, plus the CSRF token and the row marker.
        needed = Plan.objects.count() * 2 + 2
        self.assertGreater(
            dj.DATA_UPLOAD_MAX_NUMBER_FIELDS,
            needed,
            "a bulk save would be refused with a bare 400",
        )
        self.assertGreaterEqual(dj.DATA_UPLOAD_MAX_NUMBER_FIELDS, 8000)
