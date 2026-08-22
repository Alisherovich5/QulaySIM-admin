"""What the catalogue is willing to sell, now that it is data.

The ladder decided which wholesaler shapes become plans, and it was a Python
list. So "start selling the daily packages" — a commercial decision — required
an engineer, a commit and a deploy, and the owner asking "why are only 6 of
Uzbekistan's 13 packages on the site?" needed two files read to answer.

These tests pin the three things that make the table safe to hand over: an empty
table cannot take the catalogue down, a rung named for one destination stays
there, and the cache does not outlive an edit.
"""

from __future__ import annotations

from django.test import TestCase

from catalog.models import Country, Region, SellableShape
from catalog.supplier_import import DEFAULT_LADDER, ladder_rung, on_ladder, reset_rungs_cache, rungs


class LadderFromTheTableTests(TestCase):
    def setUp(self):
        reset_rungs_cache()
        SellableShape.objects.all().delete()
        region = Region.objects.create(name="Asia", slug="asia")
        self.uz = Country.objects.create(name="Uzbekistan", slug="uzbekistan", iso2="UZ", region=region)
        self.tr = Country.objects.create(name="Turkey", slug="turkey", iso2="TR", region=region)

    def tearDown(self):
        reset_rungs_cache()

    def test_an_empty_table_falls_back_to_the_shipped_ladder(self):
        """The one failure that must not be possible: a fresh database, or a row
        deleted by accident, silently stopping the whole catalogue from
        importing. Empty means "as before", not "sell nothing"."""
        self.assertEqual(rungs(), list(DEFAULT_LADDER))
        self.assertTrue(on_ladder(3072, 30))

    def test_a_row_decides_what_is_sellable(self):
        SellableShape.objects.create(data_mb=2048, days=1, network="5G", sort_order=0)
        reset_rungs_cache()
        self.assertTrue(on_ladder(2048, 1))
        # …and the shipped ladder no longer applies once the table has an opinion.
        self.assertFalse(on_ladder(3072, 30))

    def test_a_rung_can_belong_to_one_destination(self):
        """"Daily packages, but only for Uzbekistan" — the reason the country
        column exists. It must not leak into every other destination page."""
        SellableShape.objects.create(data_mb=1024, days=7, network="4G", sort_order=0)
        SellableShape.objects.create(data_mb=10240, days=1, network="5G", sort_order=1, country=self.uz)
        reset_rungs_cache()

        self.assertTrue(on_ladder(10240, 1, "UZ"))
        self.assertFalse(on_ladder(10240, 1, "TR"))
        # The shape with no country is offered in both.
        self.assertTrue(on_ladder(1024, 7, "UZ"))
        self.assertTrue(on_ladder(1024, 7, "TR"))

    def test_an_inactive_rung_stops_being_sold_without_being_deleted(self):
        shape = SellableShape.objects.create(data_mb=5120, days=30, network="5G")
        reset_rungs_cache()
        self.assertTrue(on_ladder(5120, 30))

        shape.is_active = False
        shape.save()
        # The signal drops the cache, so the next import sees the change.
        self.assertFalse(on_ladder(5120, 30))

    def test_the_order_the_owner_sets_is_the_order_the_page_reads(self):
        SellableShape.objects.create(data_mb=20480, days=30, network="5G", sort_order=0)
        SellableShape.objects.create(data_mb=1024, days=7, network="4G", sort_order=1)
        reset_rungs_cache()
        self.assertEqual(ladder_rung(20480, 30), (0, "5G"))
        self.assertEqual(ladder_rung(1024, 7), (1, "4G"))

    def test_a_shape_with_no_rung_has_none(self):
        SellableShape.objects.create(data_mb=1024, days=7, network="4G")
        reset_rungs_cache()
        self.assertIsNone(ladder_rung(3072, 15))

    def test_editing_a_rung_reaches_the_next_import(self):
        """The cache exists because a supplier file has thousands of rows and the
        table has a handful. It must not mean an admin edit waits for a restart."""
        self.assertEqual(rungs(), list(DEFAULT_LADDER))  # warms the cache
        SellableShape.objects.create(data_mb=999, days=3, network="4G")
        self.assertEqual(rungs(), [(999, 3, "4G")])
