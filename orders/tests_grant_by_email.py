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
from orders.admin import ComplimentaryGrantAdmin, ComplimentaryGrantForm


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
