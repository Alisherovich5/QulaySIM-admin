"""Cache invalidation for CMS content.

Written because the receivers here silently stopped working: each was a lambda
registered in a loop, and Django holds signal receivers weakly by default, so
once the garbage collector ran they vanished. Nothing errored — the site just
kept serving the previous content until the key expired, which to an admin looks
like the edit did not save.
"""

from __future__ import annotations

import gc
from decimal import Decimal
from unittest.mock import patch

from django.db.models.signals import post_delete, post_save
from django.test import TestCase, override_settings

from content.models import FAQ, Benefit, Device, PromoBanner, Testimonial
from orders.models import PromoCode


class ContentCacheInvalidationTests(TestCase):
    WATCHED = (Benefit, Testimonial, Device, FAQ, PromoBanner, PromoCode)

    def _saved_with_cache_cleared(self, make):
        # The signal defers to transaction.on_commit so the API cannot re-cache
        # stale content between the Redis delete and COMMIT; a TestCase never
        # commits, so the deferred callbacks are executed explicitly here.
        with patch("content.signals.invalidate_content") as invalidate:
            with self.captureOnCommitCallbacks(execute=True):
                make()
            return invalidate.called

    def test_receivers_survive_garbage_collection(self):
        # The original bug in one assertion: a weakly-held lambda is collected,
        # and the signal quietly stops firing.
        gc.collect()
        for model in self.WATCHED:
            with self.subTest(model=model.__name__):
                self.assertTrue(
                    post_save._live_receivers(model),
                    f"{model.__name__} lost its post_save receiver after gc",
                )
                self.assertTrue(post_delete._live_receivers(model))

    def test_saving_a_faq_clears_the_cache(self):
        self.assertTrue(
            self._saved_with_cache_cleared(
                lambda: FAQ.objects.create(question="Q?", answer="A.")
            )
        )

    def test_saving_a_promo_banner_clears_the_cache(self):
        self.assertTrue(
            self._saved_with_cache_cleared(
                lambda: PromoBanner.objects.create(eyebrow="E", title="T", text="X")
            )
        )

    def test_changing_the_promo_code_clears_the_cache(self):
        code = PromoCode.objects.create(
            code="SAVE20", discount_type="fixed", discount_value=Decimal("20")
        )

        def change():
            code.discount_value = Decimal("25")
            code.save()

        # The landing payload advertises this discount, so a change to it must
        # reach the site — otherwise the bar shows 20 while checkout takes 25.
        self.assertTrue(self._saved_with_cache_cleared(change))

    def test_deleting_content_clears_the_cache(self):
        faq = FAQ.objects.create(question="Q?", answer="A.")
        self.assertTrue(self._saved_with_cache_cleared(faq.delete))


class PromoBannerDiscountTests(TestCase):
    """The advertised figure comes from the code that applies it."""

    def test_a_banner_can_be_linked_to_a_code(self):
        code = PromoCode.objects.create(
            code="SAVE20", discount_type="fixed", discount_value=Decimal("20")
        )
        banner = PromoBanner.objects.create(
            eyebrow="E", title="T", text="{{code}}", promo_code=code
        )
        banner.refresh_from_db()
        self.assertEqual(banner.promo_code.discount_value, Decimal("20"))
        self.assertEqual(banner.promo_code.discount_type, "fixed")

    def test_deleting_the_code_leaves_the_banner_standing(self):
        code = PromoCode.objects.create(
            code="SAVE20", discount_type="percent", discount_value=Decimal("10")
        )
        banner = PromoBanner.objects.create(
            eyebrow="E", title="T", text="X", promo_code=code
        )
        code.delete()
        banner.refresh_from_db()
        # SET_NULL, not CASCADE: losing a promo code must not delete the banner
        # and take the landing section down with it.
        self.assertIsNone(banner.promo_code)
        self.assertTrue(PromoBanner.objects.filter(pk=banner.pk).exists())


class MoneyLabelTests(TestCase):
    """The figure an operator reads before deciding what to advertise.

    Decimal("10.00") formats as "10.00" under :g and as "1e+1" after normalize(),
    so the obvious shortcuts both produce something that reads like a mistake.
    """

    def test_whole_numbers_lose_their_zeros(self):
        from decimal import Decimal

        from content.admin import _money_label

        self.assertEqual(_money_label(Decimal("10.00")), "10")
        self.assertEqual(_money_label(Decimal("20.00")), "20")

    def test_no_scientific_notation(self):
        from decimal import Decimal

        from content.admin import _money_label

        # normalize() turns 20.00 into 1e+1-style output; this must not.
        self.assertNotIn("e", _money_label(Decimal("20.00")).lower())

    def test_real_cents_survive(self):
        from decimal import Decimal

        from content.admin import _money_label

        self.assertEqual(_money_label(Decimal("10.50")), "10.5")
        self.assertEqual(_money_label(Decimal("0.99")), "0.99")

    def test_zero_stays_zero(self):
        from decimal import Decimal

        from content.admin import _money_label

        # "0.00" would strip to "" without the guard.
        self.assertEqual(_money_label(Decimal("0.00")), "0")


@override_settings(SECURE_SSL_REDIRECT=False)
@override_settings(LANGUAGE_CODE="en")
class TestimonialModerationActionTests(TestCase):
    """The bulk actions used to update through the queryset — no post_save, no
    invalidation — so approvals sat invisible for the whole cache TTL, reading
    as "my edit did not save". Approve also force-republished rows an admin had
    deliberately deactivated."""

    def setUp(self):
        from django.contrib.auth.models import User
        from django.urls import reverse

        User.objects.create_superuser("mod-admin", "m@example.com", "pw-for-tests-only")
        self.client.force_login(User.objects.get(username="mod-admin"))
        self.url = reverse("admin:content_testimonial_changelist")

    def _review(self, **kwargs):
        defaults = {
            "name": "Aziza",
            "location": "Tashkent → Tokyo",
            "text": "Worked at the gate.",
            "moderation_status": Testimonial.ModerationStatus.PENDING,
        }
        defaults.update(kwargs)
        return Testimonial.objects.create(**defaults)

    def _act(self, action, *rows):
        return self.client.post(
            self.url,
            {
                "action": action,
                "_selected_action": [str(row.pk) for row in rows],
                "index": 0,
            },
            follow=True,
        )

    def test_approving_clears_the_landing_cache(self):
        row = self._review()
        with patch("content.admin.invalidate_content") as invalidate:
            with self.captureOnCommitCallbacks(execute=True):
                self._act("approve_reviews", row)
        self.assertTrue(invalidate.called)
        row.refresh_from_db()
        self.assertEqual(row.moderation_status, Testimonial.ModerationStatus.APPROVED)

    def test_rejecting_clears_the_landing_cache(self):
        row = self._review(moderation_status=Testimonial.ModerationStatus.APPROVED)
        with patch("content.admin.invalidate_content") as invalidate:
            with self.captureOnCommitCallbacks(execute=True):
                self._act("reject_reviews", row)
        self.assertTrue(invalidate.called)
        row.refresh_from_db()
        self.assertEqual(row.moderation_status, Testimonial.ModerationStatus.REJECTED)

    def test_approving_does_not_republish_a_deactivated_review(self):
        row = self._review(is_active=False)
        self._act("approve_reviews", row)
        row.refresh_from_db()
        self.assertEqual(row.moderation_status, Testimonial.ModerationStatus.APPROVED)
        self.assertFalse(row.is_active, "a deliberate deactivation must survive approval")

    def test_approving_a_fresh_submission_publishes_it(self):
        # Submissions arrive with is_active=True, so approval alone goes live.
        row = self._review()
        self._act("approve_reviews", row)
        row.refresh_from_db()
        self.assertTrue(row.is_active)
        self.assertEqual(row.moderation_status, Testimonial.ModerationStatus.APPROVED)
