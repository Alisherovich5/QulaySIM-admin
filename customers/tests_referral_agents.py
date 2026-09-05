"""Agentlar hisoboti — raqamlar to'g'ri chiqishi.

Bu sahifadagi ikkita son pulga aylanadi, shuning uchun ular ataylab alohida
tekshiriladi: ro'yxatdan o'tganlar va sotib olganlar. Ikkisi chalkashsa,
agentga to'lanadigan summa ham noto'g'ri bo'ladi.
"""

from django.conf import settings
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase

from customers.admin import ReferralAgentAdmin
from customers.models import Customer, Referral, ReferralAgent


class ReferralAgentReportTests(TestCase):
    def setUp(self):
        self.admin = ReferralAgentAdmin(ReferralAgent, AdminSite())
        self.request = RequestFactory().get("/")

    def _customer(self, email, name=""):
        return Customer.objects.create(email=email, full_name=name, hashed_password="x")

    def _referral(self, referrer, status, email="who@example.com"):
        return Referral.objects.create(
            referrer=referrer, referred_email=email, status=status
        )

    def test_counts_sign_ups_and_purchases_separately(self):
        agent = self._customer("agent@example.com", "Agent Aka")
        self._referral(agent, Referral.Status.COMPLETED)
        self._referral(agent, Referral.Status.COMPLETED)
        self._referral(agent, Referral.Status.PENDING)

        row = self.admin.get_queryset(self.request).get(pk=agent.pk)

        self.assertEqual(row.invited, 3)
        self.assertEqual(row.purchased, 2)

    def test_money_follows_purchases_not_sign_ups(self):
        agent = self._customer("agent2@example.com", "Ikkinchi")
        self._referral(agent, Referral.Status.COMPLETED)
        self._referral(agent, Referral.Status.PENDING)
        self._referral(agent, Referral.Status.PENDING)

        row = self.admin.get_queryset(self.request).get(pk=agent.pk)
        shown = self.admin.earned_display(row)

        expected = 1 * settings.REFERRAL_COMMISSION_UZS
        self.assertIn(f"{expected:,}".replace(",", " "), shown)

    def test_customers_who_referred_nobody_are_not_listed(self):
        """Aks holda sahifa mijozlar ro'yxatining nusxasi bo'lib qoladi va
        agentni topib bo'lmaydi."""

        self._customer("nobody@example.com", "Hech Kim")
        agent = self._customer("agent3@example.com", "Uchinchi")
        self._referral(agent, Referral.Status.PENDING)

        listed = list(self.admin.get_queryset(self.request).values_list("email", flat=True))

        self.assertEqual(listed, ["agent3@example.com"])

    def test_best_agent_comes_first(self):
        weak = self._customer("weak@example.com")
        strong = self._customer("strong@example.com")
        self._referral(weak, Referral.Status.PENDING)
        for _ in range(3):
            self._referral(strong, Referral.Status.COMPLETED)

        order = list(self.admin.get_queryset(self.request).values_list("email", flat=True))

        self.assertEqual(order[0], "strong@example.com")

    def test_agents_cannot_be_created_by_hand(self):
        """Agent taklif havolasi ishlatilganda paydo bo'ladi. Qo'lda qo'shish —
        yolg'on hisobot yasashning eng oson yo'li."""

        self.assertFalse(self.admin.has_add_permission(self.request))
