"""«Tekshirildi» ustuni — yordam xizmati uchun eng birinchi savol.

Mijoz "0 GB sarflangan, sayt buzuq" deb yozganda, javob ikki xil bo'lishi
mumkin: raqam eskirgan, yoki rostdan hech narsa sarflanmagan. Bu ustun
ikkisini ajratadi. Shuning uchun uning yolg'on gapirmasligi test bilan
mahkamlangan.
"""

from datetime import timedelta

from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase
from django.utils import timezone

from catalog.models import Country, Plan
from customers.models import Customer
from orders.admin import ESIMAdmin
from orders.models import ESIM, Order


class SyncFreshnessTests(TestCase):
    def setUp(self):
        self.admin = ESIMAdmin(ESIM, AdminSite())
        self.request = RequestFactory().get("/")
        customer = Customer.objects.create(email="a@b.c", hashed_password="x")
        country = Country.objects.create(name="Turkey", slug="turkey", iso2="TR")
        plan = Plan.objects.create(
            country=country, title="Turkey 3 GB", data_amount_mb=3072,
            validity_days=15, price_usd=4,
        )
        order = Order.objects.create(customer=customer, status=Order.Status.PAID)
        self.esim = ESIM.objects.create(
            order=order, plan=plan, customer=customer, iccid="8900000000000000001",
            qr_payload="LPA:1$x$y", data_total_mb=3072, data_used_mb=1,
        )

    def _shown(self, minutes_ago=None):
        self.esim.last_synced_at = (
            None if minutes_ago is None else timezone.now() - timedelta(minutes=minutes_ago)
        )
        return str(self.admin.synced_ago(self.esim))

    def test_never_synced_is_said_out_loud(self):
        """Eng yomon holat: raqam hech qachon so'ralmagan. Bo'sh katak buni
        yashirar edi."""

        self.assertIn("hech qachon", self._shown(None))

    def test_fresh_reads_as_now(self):
        self.assertEqual(self._shown(0), "hozir")

    def test_minutes_are_plain(self):
        self.assertEqual(self._shown(25), "25 daq oldin")

    def test_over_an_hour_is_flagged(self):
        """Sinxron har 20 daqiqada ishlaydi. Bir soat -- uch marta o'tkazib
        yuborilgan, ya'ni nimadir to'xtagan."""

        shown = self._shown(90)
        self.assertIn("1 soat oldin", shown)
        self.assertIn("9E6D14", shown)

    def test_days_are_flagged_louder(self):
        shown = self._shown(60 * 24 * 3)
        self.assertIn("3 kun oldin", shown)
        self.assertIn("D73D3D", shown)

    def test_the_column_is_read_only(self):
        """Qo'lda yozilsa ustun yolg'on gapiradi -- u o'lchov, kiritma emas."""

        self.assertIn("last_synced_at", self.admin.readonly_fields)
