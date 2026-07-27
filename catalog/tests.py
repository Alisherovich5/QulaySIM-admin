"""Pricing engine tests.

This is money logic, so every branch of the cascade is covered: which rule
wins, the margin floor, and each rounding mode.
"""

from decimal import Decimal

from django.conf import settings
from django.test import TestCase, override_settings

from catalog.models import Country, Plan, PricingRule, Region
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
