"""Pricing one traffic size across every destination at once.

A single percentage cannot price a catalogue whose supplier costs run from $0.46
to $222. At +50% the 1 GB tariffs earn 23 cents — which a card fee eats — while
the 50 GB ones earn $32. Fixing that one tariff at a time is 1400 edits across
208 destinations.

So a rule can now name a traffic size instead of a destination, and the precedence
has to be the narrowest statement wins — otherwise an operator's deliberate
exception is silently overruled by a broader rule they set weeks earlier.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from catalog.models import Country, Plan, PricingRule
from catalog.pricing import resolve_rule


class TierRuleTests(TestCase):
    def setUp(self):
        self.house = PricingRule.objects.create(
            scope=PricingRule.Scope.GLOBAL, markup_percent=Decimal("50")
        )
        self.turkey = Country.objects.create(name="Turkey", slug="turkey", iso2="TR")
        self.japan = Country.objects.create(name="Japan", slug="japan", iso2="JP")

    def _plan(self, country, mb, days, cost="1.00"):
        return Plan.objects.create(
            country=country,
            title=f"{country.name} {mb}",
            data_amount_mb=mb,
            validity_days=days,
            cost_usd=Decimal(cost),
            price_usd=Decimal("0"),
            provider="esimaccess",
            provider_package_code=f"{country.iso2}-{mb}-{days}",
        )

    def test_a_tier_rule_reprices_that_size_in_every_destination(self):
        tr = self._plan(self.turkey, 1024, 7, "0.46")
        jp = self._plan(self.japan, 1024, 7, "0.60")
        untouched = self._plan(self.turkey, 5120, 30, "2.30")

        # "1 GB earns 200%, everywhere."
        PricingRule.objects.create(
            scope=PricingRule.Scope.TIER,
            tier_data_mb=1024,
            tier_days=7,
            markup_percent=Decimal("200"),
        )
        for plan in (tr, jp, untouched):
            plan.refresh_from_db()

        self.assertEqual(tr.price_usd, Decimal("1.38"))
        self.assertEqual(jp.price_usd, Decimal("1.80"))
        # A different size keeps the house markup.
        self.assertEqual(untouched.price_usd, Decimal("3.45"))

    def test_a_size_rule_without_a_duration_covers_every_duration_of_it(self):
        week = self._plan(self.turkey, 3072, 7, "1.00")
        fortnight = self._plan(self.turkey, 3072, 15, "1.00")
        other_size = self._plan(self.turkey, 5120, 30, "1.00")

        PricingRule.objects.create(
            scope=PricingRule.Scope.TIER, tier_data_mb=3072, markup_percent=Decimal("100")
        )
        for plan in (week, fortnight, other_size):
            plan.refresh_from_db()
        self.assertEqual(week.price_usd, Decimal("2.00"))
        self.assertEqual(fortnight.price_usd, Decimal("2.00"))
        self.assertEqual(other_size.price_usd, Decimal("1.50"))

    def test_an_individual_price_still_wins_over_a_tier_rule(self):
        # This is the other half of what was asked for: one tariff editable on its
        # own, without the tier rule undoing it.
        plan = self._plan(self.turkey, 1024, 7, "0.46")
        plan.price_usd = Decimal("9.99")
        plan.price_locked = True
        plan.save()

        PricingRule.objects.create(
            scope=PricingRule.Scope.TIER, tier_data_mb=1024, markup_percent=Decimal("300")
        )
        plan.refresh_from_db()
        self.assertEqual(plan.price_usd, Decimal("9.99"))


class PrecedenceTests(TestCase):
    """Which of four possible rules governs one tariff."""

    def setUp(self):
        self.house = PricingRule.objects.create(
            scope=PricingRule.Scope.GLOBAL, markup_percent=Decimal("50")
        )
        self.turkey = Country.objects.create(name="Turkey", slug="turkey", iso2="TR")
        self.plan = Plan.objects.create(
            country=self.turkey,
            title="Turkey 1 GB",
            data_amount_mb=1024,
            validity_days=7,
            cost_usd=Decimal("1.00"),
            price_usd=Decimal("0"),
            provider="esimaccess",
            provider_package_code="TR-1024-7",
        )

    def _rules(self):
        return list(PricingRule.objects.filter(is_active=True))

    def test_the_house_rule_when_nothing_else_names_it(self):
        self.assertEqual(resolve_rule(self.plan, self._rules()), self.house)

    def test_a_provider_rule_beats_the_house(self):
        rule = PricingRule.objects.create(
            scope=PricingRule.Scope.PROVIDER, provider="esimaccess",
            markup_percent=Decimal("60"),
        )
        self.assertEqual(resolve_rule(self.plan, self._rules()), rule)

    def test_a_destination_rule_beats_a_provider_rule(self):
        PricingRule.objects.create(
            scope=PricingRule.Scope.PROVIDER, provider="esimaccess",
            markup_percent=Decimal("60"),
        )
        rule = PricingRule.objects.create(
            scope=PricingRule.Scope.COUNTRY, country=self.turkey, markup_percent=Decimal("70")
        )
        self.assertEqual(resolve_rule(self.plan, self._rules()), rule)

    def test_a_size_rule_beats_a_destination_rule(self):
        # Deliberate: a tier rule exists to fix a structural margin problem, and a
        # destination rule set weeks earlier should not quietly defeat it.
        PricingRule.objects.create(
            scope=PricingRule.Scope.COUNTRY, country=self.turkey, markup_percent=Decimal("70")
        )
        rule = PricingRule.objects.create(
            scope=PricingRule.Scope.TIER, tier_data_mb=1024, markup_percent=Decimal("200")
        )
        self.assertEqual(resolve_rule(self.plan, self._rules()), rule)

    def test_a_destination_and_size_together_beat_everything(self):
        PricingRule.objects.create(
            scope=PricingRule.Scope.TIER, tier_data_mb=1024, markup_percent=Decimal("200")
        )
        PricingRule.objects.create(
            scope=PricingRule.Scope.COUNTRY, country=self.turkey, markup_percent=Decimal("70")
        )
        rule = PricingRule.objects.create(
            scope=PricingRule.Scope.COUNTRY,
            country=self.turkey,
            tier_data_mb=1024,
            tier_days=7,
            markup_percent=Decimal("400"),
        )
        self.assertEqual(resolve_rule(self.plan, self._rules()), rule)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.price_usd, Decimal("5.00"))

    def test_a_size_rule_for_a_different_size_does_not_apply(self):
        PricingRule.objects.create(
            scope=PricingRule.Scope.TIER, tier_data_mb=5120, markup_percent=Decimal("200")
        )
        self.assertEqual(resolve_rule(self.plan, self._rules()), self.house)

    def test_a_size_rule_with_the_wrong_duration_does_not_apply(self):
        PricingRule.objects.create(
            scope=PricingRule.Scope.TIER, tier_data_mb=1024, tier_days=30,
            markup_percent=Decimal("200"),
        )
        self.assertEqual(resolve_rule(self.plan, self._rules()), self.house)


class TierRuleValidationTests(TestCase):
    def test_a_size_rule_must_name_a_size(self):
        rule = PricingRule(scope=PricingRule.Scope.TIER, markup_percent=Decimal("100"))
        with self.assertRaises(ValidationError) as caught:
            rule.full_clean()
        self.assertIn("tier_data_mb", caught.exception.message_dict)

    def test_the_house_rule_cannot_secretly_be_about_one_size(self):
        # Left on, it would govern only 1 GB while calling itself the default.
        rule = PricingRule(
            scope=PricingRule.Scope.GLOBAL, tier_data_mb=1024, markup_percent=Decimal("50")
        )
        rule.clean()
        self.assertIsNone(rule.tier_data_mb)

    def test_a_size_rule_drops_a_stray_country(self):
        country = Country.objects.create(name="Turkey", slug="turkey", iso2="TR")
        rule = PricingRule(
            scope=PricingRule.Scope.TIER,
            tier_data_mb=1024,
            country=country,
            markup_percent=Decimal("100"),
        )
        rule.clean()
        self.assertIsNone(rule.country)

    def test_the_label_reads_like_a_tariff(self):
        rule = PricingRule(
            scope=PricingRule.Scope.TIER, tier_data_mb=3072, tier_days=15,
            markup_percent=Decimal("80"),
        )
        self.assertEqual(rule.tier_label, "3 GB · 15 days")
        self.assertIn("3 GB · 15 days", str(rule))

    def test_a_sub_gigabyte_size_is_labelled_in_megabytes(self):
        rule = PricingRule(scope=PricingRule.Scope.TIER, tier_data_mb=512,
                           markup_percent=Decimal("80"))
        self.assertEqual(rule.tier_label, "512 MB")

    def test_retargeting_a_rule_reprices_the_size_it_left_behind(self):
        country = Country.objects.create(name="Turkey", slug="turkey", iso2="TR")
        PricingRule.objects.create(scope=PricingRule.Scope.GLOBAL, markup_percent=Decimal("50"))
        one_gb = Plan.objects.create(
            country=country, title="1 GB", data_amount_mb=1024, validity_days=7,
            cost_usd=Decimal("1.00"), price_usd=Decimal("0"),
            provider="esimaccess", provider_package_code="A",
        )
        rule = PricingRule.objects.create(
            scope=PricingRule.Scope.TIER, tier_data_mb=1024, markup_percent=Decimal("200")
        )
        one_gb.refresh_from_db()
        self.assertEqual(one_gb.price_usd, Decimal("3.00"))

        # Point it at another size: the 1 GB tariff must fall back to the house
        # rule rather than keep a price no rule explains any more.
        rule.tier_data_mb = 5120
        rule.save()
        one_gb.refresh_from_db()
        self.assertEqual(one_gb.price_usd, Decimal("1.50"))
