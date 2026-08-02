"""Pricing engine tests.

This is money logic, so every branch of the cascade is covered: which rule
wins, the margin floor, and each rounding mode.
"""

from decimal import Decimal

from django.conf import settings
from django.test import TestCase, override_settings

from catalog.models import Country, Plan, PricingRule, Region, SupplierOffer
from catalog.pricing import calculate_price, margin, margin_percent, resolve_rule


def plan(cost="10.00", **kwargs):
    defaults = {
        "title": "Test plan",
        "cost_usd": Decimal(cost) if cost is not None else None,
        "price_usd": Decimal("0"),
        "provider": "esimaccess",
    }
    defaults.update(kwargs)
    return Plan(**defaults)


class RuleResolutionTests(TestCase):
    """Most specific wins: plan override → destination → supplier → everything."""

    @classmethod
    def setUpTestData(cls):
        cls.region = Region.objects.create(name="Asia", slug="asia")
        cls.japan = Country.objects.create(
            name="Japan", slug="japan", iso2="JP", region=cls.region
        )
        cls.turkey = Country.objects.create(
            name="Turkey", slug="turkey", iso2="TR", region=cls.region
        )

    def test_falls_back_to_the_default_with_no_rules(self):
        self.assertEqual(calculate_price(plan()), Decimal("13.00"))  # 30% default

    def test_global_rule_applies(self):
        PricingRule.objects.create(scope="global", markup_percent=Decimal("20"))
        self.assertEqual(calculate_price(plan()), Decimal("12.00"))

    def test_provider_rule_beats_global(self):
        PricingRule.objects.create(scope="global", markup_percent=Decimal("20"))
        PricingRule.objects.create(
            scope="provider", provider="esimaccess", markup_percent=Decimal("50")
        )
        self.assertEqual(calculate_price(plan()), Decimal("15.00"))

    def test_country_rule_beats_provider(self):
        PricingRule.objects.create(scope="global", markup_percent=Decimal("20"))
        PricingRule.objects.create(
            scope="provider", provider="esimaccess", markup_percent=Decimal("50")
        )
        PricingRule.objects.create(
            scope="country", country=self.japan, markup_percent=Decimal("80")
        )
        self.assertEqual(calculate_price(plan(country=self.japan)), Decimal("18.00"))
        # A different destination still follows the supplier rule.
        self.assertEqual(calculate_price(plan(country=self.turkey)), Decimal("15.00"))

    def test_plan_override_beats_every_rule(self):
        PricingRule.objects.create(
            scope="country", country=self.japan, markup_percent=Decimal("80")
        )
        priced = plan(country=self.japan, markup_percent=Decimal("10"))
        self.assertEqual(calculate_price(priced), Decimal("11.00"))

    def test_inactive_rules_are_ignored(self):
        PricingRule.objects.create(scope="global", markup_percent=Decimal("20"))
        PricingRule.objects.create(
            scope="provider",
            provider="esimaccess",
            markup_percent=Decimal("90"),
            is_active=False,
        )
        self.assertEqual(calculate_price(plan()), Decimal("12.00"))

    def test_no_cost_means_no_calculated_price(self):
        """Plans without a supplier are priced by hand, not by the engine."""
        self.assertIsNone(calculate_price(plan(cost=None)))
        self.assertIsNone(calculate_price(plan(cost="0.00")))

    def test_resolve_rule_returns_none_when_nothing_matches(self):
        self.assertIsNone(resolve_rule(plan()))


class MarginFloorTests(TestCase):
    def test_percentage_alone_leaves_pennies_on_a_cheap_plan(self):
        PricingRule.objects.create(scope="global", markup_percent=Decimal("30"))
        self.assertEqual(calculate_price(plan(cost="0.40")), Decimal("0.52"))

    def test_floor_lifts_the_price(self):
        PricingRule.objects.create(
            scope="global", markup_percent=Decimal("30"), min_margin_usd=Decimal("1.50")
        )
        self.assertEqual(calculate_price(plan(cost="0.40")), Decimal("1.90"))

    def test_floor_does_not_lower_a_healthy_margin(self):
        PricingRule.objects.create(
            scope="global", markup_percent=Decimal("30"), min_margin_usd=Decimal("1.50")
        )
        # 30% of $100 is $30, comfortably above the $1.50 floor.
        self.assertEqual(calculate_price(plan(cost="100.00")), Decimal("130.00"))


class RoundingTests(TestCase):
    def _price(self, mode, cost="10.00", markup="17"):
        PricingRule.objects.create(
            scope="global", markup_percent=Decimal(markup), rounding=mode
        )
        return calculate_price(plan(cost=cost))

    def test_exact_cents(self):
        self.assertEqual(self._price("none"), Decimal("11.70"))

    def test_charm_pricing_ends_in_99(self):
        self.assertEqual(self._price("charm"), Decimal("11.99"))

    def test_charm_never_rounds_down(self):
        """11.99 must not become 11.99 from 12.40 — that would lose margin."""
        PricingRule.objects.create(scope="global", markup_percent=Decimal("24"), rounding="charm")
        self.assertEqual(calculate_price(plan(cost="10.00")), Decimal("12.99"))

    def test_half_rounds_up(self):
        self.assertEqual(self._price("half"), Decimal("12.00"))

    def test_whole_rounds_up(self):
        self.assertEqual(self._price("whole"), Decimal("12.00"))


class PlanSaveTests(TestCase):
    def test_saving_recalculates_the_price(self):
        PricingRule.objects.create(scope="global", markup_percent=Decimal("40"))
        p = Plan.objects.create(title="Auto", cost_usd=Decimal("5.00"), price_usd=Decimal("0"))
        self.assertEqual(p.price_usd, Decimal("7.00"))

    def test_locked_price_survives_a_save(self):
        PricingRule.objects.create(scope="global", markup_percent=Decimal("40"))
        p = Plan.objects.create(
            title="Manual",
            cost_usd=Decimal("5.00"),
            price_usd=Decimal("99.00"),
            price_locked=True,
        )
        self.assertEqual(p.price_usd, Decimal("99.00"))

    def test_changing_cost_moves_the_price(self):
        PricingRule.objects.create(scope="global", markup_percent=Decimal("50"))
        p = Plan.objects.create(title="Drift", cost_usd=Decimal("4.00"), price_usd=Decimal("0"))
        self.assertEqual(p.price_usd, Decimal("6.00"))
        p.cost_usd = Decimal("8.00")
        p.save()
        self.assertEqual(p.price_usd, Decimal("12.00"))


class MarginReportingTests(TestCase):
    def test_margin_and_percentage(self):
        p = plan(cost="10.00")
        p.price_usd = Decimal("13.50")
        self.assertEqual(margin(p), Decimal("3.50"))
        self.assertEqual(margin_percent(p), Decimal("35.00"))

    def test_margin_is_unknown_without_a_cost(self):
        p = plan(cost=None)
        p.price_usd = Decimal("9.99")
        self.assertIsNone(margin(p))
        self.assertIsNone(margin_percent(p))

    def test_negative_margin_is_reported_not_hidden(self):
        p = plan(cost="10.00")
        p.price_usd = Decimal("8.00")
        self.assertEqual(margin(p), Decimal("-2.00"))


class RuleChangePropagationTests(TestCase):
    """Changing a rule must move the prices it governs.

    Regression guard: rules used to take effect only when someone remembered
    to run the bulk action, so an admin could change a markup and see nothing
    happen.
    """

    def test_saving_a_global_rule_reprices_the_catalogue(self):
        rule = PricingRule.objects.create(scope="global", markup_percent=Decimal("20"))
        p = Plan.objects.create(title="A", cost_usd=Decimal("10.00"), price_usd=Decimal("0"))
        self.assertEqual(p.price_usd, Decimal("12.00"))

        rule.markup_percent = Decimal("50")
        rule.save()

        p.refresh_from_db()
        self.assertEqual(p.price_usd, Decimal("15.00"))

    def test_a_provider_rule_only_touches_that_provider(self):
        PricingRule.objects.create(scope="global", markup_percent=Decimal("20"))
        theirs = Plan.objects.create(
            title="Supplier", cost_usd=Decimal("10.00"), price_usd=Decimal("0"),
            provider="esimaccess",
        )
        ours = Plan.objects.create(
            title="Manual", cost_usd=Decimal("10.00"), price_usd=Decimal("0"), provider="mock"
        )

        PricingRule.objects.create(
            scope="provider", provider="esimaccess", markup_percent=Decimal("90")
        )

        theirs.refresh_from_db()
        ours.refresh_from_db()
        self.assertEqual(theirs.price_usd, Decimal("19.00"))
        self.assertEqual(ours.price_usd, Decimal("12.00"), "the global rule should still apply")

    def test_locked_plans_survive_a_rule_change(self):
        rule = PricingRule.objects.create(scope="global", markup_percent=Decimal("20"))
        locked = Plan.objects.create(
            title="Fixed",
            cost_usd=Decimal("10.00"),
            price_usd=Decimal("99.00"),
            price_locked=True,
        )
        rule.markup_percent = Decimal("80")
        rule.save()

        locked.refresh_from_db()
        self.assertEqual(locked.price_usd, Decimal("99.00"))

    def test_deleting_a_rule_reprices_what_it_governed(self):
        """Deactivating a rule reprices through save(); deleting one used to
        reprice nothing, so the catalogue kept the dead rule's prices until
        each plan happened to be saved for some other reason."""
        PricingRule.objects.create(scope="global", markup_percent=Decimal("20"))
        japan = Country.objects.create(name="Japan", slug="japan", iso2="JP")
        japan_rule = PricingRule.objects.create(
            scope="country", country=japan, markup_percent=Decimal("80")
        )
        p = Plan.objects.create(
            title="J", country=japan, cost_usd=Decimal("10.00"), price_usd=Decimal("0")
        )
        self.assertEqual(p.price_usd, Decimal("18.00"))

        japan_rule.delete()

        p.refresh_from_db()
        self.assertEqual(p.price_usd, Decimal("12.00"), "must fall back to the global rule")

    def test_retargeting_a_rule_reprices_the_old_scope_too(self):
        """Moving the rule from Japan to Turkey used to reprice Turkey only,
        leaving Japan's plans priced by a rule that no longer names them."""
        PricingRule.objects.create(scope="global", markup_percent=Decimal("20"))
        japan = Country.objects.create(name="Japan", slug="japan", iso2="JP")
        turkey = Country.objects.create(name="Turkey", slug="turkey", iso2="TR")
        rule = PricingRule.objects.create(
            scope="country", country=japan, markup_percent=Decimal("80")
        )
        in_japan = Plan.objects.create(
            title="J", country=japan, cost_usd=Decimal("10.00"), price_usd=Decimal("0")
        )
        in_turkey = Plan.objects.create(
            title="T", country=turkey, cost_usd=Decimal("10.00"), price_usd=Decimal("0")
        )
        self.assertEqual(in_japan.price_usd, Decimal("18.00"))
        self.assertEqual(in_turkey.price_usd, Decimal("12.00"))

        rule.country = turkey
        rule.save()

        in_japan.refresh_from_db()
        in_turkey.refresh_from_db()
        self.assertEqual(in_japan.price_usd, Decimal("12.00"), "the old scope must fall back")
        self.assertEqual(in_turkey.price_usd, Decimal("18.00"))

    def test_bulk_deleting_rules_from_the_changelist_reprices_too(self):
        """The changelist action deletes through the queryset, which skips
        PricingRule.delete() — the admin override has to reprice instead."""
        from django.contrib.admin.sites import site
        from django.test import RequestFactory

        PricingRule.objects.create(scope="global", markup_percent=Decimal("20"))
        japan = Country.objects.create(name="Japan", slug="japan", iso2="JP")
        japan_rule = PricingRule.objects.create(
            scope="country", country=japan, markup_percent=Decimal("80")
        )
        p = Plan.objects.create(
            title="J", country=japan, cost_usd=Decimal("10.00"), price_usd=Decimal("0")
        )
        self.assertEqual(p.price_usd, Decimal("18.00"))

        model_admin = site._registry[PricingRule]
        model_admin.delete_queryset(
            RequestFactory().post("/"), PricingRule.objects.filter(pk=japan_rule.pk)
        )

        p.refresh_from_db()
        self.assertEqual(p.price_usd, Decimal("12.00"))

    def test_repricing_clears_the_cache_only_after_the_prices_move(self):
        """The invalidation must ride on_commit: rule saves used to clear Redis
        *before* bulk_update wrote the new prices, so the API could re-cache
        the old ones and keep them for the whole TTL."""
        from unittest.mock import patch

        rule = PricingRule.objects.create(scope="global", markup_percent=Decimal("20"))
        p = Plan.objects.create(title="A", cost_usd=Decimal("10.00"), price_usd=Decimal("0"))

        # The signal's own reference is muted so only apply_to_plans' explicit
        # invalidation — the one that covers the signal-less bulk_update — is
        # measured, and nothing touches a real Redis.
        with (
            patch("config.cache.invalidate_catalogue") as invalidate,
            patch("catalog.signals.invalidate_catalogue"),
        ):
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                rule.markup_percent = Decimal("50")
                rule.save()

            p.refresh_from_db()
            self.assertEqual(p.price_usd, Decimal("15.00"), "prices move first")
            self.assertFalse(invalidate.called, "nothing is cleared before commit")

            for callback in callbacks:
                callback()
            self.assertTrue(invalidate.called, "commit is what clears the cache")


@override_settings(SECURE_SSL_REDIRECT=False)
class AdminLockoutTests(TestCase):
    """The admin login must not accept unlimited attempts.

    Regression guard: without a lockout the only barrier is the password, and
    the obscure admin path ends up doing security work it cannot carry.

    SSL redirect is disabled for these tests: with it on, the test client's
    plain-HTTP POST is answered with a 301 before it ever reaches the login
    view, which makes a lockout assertion pass without testing anything.
    """

    def setUp(self):
        from django.contrib.auth.models import User

        User.objects.create_superuser("locktest", "lock@example.com", "correct-horse-battery")
        self.login_url = f"/{settings.ADMIN_URL_PATH}/login/"

    def tearDown(self):
        from axes.utils import reset

        reset()

    def _attempt(self, username, password):
        return self.client.post(
            self.login_url, {"username": username, "password": password}, follow=False
        )

    def test_a_correct_password_signs_in(self):
        """Baseline: without this, a lockout assertion could pass because login
        was broken rather than because the lockout worked."""
        response = self._attempt("locktest", "correct-horse-battery")
        self.assertEqual(response.status_code, 302)

    def test_repeated_failures_are_locked_out(self):
        for _ in range(settings.AXES_FAILURE_LIMIT):
            self._attempt("locktest", "wrong")

        # The *correct* password must now be refused — that is the lockout.
        response = self._attempt("locktest", "correct-horse-battery")
        self.assertNotEqual(
            response.status_code, 302, "a locked-out account should not be able to sign in"
        )

    def test_a_different_account_is_unaffected(self):
        """Locked on username+IP together, so one attacker cannot lock every
        administrator out by failing logins against their names."""
        from django.contrib.auth.models import User

        User.objects.create_superuser("other", "other@example.com", "another-long-password")
        for _ in range(settings.AXES_FAILURE_LIMIT + 2):
            self._attempt("locktest", "wrong")

        response = self._attempt("other", "another-long-password")
        self.assertEqual(response.status_code, 302, "an untargeted account should still sign in")


# Both suppliers are treated as connected here: these tests pin the *generic*
# comparison mechanics (cheapest wins, fallback on outage, stable ties), which
# must hold for any pair of live suppliers. What happens when a supplier is NOT
# in FULFILLABLE_PROVIDERS is pinned separately in FulfillabilityTests.
@override_settings(FULFILLABLE_PROVIDERS=["esimaccess", "esimcard"])
class SupplierComparisonTests(TestCase):
    """The point of holding several supplier prices: the cheapest one wins.

    Each test asserts on cost, provider *and* package code together, because a
    plan sourced at the right price from the wrong package code would order the
    wrong eSIM — a failure that a price-only assertion would not catch.
    """

    def setUp(self):
        PricingRule.objects.create(scope=PricingRule.Scope.GLOBAL, markup_percent=Decimal("50"))
        self.country = Country.objects.create(name="Turkey", slug="turkey", iso2="TR")
        self.plan = Plan.objects.create(
            country=self.country,
            title="Turkey 5GB",
            data_amount_mb=5120,
            validity_days=30,
            cost_usd=Decimal("10.00"),
            price_usd=Decimal("15.00"),
        )

    def _offer(self, provider, cost, code=None, available=True):
        return SupplierOffer.objects.create(
            plan=self.plan,
            provider=provider,
            package_code=code or f"{provider.upper()}-TR-5-30",
            cost_usd=Decimal(cost),
            is_available=available,
        )

    def test_cheapest_offer_becomes_the_plans_cost_and_route(self):
        self._offer("esimaccess", "4.20")
        self._offer("esimcard", "3.80")

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.cost_usd, Decimal("3.80"))
        self.assertEqual(self.plan.provider, "esimcard")
        self.assertEqual(self.plan.provider_package_code, "ESIMCARD-TR-5-30")

    def test_price_follows_the_cheaper_cost(self):
        self._offer("esimaccess", "4.20")
        self._offer("esimcard", "3.80")

        self.plan.refresh_from_db()
        # 3.80 + 50% — the saving reaches the customer rather than being lost
        # against a cost the pricing engine never saw.
        self.assertEqual(self.plan.price_usd, Decimal("5.70"))

    def test_a_dearer_offer_does_not_take_over(self):
        self._offer("esimcard", "3.80")
        self._offer("esimaccess", "9.00")

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.provider, "esimcard")
        self.assertEqual(self.plan.cost_usd, Decimal("3.80"))

    def test_unavailable_offer_hands_over_to_the_fallback(self):
        cheap = self._offer("esimcard", "3.80")
        self._offer("esimaccess", "4.20")

        cheap.is_available = False
        cheap.unavailable_reason = "out of stock"
        cheap.save()

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.provider, "esimaccess")
        self.assertEqual(self.plan.cost_usd, Decimal("4.20"))

    def test_deleting_the_winner_hands_over_to_the_fallback(self):
        cheap = self._offer("esimcard", "3.80")
        self._offer("esimaccess", "4.20")
        cheap.delete()

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.provider, "esimaccess")
        self.assertEqual(self.plan.cost_usd, Decimal("4.20"))

    def test_plan_with_no_offers_keeps_its_hand_entered_cost(self):
        # The catalogue predates supplier comparison, so sourcing must not
        # clear the cost of a plan nobody has added offers for.
        self.plan.save()
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.cost_usd, Decimal("10.00"))
        self.assertEqual(self.plan.provider, "mock")

    def test_losing_every_offer_does_not_zero_the_cost(self):
        offer = self._offer("esimcard", "3.80")
        offer.delete()

        self.plan.refresh_from_db()
        # Both suppliers being out is an outage, not a reason to sell at cost 0.
        self.assertEqual(self.plan.cost_usd, Decimal("3.80"))

    def test_saving_reports_the_gap_to_the_runner_up(self):
        self._offer("esimaccess", "4.20")
        self._offer("esimcard", "3.80")

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.sourcing_saving_usd, Decimal("0.40"))

    def test_a_single_offer_reports_no_saving(self):
        self._offer("esimcard", "3.80")
        self.plan.refresh_from_db()
        # One price is not a comparison; claiming a saving here would be a lie.
        self.assertIsNone(self.plan.sourcing_saving_usd)

    def test_unavailable_offers_are_excluded_from_the_ranking(self):
        self._offer("esimcard", "1.00", available=False)
        self._offer("esimaccess", "4.20")

        self.plan.refresh_from_db()
        self.assertEqual([o.provider for o in self.plan.ranked_offers], ["esimaccess"])
        self.assertEqual(self.plan.cost_usd, Decimal("4.20"))

    def test_one_offer_per_supplier_per_plan(self):
        from django.db.utils import IntegrityError

        self._offer("esimcard", "3.80")
        with self.assertRaises(IntegrityError):
            self._offer("esimcard", "3.50")

    def test_narrowed_update_fields_still_persists_the_new_route(self):
        # The recalculate action saves with update_fields=["price_usd"]; before
        # widening it, a sourcing change made in the same save was dropped.
        self._offer("esimaccess", "4.20")
        self._offer("esimcard", "3.80")
        Plan.objects.filter(pk=self.plan.pk).update(
            provider="esimaccess", cost_usd=Decimal("4.20")
        )

        plan = Plan.objects.get(pk=self.plan.pk)
        plan.save(update_fields=["price_usd"])

        plan.refresh_from_db()
        self.assertEqual(plan.provider, "esimcard")
        self.assertEqual(plan.cost_usd, Decimal("3.80"))

    def test_changelist_does_not_query_per_row(self):
        from django.contrib.admin.sites import site
        from django.test import RequestFactory

        for index in range(12):
            plan = Plan.objects.create(
                country=self.country,
                title=f"Plan {index}",
                validity_days=7,
                cost_usd=Decimal("5.00"),
                price_usd=Decimal("7.50"),
            )
            SupplierOffer.objects.create(
                plan=plan, provider="esimaccess", package_code="A", cost_usd=Decimal("5.00")
            )
            SupplierOffer.objects.create(
                plan=plan, provider="esimcard", package_code="B", cost_usd=Decimal("4.50")
            )

        model_admin = site._registry[Plan]
        request = RequestFactory().get("/")
        plans = list(model_admin.get_queryset(request))
        with self.assertNumQueries(0):
            # Prefetched, so rendering the sourcing and fulfilment columns
            # touches no database.
            for plan in plans:
                model_admin.sourcing_col(plan)
                model_admin.fulfilment_warning(plan)


class SidebarNavigationTests(TestCase):
    """Every sidebar link must resolve under the configured admin path.

    These were literal "/admin/..." strings while the deployed admin lives at
    ADMIN_URL_PATH, so the whole navigation returned 404 in production.
    """

    def test_links_follow_a_renamed_admin_path(self):
        """Rebuild the URLconf under a different admin path and re-resolve.

        Asserting the prefix against the current settings would pass for the
        wrong reason: locally ADMIN_URL_PATH already is "admin", which is
        exactly the value the hardcoded links happened to match.
        """
        import importlib

        import config.urls
        from django.urls import clear_url_caches

        def rebuild():
            from django.urls import set_urlconf

            importlib.reload(config.urls)
            clear_url_caches()
            set_urlconf(None)

        # The restore sits OUTSIDE the override: rebuilding inside it re-read
        # the renamed path and left every later test resolving against
        # /secret-panel/.
        try:
            with override_settings(ADMIN_URL_PATH="secret-panel"):
                rebuild()
                links = [
                    str(item["link"])
                    for group in settings.UNFOLD["SIDEBAR"]["navigation"]
                    for item in group["items"]
                ]
                self.assertTrue(links)
                for link in links:
                    self.assertTrue(
                        link.startswith("/secret-panel/"),
                        f"{link} did not follow the renamed admin path",
                    )
        finally:
            rebuild()

    @override_settings(SECURE_SSL_REDIRECT=False)
    def test_every_sidebar_link_is_a_real_view(self):
        from django.contrib.auth.models import User

        User.objects.create_superuser("nav-admin", "nav@example.com", "pw-for-tests-only")
        self.client.force_login(User.objects.get(username="nav-admin"))

        for group in settings.UNFOLD["SIDEBAR"]["navigation"]:
            for item in group["items"]:
                with self.subTest(link=str(item["link"])):
                    response = self.client.get(str(item["link"]))
                    self.assertEqual(response.status_code, 200)


@override_settings(SECURE_SSL_REDIRECT=False)
@override_settings(LANGUAGE_CODE="en")
class PlanAdminRenderTests(TestCase):
    """The plan editor and its comparison block must actually render.

    A broken format_html call or a fieldset naming a field that does not exist
    raises at render time, not at check time — so only a request catches it.
    """

    def setUp(self):
        from django.contrib.auth.models import User

        PricingRule.objects.create(scope=PricingRule.Scope.GLOBAL, markup_percent=Decimal("40"))
        self.country = Country.objects.create(name="Japan", slug="japan", iso2="JP")
        self.plan = Plan.objects.create(
            country=self.country,
            title="Japan 3GB",
            data_amount_mb=3072,
            validity_days=15,
            cost_usd=Decimal("6.00"),
            price_usd=Decimal("8.40"),
        )
        User.objects.create_superuser("render-admin", "r@example.com", "pw-for-tests-only")
        self.client.force_login(User.objects.get(username="render-admin"))

    def _change_url(self):
        from django.urls import reverse

        return reverse("admin:catalog_plan_change", args=[self.plan.pk])

    def test_change_page_renders_with_no_offers(self):
        response = self.client.get(self._change_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No supplier offers yet")

    # Both suppliers connected, so the readout renders its winner + fallback
    # shape; the not-connected rendering has its own test below.
    @override_settings(FULFILLABLE_PROVIDERS=["esimaccess", "esimcard"])
    def test_change_page_shows_the_winner_and_the_fallback(self):
        SupplierOffer.objects.create(
            plan=self.plan, provider="esimaccess", package_code="JP_3_15", cost_usd=Decimal("6.00")
        )
        SupplierOffer.objects.create(
            plan=self.plan, provider="esimcard", package_code="jp-3gb", cost_usd=Decimal("5.25")
        )

        response = self.client.get(self._change_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ordered from here")
        self.assertContains(response, "fallback")

    def test_change_page_marks_an_unconnected_supplier(self):
        """A cheaper offer from a supplier the API cannot buy from must not be
        presented as the source or as a usable fallback."""
        SupplierOffer.objects.create(
            plan=self.plan, provider="esimaccess", package_code="JP_3_15", cost_usd=Decimal("6.00")
        )
        SupplierOffer.objects.create(
            plan=self.plan, provider="esimcard", package_code="jp-3gb", cost_usd=Decimal("5.25")
        )

        response = self.client.get(self._change_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ordered from here")
        self.assertContains(response, "not connected")
        # "· fallback" is the comparison row's label; the plain word also
        # appears in the fieldset's explanatory text, which is fine.
        self.assertNotContains(response, "· fallback")

    def test_change_page_shows_why_an_offer_is_out(self):
        SupplierOffer.objects.create(
            plan=self.plan,
            provider="esimcard",
            package_code="jp-3gb",
            cost_usd=Decimal("1.00"),
            is_available=False,
            unavailable_reason="no balance",
        )
        response = self.client.get(self._change_url())
        self.assertContains(response, "no balance")

    # Both suppliers connected: this pins the saving figure the column shows
    # when the comparison is real. The not-connected shape is pinned below.
    @override_settings(FULFILLABLE_PROVIDERS=["esimaccess", "esimcard"])
    def test_changelist_renders_the_sourcing_column(self):
        from django.urls import reverse

        SupplierOffer.objects.create(
            plan=self.plan, provider="esimaccess", package_code="JP_3_15", cost_usd=Decimal("6.00")
        )
        SupplierOffer.objects.create(
            plan=self.plan, provider="esimcard", package_code="jp-3gb", cost_usd=Decimal("5.25")
        )
        response = self.client.get(reverse("admin:catalog_plan_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "eSIMCard")
        self.assertContains(response, "$0.75")

    def test_changelist_names_the_cheaper_unconnected_supplier(self):
        """The connected winner is the source; the cheaper unconnected offer is
        named so the saving is visible the day its integration lands."""
        from django.urls import reverse

        SupplierOffer.objects.create(
            plan=self.plan, provider="esimaccess", package_code="JP_3_15", cost_usd=Decimal("6.00")
        )
        SupplierOffer.objects.create(
            plan=self.plan, provider="esimcard", package_code="jp-3gb", cost_usd=Decimal("5.25")
        )
        response = self.client.get(reverse("admin:catalog_plan_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "eSIM Access")
        self.assertContains(response, "not connected")
        self.assertContains(response, "$5.25")

    def test_supplier_offer_changelist_renders_verdicts(self):
        from django.urls import reverse

        SupplierOffer.objects.create(
            plan=self.plan, provider="esimaccess", package_code="JP_3_15", cost_usd=Decimal("6.00")
        )
        SupplierOffer.objects.create(
            plan=self.plan, provider="esimcard", package_code="jp-3gb", cost_usd=Decimal("5.25")
        )
        response = self.client.get(reverse("admin:catalog_supplieroffer_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cheapest")
        self.assertContains(response, "fallback")


@override_settings(SECURE_SSL_REDIRECT=False)
@override_settings(LANGUAGE_CODE="en")
class MarginSortingTests(TestCase):
    """The margin column's header must sort the changelist.

    The displayed figure is a Python property, which Django cannot order by —
    the column is backed by a price-minus-cost annotation instead, and this
    pins the two to each other: if someone renames the annotation or the
    property drifts (say, margin net of a fee), the mismatch shows up here
    rather than as a column that silently sorts by the wrong number.
    """

    def setUp(self):
        from django.contrib.auth.models import User

        country = Country.objects.create(name="Japan", slug="japan", iso2="JP")
        # Chosen so that ordering by margin, by price and by cost each produce
        # a DIFFERENT sequence — a column that quietly sorts by the wrong
        # number cannot pass by coincidence.
        #   margin: C (0.20) < A (0.50) < B (5.00)
        #   price:  B (6.00) < C (7.00) < A (9.00)
        #   cost:   B (1.00) < C (6.80) < A (8.50)
        for title, cost, price in (
            ("A", Decimal("8.50"), Decimal("9.00")),
            ("B", Decimal("1.00"), Decimal("6.00")),
            ("C", Decimal("6.80"), Decimal("7.00")),
            ("Manual", None, Decimal("9.99")),            # no cost — margin unknowable
        ):
            # price_locked, or Plan.save() reprices the row from the default
            # markup and silently rewrites this fixture's arithmetic — which is
            # exactly what it did to the first version of this test.
            Plan.objects.create(
                country=country,
                title=title,
                data_amount_mb=1024,
                validity_days=7,
                cost_usd=cost,
                price_usd=price,
                price_locked=True,
            )
        User.objects.create_superuser("sort-admin", "s@example.com", "pw-for-tests-only")
        self.client.force_login(User.objects.get(username="sort-admin"))

    def _changelist(self, query=None):
        from django.urls import reverse

        response = self.client.get(reverse("admin:catalog_plan_changelist"), query or {})
        self.assertEqual(response.status_code, 200)
        return response

    def _margin_sort_href(self):
        """The href the Margin header actually renders, whatever Django's
        column-index convention is this release."""
        import re

        html = self._changelist().content.decode()
        # Non-greedy across th boundaries would happily borrow the next
        # column's link; cut the haystack down to this one header cell first.
        cell = re.search(r'<th[^>]*column-margin_col[^>]*>(.*?)</th>', html, re.S)
        self.assertIsNotNone(cell, "Margin header cell not rendered")
        cell = re.search(r'href="\?([^"]+)"', cell.group(1))
        self.assertIsNotNone(cell, "Margin header has no sort link — the annotation ordering is gone")
        from urllib.parse import parse_qs

        return {k: v[0] for k, v in parse_qs(cell.group(1)).items()}

    def _titles(self, query):
        return [p.title for p in self._changelist(query).context["cl"].result_list]

    def test_the_margin_header_orders_by_price_minus_cost(self):
        # Whichever direction the theme's first click uses, the priced rows
        # must come out in margin order — CAB ascending or BAC descending —
        # and the negated parameter must produce the exact reverse.
        link = self._margin_sort_href()
        first = [t for t in self._titles(link) if t != "Manual"]
        self.assertIn(first, (["C", "A", "B"], ["B", "A", "C"]))
        flipped = {
            k: (v[1:] if v.startswith("-") else f"-{v}") if k == "o" else v
            for k, v in link.items()
        }
        second = [t for t in self._titles(flipped) if t != "Manual"]
        self.assertEqual(second, list(reversed(first)))

    def test_a_costless_plan_does_not_break_the_ordering(self):
        titles = self._titles(self._margin_sort_href())
        self.assertEqual(len(titles), 4)
        self.assertIn("Manual", titles)


class PopularDestinationAdminTests(TestCase):
    """Choosing, ordering and dropping the destinations the landing page shows.

    All three happen from the country list, so all three are asserted through
    the changelist rather than against the model: an action that works but is
    not reachable from the page is not a workflow anybody can use.
    """

    def setUp(self):
        from django.contrib.auth.models import User
        from django.urls import reverse

        self.turkey = self._country("Turkey", "TR", popular=True, order=1)
        self.japan = self._country("Japan", "JP", popular=True, order=2)
        # Promoted with nothing to sell — the empty card the site would render.
        self.oman = self._country("Oman", "OM", popular=True, order=3, plans=False)
        self.qatar = self._country("Qatar", "QA")

        User.objects.create_superuser("dest-admin", "d@example.com", "pw-for-tests-only")
        self.client.force_login(User.objects.get(username="dest-admin"))
        self.url = reverse("admin:catalog_country_changelist")

    def _country(self, name, iso2, *, popular=False, order=0, plans=True):
        country = Country.objects.create(
            name=name,
            slug=name.lower(),
            iso2=iso2,
            is_popular=popular,
            sort_order=order,
        )
        if plans:
            Plan.objects.create(
                country=country,
                title=f"{name} 3GB",
                validity_days=15,
                price_usd=Decimal("9.00"),
                price_locked=True,
            )
        return country

    def _act(self, action, *countries, follow=True):
        return self.client.post(
            self.url,
            {
                "action": action,
                "_selected_action": [str(c.pk) for c in countries],
                "index": 0,
            },
            follow=follow,
        )

    def _messages(self, response):
        return [str(message) for message in response.context["messages"]]

    # --- promoting and demoting --------------------------------------------

    def test_promote_puts_a_destination_on_the_landing_page(self):
        self._act("promote", self.qatar)
        self.qatar.refresh_from_db()
        self.assertTrue(self.qatar.is_popular)

    def test_a_newly_promoted_destination_lands_at_the_end_of_the_row(self):
        self._act("promote", self.qatar)
        self.qatar.refresh_from_db()
        # Three were already promoted, so this one is fourth rather than tying
        # with every unpromoted country at 0.
        self.assertEqual(self.qatar.sort_order, 4)

    def test_promoting_does_not_move_one_that_is_already_there(self):
        self._act("promote", self.turkey, self.qatar)
        self.turkey.refresh_from_db()
        self.assertEqual(self.turkey.sort_order, 1, "an existing place must survive")

    def test_promoting_one_that_is_already_there_says_so(self):
        response = self._act("promote", self.turkey)
        # Otherwise a selection that changed nothing looks like a failed action.
        self.assertTrue(
            any("already promoted" in message for message in self._messages(response))
        )

    def test_promote_warns_when_there_is_nothing_to_sell(self):
        laos = self._country("Laos", "LA", plans=False)
        response = self._act("promote", laos)
        self.assertTrue(
            any("Laos" in message for message in self._messages(response)),
            "promoting an empty destination must say so",
        )

    def test_promote_says_nothing_about_destinations_that_have_plans(self):
        response = self._act("promote", self.qatar)
        self.assertFalse(
            any("empty cards" in message for message in self._messages(response))
        )

    def test_demote_takes_it_off_the_landing_page(self):
        self._act("demote", self.japan)
        self.japan.refresh_from_db()
        self.assertFalse(self.japan.is_popular)

    def test_demote_keeps_the_place_it_had(self):
        self._act("demote", self.japan)
        self.japan.refresh_from_db()
        # Promoting it again should put it back where it was, not at the front.
        self.assertEqual(self.japan.sort_order, 2)

    def test_the_actions_clear_the_storefront_cache(self):
        """Regression guard: a queryset.update() would fire no post_save, so the
        site would keep showing the old row for the whole cache TTL."""
        from unittest.mock import patch

        for action, country in (("promote", self.qatar), ("demote", self.japan)):
            with self.subTest(action=action):
                with patch("catalog.signals.invalidate_catalogue") as invalidate:
                    # Invalidation rides transaction.on_commit now; a TestCase
                    # never commits, so the callbacks run here explicitly.
                    with self.captureOnCommitCallbacks(execute=True):
                        self._act(action, country)
                self.assertTrue(invalidate.called)

    # --- reading the list ---------------------------------------------------

    def test_the_changelist_shows_the_promoted_order(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("★ 1", body)
        self.assertIn("★ 3", body)
        self.assertIn("/ 3", body)

    def test_the_order_shown_is_the_order_the_site_uses(self):
        # Japan was promoted second but sits last on the site once its sort
        # order moves, and the number in the list has to follow the site.
        self.japan.sort_order = 9
        self.japan.save()

        response = self.client.get(self.url)
        changelist = response.context["cl"]
        shown = {
            country.name: str(changelist.model_admin.promotion(country))
            for country in changelist.result_list
        }
        self.assertIn("★ 1", shown["Turkey"])
        self.assertIn("★ 2", shown["Oman"])
        self.assertIn("★ 3", shown["Japan"])
        self.assertIn("—", shown["Qatar"])

    def test_promoted_destinations_come_first(self):
        response = self.client.get(self.url)
        names = [country.name for country in response.context["cl"].result_list]
        self.assertEqual(names[:3], ["Turkey", "Japan", "Oman"])
        self.assertEqual(names[3], "Qatar")

    def test_a_promoted_destination_with_no_plans_is_called_out(self):
        response = self.client.get(self.url)
        self.assertContains(response, "empty card on the site")

    def test_an_unpromoted_destination_with_no_plans_is_only_noted(self):
        self.oman.is_popular = False
        self.oman.save()
        response = self.client.get(self.url)
        # Nothing is broken until it is promoted, so the wording is quieter.
        self.assertContains(response, "no active plans")
        self.assertNotContains(response, "empty card on the site")

    def test_the_filter_shows_just_the_promoted_ones(self):
        response = self.client.get(self.url + "?promoted=yes")
        names = {c.name for c in response.context["cl"].result_list}
        self.assertEqual(names, {"Turkey", "Japan", "Oman"})

    def test_the_filter_finds_the_promoted_ones_with_nothing_to_sell(self):
        response = self.client.get(self.url + "?promoted=empty")
        names = [c.name for c in response.context["cl"].result_list]
        self.assertEqual(names, ["Oman"])

    def test_an_inactive_plan_does_not_count_as_something_to_sell(self):
        Plan.objects.filter(country=self.turkey).update(is_active=False)
        response = self.client.get(self.url + "?promoted=empty")
        names = {c.name for c in response.context["cl"].result_list}
        self.assertEqual(names, {"Oman", "Turkey"})

    def test_the_changelist_does_not_query_per_row(self):
        from django.contrib.admin.sites import site
        from django.test import RequestFactory

        for index in range(12):
            self._country(f"Country {index}", "ZZ", popular=index < 6, order=index)

        model_admin = site._registry[Country]
        countries = list(model_admin.get_queryset(RequestFactory().get("/")))
        with self.assertNumQueries(0):
            # Both counts and both positions come from get_queryset, so drawing
            # the rows touches no database.
            for country in countries:
                model_admin.promotion(country)
                model_admin.plan_count(country)
                model_admin.from_price(country)

    # --- reordering from the list ------------------------------------------

    def test_sort_order_can_be_changed_without_leaving_the_list(self):
        response = self.client.get(self.url)
        rows = list(response.context["cl"].result_list)
        data = {
            "form-TOTAL_FORMS": str(len(rows)),
            "form-INITIAL_FORMS": str(len(rows)),
            "_save": "",
        }
        for index, country in enumerate(rows):
            data[f"form-{index}-id"] = str(country.pk)
            data[f"form-{index}-sort_order"] = (
                "7" if country.pk == self.japan.pk else str(country.sort_order)
            )
            if country.is_popular:
                data[f"form-{index}-is_popular"] = "on"
            if country.is_active:
                data[f"form-{index}-is_active"] = "on"

        response = self.client.post(self.url, data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.japan.refresh_from_db()
        self.assertEqual(self.japan.sort_order, 7)

    def test_promotion_can_be_toggled_from_the_same_row(self):
        response = self.client.get(self.url)
        rows = list(response.context["cl"].result_list)
        data = {
            "form-TOTAL_FORMS": str(len(rows)),
            "form-INITIAL_FORMS": str(len(rows)),
            "_save": "",
        }
        for index, country in enumerate(rows):
            data[f"form-{index}-id"] = str(country.pk)
            data[f"form-{index}-sort_order"] = str(country.sort_order)
            # Turkey's box is cleared and Qatar's is ticked in one save.
            promoted = country.pk == self.qatar.pk or (
                country.is_popular and country.pk != self.turkey.pk
            )
            if promoted:
                data[f"form-{index}-is_popular"] = "on"
            if country.is_active:
                data[f"form-{index}-is_active"] = "on"

        self.client.post(self.url, data, follow=True)
        self.turkey.refresh_from_db()
        self.qatar.refresh_from_db()
        self.assertFalse(self.turkey.is_popular)
        self.assertTrue(self.qatar.is_popular)
        # Ticking the box is the obvious way to promote, so it has to follow the
        # same rule as the action: land at the end of the row, not at
        # sort_order 0, which would sort ahead of everything and make the new
        # destination the first card on the landing page.
        self.assertGreater(self.qatar.sort_order, 0)
        self.assertGreaterEqual(
            self.qatar.sort_order,
            max(self.japan.sort_order, self.oman.sort_order),
        )

    def test_a_destination_can_be_found_by_its_translated_name(self):
        """Staff work in Uzbek and search for the name they see on the site."""
        self.turkey.name_uz = "Turkiya"
        self.turkey.name_ru = "Турция"
        self.turkey.save(update_fields=["name_uz", "name_ru"])

        for query in ("Turkiya", "Турция", "Turkey"):
            with self.subTest(query=query):
                response = self.client.get(self.url, {"q": query})
                found = [c.pk for c in response.context["cl"].result_list]
                self.assertIn(self.turkey.pk, found)
                self.assertNotIn(self.japan.pk, found)

    def test_an_inactive_promoted_country_is_not_given_a_position(self):
        """The storefront lists active countries only, so a hidden one has no
        place in the row — and numbering it pushed every country after it one
        position too high."""
        self.japan.is_active = False
        self.japan.save(update_fields=["is_active"])

        response = self.client.get(self.url)
        html = response.content.decode()
        self.assertIn("not shown — inactive", html)
        # Two active promoted countries remain, so the totals must say two.
        self.assertIn("/ 2", html)
        self.assertNotIn("/ 3", html)

    def test_position_survives_a_deactivated_country_in_the_middle(self):
        self.japan.is_active = False
        self.japan.save(update_fields=["is_active"])

        response = self.client.get(self.url)
        rows = {c.pk: c for c in response.context["cl"].result_list}
        # Turkey is first, and Oman moves up to second now that Japan is hidden
        # rather than staying third behind a country nobody can see.
        self.assertEqual(rows[self.turkey.pk]._landing_position, 0)
        self.assertEqual(rows[self.oman.pk]._landing_position, 1)

    def test_the_position_costs_the_same_however_many_rows(self):
        """The position used to be a dict rebuilt on every queryset; as an
        annotation it has to stay a fixed cost rather than one query per row.

        The absolute number is not the point and would be brittle — that it does
        not move when the row count quadruples is."""
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        def count_queries():
            with CaptureQueriesContext(connection) as captured:
                list(self.client.get(self.url).context["cl"].result_list)
            return len(captured)

        with_four = count_queries()
        # iso2 is two characters, so the filler codes are AA, AB, AC ...
        for index in range(12):
            self._country(
                f"Filler {index}",
                f"A{chr(ord('A') + index)}",
                popular=True,
                order=index + 10,
            )
        self.assertEqual(count_queries(), with_four)

    def test_the_position_is_not_kept_on_the_admin_instance(self):
        """A ModelAdmin is built once at registration and shared across threads,
        so per-request state on it races between concurrent requests."""
        from django.contrib import admin as django_admin

        model_admin = django_admin.site._registry[Country]
        self.assertFalse(
            hasattr(model_admin, "_promoted_positions"),
            "landing positions must be an annotation, not instance state",
        )


class RenderedLinkPathTests(TestCase):
    """No rendered admin page may contain a link hardcoded to /admin/.

    The sidebar was not the only offender — the dashboard template linked to
    /admin/orders/order/ directly. Asserting against the current settings would
    prove nothing, because ADMIN_URL_PATH is "admin" in development, so this
    rebuilds the URLconf under a different path: anything still pointing at
    /admin/ then stands out.
    """

    def test_no_page_links_to_the_old_admin_path(self):
        import importlib

        import config.urls
        from django.contrib.auth.models import User
        from django.urls import clear_url_caches

        def rebuild():
            from django.urls import set_urlconf

            importlib.reload(config.urls)
            clear_url_caches()
            # clear_url_caches() empties the resolver cache but not the
            # thread-local the test client leaves behind, so reverse() kept
            # resolving against the renamed URLconf in later tests.
            set_urlconf(None)

        # The override is a context manager rather than a decorator so the
        # restoring rebuild happens with ADMIN_URL_PATH back to its real value.
        # As a decorator, the rebuild in `finally` still saw "secret-panel" and
        # left the URLconf — and reverse() — pointing at it for every test that
        # ran afterwards.
        try:
            with override_settings(
                SECURE_SSL_REDIRECT=False, ADMIN_URL_PATH="secret-panel"
            ):
                rebuild()
                User.objects.create_superuser(
                    "link-admin", "l@example.com", "pw-for-tests-only"
                )
                self.client.force_login(User.objects.get(username="link-admin"))

                pages = ["/secret-panel/"] + [
                    str(item["link"])
                    for group in settings.UNFOLD["SIDEBAR"]["navigation"]
                    for item in group["items"]
                ]
                for page in pages:
                    with self.subTest(page=page):
                        response = self.client.get(page)
                        self.assertEqual(response.status_code, 200)
                        body = response.content.decode()
                        self.assertNotIn(
                            'href="/admin/',
                            body,
                            f"{page} still links to the pre-rename admin path",
                        )
        finally:
            rebuild()
            clear_url_caches()


class CacheInvalidationTests(TestCase):
    """A supplier price change must reach the storefront, not wait out the TTL.

    The catalogue is cached in Redis for five minutes, so without this an
    operator would change a supplier price, reload the site, see the old one,
    and reasonably conclude the feature is broken.
    """

    def setUp(self):
        self.country = Country.objects.create(name="Korea", slug="korea", iso2="KR")
        self.plan = Plan.objects.create(
            country=self.country,
            title="Korea 2GB",
            validity_days=10,
            cost_usd=Decimal("7.00"),
            price_usd=Decimal("9.10"),
        )

    def _cleared_by(self, action):
        from unittest.mock import patch

        # The signal defers to transaction.on_commit so the API cannot re-cache
        # stale rows between the Redis delete and COMMIT; inside a TestCase
        # nothing ever commits, so the callbacks are executed explicitly.
        with patch("catalog.signals.invalidate_catalogue") as invalidate:
            with self.captureOnCommitCallbacks(execute=True):
                action()
            return invalidate.called

    def test_creating_an_offer_clears_the_cache(self):
        def create():
            SupplierOffer.objects.create(
                plan=self.plan, provider="esimcard", package_code="kr", cost_usd=Decimal("6.00")
            )

        self.assertTrue(self._cleared_by(create))

    def test_deleting_an_offer_clears_the_cache(self):
        offer = SupplierOffer.objects.create(
            plan=self.plan, provider="esimcard", package_code="kr", cost_usd=Decimal("6.00")
        )
        self.assertTrue(self._cleared_by(offer.delete))

    def test_taking_an_offer_out_of_the_running_clears_the_cache(self):
        cheap = SupplierOffer.objects.create(
            plan=self.plan, provider="esimcard", package_code="kr", cost_usd=Decimal("6.00")
        )
        SupplierOffer.objects.create(
            plan=self.plan, provider="esimaccess", package_code="KR", cost_usd=Decimal("7.00")
        )

        def take_out():
            cheap.is_available = False
            cheap.save()

        self.assertTrue(self._cleared_by(take_out))


@override_settings(SECURE_SSL_REDIRECT=False)
class BrandAssetTests(TestCase):
    """The admin must actually serve the wordmark, not just declare it.

    UNFOLD takes SITE_LOGO as a callable, so a wrong static path or a renamed
    file fails silently — the page renders without a logo and nothing complains.
    """

    def setUp(self):
        from django.contrib.auth.models import User

        User.objects.create_superuser("brand-admin", "b@example.com", "pw-for-tests-only")

    def test_logo_and_favicon_files_exist(self):
        from django.contrib.staticfiles import finders

        for path in ("admin/qulaysim-logo.svg", "admin/qulaysim-favicon.svg"):
            with self.subTest(path=path):
                self.assertIsNotNone(finders.find(path), f"{path} is not on the static path")

    def test_wordmark_inherits_the_surrounding_text_colour(self):
        from django.contrib.staticfiles import finders

        svg = open(finders.find("admin/qulaysim-logo.svg")).read()
        # "Qulay" is white in the source artwork, which is invisible on a light
        # admin. currentColor is what makes one file work in both themes.
        self.assertIn("currentColor", svg)
        self.assertIn("#249279", svg)
        self.assertNotIn('fill="#ffffff"', svg.lower())

    def test_admin_pages_reference_the_wordmark(self):
        from django.urls import reverse

        self.client.force_login(
            __import__("django.contrib.auth", fromlist=["models"]).models.User.objects.get(
                username="brand-admin"
            )
        )
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("qulaysim-logo.svg", body)
        self.assertIn("qulaysim-favicon.svg", body)


class PriceCeilingTests(TestCase):
    """A price field an operator can type any figure into.

    The column was max_digits=8, so anything from 1,000,000.00 up was rejected
    with a validation error that read like a bug rather than a policy.
    """

    def setUp(self):
        PricingRule.objects.create(scope=PricingRule.Scope.GLOBAL, markup_percent=Decimal("20"))

    def test_a_price_above_the_old_ceiling_is_accepted(self):
        plan = Plan.objects.create(
            title="Enterprise bulk",
            validity_days=30,
            price_usd=Decimal("2500000.00"),
            price_locked=True,
        )
        plan.refresh_from_db()
        self.assertEqual(plan.price_usd, Decimal("2500000.00"))

    def test_a_cost_above_the_old_ceiling_survives_the_pricing_engine(self):
        plan = Plan.objects.create(
            title="Enterprise bulk",
            validity_days=30,
            cost_usd=Decimal("1500000.00"),
            price_usd=Decimal("0"),
        )
        plan.refresh_from_db()
        # 1,500,000 + 20% — the calculated price must also clear the old limit,
        # or raising it on cost alone would just move the failure.
        self.assertEqual(plan.price_usd, Decimal("1800000.00"))

    def test_a_supplier_offer_above_the_old_ceiling_is_accepted(self):
        plan = Plan.objects.create(title="Bulk", validity_days=30, price_usd=Decimal("1"))
        SupplierOffer.objects.create(
            plan=plan, provider="esimaccess", package_code="BULK", cost_usd=Decimal("1200000.00")
        )
        plan.refresh_from_db()
        self.assertEqual(plan.cost_usd, Decimal("1200000.00"))

    def test_cents_are_still_kept(self):
        plan = Plan.objects.create(
            title="Precise", validity_days=7, price_usd=Decimal("1234567.89"), price_locked=True
        )
        plan.refresh_from_db()
        # Widening the integer part must not have cost the decimal part.
        self.assertEqual(plan.price_usd, Decimal("1234567.89"))


class PriceNoteTests(TestCase):
    """Text beside the price for what an amount cannot express."""

    def setUp(self):
        self.country = Country.objects.create(name="Oman", slug="oman", iso2="OM")
        self.plan = Plan.objects.create(
            country=self.country,
            title="Oman 5GB",
            validity_days=30,
            price_usd=Decimal("500.00"),
            price_locked=True,
        )

    def test_note_is_optional(self):
        self.assertEqual(self.plan.price_note, "")

    def test_note_is_stored_verbatim(self):
        self.plan.price_note = "+ 200$ depozit, qaytariladi"
        self.plan.save()
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.price_note, "+ 200$ depozit, qaytariladi")

    def test_note_does_not_disturb_the_price_or_the_margin(self):
        PricingRule.objects.create(scope=PricingRule.Scope.GLOBAL, markup_percent=Decimal("25"))
        plan = Plan.objects.create(
            country=self.country,
            title="Oman 3GB",
            validity_days=30,
            cost_usd=Decimal("400.00"),
            price_usd=Decimal("0"),
            price_note="+ deposit",
        )
        plan.refresh_from_db()
        # The whole point of keeping the note in its own column: arithmetic
        # never sees it.
        self.assertEqual(plan.price_usd, Decimal("500.00"))
        self.assertEqual(plan.margin_usd, Decimal("100.00"))

    @override_settings(SECURE_SSL_REDIRECT=False)
    def test_admin_shows_the_note_next_to_the_price(self):
        from django.contrib.auth.models import User
        from django.urls import reverse

        self.plan.price_note = "+ deposit"
        self.plan.save()
        User.objects.create_superuser("note-admin", "n@example.com", "pw-for-tests-only")
        self.client.force_login(User.objects.get(username="note-admin"))

        response = self.client.get(reverse("admin:catalog_plan_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "+ deposit")

        response = self.client.get(reverse("admin:catalog_plan_change", args=[self.plan.pk]))
        self.assertContains(response, "price_note")


@override_settings(SECURE_SSL_REDIRECT=False)
class LoginRedirectTests(TestCase):
    """Signing in must land on the admin, never on a 404.

    Django defaults LOGIN_REDIRECT_URL to /accounts/profile/, which this project
    does not serve. The usual route carries ?next=, which masked it — so this
    asserts the case without one.
    """

    def setUp(self):
        from django.contrib.auth.models import User

        User.objects.create_user(
            "redirect-admin", "r@example.com", "pw-for-tests-only", is_staff=True, is_superuser=True
        )

    def test_login_without_next_lands_on_the_admin_index(self):
        from django.urls import reverse

        response = self.client.post(
            reverse("admin:login"),
            {"username": "redirect-admin", "password": "pw-for-tests-only"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("admin:index"))

    def test_the_landing_page_actually_exists(self):
        from django.urls import reverse

        self.client.force_login(
            __import__("django.contrib.auth", fromlist=["models"]).models.User.objects.get(
                username="redirect-admin"
            )
        )
        # Asserting the redirect target alone would still pass if that target
        # were itself broken.
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)

    def test_login_still_honours_next(self):
        from django.urls import reverse

        target = reverse("admin:catalog_plan_changelist")
        response = self.client.post(
            reverse("admin:login") + f"?next={target}",
            {"username": "redirect-admin", "password": "pw-for-tests-only", "next": target},
        )
        self.assertEqual(response["Location"], target)


@override_settings(SECURE_SSL_REDIRECT=False)
@override_settings(LANGUAGE_CODE="en")
class SupplierImportPageTests(TestCase):
    """Uploading a wholesaler price list instead of building eleven objects.

    The page has to be safe in two ways: behind admin auth like every other
    admin page, and a preview must write nothing at all — an operator who is
    checking what would happen has not agreed to it happening.
    """

    CSV = (
        "package_code,location,data_gb,days,cost_usd\n"
        "TR_3_15,TR,3,15,1.42\n"
        "TR_5_30,TR,5,30,2.30\n"
        "TR_1_7,TR,1,7,0.60\n"
        "XX_9_99,XX,9,99,5.00\n"       # a destination we do not sell
        "TR_777,TR,7,77,9.99\n"        # a shape with no rung in the ladder
    )

    def setUp(self):
        from django.contrib.auth.models import User
        from django.urls import reverse

        PricingRule.objects.create(scope=PricingRule.Scope.GLOBAL, markup_percent=Decimal("30"))
        self.turkey = Country.objects.create(name="Turkey", slug="turkey", iso2="TR")
        self.url = reverse("admin:catalog_plan_import_prices")
        User.objects.create_superuser("import-admin", "i@example.com", "pw-for-tests-only")
        self.staff = User.objects.create_user(
            "no-perms", "n@example.com", "pw-for-tests-only", is_staff=True
        )

    def _upload(self, *, dry_run, csv=None, user="import-admin"):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.contrib.auth.models import User

        self.client.force_login(User.objects.get(username=user))
        data = {
            "provider": "esimaccess",
            "csv_file": SimpleUploadedFile(
                "prices.csv", (csv or self.CSV).encode(), content_type="text/csv"
            ),
        }
        if dry_run:
            data["dry_run"] = "on"
        return self.client.post(self.url, data)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_staff_without_change_permission_cannot_import(self):
        response = self._upload(dry_run=False, user="no-perms")
        self.assertEqual(response.status_code, 302)
        # The whole point of the page is writing to the catalogue, so a user who
        # may not change plans must not reach it.
        self.assertEqual(Plan.objects.count(), 0)

    def test_preview_writes_nothing(self):
        response = self._upload(dry_run=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "nothing has been written")
        self.assertEqual(Plan.objects.count(), 0)
        self.assertEqual(SupplierOffer.objects.count(), 0)

    def test_preview_reports_what_would_be_created(self):
        response = self._upload(dry_run=True)
        body = response.content.decode()
        # Three rows match a rung of the ladder; the other two do not.
        self.assertIn("Turkey 3 GB · 15 days", body)
        self.assertIn("Turkey 1 GB · 7 days", body)
        self.assertNotIn("7 GB · 77 days", body)

    def test_applying_creates_the_plans_and_offers(self):
        self._upload(dry_run=False)
        self.assertEqual(Plan.objects.filter(country=self.turkey).count(), 3)
        self.assertEqual(SupplierOffer.objects.count(), 3)
        plan = Plan.objects.get(data_amount_mb=3072, validity_days=15)
        self.assertEqual(plan.cost_usd, Decimal("1.42"))
        # 1.42 + 30% — the retail price follows from the cost, not the file.
        self.assertEqual(plan.price_usd, Decimal("1.85"))

    def test_the_middle_rung_is_the_one_promoted(self):
        self._upload(dry_run=False)
        popular = Plan.objects.filter(is_popular=True)
        self.assertEqual([p.data_amount_mb for p in popular], [3072])

    def test_unknown_destinations_are_ignored_not_invented(self):
        self._upload(dry_run=False)
        self.assertFalse(Country.objects.filter(iso2="XX").exists())

    def test_reapplying_updates_rather_than_duplicates(self):
        self._upload(dry_run=False)
        cheaper = self.CSV.replace("TR_3_15,TR,3,15,1.42", "TR_3_15,TR,3,15,0.99")
        self._upload(dry_run=False, csv=cheaper)
        self.assertEqual(SupplierOffer.objects.filter(provider="esimaccess").count(), 3)
        plan = Plan.objects.get(data_amount_mb=3072, validity_days=15)
        plan.refresh_from_db()
        self.assertEqual(plan.cost_usd, Decimal("0.99"))

    def test_limiting_to_a_destination_leaves_others_alone(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.contrib.auth.models import User

        japan = Country.objects.create(name="Japan", slug="japan", iso2="JP")
        csv = self.CSV + "JP_3_15,JP,3,15,1.70\n"
        self.client.force_login(User.objects.get(username="import-admin"))
        self.client.post(
            self.url,
            {
                "provider": "esimaccess",
                "csv_file": SimpleUploadedFile("p.csv", csv.encode(), content_type="text/csv"),
                "only_countries": [str(self.turkey.pk)],
            },
        )
        self.assertEqual(Plan.objects.filter(country=japan).count(), 0)
        self.assertEqual(Plan.objects.filter(country=self.turkey).count(), 3)

    def test_a_file_missing_columns_is_refused_with_a_reason(self):
        response = self._upload(dry_run=True, csv="foo,bar\n1,2\n")
        self.assertEqual(Plan.objects.count(), 0)
        self.assertContains(response, "missing the columns")

    def test_a_non_csv_upload_is_refused(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.contrib.auth.models import User

        self.client.force_login(User.objects.get(username="import-admin"))
        response = self.client.post(
            self.url,
            {
                "provider": "esimaccess",
                "csv_file": SimpleUploadedFile("p.xlsx", b"\x50\x4b\x03\x04\xff\xfe", content_type="application/octet-stream"),
                "dry_run": "on",
            },
        )
        self.assertEqual(Plan.objects.count(), 0)
        self.assertContains(response, "Export it as CSV")


class FulfillabilityTests(TestCase):
    """Sourcing must never route to a supplier the API cannot order from.

    The backend registers its supplier integrations (only eSIM Access today);
    a plan priced from anyone else is either sold at a loss — the paid order
    falls back to the dearer connected route — or, with no connected offer at
    all, paid for and never provisioned. Offers from unconnected suppliers
    stay on file for comparison, and FULFILLABLE_PROVIDERS is the switch that
    brings one into the running when its integration lands.
    """

    def setUp(self):
        PricingRule.objects.create(scope=PricingRule.Scope.GLOBAL, markup_percent=Decimal("50"))
        self.country = Country.objects.create(name="Turkey", slug="turkey", iso2="TR")
        self.plan = Plan.objects.create(
            country=self.country,
            title="Turkey 5GB",
            data_amount_mb=5120,
            validity_days=30,
            cost_usd=Decimal("10.00"),
            price_usd=Decimal("15.00"),
        )

    def _offer(self, provider, cost, available=True):
        return SupplierOffer.objects.create(
            plan=self.plan,
            provider=provider,
            package_code=f"{provider.upper()}-TR-5-30",
            cost_usd=Decimal(cost),
            is_available=available,
        )

    def test_a_cheaper_unconnected_offer_does_not_take_over(self):
        self._offer("esimaccess", "4.20")
        self._offer("esimcard", "3.80")

        self.plan.refresh_from_db()
        # eSIMCard is cheaper, but fulfilment cannot buy there: pricing from it
        # would sell at $5.70 an eSIM that really costs $4.20 to deliver.
        self.assertEqual(self.plan.provider, "esimaccess")
        self.assertEqual(self.plan.cost_usd, Decimal("4.20"))
        self.assertEqual(self.plan.provider_package_code, "ESIMACCESS-TR-5-30")
        self.assertEqual(self.plan.price_usd, Decimal("6.30"))

    def test_only_unconnected_offers_leave_the_plan_untouched(self):
        self._offer("esimcard", "3.80")

        self.plan.refresh_from_db()
        # Nothing usable to source from, so the hand-entered route survives —
        # the same rule as a supplier outage — and the plan is flagged instead.
        self.assertEqual(self.plan.cost_usd, Decimal("10.00"))
        self.assertEqual(self.plan.provider, "mock")
        self.assertTrue(self.plan.unfulfillable_only)

    def test_a_plan_without_offers_is_not_flagged(self):
        self.assertFalse(self.plan.unfulfillable_only)

    def test_a_plan_with_a_connected_offer_is_not_flagged(self):
        self._offer("esimaccess", "4.20")
        self._offer("esimcard", "3.80")
        self.plan.refresh_from_db()
        self.assertFalse(self.plan.unfulfillable_only)

    def test_connecting_the_supplier_puts_it_back_in_the_running(self):
        """The documented re-enable path: when eSIMCard's API is wired up,
        adding it to FULFILLABLE_PROVIDERS lets its offers win again."""
        self._offer("esimaccess", "4.20")
        self._offer("esimcard", "3.80")

        with override_settings(FULFILLABLE_PROVIDERS=["esimaccess", "esimcard"]):
            self.plan.save()

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.provider, "esimcard")
        self.assertEqual(self.plan.cost_usd, Decimal("3.80"))
        self.assertEqual(self.plan.price_usd, Decimal("5.70"))


@override_settings(SECURE_SSL_REDIRECT=False)
@override_settings(LANGUAGE_CODE="en")
class FulfilmentBadgeTests(TestCase):
    """Stranded plans must be visible on the list, not only in API error logs."""

    def setUp(self):
        from django.contrib.auth.models import User

        self.country = Country.objects.create(name="Turkey", slug="turkey", iso2="TR")
        self.stranded = Plan.objects.create(
            country=self.country,
            title="Turkey 5GB",
            data_amount_mb=5120,
            validity_days=30,
            price_usd=Decimal("15.00"),
            price_locked=True,
        )
        SupplierOffer.objects.create(
            plan=self.stranded,
            provider="esimcard",
            package_code="tr-5gb",
            cost_usd=Decimal("3.80"),
        )
        self.healthy = Plan.objects.create(
            country=self.country,
            title="Turkey 1GB",
            data_amount_mb=1024,
            validity_days=7,
            price_usd=Decimal("5.00"),
            price_locked=True,
        )
        SupplierOffer.objects.create(
            plan=self.healthy,
            provider="esimaccess",
            package_code="TR_1_7",
            cost_usd=Decimal("2.00"),
        )
        User.objects.create_superuser("badge-admin", "b@example.com", "pw-for-tests-only")
        self.client.force_login(User.objects.get(username="badge-admin"))

    def test_the_changelist_badges_the_stranded_plan(self):
        from django.urls import reverse

        response = self.client.get(reverse("admin:catalog_plan_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no connected supplier")

    def test_a_plan_with_a_connected_route_carries_no_badge(self):
        from django.contrib.admin.sites import site

        model_admin = site._registry[Plan]
        self.assertEqual(model_admin.fulfilment_warning(self.healthy), "")
        self.assertIn("no connected supplier", str(model_admin.fulfilment_warning(self.stranded)))


@override_settings(SECURE_SSL_REDIRECT=False)
@override_settings(LANGUAGE_CODE="en")
# Both suppliers connected: the handover this pins is the generic one — the
# bulk action must behave exactly like deleting the offers one at a time.
@override_settings(FULFILLABLE_PROVIDERS=["esimaccess", "esimcard"])
class SupplierOfferBulkDeleteTests(TestCase):
    """The changelist bulk delete skips SupplierOffer.delete(), so the admin
    override has to re-decide sourcing — without it, plans kept routing to a
    supplier whose offer had just been deliberately removed."""

    def setUp(self):
        from django.contrib.auth.models import User
        from django.urls import reverse

        PricingRule.objects.create(scope=PricingRule.Scope.GLOBAL, markup_percent=Decimal("50"))
        self.country = Country.objects.create(name="Turkey", slug="turkey", iso2="TR")
        self.plan = Plan.objects.create(
            country=self.country,
            title="Turkey 5GB",
            data_amount_mb=5120,
            validity_days=30,
            cost_usd=Decimal("10.00"),
            price_usd=Decimal("15.00"),
        )
        self.cheap = SupplierOffer.objects.create(
            plan=self.plan, provider="esimcard", package_code="tr-5gb", cost_usd=Decimal("3.80")
        )
        self.dear = SupplierOffer.objects.create(
            plan=self.plan, provider="esimaccess", package_code="TR_5_30", cost_usd=Decimal("4.20")
        )
        User.objects.create_superuser("offer-admin", "o@example.com", "pw-for-tests-only")
        self.client.force_login(User.objects.get(username="offer-admin"))
        self.url = reverse("admin:catalog_supplieroffer_changelist")

    def _bulk_delete(self, *offers):
        return self.client.post(
            self.url,
            {
                "action": "delete_selected",
                "_selected_action": [str(offer.pk) for offer in offers],
                "post": "yes",  # the confirmation page's consent
            },
            follow=True,
        )

    def test_bulk_deleting_the_winner_hands_over_to_the_runner_up(self):
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.provider, "esimcard")

        self._bulk_delete(self.cheap)

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.provider, "esimaccess")
        self.assertEqual(self.plan.cost_usd, Decimal("4.20"))
        self.assertEqual(self.plan.provider_package_code, "TR_5_30")
        # The price follows the new cost — 4.20 + 50%.
        self.assertEqual(self.plan.price_usd, Decimal("6.30"))

    def test_bulk_deleting_every_offer_keeps_the_last_route(self):
        self._bulk_delete(self.cheap, self.dear)

        self.plan.refresh_from_db()
        # Same rule as losing every offer one by one: an empty comparison is
        # an outage, not a reason to zero the cost.
        self.assertEqual(self.plan.cost_usd, Decimal("3.80"))
        self.assertEqual(SupplierOffer.objects.count(), 0)


class SetupDestinationsCommandTests(TestCase):
    """--apply demotes through a queryset.update(), which fires no signal, and
    every signal its saves do fire runs inside the transaction — so the command
    has to clear the storefront cache itself, after commit."""

    CSV = "package_code,location,data_gb,days,cost_usd\nTR_1_7,TR,1,7,2.50\n"

    def _run_apply(self):
        import tempfile
        from io import StringIO
        from pathlib import Path as _Path

        from django.core.management import call_command

        with tempfile.TemporaryDirectory() as folder:
            path = _Path(folder) / "prices.csv"
            path.write_text(self.CSV)
            call_command("setup_destinations", esimaccess=path, apply=True, stdout=StringIO())

    def test_apply_clears_the_storefront_cache(self):
        from unittest.mock import patch

        for slug, name in (("europe", "Europe"), ("asia", "Asia"), ("middle-east", "Middle East")):
            Region.objects.create(name=name, slug=slug)
        stale = Country.objects.create(
            name="Oldland", slug="oldland", iso2="OL", is_popular=True, sort_order=1
        )

        with patch("config.cache.invalidate_catalogue") as invalidate:
            self._run_apply()

        stale.refresh_from_db()
        self.assertFalse(stale.is_popular, "destinations off the list must be demoted")
        self.assertTrue(invalidate.called, "the demotion must reach the storefront cache")
