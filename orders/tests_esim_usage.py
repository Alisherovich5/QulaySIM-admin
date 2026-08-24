"""The eSIM monitoring page: how much is left, and how long it has got.

The list existed before this; what it could not do was answer the question the
page is opened for. "36% used" is the same fact as "1.9 GB left" only after
arithmetic, a computed column that cannot be sorted cannot put the nearly-empty
profiles on top, and nothing separated a profile that is spent from one that is
merely half gone.

Unlimited plans are stored as `data_total_mb = 0`, and every assertion about a
threshold here exists because a percentage of zero is not a small number -- it
is not a number, and treating it as one puts unlimited profiles at the top of a
list sorted by "emptiest first".
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalog.models import Plan
from customers.models import Customer
from orders.admin import DataLeftFilter, ESIMAdmin, ExpiryFilter
from orders.models import ESIM, Order
from django.contrib.admin.sites import site


@override_settings(SECURE_SSL_REDIRECT=False, LANGUAGE_CODE="en")
class EsimUsagePageTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            email="c@example.com", full_name="C", hashed_password="x", is_active=True
        )
        self.plan = Plan.objects.create(
            title="Any 3GB", validity_days=15, price_usd=Decimal("5.00"), price_locked=True
        )
        self.order = Order.objects.create(customer=self.customer)
        self.admin = ESIMAdmin(ESIM, site)
        self.factory = RequestFactory()
        self.now = timezone.now()
        self.serial = 0

    def _esim(self, total, used, *, expires_in_days=10, status=ESIM.Status.ACTIVE):
        self.serial += 1
        return ESIM.objects.create(
            order=self.order,
            plan=self.plan,
            customer=self.customer,
            iccid=f"899800000000000{self.serial:04d}",
            qr_payload="LPA:1$rsp.example.com$CODE",
            status=status,
            data_total_mb=total,
            data_used_mb=used,
            expires_at=self.now + timedelta(days=expires_in_days),
        )

    def _rows(self, **params):
        request = self.factory.get("/", params)
        request.user = User(is_superuser=True, is_staff=True)
        return self.admin.get_changelist_instance(request).get_queryset(request)

    # --- the number the page exists to show -------------------------------

    def test_remaining_is_shown_in_the_units_a_person_would_say(self):
        # The Dubai 3 GB profile this page was asked for: 1124 of 3072 spent.
        esim = self._esim(3072, 1124)
        self.assertIn("1.9 GB", self.admin.data_left(esim))
        self.assertIn("1.1 GB", self.admin.usage_bar(esim))

    def test_below_a_gigabyte_stays_in_megabytes(self):
        # "0.2 GB" reads as less precise than the number it came from.
        self.assertIn("200 MB", self.admin.data_left(self._esim(1024, 824)))

    def test_an_overspent_profile_reports_zero_not_a_negative(self):
        """The wholesaler's figure can exceed the allowance, and "-40 MB left"
        is a number nobody can act on."""
        self.assertIn("0 MB", self.admin.data_left(self._esim(1024, 1064)))

    def test_an_unlimited_profile_says_so_instead_of_a_number(self):
        self.assertIn("Unlimited", self.admin.data_left(self._esim(0, 5000)))

    def test_days_left_counts_down_and_names_an_expired_profile(self):
        self.assertEqual(self.admin.days_left(self._esim(1024, 0, expires_in_days=11)), "11")
        self.assertIn("expired", self.admin.days_left(self._esim(1024, 0, expires_in_days=-1)))

    # --- sorting, which is what makes it a monitoring page ----------------

    def test_sorting_by_remaining_puts_the_emptiest_first(self):
        roomy = self._esim(3072, 100)
        nearly_empty = self._esim(3072, 3000)
        middle = self._esim(3072, 1500)

        order = list(self._rows(o="4.1").order_by("left_mb").values_list("id", flat=True))

        self.assertEqual(order, [nearly_empty.id, middle.id, roomy.id])

    def test_unlimited_is_not_treated_as_the_emptiest_profile(self):
        """Annotating it to zero would park every unlimited profile at the top
        of the list the support desk works from."""
        unlimited = self._esim(0, 9000)
        rows = {row.id: row for row in self._rows().annotate()}
        self.assertIsNone(rows[unlimited.id].left_mb)
        self.assertIsNone(rows[unlimited.id].used_pct)

    # --- filters ----------------------------------------------------------

    def _filtered(self, value):
        request = self.factory.get("/", {"data_left": value})
        request.user = User(is_superuser=True, is_staff=True)
        flt = DataLeftFilter(request, {"data_left": [value]}, ESIM, self.admin)
        return set(flt.queryset(request, self.admin.get_queryset(request)).values_list("id", flat=True))

    def test_finished_is_separated_from_almost_gone(self):
        spent = self._esim(1024, 1024)
        over = self._esim(1024, 1200)
        ninety = self._esim(1000, 950)
        half = self._esim(1000, 600)
        self._esim(1000, 0)

        # They need different actions: one is still a sale, the other is a
        # support call that already happened.
        self.assertEqual(self._filtered("finished"), {spent.id, over.id})
        self.assertEqual(self._filtered("low"), {spent.id, over.id, ninety.id})
        self.assertEqual(self._filtered("half"), {spent.id, over.id, ninety.id, half.id})

    def test_never_used_and_unlimited_are_their_own_answers(self):
        untouched = self._esim(1024, 0)
        unlimited = self._esim(0, 4000)
        self._esim(1024, 500)

        self.assertEqual(self._filtered("unused"), {untouched.id})
        self.assertEqual(self._filtered("unlimited"), {unlimited.id})

    def test_no_threshold_filter_ever_includes_an_unlimited_profile(self):
        self._esim(0, 9999)
        for value in ("finished", "low", "half"):
            self.assertEqual(self._filtered(value), set(), value)

    def _expiry(self, value):
        request = self.factory.get("/", {"expiry": value})
        request.user = User(is_superuser=True, is_staff=True)
        flt = ExpiryFilter(request, {"expiry": [value]}, ESIM, self.admin)
        return set(flt.queryset(request, self.admin.get_queryset(request)).values_list("id", flat=True))

    def test_expiry_windows_are_bounded_at_both_ends(self):
        gone = self._esim(1024, 10, expires_in_days=-2)
        tomorrow = self._esim(1024, 10, expires_in_days=1)
        in_five = self._esim(1024, 10, expires_in_days=5)
        in_thirty = self._esim(1024, 10, expires_in_days=30)

        self.assertEqual(self._expiry("expired"), {gone.id})
        # Three days must not sweep in the five-day one, and neither window
        # may include the profile that has already expired.
        self.assertEqual(self._expiry("d3"), {tomorrow.id})
        self.assertEqual(self._expiry("d7"), {tomorrow.id, in_five.id})
        self.assertEqual(self._expiry("live"), {tomorrow.id, in_five.id, in_thirty.id})

    # --- the totals, end to end through a real request --------------------

    def test_the_page_renders_and_its_title_carries_the_totals(self):
        self._esim(3072, 1124)
        self._esim(1024, 512)
        User.objects.create_superuser("root", "root@example.com", "pw-not-a-secret")
        self.client.force_login(User.objects.get(username="root"))

        response = self.client.get(reverse("admin:orders_esim_changelist"))

        self.assertEqual(response.status_code, 200)
        # 4096 sold, 1636 used, 2460 left.
        self.assertContains(response, "sold 4.0 GB")
        self.assertContains(response, "used 1.6 GB")
        self.assertContains(response, "left 2.4 GB")

    def test_the_totals_follow_the_current_filter(self):
        """Otherwise "Finished" shows a page of spent profiles above a total
        that describes every sale ever made."""
        self._esim(1024, 1024)
        self._esim(3072, 100)
        User.objects.create_superuser("root2", "root2@example.com", "pw-not-a-secret")
        self.client.force_login(User.objects.get(username="root2"))

        response = self.client.get(reverse("admin:orders_esim_changelist"), {"data_left": "finished"})

        self.assertContains(response, "sold 1.0 GB")
        self.assertContains(response, "left 0.0 GB")

    def test_an_unlimited_profile_does_not_distort_the_totals(self):
        self._esim(1024, 512)
        self._esim(0, 8000)
        User.objects.create_superuser("root3", "root3@example.com", "pw-not-a-secret")
        self.client.force_login(User.objects.get(username="root3"))

        response = self.client.get(reverse("admin:orders_esim_changelist"))

        self.assertContains(response, "sold 1.0 GB")
        self.assertContains(response, "used 0.5 GB")
