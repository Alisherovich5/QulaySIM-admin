"""Agentlar hisoboti — raqamlar to'g'ri chiqishi.

Bu sahifadagi ikkita son pulga aylanadi, shuning uchun ular ataylab alohida
tekshiriladi: ro'yxatdan o'tganlar va sotib olganlar. Ikkisi chalkashsa,
agentga to'lanadigan summa ham noto'g'ri bo'ladi.
"""

from datetime import timedelta

from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from customers.admin import ReferralAgentAdmin
from customers.models import Customer, Referral, ReferralAgent
from orders.models import Order


class ReferralAgentReportTests(TestCase):
    def setUp(self):
        self.admin = ReferralAgentAdmin(ReferralAgent, AdminSite())
        self.request = RequestFactory().get("/")

    def _customer(self, email, name=""):
        return Customer.objects.create(email=email, full_name=name, hashed_password="x")

    def _referral(self, referrer, status, email="who@example.com", paid_uzs="150000",
                  minutes=0):
        """Sotib olgan referal uchun to'langan buyurtma ham yaratiladi.

        Foizli stavka aynan o'sha buyurtmadan hisoblanadi, ya'ni buyurtmasiz
        referal bilan pulni tekshirib bo'lmaydi."""

        invitee = None
        if status == Referral.Status.COMPLETED:
            invitee = Customer.objects.create(
                email=f"inv-{Customer.objects.count()}-{email}", hashed_password="x"
            )
            if paid_uzs is not None:
                Order.objects.create(
                    customer=invitee, status=Order.Status.PAID, amount_uzs=paid_uzs
                )
        return Referral.objects.create(
            referrer=referrer,
            referred=invitee,
            referred_email=email,
            status=status,
            completed_at=(
                timezone.now() + timedelta(minutes=minutes)
                if status == Referral.Status.COMPLETED
                else None
            ),
        )

    def test_counts_sign_ups_and_purchases_separately(self):
        agent = self._customer("agent@example.com", "Agent Aka")
        self._referral(agent, Referral.Status.COMPLETED)
        self._referral(agent, Referral.Status.COMPLETED)
        self._referral(agent, Referral.Status.PENDING)

        row = self.admin.get_queryset(self.request).get(pk=agent.pk)

        self.assertEqual(row.invited, 3)
        self.assertEqual(row.purchased, 2)

    @override_settings(REFERRAL_COMMISSION_TIERS="0:5%")
    def test_money_follows_purchases_not_sign_ups(self):
        agent = self._customer("agent2@example.com", "Ikkinchi")
        self._referral(agent, Referral.Status.COMPLETED)
        self._referral(agent, Referral.Status.PENDING)
        self._referral(agent, Referral.Status.PENDING)

        row = self.admin.get_queryset(self.request).get(pk=agent.pk)

        # 150 000 dan 5% -- bitta sotib olgan mijoz uchun, ikkita kutayotgani
        # uchun emas.
        self.assertIn("7 500", self.admin.earned_display(row))

    @override_settings(REFERRAL_COMMISSION_TIERS="0:5000,2:6000")
    def test_the_rate_rises_and_earlier_clients_keep_theirs(self):
        """Panelda ko'rsatilgan summa hisobotda boshqacha chiqmasligi kerak --
        shuning uchun bu yerdagi qoida FastAPI'dagisi bilan bir xil."""

        agent = self._customer("agent-tier@example.com", "Pog'onali")
        for index in range(3):
            self._referral(agent, Referral.Status.COMPLETED, minutes=index)

        row = self.admin.get_queryset(self.request).get(pk=agent.pk)

        self.assertIn("16 000", self.admin.earned_display(row))
        self.assertEqual(self.admin.rate_display(row), "6\u00a0000 so'm")

    @override_settings(REFERRAL_COMMISSION_TIERS="0:5%,1:10%")
    def test_the_order_of_arrival_decides_who_gets_which_rate(self):
        """Kim oldin kelgan bo'lsa, o'sha paytdagi stavkani oladi. Tartib
        e'tiborga olinmasa, summa qaysi qator oldin o'qilishiga qarab
        o'zgarib turadi."""

        agent = self._customer("agent-order@example.com")
        self._referral(agent, Referral.Status.COMPLETED, paid_uzs="100000", minutes=0)
        self._referral(agent, Referral.Status.COMPLETED, paid_uzs="200000", minutes=5)

        row = self.admin.get_queryset(self.request).get(pk=agent.pk)

        # Birinchisi 100 000 dan 5% = 5 000, ikkinchisi 200 000 dan 10% = 20 000.
        self.assertIn("25 000", self.admin.earned_display(row))

    @override_settings(REFERRAL_COMMISSION_TIERS="0:5%")
    def test_an_order_without_a_som_amount_pays_nothing(self):
        """Eski buyurtmalarda so'm ustuni bo'sh; foizni yo'qdan hisoblab
        bo'lmaydi va to'qib chiqarilgan summadan ko'ra nol xavfsizroq."""

        agent = self._customer("agent-old@example.com")
        self._referral(agent, Referral.Status.COMPLETED, paid_uzs=None)

        row = self.admin.get_queryset(self.request).get(pk=agent.pk)

        self.assertIn("<b>0</b>", self.admin.earned_display(row))

    @override_settings(REFERRAL_COMMISSION_TIERS="besh foiz", REFERRAL_COMMISSION_UZS=6000)
    def test_a_broken_setting_falls_back_instead_of_breaking_the_page(self):
        agent = self._customer("agent-bad@example.com")
        self._referral(agent, Referral.Status.COMPLETED)

        row = self.admin.get_queryset(self.request).get(pk=agent.pk)

        self.assertIn("6 000", self.admin.earned_display(row))

    def test_one_query_per_page_not_one_per_agent(self):
        """Prefetch bo'lmasa har bir agent o'ziga alohida so'rov yuboradi va
        sahifa agentlar soni bilan sekinlashadi."""

        for index in range(4):
            agent = self._customer(f"many-{index}@example.com")
            self._referral(agent, Referral.Status.COMPLETED)

        rows = list(self.admin.get_queryset(self.request))
        with self.assertNumQueries(0):
            for row in rows:
                self.admin.earned_display(row)

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
