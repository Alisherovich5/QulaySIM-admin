"""Multi-country eSIMs: which region they belong to, and what we refuse to sell.

Both wholesalers list bundles covering several countries — eSIM Access alone has
301 of them, up to 128 countries in one package. They were read and thrown away,
which left the traveller doing Vienna, Prague and Budapest buying three separate
eSIMs.

The risk in selling them is misdescription. A package covering Poland and
Czechia is filed under Europe by any honest rule, and sold as "Yevropa 5 GB" it
is a complaint waiting to happen: the customer buys a continent and lands in the
third country with no data.
"""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from catalog import geo, supplier_api, supplier_import
from catalog.models import Plan, PricingRule, Region

EUROPE_41 = (
    "AX AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI "
    "ES SE GB NO CH IS TR LI MC SM VA AD ME RS MK"
).split()
SOUTH_AMERICA = "AR BO BR CL CO EC PE UY PY VE GY SR".split()
GLOBAL_MIX = "US GB JP BR ZA AU IN FR MX KE TH CA SG AE".split()
ASIA_EIGHT = "JP KR CN TH VN SG PH MY".split()


class RegionFromCoverageTests(TestCase):
    """Which region a bundle belongs to, decided from what it covers."""

    def test_a_european_bundle_is_european_despite_its_outliers(self):
        # eSIM Access's "Europe 41 countries" includes Turkey. Demanding purity
        # would file it as global, where nobody shopping for Europe looks.
        self.assertEqual(geo.region_for_coverage(EUROPE_41), geo.EUROPE)

    def test_south_america_is_latin_america(self):
        self.assertEqual(geo.region_for_coverage(SOUTH_AMERICA), geo.LATIN_AMERICA)

    def test_a_bundle_spanning_continents_is_global(self):
        self.assertEqual(geo.region_for_coverage(GLOBAL_MIX), geo.GLOBAL)

    def test_asia(self):
        self.assertEqual(geo.region_for_coverage(ASIA_EIGHT), geo.ASIA)

    def test_empty_coverage_falls_back_to_global_rather_than_crashing(self):
        self.assertEqual(geo.region_for_coverage([]), geo.GLOBAL)

    def test_every_region_in_the_map_can_be_created(self):
        # A slug with no name entry would make the sync crash halfway through
        # creating regions, leaving the catalogue half-built.
        for slug in set(geo.REGION_BY_ISO2.values()) | {geo.GLOBAL}:
            self.assertIn(slug, geo.REGION_NAMES, slug)


class TooNarrowToBeRegionalTests(TestCase):
    def test_a_two_country_bundle_is_not_a_region(self):
        catalogue = supplier_api.FetchedCatalogue()
        supplier_api._add_regional(catalogue, ["PL", "CZ"], 5.0, 30, "code-1", Decimal("3.20"))
        # Not recorded, and counted — so the sync reports it instead of the
        # package vanishing without trace.
        self.assertEqual(catalogue.regional, {})
        self.assertEqual(catalogue.too_narrow, 1)

    def test_the_floor_is_where_it_says_it_is(self):
        catalogue = supplier_api.FetchedCatalogue()
        codes = (EUROPE_41 * 2)[: supplier_api.MIN_REGIONAL_COVERAGE]
        supplier_api._add_regional(catalogue, codes, 5.0, 30, "wide", Decimal("9.00"))
        self.assertEqual(len(catalogue.regional), 1)


class CoverageBeatsPriceTests(TestCase):
    """The opposite rule from a single-country tariff, and deliberately so."""

    def test_the_wider_bundle_wins_even_when_it_costs_more(self):
        catalogue = supplier_api.FetchedCatalogue()
        supplier_api._add_regional(
            catalogue, EUROPE_41[:12], 5.0, 30, "narrow-cheap", Decimal("8.00")
        )
        supplier_api._add_regional(catalogue, EUROPE_41, 5.0, 30, "wide-dear", Decimal("11.00"))

        (code, cost, coverage) = next(iter(catalogue.regional.values()))
        # Saving $3 by dropping 29 countries is not a saving. For a local tariff
        # the product is fixed and only the cost varies; for a regional one the
        # coverage IS the product.
        self.assertEqual(code, "wide-dear")
        self.assertEqual(coverage, len(EUROPE_41))
        self.assertEqual(cost, Decimal("11.00"))

    def test_at_equal_coverage_the_cheaper_one_wins(self):
        catalogue = supplier_api.FetchedCatalogue()
        supplier_api._add_regional(catalogue, EUROPE_41, 5.0, 30, "dear", Decimal("12.00"))
        supplier_api._add_regional(catalogue, EUROPE_41, 5.0, 30, "cheap", Decimal("9.50"))
        self.assertEqual(next(iter(catalogue.regional.values()))[0], "cheap")

    def test_different_shapes_do_not_compete(self):
        catalogue = supplier_api.FetchedCatalogue()
        supplier_api._add_regional(catalogue, EUROPE_41, 5.0, 30, "five", Decimal("11.00"))
        supplier_api._add_regional(catalogue, EUROPE_41, 10.0, 30, "ten", Decimal("19.00"))
        self.assertEqual(len(catalogue.regional), 2)


class ApplyRegionalTests(TestCase):
    """Writing the tariffs, and what a customer ends up seeing."""

    def setUp(self):
        PricingRule.objects.create(scope=PricingRule.Scope.GLOBAL, markup_percent=Decimal("30"))
        self.europe = Region.objects.create(name="Europe", slug="europe", sort_order=1)
        self.world = Region.objects.create(name="Global", slug="global", sort_order=8)

    def test_a_regional_tariff_is_created_priced_and_left_switched_off(self):
        result = supplier_import.apply_regional(
            {("europe", 5.0, 30): ("EU_5_30", Decimal("11.00"), 41)}, "esimaccess"
        )
        self.assertEqual(result["plans_created"], 1)

        plan = Plan.objects.get(region=self.europe, country__isnull=True)
        self.assertEqual(plan.scope, Plan.Scope.REGIONAL)
        self.assertEqual(plan.cost_usd, Decimal("11.00"))
        # Priced through the same rules as everything else.
        self.assertEqual(plan.price_usd, Decimal("14.30"))
        # Off until someone looks at it, same as a new destination.
        self.assertFalse(plan.is_active)
        # The coverage count is the reason to buy: "Europe 5 GB" alone leaves the
        # customer guessing whether their stop is included.
        self.assertEqual(plan.price_note, "41 ta davlat")

    def test_a_global_bundle_is_scoped_global_not_regional(self):
        supplier_import.apply_regional(
            {("global", 1.0, 7): ("GL139", Decimal("7.60"), 128)}, "esimaccess"
        )
        plan = Plan.objects.get(region=self.world)
        self.assertEqual(plan.scope, Plan.Scope.GLOBAL)
        self.assertEqual(plan.price_note, "128 ta davlat")

    def test_a_second_sync_updates_rather_than_duplicates(self):
        payload = {("europe", 5.0, 30): ("EU_5_30", Decimal("11.00"), 41)}
        supplier_import.apply_regional(payload, "esimaccess")
        supplier_import.apply_regional(payload, "esimaccess")
        self.assertEqual(Plan.objects.filter(region=self.europe).count(), 1)

    def test_a_bundle_that_grew_has_its_coverage_refreshed(self):
        supplier_import.apply_regional(
            {("europe", 5.0, 30): ("EU_5_30", Decimal("11.00"), 41)}, "esimaccess"
        )
        # The supplier added three countries to the same package.
        supplier_import.apply_regional(
            {("europe", 5.0, 30): ("EU_5_30", Decimal("11.00"), 44)}, "esimaccess"
        )
        plan = Plan.objects.get(region=self.europe)
        self.assertEqual(plan.price_note, "44 ta davlat", "coverage must not be frozen")

    def test_both_suppliers_are_kept_side_by_side_for_the_same_tariff(self):
        supplier_import.apply_regional(
            {("europe", 5.0, 30): ("EU_5_30", Decimal("11.00"), 41)}, "esimaccess"
        )
        supplier_import.apply_regional(
            {("europe", 5.0, 30): ("eu-5-30", Decimal("9.80"), 41)}, "esimcard"
        )
        plan = Plan.objects.get(region=self.europe)
        self.assertEqual(plan.offers.count(), 2)
        # Same comparison as a destination tariff: the cheaper supplier wins.
        self.assertEqual(plan.provider, "esimcard")
        self.assertEqual(plan.cost_usd, Decimal("9.80"))

    def test_a_shape_with_no_rung_is_not_written(self):
        result = supplier_import.apply_regional(
            {("europe", 2.0, 3): ("EU_2_3", Decimal("4.00"), 41)}, "esimaccess"
        )
        self.assertEqual(result["plans_created"], 0)

    def test_an_unknown_region_is_skipped_not_crashed(self):
        result = supplier_import.apply_regional(
            {("atlantis", 5.0, 30): ("X", Decimal("5.00"), 41)}, "esimaccess"
        )
        self.assertEqual(result["plans_created"], 0)
