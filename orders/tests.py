"""Admin hardening tests for the money side: promo codes, orders, eSIMs, Payme.

Each class pins one way an admin edit could contradict what the FastAPI side —
the only writer of payment and provisioning state — believes to be true.
"""

from decimal import Decimal

from django.contrib.admin.sites import site
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalog.models import Country, Plan
from customers.models import Customer
from django.db import transaction
from orders.models import (
    ESIM,
    AtmosTransaction,
    Order,
    OrderItem,
    PaymeTransaction,
    PromoCode,
)


class PromoCodeNormalisationTests(TestCase):
    """Checkout uppercases what the customer types and compares it verbatim to
    the stored row, so a lowercase code can never match: the banner advertises
    it while every attempt is told the code is invalid."""

    def _promo(self, **kwargs):
        defaults = {
            "code": "SAVE10",
            "discount_type": "percent",
            "discount_value": Decimal("10"),
        }
        defaults.update(kwargs)
        return PromoCode.objects.create(**defaults)

    def test_save_uppercases_and_strips_the_code(self):
        promo = self._promo(code="  summer25 ")
        promo.refresh_from_db()
        self.assertEqual(promo.code, "SUMMER25")

    def test_a_narrowed_save_still_persists_the_healed_code(self):
        promo = self._promo(code="OK10")
        # A legacy lowercase row, written before normalisation existed.
        PromoCode.objects.filter(pk=promo.pk).update(code="ok10")

        stale = PromoCode.objects.get(pk=promo.pk)
        stale.save(update_fields=["is_active"])

        stale.refresh_from_db()
        self.assertEqual(stale.code, "OK10", "healing must survive update_fields")

    def test_the_admin_form_normalises_before_the_uniqueness_check(self):
        """"sale" next to an existing "SALE" must be a form error the operator
        can read, not an IntegrityError at save time."""
        from orders.admin import PromoCodeForm

        self._promo(code="SALE")
        form = PromoCodeForm(
            data={
                "code": " sale ",
                "discount_type": "percent",
                "discount_value": "10",
                "min_order_usd": "0",
                "max_uses": "0",
                "used_count": "0",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("code", form.errors)

    def test_the_admin_form_saves_the_uppercase_code(self):
        from orders.admin import PromoCodeForm

        form = PromoCodeForm(
            data={
                "code": "spring",
                "discount_type": "percent",
                "discount_value": "10",
                "min_order_usd": "0",
                "max_uses": "0",
                "used_count": "0",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().code, "SPRING")


class PromoCodeValidationTests(TestCase):
    """The shapes that quietly zero out orders are refused at the door."""

    def _promo(self, **kwargs):
        defaults = {
            "code": "CHECK",
            "discount_type": "percent",
            "discount_value": Decimal("10"),
            "min_order_usd": Decimal("0"),
        }
        defaults.update(kwargs)
        return PromoCode(**defaults)

    def test_a_percentage_above_100_is_refused(self):
        promo = self._promo(discount_type="percent", discount_value=Decimal("150"))
        with self.assertRaises(ValidationError) as caught:
            promo.full_clean()
        self.assertIn("discount_value", caught.exception.message_dict)

    def test_a_full_100_percent_is_a_deliberate_choice_and_allowed(self):
        self._promo(discount_type="percent", discount_value=Decimal("100")).full_clean()

    def test_a_fixed_discount_without_a_higher_minimum_is_refused(self):
        """$5 off with no minimum makes every plan at or under $5 free; the
        checkout-side cap hides the giveaway instead of refusing it."""
        promo = self._promo(discount_type="fixed", discount_value=Decimal("5"))
        with self.assertRaises(ValidationError) as caught:
            promo.full_clean()
        self.assertIn("min_order_usd", caught.exception.message_dict)

    def test_a_minimum_equal_to_the_discount_is_still_refused(self):
        promo = self._promo(
            discount_type="fixed",
            discount_value=Decimal("5"),
            min_order_usd=Decimal("5"),
        )
        with self.assertRaises(ValidationError):
            promo.full_clean()

    def test_a_fixed_discount_under_a_real_minimum_passes(self):
        self._promo(
            discount_type="fixed",
            discount_value=Decimal("5"),
            min_order_usd=Decimal("10"),
        ).full_clean()


@override_settings(SECURE_SSL_REDIRECT=False)
@override_settings(LANGUAGE_CODE="en")
class OrderAdminHardeningTests(TestCase):
    """Payment state belongs to the payment provider.

    Flipping an order to "paid" in the admin provisions nothing — fulfilment
    only runs off Payme's PerformTransaction — and editing the frozen som
    amount makes Payme reject the checkout link the customer already holds.
    The line items are the money, and editing them never recomputed the frozen
    totals, so they are read-only like the eSIM rows.
    """

    def setUp(self):
        self.customer = Customer.objects.create(
            email="buyer@example.com", full_name="Buyer", hashed_password="x", is_active=True
        )
        self.country = Country.objects.create(name="Turkey", slug="turkey", iso2="TR")
        self.plan = Plan.objects.create(
            country=self.country,
            title="Turkey 3GB",
            validity_days=15,
            price_usd=Decimal("10.00"),
            price_locked=True,
        )
        self.order = Order.objects.create(
            customer=self.customer,
            status=Order.Status.PENDING,
            subtotal=Decimal("10.00"),
            discount=Decimal("0"),
            total=Decimal("10.00"),
            amount_uzs=Decimal("125000.00"),
            exchange_rate=Decimal("12500.0000"),
        )
        self.item = OrderItem.objects.create(
            order=self.order, plan=self.plan, unit_price=Decimal("10.00"), quantity=1
        )
        User.objects.create_superuser("order-admin", "o@example.com", "pw-for-tests-only")
        self.client.force_login(User.objects.get(username="order-admin"))
        self.url = reverse("admin:orders_order_change", args=[self.order.pk])

    def _post_change(self, extra=None):
        data = {
            "customer": str(self.customer.pk),
            "promo_code": "",
            "provider": "mock",
            "provider_transaction_id": "",
            "provider_order_no": "",
            "provider_status": "",
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "1",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-id": str(self.item.pk),
            "items-0-order": str(self.order.pk),
            "esims-TOTAL_FORMS": "0",
            "esims-INITIAL_FORMS": "0",
            "esims-MIN_NUM_FORMS": "0",
            "esims-MAX_NUM_FORMS": "1000",
            "_save": "Save",
        }
        data.update(extra or {})
        return self.client.post(self.url, data)

    def test_payment_state_is_read_only(self):
        readonly = set(site._registry[Order].readonly_fields)
        self.assertTrue({"status", "amount_uzs", "exchange_rate"} <= readonly)

    def test_a_smuggled_status_change_is_ignored(self):
        response = self._post_change({"status": "paid", "amount_uzs": "1.00"})
        self.assertEqual(response.status_code, 302, "the save itself must succeed")

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)
        self.assertEqual(self.order.amount_uzs, Decimal("125000.00"))
        self.assertIsNone(self.order.paid_at)

    def test_a_new_line_item_cannot_be_added(self):
        """This POST used to 500 on the NOT NULL unit_price — and had it saved,
        the frozen totals would no longer match the items."""
        response = self._post_change(
            {
                "items-TOTAL_FORMS": "2",
                "items-1-id": "",
                "items-1-order": str(self.order.pk),
                "items-1-plan": str(self.plan.pk),
                "items-1-quantity": "3",
            }
        )
        self.assertIn(response.status_code, (200, 302), "must not crash")
        self.assertEqual(OrderItem.objects.count(), 1, "no item may be added")

    def test_a_line_item_cannot_be_deleted(self):
        response = self._post_change({"items-0-DELETE": "on"})
        self.assertIn(response.status_code, (200, 302))
        self.assertTrue(OrderItem.objects.filter(pk=self.item.pk).exists())

        self.order.refresh_from_db()
        self.assertEqual(self.order.total, Decimal("10.00"), "totals stay consistent")


class PaymeTransactionHasNoAdminPageTests(TestCase):
    """Payme owns this lifecycle end to end, and now nothing here can touch it.

    These tests used to assert that add, change and delete were each refused on
    the admin page — the concern being that a deleted row answers
    CheckTransaction/GetStatement with "not found" for a transaction Payme knows
    it performed, which desynchronises reconciliation.

    The page itself is gone now (Payme is not the active provider), which enforces
    the same rule more strongly than three permission checks could: there is no
    form to submit. What is asserted is therefore the absence of the page — and,
    separately, that the model and its table survive, because losing the rows
    would break reconciliation just as badly as editing them.
    """

    def test_there_is_no_admin_page(self):
        self.assertNotIn(PaymeTransaction, site._registry)

    def test_the_records_are_still_kept(self):
        from django.db import connection

        self.assertIn(
            PaymeTransaction._meta.db_table, connection.introspection.table_names()
        )

    def test_a_transaction_can_still_be_written_by_code(self):
        """The integration keeps working; only the screen went away."""
        customer = Customer.objects.create(
            email="c@example.com", full_name="C", hashed_password="x", is_active=True
        )
        order = Order.objects.create(customer=customer)
        transaction = PaymeTransaction.objects.create(
            order=order, transaction_id="tx-1", amount_tiyin=1000000, account=str(order.pk)
        )
        self.assertEqual(PaymeTransaction.objects.get(pk=transaction.pk).transaction_id, "tx-1")

class ESIMExpiryValidationTests(TestCase):
    """The API's clean-up job only expires rows whose expires_at has passed, so
    an admin flipping one to 'active' without a date creates a profile that
    stays 'active' forever and over-reports on every dashboard."""

    def test_active_without_an_expiry_is_refused(self):
        esim = ESIM(status=ESIM.Status.ACTIVE)
        with self.assertRaises(ValidationError) as caught:
            esim.clean()
        self.assertIn("expires_at", caught.exception.message_dict)

    def test_active_with_an_expiry_passes(self):
        ESIM(status=ESIM.Status.ACTIVE, expires_at=timezone.now()).clean()

    def test_pending_without_an_expiry_is_fine(self):
        # Provisioning has not finished; there is nothing to expire yet.
        ESIM(status=ESIM.Status.PENDING).clean()

    def test_full_clean_carries_the_same_rule(self):
        """The admin form validates through full_clean, so the rule must
        surface there, not only when clean() is called directly."""
        customer = Customer.objects.create(
            email="e@example.com", full_name="E", hashed_password="x", is_active=True
        )
        plan = Plan.objects.create(
            title="Any 1GB", validity_days=7, price_usd=Decimal("5.00"), price_locked=True
        )
        order = Order.objects.create(customer=customer)
        esim = ESIM(
            order=order,
            plan=plan,
            customer=customer,
            iccid="8998000000000000001",
            qr_payload="LPA:1$rsp.example.com$CODE",
            status=ESIM.Status.ACTIVE,
        )
        with self.assertRaises(ValidationError) as caught:
            esim.full_clean()
        self.assertIn("expires_at", caught.exception.message_dict)


@override_settings(SECURE_SSL_REDIRECT=False)
@override_settings(LANGUAGE_CODE="en")
class DashboardCogsTests(TestCase):
    """Profit reporting must read the cost captured at the time of sale.

    Joining the plan's *current* cost meant every supplier repricing silently
    rewrote historical margins. Rows that predate the snapshot fall back to
    today's cost — and the dashboard has to say that is an estimate.
    """

    def setUp(self):
        self.customer = Customer.objects.create(
            email="d@example.com", full_name="D", hashed_password="x", is_active=True
        )
        self.plan = Plan.objects.create(
            title="Any 3GB",
            validity_days=15,
            cost_usd=Decimal("5.00"),
            price_usd=Decimal("10.00"),
            price_locked=True,
        )
        self.order = Order.objects.create(
            customer=self.customer,
            status=Order.Status.PAID,
            subtotal=Decimal("10.00"),
            total=Decimal("10.00"),
            paid_at=timezone.now(),
        )
        self.item = OrderItem.objects.create(
            order=self.order,
            plan=self.plan,
            unit_price=Decimal("10.00"),
            unit_cost=Decimal("2.00"),
            quantity=1,
        )

    def _context(self):
        from config.dashboard import dashboard_callback

        return dashboard_callback(RequestFactory().get("/"), {})

    def test_cogs_reads_the_snapshot_not_todays_cost(self):
        # The supplier reprices after the sale; history must not move.
        self.plan.cost_usd = Decimal("7.00")
        self.plan.save()

        context = self._context()
        self.assertEqual(context["fs_cost"], Decimal("2.00"))
        self.assertEqual(context["fs_profit"], Decimal("8.00"))
        self.assertFalse(context["fs_cost_estimated"])

    def test_rows_without_a_snapshot_fall_back_and_are_flagged(self):
        OrderItem.objects.create(
            order=self.order,
            plan=self.plan,
            unit_price=Decimal("10.00"),
            quantity=1,  # pre-snapshot row: unit_cost is NULL
        )
        context = self._context()
        # 2.00 snapshotted + 5.00 estimated at today's plan cost.
        self.assertEqual(context["fs_cost"], Decimal("7.00"))
        self.assertTrue(context["fs_cost_estimated"])

    def test_the_page_labels_the_estimate(self):
        OrderItem.objects.create(
            order=self.order, plan=self.plan, unit_price=Decimal("10.00"), quantity=1
        )
        User.objects.create_superuser("dash-admin", "da@example.com", "pw-for-tests-only")
        self.client.force_login(User.objects.get(username="dash-admin"))

        from django.urls import reverse

        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "estimated at today")

    def test_fully_snapshotted_history_carries_no_estimate_label(self):
        User.objects.create_superuser("dash-admin2", "d2@example.com", "pw-for-tests-only")
        self.client.force_login(User.objects.get(username="dash-admin2"))

        from django.urls import reverse

        response = self.client.get(reverse("admin:index"))
        self.assertNotContains(response, "estimated at today")
