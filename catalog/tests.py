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
            # Prefetched, so rendering the sourcing column touches no database.
            for plan in plans:
                model_admin.sourcing_col(plan)


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

        with override_settings(ADMIN_URL_PATH="secret-panel"):
            importlib.reload(config.urls)
            clear_url_caches()
            try:
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
                # Leave the URLconf as the rest of the suite expects it.
                importlib.reload(config.urls)
                clear_url_caches()

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


class RenderedLinkPathTests(TestCase):
    """No rendered admin page may contain a link hardcoded to /admin/.

    The sidebar was not the only offender — the dashboard template linked to
    /admin/orders/order/ directly. Asserting against the current settings would
    prove nothing, because ADMIN_URL_PATH is "admin" in development, so this
    rebuilds the URLconf under a different path: anything still pointing at
    /admin/ then stands out.
    """

    @override_settings(SECURE_SSL_REDIRECT=False, ADMIN_URL_PATH="secret-panel")
    def test_no_page_links_to_the_old_admin_path(self):
        import importlib

        import config.urls
        from django.contrib.auth.models import User
        from django.urls import clear_url_caches

        importlib.reload(config.urls)
        clear_url_caches()
        try:
            User.objects.create_superuser("link-admin", "l@example.com", "pw-for-tests-only")
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
            importlib.reload(config.urls)
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

        with patch("catalog.signals.invalidate_catalogue") as invalidate:
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
