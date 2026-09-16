"""Unlimited packages, which the ladder had no way of expressing.

eSIMCard sells 3,377 of them and the catalogue held none. Two separate reasons,
and each one alone was enough to drop every single package:

  * the fetcher treated "no gigabyte figure" as "unreadable" and binned it,
  * and the ladder is a list of traffic sizes, so nothing could ever match.

There is a third that only bites unlimited because of how eSIMCard writes them:
coverage lists one row per NETWORK, so a single-destination package for South
Korea arrives with two rows and was counted as a multi-country regional
package. 639 of the 3,377 have exactly that shape.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from catalog import supplier_api
from catalog.models import Country, Plan, Region, SellableShape
from catalog.supplier_import import apply, on_ladder, plan_label, reset_rungs_cache


def _package(**over):
    """A package in the shape eSIMCard actually sends."""
    package = {
        "id": "789e69e8-e286-483f-b60c-51cb533cf1d8",
        "name": "Unlimited eSIM Data for 1 Day in South Korea",
        "price": 0.57,
        "data_quantity": -1,
        "data_unit": "GB",
        "package_validity": 1,
        "package_validity_unit": "Day",
        "unlimited": True,
        "coverage": [
            {"code": "kr", "country_name": "South Korea", "network_name": "Korea Telecom"},
            {"code": "kr", "country_name": "South Korea", "network_name": "SK Telecom"},
        ],
    }
    package.update(over)
    return package


class TheFetcherKeepsUnlimitedTests(TestCase):
    def test_an_unlimited_package_is_recorded_with_no_gigabytes(self):
        catalogue = supplier_api.FetchedCatalogue()

        supplier_api._add(catalogue, "KR", 0.0, 1, "code-1", Decimal("0.57"), unlimited=True)

        self.assertEqual(catalogue.prices.best, {("KR", 0.0, 1): ("code-1", Decimal("0.57"))})
        self.assertEqual(catalogue.unusable, 0)

    def test_zero_gigabytes_without_the_flag_is_still_unusable(self):
        """A package whose size could not be read must keep being dropped —
        that is what stops a "0 GB" plan reaching the site."""
        catalogue = supplier_api.FetchedCatalogue()

        supplier_api._add(catalogue, "KR", 0.0, 1, "code-1", Decimal("0.57"))

        self.assertEqual(catalogue.prices.best, {})
        self.assertEqual(catalogue.unusable, 1)

    def test_the_cheapest_unlimited_for_a_duration_wins(self):
        catalogue = supplier_api.FetchedCatalogue()

        supplier_api._add(catalogue, "KR", 0.0, 7, "dear", Decimal("9.00"), unlimited=True)
        supplier_api._add(catalogue, "KR", 0.0, 7, "cheap", Decimal("6.00"), unlimited=True)

        self.assertEqual(catalogue.prices.best[("KR", 0.0, 7)], ("cheap", Decimal("6.00")))

    def test_an_unlimited_package_with_no_price_is_refused(self):
        catalogue = supplier_api.FetchedCatalogue()

        supplier_api._add(catalogue, "KR", 0.0, 7, "code-1", Decimal("0"), unlimited=True)

        self.assertEqual(catalogue.prices.best, {})
        self.assertEqual(catalogue.unusable, 1)


class OneCountryTwoNetworksTests(TestCase):
    """The coverage list is per network, so counting rows counts the wrong thing.

    Driven through `fetch_esimcard` rather than by re-writing its logic here: a
    test that recomputes the expression it is checking passes whatever the
    production code does.
    """

    def _fetch(self, packages):
        class _Response:
            status_code = 200

            @staticmethod
            def json():
                return {
                    "status": True,
                    "meta": {"lastPage": 1},
                    "data": packages,
                }

        class _Session:
            headers: dict = {}

            def get(self, *_args, **_kwargs):
                return _Response()

        with patch.object(supplier_api.requests, "Session", _Session), self.settings(
            ESIMCARD_API_TOKEN="test-token"
        ):
            return supplier_api.fetch_esimcard()

    def test_two_coverage_rows_for_one_country_stay_one_country(self):
        catalogue = self._fetch([_package()])

        self.assertEqual(
            catalogue.prices.best, {("KR", 0.0, 1): ("789e69e8-e286-483f-b60c-51cb533cf1d8", Decimal("0.57"))}
        )
        self.assertEqual(catalogue.multi_country, 0)

    def test_two_real_countries_are_still_regional(self):
        catalogue = self._fetch(
            [_package(coverage=[{"code": "kr"}, {"code": "jp"}])]
        )

        self.assertEqual(catalogue.prices.best, {})
        self.assertEqual(catalogue.multi_country, 1)

    def test_a_gigabyte_package_still_lands_where_it_did(self):
        catalogue = self._fetch(
            [
                _package(
                    unlimited=False,
                    data_quantity=3,
                    data_unit="GB",
                    package_validity=15,
                    coverage=[{"code": "kr"}],
                )
            ]
        )

        self.assertEqual(list(catalogue.prices.best), [("KR", 3.0, 15)])


class TheUnlimitedRungTests(TestCase):
    def setUp(self):
        reset_rungs_cache()
        SellableShape.objects.all().delete()
        self.region = Region.objects.create(name="Asia", slug="asia")
        self.kr = Country.objects.create(
            name="South Korea", slug="south-korea", iso2="KR", region=self.region
        )

    def tearDown(self):
        reset_rungs_cache()

    def test_without_a_rung_unlimited_is_not_sellable(self):
        SellableShape.objects.create(data_mb=1024, days=7, network="4G")

        self.assertFalse(on_ladder(0, 1))

    def test_a_zero_megabyte_rung_is_the_unlimited_rung(self):
        SellableShape.objects.create(data_mb=0, days=1, network="5G")

        self.assertTrue(on_ladder(0, 1))

    def test_the_rung_is_per_duration(self):
        SellableShape.objects.create(data_mb=0, days=1, network="5G")

        self.assertTrue(on_ladder(0, 1))
        self.assertFalse(on_ladder(0, 30))


class TheUnlimitedPlanTests(TestCase):
    def setUp(self):
        reset_rungs_cache()
        SellableShape.objects.all().delete()
        SellableShape.objects.create(data_mb=0, days=7, network="5G", sort_order=100)
        region = Region.objects.create(name="Asia", slug="asia")
        self.kr = Country.objects.create(
            name="South Korea", slug="south-korea", iso2="KR", region=region
        )

    def tearDown(self):
        reset_rungs_cache()

    def _prices(self):
        prices = supplier_api.ParsedPrices()
        prices.best[("KR", 0.0, 7)] = ("pkg-1", Decimal("6.00"))
        return prices

    def test_the_plan_is_flagged_unlimited(self):
        apply(self._prices(), "esimcard")

        plan = Plan.objects.get(country=self.kr, validity_days=7)
        self.assertTrue(plan.is_unlimited)
        self.assertEqual(plan.data_amount_mb, 0)

    def test_it_is_never_called_zero_gigabytes(self):
        """The failure the old comment in supplier_api warned about."""
        apply(self._prices(), "esimcard")

        plan = Plan.objects.get(country=self.kr, validity_days=7)
        self.assertNotIn("0 GB", plan.title)
        self.assertIn("Unlimited", plan.title)
        self.assertEqual(plan.data_label, "Unlimited")

    def test_the_supplier_offer_carries_the_package_code(self):
        apply(self._prices(), "esimcard")

        offer = Plan.objects.get(country=self.kr, validity_days=7).offers.get(provider="esimcard")
        self.assertEqual(offer.package_code, "pkg-1")
        self.assertEqual(offer.cost_usd, Decimal("6.00"))

    def test_a_gigabyte_plan_is_not_flagged_unlimited(self):
        SellableShape.objects.create(data_mb=1024, days=7, network="4G", sort_order=0)
        reset_rungs_cache()
        prices = supplier_api.ParsedPrices()
        prices.best[("KR", 1.0, 7)] = ("pkg-2", Decimal("1.00"))

        apply(prices, "esimcard")

        plan = Plan.objects.get(country=self.kr, data_amount_mb=1024)
        self.assertFalse(plan.is_unlimited)
        self.assertIn("1 GB", plan.title)


class TheLabelTests(TestCase):
    def test_zero_gigabytes_reads_as_unlimited(self):
        self.assertEqual(plan_label("South Korea", 0.0, 7), "South Korea Unlimited · 7 days")

    def test_a_size_still_reads_as_a_size(self):
        self.assertEqual(plan_label("South Korea", 3.0, 15), "South Korea 3 GB · 15 days")


class ATopUpMirrorDoesNotStopTheSyncTests(TestCase):
    """The sync died the first time it ran after a top-up had ever been sold.

    A top-up needs a Plan row of its own so the order line has something to
    point at, and that row carries the same country, size and duration as the
    plan it tops up. China 5 GB / 30 days therefore exists twice, and the
    importer's get_or_create on exactly those three fields raised
    MultipleObjectsReturned and took the whole catalogue sync down with it —
    every destination, both suppliers, nothing written.
    """

    def setUp(self):
        reset_rungs_cache()
        SellableShape.objects.all().delete()
        SellableShape.objects.create(data_mb=5120, days=30, network="5G", sort_order=3)
        region = Region.objects.create(name="Asia", slug="asia")
        self.cn = Country.objects.create(
            name="China", slug="china", iso2="CN", region=region
        )
        self.real = Plan.objects.create(
            country=self.cn,
            scope=Plan.Scope.LOCAL,
            title="China 5 GB · 30 days",
            data_amount_mb=5120,
            validity_days=30,
            price_usd=Decimal("7.99"),
            cost_usd=Decimal("2.96"),
            sort_order=5,
        )
        # Sorted ahead of the destination plan on purpose. Plan.Meta orders by
        # (sort_order, price_usd), so this is the row a bare .first() returns —
        # which is what makes the two tests below fail if the exclude is
        # dropped. In production both rows carried 7.99 and the tie made the
        # winner undefined, so "it happened to pick the right one" was never a
        # property anything guaranteed.
        self.mirror = Plan.objects.create(
            country=self.cn,
            scope=Plan.Scope.TOPUP,
            title="China 5GB 30Days (qo'shimcha)",
            data_amount_mb=5120,
            validity_days=30,
            price_usd=Decimal("7.99"),
            # A different cost, so the two rows end up at different prices once
            # save() recomputes them. That is what lets the preview test below
            # tell which row it read.
            cost_usd=Decimal("9.00"),
            sort_order=0,
            is_active=False,
        )

    def tearDown(self):
        reset_rungs_cache()

    def _prices(self):
        prices = supplier_api.ParsedPrices()
        prices.best[("CN", 5.0, 30)] = ("pkg-cn", Decimal("2.50"))
        return prices

    def test_the_sync_completes(self):
        result = apply(self._prices(), "esimcard")

        self.assertEqual(result["plans_created"], 0)

    def test_the_offer_lands_on_the_destination_plan_not_the_mirror(self):
        apply(self._prices(), "esimcard")

        self.assertTrue(self.real.offers.filter(provider="esimcard").exists())
        self.assertFalse(self.mirror.offers.filter(provider="esimcard").exists())

    def test_the_preview_reads_the_destination_plan_too(self):
        from catalog.supplier_import import plan_changes

        changes = plan_changes(self._prices(), "esimcard")

        self.assertEqual([c.kind for c in changes], ["new-offer"])
        # Which row it read, not just that it read one. The mirror costs more,
        # so it is priced higher — reading it would report the wrong "before".
        self.real.refresh_from_db()
        self.mirror.refresh_from_db()
        self.assertNotEqual(self.real.price_usd, self.mirror.price_usd)
        self.assertEqual(changes[0].price_before, self.real.price_usd)
