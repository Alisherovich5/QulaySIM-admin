"""Komissiya pog'onalari — FastAPI'dagi nusxa bilan bir xil javob berishi kerak.

`customers/commission.py` — QulaySIM-backend'dagi `app/domain/referral.py` ning
nusxasi. Ikki repo alohida, import qilib bo'lmaydi, shuning uchun yagona
himoya — ikkala tomonda BIR XIL raqamlarni tekshiradigan testlar. Bu yerdagi
kutilgan qiymatlar backend'dagi `tests/unit/test_referral_tiers.py` dan
ko'chirilgan; biri o'zgarib ikkinchisi qolsa, agentga panelda bir summa,
hisobotda boshqasi ko'rinadi.
"""

from decimal import Decimal

from django.test import SimpleTestCase

from customers.commission import parse_tiers, tier_for


class CommissionTierTests(SimpleTestCase):
    def test_the_written_example(self):
        """#2002: "150 minglik olsa 7500 bo'ladi"."""

        tiers = parse_tiers("0:5%,100:6%,300:6.5%")

        self.assertEqual(tier_for(tiers, 0).amount_for(Decimal("150000")), 7500)
        self.assertEqual(tier_for(tiers, 100).amount_for(Decimal("150000")), 9000)
        self.assertEqual(tier_for(tiers, 300).amount_for(Decimal("150000")), 9750)

    def test_more_than_a_hundred_means_the_hundred_and_first(self):
        tiers = parse_tiers("0:5%,100:6%")

        self.assertEqual(tier_for(tiers, 99).label, "5%")
        self.assertEqual(tier_for(tiers, 100).label, "6%")

    def test_rounds_to_the_nearest_som(self):
        self.assertEqual(parse_tiers("0:5%")[0].amount_for(Decimal("149999")), 7500)

    def test_a_flat_rate_ignores_the_order_size(self):
        tiers = parse_tiers("0:5000")

        self.assertEqual(tiers[0].amount_for(Decimal("150000")), 5000)
        self.assertEqual(tiers[0].amount_for(Decimal("1500000")), 5000)

    def test_sorts_by_threshold_whatever_the_order_written(self):
        self.assertEqual(
            [t.from_count for t in parse_tiers("300:6.5%,0:5%,100:6%")], [0, 100, 300]
        )

    def test_a_typo_is_refused_rather_than_dropped(self):
        with self.assertRaises(ValueError):
            parse_tiers("0:5%,yuz:6%")
        with self.assertRaises(ValueError):
            parse_tiers("besh foiz")

    def test_empty_promises_nothing(self):
        self.assertEqual(parse_tiers("")[0].amount_for(Decimal("150000")), 0)

    def test_labels_stay_short(self):
        self.assertEqual(parse_tiers("0:6.50%")[0].label, "6.5%")
        self.assertEqual(parse_tiers("0:6000")[0].label, "6\u00a0000 so'm")

    def test_a_list_that_does_not_start_at_zero_uses_its_lowest_rate(self):
        self.assertEqual(tier_for(parse_tiers("1:5%,100:6%"), 0).label, "5%")
