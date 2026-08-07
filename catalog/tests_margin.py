"""The margin report has to be arithmetic, and it has to be the right arithmetic.

Two mistakes it would be easy to make and hard to notice. Averaging per-tariff
percentages weights a $0.46 tariff the same as a $222 one, which is how a
catalogue reports a healthy margin while losing money on the tariffs that
actually sell. And counting a tariff with no supplier cost as zero margin
invents a problem that is really a missing number.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.margin import THIN_MARGIN_USD, build_report, size_label
from catalog.models import Country, Plan, PricingRule
from customers.models import Customer
from orders.models import Order, OrderItem


def make_plan(country, mb, days, cost, price, **kwargs):
    return Plan.objects.create(
        country=country,
        title=f"{country.name} {mb}",
        data_amount_mb=mb,
        validity_days=days,
        cost_usd=Decimal(cost),
        price_usd=Decimal(price),
        price_locked=True,  # so the figures under test survive the save
        provider=kwargs.pop("provider", "esimaccess"),
        provider_package_code=kwargs.pop("code", f"{country.iso2}-{mb}-{days}"),
        **kwargs,
    )


class ArithmeticTests(TestCase):
    def setUp(self):
        self.turkey = Country.objects.create(name="Turkey", name_uz="Turkiya", slug="tr", iso2="TR")

    def test_the_blend_is_total_over_total_not_a_mean_of_percentages(self):
        # +100% on a cheap tariff and +10% on an expensive one. A mean of the two
        # percentages says 55%; what we actually add is $101 on $1001, or 10.09%.
        make_plan(self.turkey, 1024, 7, "1.00", "2.00")
        make_plan(self.turkey, 51200, 30, "1000.00", "1100.00")

        report = build_report()
        self.assertEqual(report["overall"].markup_percent, Decimal("10.09"))
        self.assertEqual(report["overall"].margin_total, Decimal("101.00"))

    def test_a_tariff_with_no_supplier_cost_is_left_out_not_counted_as_zero(self):
        make_plan(self.turkey, 1024, 7, "1.00", "2.00")
        Plan.objects.create(
            country=self.turkey, title="No cost", data_amount_mb=2048, validity_days=7,
            cost_usd=None, price_usd=Decimal("5.00"), price_locked=True,
            provider="esimaccess", provider_package_code="TR-nocost",
        )

        report = build_report()
        self.assertEqual(report["counted"], 1)
        self.assertEqual(report["no_cost"], 1)
        # Unaffected by the row it cannot price.
        self.assertEqual(report["overall"].markup_percent, Decimal("100.00"))

    def test_a_switched_off_tariff_is_not_in_the_figures(self):
        make_plan(self.turkey, 1024, 7, "1.00", "2.00")
        make_plan(self.turkey, 2048, 7, "1.00", "9.00", is_active=False, code="TR-off")

        report = build_report()
        self.assertEqual(report["counted"], 1)
        self.assertEqual(report["inactive"], 1)

    def test_thin_margins_are_absolute_dollars_not_a_percentage(self):
        # +100% is a fine-looking percentage and 23 cents of profit. A card fee
        # is charged in dollars, so the floor has to be in dollars too.
        thin = make_plan(self.turkey, 1024, 7, "0.23", "0.46")
        fat = make_plan(self.turkey, 5120, 30, "10.00", "12.00", code="TR-fat")

        report = build_report()
        self.assertEqual(report["thin_total"], 1)
        self.assertEqual(report["thin"][0].plan_id, thin.id)
        self.assertLess(report["thin"][0].margin, THIN_MARGIN_USD)
        self.assertNotIn(fat.id, [row.plan_id for row in report["thin"]])

    def test_the_thin_list_leads_with_the_worst(self):
        make_plan(self.turkey, 1024, 7, "1.00", "1.40")
        worst = make_plan(self.turkey, 2048, 7, "1.00", "1.05", code="TR-worst")

        report = build_report()
        self.assertEqual(report["thin"][0].plan_id, worst.id)

    def test_sizes_are_listed_smallest_first_not_alphabetically(self):
        make_plan(self.turkey, 10240, 30, "5.00", "7.00", code="TR-10")
        make_plan(self.turkey, 3072, 15, "2.00", "3.00", code="TR-3")
        make_plan(self.turkey, 512, 7, "1.00", "2.00", code="TR-512")

        labels = [row.label for row in build_report()["sizes"]]
        self.assertEqual(labels, ["512 MB", "3 GB", "10 GB"])

    def test_destinations_lead_with_the_worst_markup(self):
        japan = Country.objects.create(name="Japan", name_uz="Yaponiya", slug="jp", iso2="JP")
        make_plan(self.turkey, 1024, 7, "1.00", "3.00")   # +200%
        make_plan(japan, 1024, 7, "1.00", "1.10", code="JP-1")  # +10%

        rows = build_report()["destinations"]
        self.assertEqual(rows[0].label, "Yaponiya")

    def test_a_plan_is_grouped_under_its_uzbek_destination_name(self):
        make_plan(self.turkey, 1024, 7, "1.00", "2.00")
        self.assertEqual(build_report()["destinations"][0].label, "Turkiya")

    def test_size_label_reads_the_way_a_tariff_does(self):
        self.assertEqual(size_label(1024), "1 GB")
        self.assertEqual(size_label(512), "512 MB")
        self.assertEqual(size_label(0), "—")
        self.assertEqual(size_label(1024, unlimited=True), "∞")

    def test_an_empty_catalogue_reports_nothing_rather_than_dividing_by_zero(self):
        report = build_report()
        self.assertEqual(report["counted"], 0)
        self.assertIsNone(report["overall"].markup_percent)
        self.assertIsNone(report["overall"].margin_each)


class RealisedMarginTests(TestCase):
    def setUp(self):
        self.turkey = Country.objects.create(name="Turkey", name_uz="Turkiya", slug="tr", iso2="TR")
        self.plan = make_plan(self.turkey, 3072, 15, "1.39", "2.09")
        self.customer = Customer.objects.create(email="m@example.com")

    def _order(self, status, quantity=1, unit_price="2.09"):
        order = Order.objects.create(
            customer=self.customer, status=status, total=Decimal(unit_price) * quantity
        )
        OrderItem.objects.create(
            order=order, plan=self.plan, unit_price=Decimal(unit_price), quantity=quantity
        )
        return order

    def test_only_paid_orders_count_as_realised(self):
        self._order("paid", quantity=2)
        self._order("pending", quantity=5)

        sales = build_report()["sales"]
        self.assertEqual(sales["units"], 2)
        self.assertEqual(sales["revenue"], Decimal("4.18"))
        self.assertEqual(sales["cost"], Decimal("2.78"))
        self.assertEqual(sales["margin"], Decimal("1.40"))

    def test_nothing_sold_is_zero_rather_than_a_crash(self):
        sales = build_report()["sales"]
        self.assertEqual(sales["units"], 0)
        self.assertIsNone(sales["markup_percent"])


@override_settings(SECURE_SSL_REDIRECT=False)
class ReportPageTests(TestCase):
    def setUp(self):
        PricingRule.objects.create(scope=PricingRule.Scope.GLOBAL, markup_percent=Decimal("50"))
        turkey = Country.objects.create(name="Turkey", name_uz="Turkiya", slug="tr", iso2="TR")
        make_plan(turkey, 1024, 7, "0.46", "0.69")
        self.client.force_login(
            get_user_model().objects.create_superuser("mr", "m@x.uz", "Pw-1234-abcd")
        )
        self.url = reverse("admin:catalog_plan_margin_report")

    def test_it_opens(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Turkiya")

    def test_it_leaks_no_template_commentary(self):
        # {# #} is single-line only in Django; a multi-line one renders verbatim
        # onto the page, which is how internal notes reached an operator before.
        body = self.client.get(self.url).content.decode()
        self.assertNotIn("{#", body)
        self.assertNotIn("catalog/margin.py", body)

    def test_it_carries_print_styling_so_save_as_pdf_produces_a_document(self):
        self.assertContains(self.client.get(self.url), "@media print")

    def test_a_signed_out_visitor_cannot_read_supplier_costs(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (302, 403))
        self.assertNotIn("0.46", response.content.decode())
