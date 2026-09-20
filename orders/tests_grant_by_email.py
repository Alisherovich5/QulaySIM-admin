"""Giving an eSIM to an address, not to a row in a table.

The grant page asked for an existing customer. Almost nobody being given an
eSIM is one: it is a friend, a colleague, somebody who wrote in. So the person
who wanted to hand one out could not, and asked somebody else to do it for
them, every time — which is the cost this fixes, not a slow form.
"""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from catalog.models import Country, Plan
from customers.models import Customer
from django.contrib.admin.sites import site

from orders.admin import (
    ComplimentaryGrantAdmin,
    ComplimentaryGrantForm,
    ESIMAdmin,
)
from orders.models import ESIM, ComplimentaryGrant, Order


class GrantByEmailTests(TestCase):
    def setUp(self) -> None:
        country = Country.objects.create(name="Vietnam", slug="vietnam", iso2="VN")
        self.plan = Plan.objects.create(
            country=country,
            title="Vietnam 3 GB · 15 days",
            data_amount_mb=3072,
            validity_days=15,
            price_usd=Decimal("6.00"),
            cost_usd=Decimal("4.00"),
        )

    def _form(self, email: str) -> ComplimentaryGrantForm:
        return ComplimentaryGrantForm(
            data={"email": email, "plan": self.plan.pk, "reason": "Dilnur aka so‘radi"}
        )

    def test_an_unknown_address_becomes_a_customer(self) -> None:
        form = self._form("yangi.odam@gmail.com")
        self.assertTrue(form.is_valid(), form.errors)
        grant = form.save(commit=False)
        self.assertTrue(form.customer_created)
        self.assertEqual(grant.customer.email, "yangi.odam@gmail.com")
        self.assertEqual(Customer.objects.filter(email="yangi.odam@gmail.com").count(), 1)

    def test_a_known_address_is_reused_rather_than_duplicated(self) -> None:
        existing = Customer.objects.create(email="bor@gmail.com", full_name="Bor Odam")
        form = self._form("bor@gmail.com")
        self.assertTrue(form.is_valid(), form.errors)
        grant = form.save(commit=False)
        self.assertFalse(form.customer_created)
        self.assertEqual(grant.customer.pk, existing.pk)
        self.assertEqual(Customer.objects.count(), 1)

    def test_case_and_spaces_are_the_same_person(self) -> None:
        """Typed by hand, from a phone, off another screen."""
        existing = Customer.objects.create(email="bor@gmail.com")
        form = self._form("  BOR@Gmail.COM ")
        self.assertTrue(form.is_valid(), form.errors)
        grant = form.save(commit=False)
        self.assertEqual(grant.customer.pk, existing.pk)
        self.assertEqual(Customer.objects.count(), 1)

    def test_a_customer_created_here_can_sign_in_later(self) -> None:
        """No password is not a locked account: it is the same shape the
        storefront gives anybody who signs in with Google."""
        form = self._form("google.odam@gmail.com")
        self.assertTrue(form.is_valid(), form.errors)
        form.save(commit=False)
        made = Customer.objects.get(email="google.odam@gmail.com")
        self.assertEqual(made.hashed_password, "")
        self.assertTrue(made.is_active)

    def test_a_bad_address_is_refused_before_anybody_is_created(self) -> None:
        form = self._form("bu email emas")
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)
        self.assertEqual(Customer.objects.count(), 0)

    def test_the_page_asks_for_an_email_and_not_for_a_customer(self) -> None:
        """The whole point is that there is one field and it takes an address."""
        self.assertIn("email", ComplimentaryGrantAdmin.fields)
        self.assertNotIn("customer", ComplimentaryGrantAdmin.fields)
        self.assertNotIn("customer", ComplimentaryGrantAdmin.autocomplete_fields)


class GrantShowsTheQrTests(TestCase):
    """Handing the eSIM over is the rest of the job.

    The grant page produced an order number and nothing else. The profile
    arrives from the wholesaler seconds later, on a different page, findable
    only by searching a list of every eSIM ever sold — so the person who had
    just given one away still could not send it to anybody.
    """

    def setUp(self) -> None:
        country = Country.objects.create(name="Vietnam", slug="vietnam", iso2="VN")
        self.plan = Plan.objects.create(
            country=country,
            title="Vietnam 3 GB · 15 days",
            data_amount_mb=3072,
            validity_days=15,
            price_usd=Decimal("6.00"),
            cost_usd=Decimal("4.00"),
        )
        self.customer = Customer.objects.create(email="qr@gmail.com")
        self.order = Order.objects.create(customer=self.customer, status=Order.Status.PAID)
        self.grant = ComplimentaryGrant.objects.create(
            customer=self.customer, plan=self.plan, cost_usd=Decimal("4.00"), order=self.order
        )
        self.admin = ComplimentaryGrantAdmin(ComplimentaryGrant, site)

    def test_it_says_waiting_while_the_wholesaler_has_not_answered(self) -> None:
        """Asserted as "no link", not as a word: the column is translated, and
        a test that pins the English text fails the moment somebody reads the
        page in Uzbek — which is everybody who uses it."""
        html = self.admin.qr_link(self.grant)
        self.assertNotIn("<a ", html)
        self.assertIn("<span>", html)

    def test_it_links_to_the_profile_once_there_is_one(self) -> None:
        esim = ESIM.objects.create(
            order=self.order,
            customer=self.customer,
            plan=self.plan,
            iccid="8900000000000000001",
            qr_payload="LPA:1$example.com$ABC",
        )
        html = self.admin.qr_link(self.grant)
        self.assertIn(f"/orders/esim/{esim.pk}/change/", html)


class QrIsSendableTests(TestCase):
    """A 160px image in a page is something to photograph, not to forward."""

    def setUp(self) -> None:
        country = Country.objects.create(name="Vietnam", slug="vietnam", iso2="VN")
        plan = Plan.objects.create(
            country=country, title="Vietnam 3 GB", data_amount_mb=3072,
            validity_days=15, price_usd=Decimal("6.00"), cost_usd=Decimal("4.00"),
        )
        customer = Customer.objects.create(email="qr2@gmail.com")
        order = Order.objects.create(customer=customer, status=Order.Status.PAID)
        self.esim = ESIM.objects.create(
            order=order, customer=customer, plan=plan,
            iccid="8900000000000000002",
            qr_payload="LPA:1$rsp.example.com$ABCDEF",
            qr_image="data:image/png;base64,iVBORw0KGgo=",
        )
        self.admin = ESIMAdmin(ESIM, site)

    def test_the_picture_can_be_saved_as_a_file(self) -> None:
        html = self.admin.qr_preview(self.esim)
        self.assertIn("download=", html)
        self.assertIn("8900000000000000002", html)

    def test_the_line_to_paste_is_there_too(self) -> None:
        """Most phone cameras will not read a QR off another screen."""
        self.assertIn("LPA:1$rsp.example.com$ABCDEF", self.admin.qr_preview(self.esim))

    def test_an_unissued_profile_says_so_instead_of_a_broken_image(self) -> None:
        self.esim.qr_image = ""
        self.assertNotIn("<img", self.admin.qr_preview(self.esim))
