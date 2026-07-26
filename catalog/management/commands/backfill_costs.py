"""Derive a supplier cost for plans that predate the pricing engine.

Existing rows have a selling price but no cost, so margin reporting shows
nothing and a recalculation would refuse to run. This works backwards from the
current price at an assumed markup, leaving every displayed price unchanged.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from django.core.management.base import BaseCommand

from catalog.models import Plan


class Command(BaseCommand):
    help = "Set cost_usd on plans that have none, derived from the current price."

    def add_arguments(self, parser):
        parser.add_argument(
            "--markup",
            type=Decimal,
            default=Decimal("30"),
            help="Markup the current prices are assumed to already include (default 30).",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Report what would change and stop."
        )

    def handle(self, *args, **options):
        markup: Decimal = options["markup"]
        dry_run: bool = options["dry_run"]
        divisor = Decimal("1") + markup / Decimal("100")

        plans = Plan.objects.filter(cost_usd__isnull=True)
        total = plans.count()
        if not total:
            self.stdout.write(self.style.SUCCESS("Every plan already has a cost."))
            return

        updated = []
        for plan in plans:
            cost = (plan.price_usd / divisor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            plan.cost_usd = cost
            updated.append(plan)

        if dry_run:
            for plan in updated[:10]:
                self.stdout.write(f"  {plan.title}: ${plan.price_usd} -> cost ${plan.cost_usd}")
            if total > 10:
                self.stdout.write(f"  ... and {total - 10} more")
            self.stdout.write(self.style.WARNING(f"Dry run: {total} plan(s) would change."))
            return

        # bulk_update skips save(), so no price is recalculated — the point of
        # this command is to record cost while leaving prices exactly as they are.
        Plan.objects.bulk_update(updated, ["cost_usd"], batch_size=500)
        self.stdout.write(self.style.SUCCESS(f"Backfilled cost on {total} plan(s)."))
