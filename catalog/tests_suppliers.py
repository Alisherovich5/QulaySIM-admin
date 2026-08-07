"""The supplier board, and the one distinction it must never blur.

A balance we could not read is not a balance of zero. One means "the network is
down, try again"; the other means "a customer will be charged and get no eSIM".
The page decides whether it is safe to switch card payments on, so conflating
them would be worse than having no page.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog import suppliers
from catalog.models import Country, Plan, PricingRule, SupplierOffer


def offer(plan, provider, cost, code=""):
    return SupplierOffer.objects.create(
        plan=plan, provider=provider, cost_usd=Decimal(cost),
        package_code=code or f"{provider}-{plan.pk}",
    )


class BalanceTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    @override_settings(ESIMACCESS_ACCESS_CODE="", ESIMCARD_API_TOKEN="")
    def test_an_unconfigured_supplier_is_unknown_not_zero(self):
        for balance in suppliers.balances(refresh=True):
            self.assertFalse(balance.known)
            self.assertFalse(balance.empty)
            self.assertIn("configured", balance.error)

    @override_settings(ESIMACCESS_ACCESS_CODE="code", ESIMACCESS_SECRET_KEY="s")
    def test_a_network_failure_is_unknown_not_zero(self):
        with patch("requests.post", side_effect=OSError("connection refused")):
            balance = suppliers._esimaccess_balance()
        self.assertFalse(balance.known)
        self.assertFalse(balance.empty)
        self.assertIn("connection refused", balance.error)

    @override_settings(ESIMACCESS_ACCESS_CODE="code", ESIMACCESS_SECRET_KEY="s")
    def test_a_refusal_arriving_as_http_200_is_still_a_refusal(self):
        # eSIM Access answers a rejected request with 200 and success=false, so
        # the status code alone proves nothing.
        class Response:
            status_code = 200

            def json(self):
                return {"success": False, "errorMsg": "bad signature"}

        with patch("requests.post", return_value=Response()):
            balance = suppliers._esimaccess_balance()
        self.assertFalse(balance.known)
        self.assertEqual(balance.error, "bad signature")

    @override_settings(ESIMACCESS_ACCESS_CODE="code", ESIMACCESS_SECRET_KEY="s")
    def test_a_real_zero_is_reported_as_empty(self):
        class Response:
            status_code = 200

            def json(self):
                return {"success": True, "obj": {"balance": 0}}

        with patch("requests.post", return_value=Response()):
            balance = suppliers._esimaccess_balance()
        self.assertTrue(balance.known)
        self.assertTrue(balance.empty)
        self.assertEqual(balance.amount, Decimal("0.00"))

    @override_settings(ESIMACCESS_ACCESS_CODE="code", ESIMACCESS_SECRET_KEY="s")
    def test_a_funded_wallet_is_neither_unknown_nor_empty(self):
        class Response:
            status_code = 200

            def json(self):
                return {"success": True, "obj": {"balance": 250.5}}

        with patch("requests.post", return_value=Response()):
            balance = suppliers._esimaccess_balance()
        self.assertTrue(balance.known)
        self.assertFalse(balance.empty)
        self.assertEqual(balance.amount, Decimal("250.50"))

    @override_settings(ESIMCARD_API_TOKEN="tok")
    def test_esimcard_http_error_is_unknown(self):
        class Response:
            status_code = 401

            def json(self):
                return {}

        with patch("requests.get", return_value=Response()):
            balance = suppliers._esimcard_balance()
        self.assertFalse(balance.known)
        self.assertEqual(balance.error, "HTTP 401")

    @override_settings(ESIMACCESS_ACCESS_CODE="code", ESIMACCESS_SECRET_KEY="s")
    def test_the_balance_is_cached_so_a_refresh_is_not_two_round_trips(self):
        class Response:
            status_code = 200

            def json(self):
                return {"success": True, "obj": {"balance": 7}}

        with patch("requests.post", return_value=Response()) as post, patch(
            "requests.get", side_effect=OSError("x")
        ):
            suppliers.balances(refresh=True)
            calls_after_first = post.call_count
            suppliers.balances()
            self.assertEqual(post.call_count, calls_after_first)

    @override_settings(ESIMACCESS_ACCESS_CODE="code", ESIMACCESS_SECRET_KEY="s")
    def test_refresh_skips_the_cache(self):
        class Response:
            status_code = 200

            def json(self):
                return {"success": True, "obj": {"balance": 7}}

        with patch("requests.post", return_value=Response()) as post, patch(
            "requests.get", side_effect=OSError("x")
        ):
            suppliers.balances(refresh=True)
            first = post.call_count
            suppliers.balances(refresh=True)
            self.assertGreater(post.call_count, first)


class ComparisonTests(TestCase):
    def setUp(self):
        PricingRule.objects.create(scope=PricingRule.Scope.GLOBAL, markup_percent=Decimal("50"))
        self.turkey = Country.objects.create(name="Turkey", name_uz="Turkiya", slug="tr", iso2="TR")
        self.japan = Country.objects.create(name="Japan", name_uz="Yaponiya", slug="jp", iso2="JP")

    def _plan(self, country, mb, code):
        return Plan.objects.create(
            country=country, title=f"{country.iso2} {mb}", data_amount_mb=mb, validity_days=7,
            cost_usd=Decimal("1.00"), price_usd=Decimal("0"),
            provider="esimaccess", provider_package_code=code,
        )

    def test_it_counts_which_supplier_is_cheaper(self):
        cheap_access = self._plan(self.turkey, 1024, "a1")
        offer(cheap_access, "esimaccess", "1.00")
        offer(cheap_access, "esimcard", "2.00")

        cheap_card = self._plan(self.turkey, 3072, "a2")
        offer(cheap_card, "esimaccess", "9.00")
        offer(cheap_card, "esimcard", "4.00")

        result = suppliers.comparison()
        access, card = result["stats"]
        self.assertEqual(access.cheaper_on, 1)
        self.assertEqual(card.cheaper_on, 1)
        self.assertEqual(result["both_plans"], 2)

    def test_a_destination_only_one_supplier_covers_is_counted_as_exclusive(self):
        only_access = self._plan(self.japan, 1024, "j1")
        offer(only_access, "esimaccess", "1.00")

        result = suppliers.comparison()
        access, card = result["stats"]
        self.assertEqual(access.exclusive_countries, 1)
        self.assertEqual(card.exclusive_countries, 0)
        row = next(r for r in result["countries"] if r.iso2 == "JP")
        self.assertEqual(row.only, "esimaccess")
        self.assertFalse(row.both)

    def test_sourcing_from_the_dearer_offer_is_reported_not_averaged_away(self):
        # Sourcing picks the cheapest on save, so this state means a price moved
        # without the plan being rewritten — worth naming, not hiding.
        plan = self._plan(self.turkey, 1024, "a1")
        offer(plan, "esimaccess", "5.00")
        offer(plan, "esimcard", "1.00")
        Plan.objects.filter(pk=plan.pk).update(provider="esimaccess", cost_usd=Decimal("5.00"))

        self.assertEqual(suppliers.comparison()["mis_sourced"], 1)

    def test_a_correctly_sourced_catalogue_reports_no_problem(self):
        plan = self._plan(self.turkey, 1024, "a1")
        offer(plan, "esimaccess", "5.00")
        offer(plan, "esimcard", "1.00")
        plan.resolve_sourcing(save=True)
        plan.refresh_from_db()

        self.assertEqual(plan.provider, "esimcard")
        self.assertEqual(suppliers.comparison()["mis_sourced"], 0)

    def test_the_biggest_gap_per_destination_is_recorded(self):
        plan = self._plan(self.turkey, 20480, "a1")
        offer(plan, "esimaccess", "265.38")
        offer(plan, "esimcard", "142.42")

        row = next(r for r in suppliers.comparison()["countries"] if r.iso2 == "TR")
        self.assertEqual(row.best_saving, Decimal("122.96"))

    def test_an_empty_catalogue_does_not_divide_by_zero(self):
        result = suppliers.comparison()
        self.assertEqual(result["both_plans"], 0)
        self.assertEqual(result["countries"], [])
        self.assertIsNone(result["cheapest_overall"])


@override_settings(SECURE_SSL_REDIRECT=False, ESIMACCESS_ACCESS_CODE="", ESIMCARD_API_TOKEN="")
class BoardPageTests(TestCase):
    def setUp(self):
        cache.clear()
        PricingRule.objects.create(scope=PricingRule.Scope.GLOBAL, markup_percent=Decimal("50"))
        country = Country.objects.create(name="Turkey", name_uz="Turkiya", slug="tr", iso2="TR")
        plan = Plan.objects.create(
            country=country, title="TR 1 GB", data_amount_mb=1024, validity_days=7,
            cost_usd=Decimal("1.00"), price_usd=Decimal("0"),
            provider="esimaccess", provider_package_code="a1",
        )
        offer(plan, "esimaccess", "1.00")
        offer(plan, "esimcard", "2.00")
        self.client.force_login(
            get_user_model().objects.create_superuser("sb", "s@x.uz", "Pw-1234-abcd")
        )
        self.url = reverse("admin:catalog_supplieroffer_board")

    def tearDown(self):
        cache.clear()

    def test_it_opens_even_when_no_supplier_can_be_reached(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "eSIM Access")
        self.assertContains(response, "eSIMCard")

    def test_an_unreadable_balance_does_not_trigger_the_empty_wallet_warning(self):
        # The warning means "a customer would be charged and get nothing". Showing
        # it because the network blipped would train the operator to ignore it.
        # Asserted on a data attribute, not the CSS class: the class name also
        # appears in the page's own stylesheet, so a class-based assertion passes
        # or fails for reasons that have nothing to do with the warning.
        response = self.client.get(self.url)
        self.assertNotContains(response, 'data-warn="empty-wallet"')

    @override_settings(ESIMACCESS_ACCESS_CODE="code", ESIMACCESS_SECRET_KEY="s")
    def test_a_real_zero_does_trigger_it(self):
        class Response:
            status_code = 200

            def json(self):
                return {"success": True, "obj": {"balance": 0}}

        with patch("requests.post", return_value=Response()), patch(
            "requests.get", side_effect=OSError("x")
        ):
            response = self.client.get(self.url)
        self.assertContains(response, 'data-warn="empty-wallet"')

    def test_it_says_topping_up_here_is_impossible_and_links_to_the_portals(self):
        response = self.client.get(self.url)
        self.assertContains(response, "portal.esimcard.com")
        self.assertContains(response, "esimaccess.com")

    def test_it_leaks_no_template_commentary(self):
        body = self.client.get(self.url).content.decode()
        self.assertNotIn("{#", body)
        self.assertNotIn("suppliers.py", body)

    def test_a_signed_out_visitor_cannot_read_supplier_costs(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (302, 403))
        self.assertNotIn("eSIMCard", response.content.decode())
