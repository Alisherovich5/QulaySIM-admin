"""One payment, one printable document.

Two things it must not do. It must not call itself a fiscal receipt: ATMOS is
registered as our commissioner and issues that itself, so presenting this as the
chek would be presenting a non-fiscal document as one.

And it must not recompute a total. The som figure was frozen at checkout against
that moment's rate; recalculating from today's prices would disagree with the
customer's bank statement exactly when someone asks for the receipt.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import Country, Plan, PricingRule
from customers.models import Customer
from orders.models import Order, OrderItem, Payment


@override_settings(
    SECURE_SSL_REDIRECT=False,
    COMPANY_NAME="QulaySIM MCHJ",
    COMPANY_INN="123456789",
    COMPANY_ADDRESS="Toshkent",
    COMPANY_PHONE="+998 90 000 00 00",
    COMPANY_EMAIL="support@qulaysim.uz",
    COMPANY_BANK="Bank, 2020 8000 0000 0000",
)
class ReceiptTests(TestCase):
    def setUp(self):
        PricingRule.objects.create(scope=PricingRule.Scope.GLOBAL, markup_percent=Decimal("50"))
        country = Country.objects.create(
            name="Turkey", name_uz="Turkiya", slug="turkey", iso2="TR"
        )
        self.plan = Plan.objects.create(
            country=country,
            title="Turkiya 3 GB · 15 kun",
            data_amount_mb=3072,
            validity_days=15,
            cost_usd=Decimal("1.39"),
            price_usd=Decimal("0"),
            provider="esimaccess",
            provider_package_code="TR_3_15",
        )
        self.customer = Customer.objects.create(email="mijoz@example.com", full_name="Otabek Q.")
        self.order = Order.objects.create(
            customer=self.customer,
            status="paid",
            subtotal=Decimal("4.18"),
            discount=Decimal("0.42"),
            total=Decimal("3.76"),
            amount_uzs=Decimal("44700"),
            exchange_rate=Decimal("11886.00"),
        )
        OrderItem.objects.create(
            order=self.order, plan=self.plan, unit_price=Decimal("2.09"), quantity=2
        )
        self.payment = Payment.objects.create(
            order=self.order,
            method="atmos",
            amount=Decimal("3.76"),
            status="success",
            provider_ref="tx-12769130",
        )
        self.client.force_login(
            get_user_model().objects.create_superuser("rcp", "r@x.uz", "Pw-1234-abcd")
        )
        self.url = reverse("admin:orders_payment_receipt", args=[self.payment.pk])

    def _text(self):
        import re

        body = self.client.get(self.url).content.decode()
        return " ".join(re.sub(r"<[^>]+>", " ", body).split())

    def test_it_opens_from_the_payment_list(self):
        body = self.client.get(reverse("admin:orders_payment_changelist")).content.decode()
        self.assertIn(f"/{self.payment.pk}/receipt/", body)

    def test_it_carries_what_was_bought_and_for_how_much(self):
        text = self._text()
        self.assertIn("Turkiya 3 GB", text)
        self.assertIn("mijoz@example.com", text)
        self.assertIn("Otabek Q.", text)
        self.assertIn("tx-12769130", text)
        # Localised decimals: the Uzbek locale renders 3.76 as 3,76.
        self.assertTrue("3,76" in text or "3.76" in text)

    def test_the_som_figure_is_the_frozen_one_not_a_fresh_conversion(self):
        # Move the retail price after the sale, the way a nightly sync does.
        self.plan.price_usd = Decimal("99.00")
        self.plan.price_locked = True
        self.plan.save()

        text = self._text()
        self.assertIn("44", text)
        self.assertNotIn("99,00", text)
        self.assertTrue("11 886" in text or "11886" in text)

    def test_it_says_atmos_issues_the_fiscal_receipt(self):
        # The document must not be mistaken for the chek.
        text = self._text()
        self.assertIn("ATMOS", text)
        self.assertIn("fiskal", text.lower())

    def test_the_seller_block_is_printed(self):
        text = self._text()
        self.assertIn("QulaySIM MCHJ", text)
        self.assertIn("123456789", text)

    @override_settings(COMPANY_NAME="", COMPANY_INN="", COMPANY_ADDRESS="")
    def test_missing_seller_details_are_visible_gaps_not_blanks(self):
        # A document that looks complete while lacking the INN is worse than one
        # that shows what it is missing.
        body = self.client.get(self.url).content.decode()
        self.assertIn("qs-rc__gap", body)
        self.assertIn("qs-rc__warn", body)

    def test_a_line_total_is_quantity_times_unit_price(self):
        item = self.order.items.first()
        self.assertEqual(item.line_total, Decimal("4.18"))

    def test_an_unknown_payment_is_a_404_not_a_crash(self):
        response = self.client.get(reverse("admin:orders_payment_receipt", args=[999999]))
        self.assertEqual(response.status_code, 404)

    def test_a_signed_out_visitor_cannot_read_a_receipt(self):
        # It carries a customer's name, e-mail and what they bought.
        self.client.logout()
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (302, 403))
        self.assertNotIn("mijoz@example.com", response.content.decode())

    def test_it_survives_an_order_with_no_line_items(self):
        bare = Order.objects.create(customer=self.customer, status="paid", total=Decimal("1.00"))
        payment = Payment.objects.create(
            order=bare, method="atmos", amount=Decimal("1.00"), status="success"
        )
        response = self.client.get(reverse("admin:orders_payment_receipt", args=[payment.pk]))
        self.assertEqual(response.status_code, 200)
