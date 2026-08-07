"""The assistant proposes; the server decides; a person approves.

These tests are mostly about the second and third clauses. The model is stubbed
throughout — what is under test is not whether Claude understands Uzbek, it is
that a *wrong* answer from it cannot reach a live price:

  * a markup outside the band, a destination that does not exist, a size nothing
    carries, or more rules than the cap are all refused after the model has
    spoken, in code it never sees;
  * the preview is the real pricing code on the real catalogue, so what it shows
    is what approving produces — not an approximation of it;
  * asking never writes. Only a second, explicit POST does, and that POST
    re-validates from scratch rather than trusting what came back from the page.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog import ai_pricing
from catalog.models import Country, Plan, PricingRule


def answer(rules, *, understood="Tushundim", confident=True, question=""):
    """The shape structured outputs guarantees, so the stub can be exact."""
    return {
        "understood": understood,
        "confident": confident,
        "question": question,
        "rules": [
            {
                "scope": "tier",
                "country_iso2": "",
                "provider": "",
                "tier_data_mb": 0,
                "tier_days": 0,
                "markup_percent": 0,
                "min_margin_usd": 0,
                "rounding": "none",
                "note": "",
                "reason": "",
                **rule,
            }
            for rule in rules
        ],
    }


class ValidationTests(TestCase):
    """The rails, checked after the model has answered."""

    def setUp(self):
        self.turkey = Country.objects.create(name="Turkey", name_uz="Turkiya", slug="tr", iso2="TR")
        Plan.objects.create(
            country=self.turkey, title="TR 1 GB", data_amount_mb=1024, validity_days=7,
            cost_usd=Decimal("1.00"), price_usd=Decimal("0"),
            provider="esimaccess", provider_package_code="TR-1",
        )

    def test_a_sound_tier_rule_passes(self):
        [proposal] = ai_pricing.validate(
            answer([{"scope": "tier", "tier_data_mb": 1024, "markup_percent": 200}])
        )
        self.assertTrue(proposal.ok)
        self.assertEqual(proposal.markup_percent, Decimal("200.00"))

    def test_a_markup_above_the_ceiling_is_refused(self):
        # A misplaced decimal point is the realistic version of this: 5000
        # instead of 500 prices a $1 tariff at $51.
        [proposal] = ai_pricing.validate(
            answer([{"scope": "tier", "tier_data_mb": 1024, "markup_percent": 99999}])
        )
        self.assertFalse(proposal.ok)
        self.assertIn("0", str(proposal.problem))

    def test_a_negative_markup_is_refused(self):
        [proposal] = ai_pricing.validate(
            answer([{"scope": "tier", "tier_data_mb": 1024, "markup_percent": -20}])
        )
        self.assertFalse(proposal.ok)

    def test_a_destination_that_does_not_exist_is_refused(self):
        # "MC" is a real ISO code and not one we sell; a hallucinated country
        # would otherwise create a rule that governs nothing forever.
        [proposal] = ai_pricing.validate(
            answer([{"scope": "country", "country_iso2": "MC", "markup_percent": 50}])
        )
        self.assertFalse(proposal.ok)

    def test_a_known_destination_is_resolved_to_its_row(self):
        [proposal] = ai_pricing.validate(
            answer([{"scope": "country", "country_iso2": "tr", "markup_percent": 50}])
        )
        self.assertTrue(proposal.ok)
        self.assertEqual(proposal.country_id, self.turkey.id)
        self.assertEqual(proposal.country_name, "Turkiya")

    def test_a_size_no_tariff_carries_is_refused(self):
        [proposal] = ai_pricing.validate(
            answer([{"scope": "tier", "tier_data_mb": 2_097_152, "markup_percent": 50}])
        )
        self.assertFalse(proposal.ok)

    def test_a_supplier_we_do_not_buy_from_is_refused(self):
        [proposal] = ai_pricing.validate(
            answer([{"scope": "provider", "provider": "airalo", "markup_percent": 50}])
        )
        self.assertFalse(proposal.ok)

    def test_an_unknown_scope_is_refused(self):
        [proposal] = ai_pricing.validate(answer([{"scope": "everything", "markup_percent": 50}]))
        self.assertFalse(proposal.ok)

    def test_an_unknown_rounding_mode_is_refused(self):
        [proposal] = ai_pricing.validate(
            answer([{"scope": "tier", "tier_data_mb": 1024, "markup_percent": 50,
                     "rounding": "nearest_hundred"}])
        )
        self.assertFalse(proposal.ok)

    def test_more_rules_than_the_cap_are_cut_off(self):
        many = [
            {"scope": "tier", "tier_data_mb": 1024, "markup_percent": 50}
            for _ in range(ai_pricing.MAX_RULES + 15)
        ]
        self.assertEqual(len(ai_pricing.validate(answer(many))), ai_pricing.MAX_RULES)

    def test_a_refused_rule_is_kept_and_labelled_rather_than_dropped(self):
        # An operator who named five destinations and sees four listed should be
        # told which one was refused, not left to count.
        proposals = ai_pricing.validate(
            answer([
                {"scope": "tier", "tier_data_mb": 1024, "markup_percent": 200},
                {"scope": "country", "country_iso2": "ZZ", "markup_percent": 50},
            ])
        )
        self.assertEqual(len(proposals), 2)
        self.assertTrue(proposals[0].ok)
        self.assertFalse(proposals[1].ok)
        self.assertTrue(str(proposals[1].problem))


class PreviewTests(TestCase):
    """What the preview promises has to be what approving delivers."""

    def setUp(self):
        PricingRule.objects.create(scope=PricingRule.Scope.GLOBAL, markup_percent=Decimal("50"))
        self.turkey = Country.objects.create(name="Turkey", name_uz="Turkiya", slug="tr", iso2="TR")
        self.japan = Country.objects.create(name="Japan", name_uz="Yaponiya", slug="jp", iso2="JP")
        self.tr = self._plan(self.turkey, 1024, "1.00")
        self.jp = self._plan(self.japan, 1024, "2.00")
        self.big = self._plan(self.turkey, 5120, "10.00")

    def _plan(self, country, mb, cost):
        return Plan.objects.create(
            country=country, title=f"{country.iso2} {mb}", data_amount_mb=mb, validity_days=7,
            cost_usd=Decimal(cost), price_usd=Decimal("0"),
            provider="esimaccess", provider_package_code=f"{country.iso2}-{mb}",
        )

    def _proposals(self, rules):
        return ai_pricing.validate(answer(rules))

    def test_the_preview_names_every_tariff_that_moves_and_no_others(self):
        result = ai_pricing.preview(
            self._proposals([{"scope": "tier", "tier_data_mb": 1024, "markup_percent": 200}])
        )
        moved = {change.plan_id for change in result.changes}
        self.assertEqual(moved, {self.tr.id, self.jp.id})
        self.assertEqual(result.unchanged, 1)

    def test_the_preview_prices_are_the_prices_approving_produces(self):
        # The point of the whole design: the preview runs the same code the save
        # will, so an operator approves a number they have actually seen.
        proposals = self._proposals(
            [{"scope": "tier", "tier_data_mb": 1024, "markup_percent": 200}]
        )
        promised = {c.plan_id: c.after for c in ai_pricing.preview(proposals).changes}

        ai_pricing.apply(proposals, actor="test")
        for plan_id, price in promised.items():
            self.assertEqual(Plan.objects.get(pk=plan_id).price_usd, price)

    def test_previewing_writes_nothing(self):
        before = self.tr.price_usd
        ai_pricing.preview(
            self._proposals([{"scope": "tier", "tier_data_mb": 1024, "markup_percent": 900}])
        )
        self.tr.refresh_from_db()
        self.assertEqual(self.tr.price_usd, before)
        self.assertEqual(PricingRule.objects.count(), 1)

    def test_a_hand_typed_price_is_reported_as_untouched_not_as_a_change(self):
        self.tr.price_usd = Decimal("9.99")
        self.tr.price_locked = True
        self.tr.save()

        result = ai_pricing.preview(
            self._proposals([{"scope": "tier", "tier_data_mb": 1024, "markup_percent": 300}])
        )
        self.assertEqual(result.locked_skipped, 1)
        self.assertNotIn(self.tr.id, [c.plan_id for c in result.changes])

    def test_the_preview_replaces_a_rule_it_would_overwrite_rather_than_stacking(self):
        # Saving a tier rule replaces the tier rule already there, so the preview
        # has to model the replacement or it shows the old rule still winning.
        PricingRule.objects.create(
            scope=PricingRule.Scope.TIER, tier_data_mb=1024, markup_percent=Decimal("100")
        )
        result = ai_pricing.preview(
            self._proposals([{"scope": "tier", "tier_data_mb": 1024, "markup_percent": 400}])
        )
        after = {c.plan_id: c.after for c in result.changes}
        self.assertEqual(after[self.tr.id], Decimal("5.00"))

    def test_the_summed_margin_change_is_per_unit_and_signed(self):
        result = ai_pricing.preview(
            self._proposals([{"scope": "tier", "tier_data_mb": 1024, "markup_percent": 200}])
        )
        # TR: $1.50 -> $3.00 margin $0.50 -> $2.00; JP: $3.00 -> $6.00, $1 -> $4.
        self.assertEqual(result.margin_delta, Decimal("4.50"))
        self.assertEqual(result.raised, 2)
        self.assertEqual(result.lowered, 0)

    def test_a_markup_cut_is_shown_as_prices_going_down(self):
        result = ai_pricing.preview(
            self._proposals([{"scope": "tier", "tier_data_mb": 1024, "markup_percent": 10}])
        )
        self.assertEqual(result.lowered, 2)
        self.assertLess(result.margin_delta, 0)

    def test_a_refused_rule_never_reaches_the_preview(self):
        result = ai_pricing.preview(
            self._proposals([{"scope": "country", "country_iso2": "ZZ", "markup_percent": 999999}])
        )
        self.assertEqual(result.changes, [])
        self.assertEqual(len(result.rejected), 1)

    def test_applying_nothing_usable_writes_nothing(self):
        proposals = self._proposals([{"scope": "country", "country_iso2": "ZZ", "markup_percent": 50}])
        self.assertEqual(ai_pricing.apply(proposals, actor="test"), 0)
        self.assertEqual(PricingRule.objects.count(), 1)

    def test_applying_twice_updates_the_rule_instead_of_colliding(self):
        # The unique constraints make a second insert an IntegrityError; two runs
        # of the same instruction has to be an ordinary thing to do.
        first = self._proposals([{"scope": "tier", "tier_data_mb": 1024, "markup_percent": 200}])
        second = self._proposals([{"scope": "tier", "tier_data_mb": 1024, "markup_percent": 300}])
        ai_pricing.apply(first, actor="test")
        ai_pricing.apply(second, actor="test")

        rules = PricingRule.objects.filter(scope=PricingRule.Scope.TIER)
        self.assertEqual(rules.count(), 1)
        self.assertEqual(rules.first().markup_percent, Decimal("300.00"))


class NoKeyTests(TestCase):
    @override_settings(ANTHROPIC_API_KEY="")
    def test_a_missing_key_is_a_clear_message_not_a_stack_trace(self):
        with self.assertRaises(ai_pricing.AiUnavailable):
            ai_pricing.ask("1 GB tariflarga 200% quy")


@override_settings(SECURE_SSL_REDIRECT=False, ANTHROPIC_API_KEY="test-key")
class AssistantPageTests(TestCase):
    def setUp(self):
        PricingRule.objects.create(scope=PricingRule.Scope.GLOBAL, markup_percent=Decimal("50"))
        self.turkey = Country.objects.create(name="Turkey", name_uz="Turkiya", slug="tr", iso2="TR")
        self.plan = Plan.objects.create(
            country=self.turkey, title="TR 1 GB", data_amount_mb=1024, validity_days=7,
            cost_usd=Decimal("1.00"), price_usd=Decimal("0"),
            provider="esimaccess", provider_package_code="TR-1",
        )
        self.client.force_login(
            get_user_model().objects.create_superuser("ai", "a@x.uz", "Pw-1234-abcd")
        )
        self.url = reverse("admin:catalog_pricingrule_assistant")

    def test_it_opens(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_it_leaks_no_template_commentary(self):
        body = self.client.get(self.url).content.decode()
        self.assertNotIn("{#", body)
        self.assertNotIn("ai_pricing.py", body)

    def test_asking_shows_the_preview_and_saves_nothing(self):
        payload = answer(
            [{"scope": "tier", "tier_data_mb": 1024, "markup_percent": 200}],
            understood="1 GB tariflar 200% ustama bilan sotiladi",
        )
        with patch("catalog.ai_pricing.ask", return_value=payload):
            response = self.client.post(self.url, {"instruction": "1 GB ga 200%"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1 GB tariflar 200% ustama bilan sotiladi")
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.price_usd, Decimal("1.50"))
        self.assertEqual(PricingRule.objects.filter(scope=PricingRule.Scope.TIER).count(), 0)

    def test_approving_is_a_second_deliberate_post(self):
        import json

        payload = answer([{"scope": "tier", "tier_data_mb": 1024, "markup_percent": 200}])
        response = self.client.post(
            self.url,
            {"step": "apply", "payload": json.dumps(payload), "instruction": "1 GB ga 200%"},
        )
        self.assertEqual(response.status_code, 302)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.price_usd, Decimal("3.00"))

    def test_approving_re_validates_rather_than_trusting_the_form(self):
        # The hidden field is operator input like any other. A rule that was
        # refused when it was proposed has to stay refused when it comes back,
        # even if the value in the form has been edited in the browser.
        import json

        payload = answer([{"scope": "tier", "tier_data_mb": 1024, "markup_percent": 99999}])
        self.client.post(
            self.url, {"step": "apply", "payload": json.dumps(payload), "instruction": "x"}
        )
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.price_usd, Decimal("1.50"))
        self.assertEqual(PricingRule.objects.filter(scope=PricingRule.Scope.TIER).count(), 0)

    def test_a_corrupt_payload_is_a_message_not_a_crash(self):
        response = self.client.post(
            self.url, {"step": "apply", "payload": "{not json", "instruction": "x"}
        )
        self.assertEqual(response.status_code, 302)

    def test_an_unreachable_assistant_leaves_the_page_usable(self):
        with patch(
            "catalog.ai_pricing.ask", side_effect=ai_pricing.AiUnavailable("network is down")
        ):
            response = self.client.post(self.url, {"instruction": "1 GB ga 200%"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "network is down")

    def test_staff_without_permission_cannot_reprice_the_catalogue(self):
        viewer = get_user_model().objects.create_user("viewer", "v@x.uz", "Pw-1234-abcd")
        viewer.is_staff = True
        viewer.save()
        self.client.force_login(viewer)

        response = self.client.get(self.url)
        self.assertIn(response.status_code, (302, 403))

    def test_a_signed_out_visitor_cannot_reach_it(self):
        self.client.logout()
        self.assertIn(self.client.get(self.url).status_code, (302, 403))
