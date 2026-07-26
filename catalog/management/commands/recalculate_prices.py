"""Apply the markup rules across the catalogue.

Run this after the eSIM Access sync writes new supplier costs: the sync
records cost only, so prices do not move until the rules are applied.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from catalog.models import Plan, PricingRule


class Command(BaseCommand):
    help = "Recalculate plan prices from supplier cost and the active pricing rules."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report changes and stop.")
        parser.add_argument("--provider", help="Limit to one supplier, e.g. esimaccess.")

    def handle(self, *args, **options):
        rules = list(PricingRule.objects.filter(is_active=True))
        if not rules:
            self.stdout.write(
                self.style.WARNING("No active pricing rules — the built-in default applies.")
            )

        plans = Plan.objects.filter(price_locked=False, cost_usd__isnull=False)
        if options["provider"]:
            plans = plans.filter(provider=options["provider"])

        changed = []
        for plan in plans:
            before = plan.price_usd
            if plan.recalculate_price(rules):
                changed.append((plan, before))

        if options["dry_run"]:
            for plan, before in changed[:15]:
                self.stdout.write(f"  {plan.title}: ${before} -> ${plan.price_usd}")
            if len(changed) > 15:
                self.stdout.write(f"  ... and {len(changed) - 15} more")
            self.stdout.write(self.style.WARNING(f"Dry run: {len(changed)} price(s) would change."))
            return

        Plan.objects.bulk_update([p for p, _ in changed], ["price_usd"], batch_size=500)

        locked = Plan.objects.filter(price_locked=True).count()
        uncosted = Plan.objects.filter(cost_usd__isnull=True, is_active=True).count()
        self.stdout.write(self.style.SUCCESS(f"{len(changed)} price(s) updated."))
        if locked:
            self.stdout.write(f"  {locked} plan(s) skipped: price locked.")
        if uncosted:
            self.stdout.write(f"  {uncosted} active plan(s) skipped: no supplier cost.")
