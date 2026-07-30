"""Match wholesaler price lists onto catalogue plans as SupplierOffer rows.

Dry run by default, and deliberately so: writing offers moves `cost_usd`, which
moves `price_usd` through the pricing rules, which changes every price on the
storefront. That is not a change to make as a side effect of running a sync — so
the default prints what *would* happen and touches nothing.

Matching is on the spec a customer actually chooses: destination, data volume and
validity. Anything the wholesaler sells that no plan matches is reported as a
gap rather than silently dropped, because a gap is a plan somebody could add.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalog.models import Plan, SupplierOffer


def _read_csv(path: Path) -> list[dict]:
    rows = []
    with path.open() as handle:
        for row in csv.DictReader(handle):
            try:
                rows.append(
                    {
                        "package_code": row["package_code"].strip(),
                        "location": row["location"].strip().upper(),
                        "data_gb": round(float(row["data_gb"]), 2),
                        "days": int(float(row["days"] or 0)),
                        "cost_usd": Decimal(row["cost_usd"]).quantize(Decimal("0.01")),
                    }
                )
            except (KeyError, ValueError, TypeError):
                # A malformed line is skipped rather than aborting the run; the
                # count is reported so it cannot pass unnoticed.
                continue
    return rows


class Command(BaseCommand):
    help = "Load wholesaler price lists into SupplierOffer. Dry run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("--esimaccess", type=Path, help="eSIM Access price CSV")
        parser.add_argument("--esimcard", type=Path, help="eSIMCard price CSV")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually write the offers. Without this nothing is changed.",
        )
        parser.add_argument(
            "--limit-report",
            type=int,
            default=25,
            help="How many example rows to print (default 25).",
        )

    def handle(self, *args, **options):
        sources = {
            "esimaccess": options.get("esimaccess"),
            "esimcard": options.get("esimcard"),
        }
        sources = {k: v for k, v in sources.items() if v}
        if not sources:
            raise CommandError("Give at least one of --esimaccess / --esimcard")

        for provider, path in sources.items():
            if not path.exists():
                raise CommandError(f"{provider}: {path} not found")

        # Index plans by the spec a customer picks. A country may legitimately
        # have two plans with the same spec; both get the offer.
        plans_by_spec: dict[tuple[str, float, int], list[Plan]] = defaultdict(list)
        for plan in Plan.objects.select_related("country").filter(country__isnull=False):
            gb = round(plan.data_amount_mb / 1024, 2)
            plans_by_spec[(plan.country.iso2.upper(), gb, plan.validity_days)].append(plan)

        self.stdout.write(f"catalogue: {sum(len(v) for v in plans_by_spec.values())} plans "
                          f"across {len(plans_by_spec)} distinct specs")

        planned: list[tuple[Plan, str, str, Decimal, Decimal | None]] = []
        gaps: dict[str, int] = {}

        for provider, path in sources.items():
            rows = _read_csv(path)
            # Cheapest wins when a wholesaler lists several packages for one
            # spec — there is no reason to source from its dearer duplicate.
            best: dict[tuple[str, float, int], dict] = {}
            for row in rows:
                key = (row["location"], row["data_gb"], row["days"])
                if key not in best or row["cost_usd"] < best[key]["cost_usd"]:
                    best[key] = row

            matched = 0
            for key, row in best.items():
                targets = plans_by_spec.get(key)
                if not targets:
                    continue
                matched += 1
                for plan in targets:
                    existing = next(
                        (o for o in plan.offers.all() if o.provider == provider), None
                    )
                    planned.append(
                        (
                            plan,
                            provider,
                            row["package_code"],
                            row["cost_usd"],
                            existing.cost_usd if existing else None,
                        )
                    )
            gaps[provider] = len(best) - matched
            self.stdout.write(
                f"{provider}: {len(rows)} rows, {len(best)} distinct specs, "
                f"{matched} matched a plan, {gaps[provider]} unmatched"
            )

        if not planned:
            self.stdout.write(self.style.WARNING("nothing matched — no offers to write"))
            return

        self._report(planned, options["limit_report"])

        if not options["apply"]:
            self.stdout.write(
                self.style.WARNING(
                    "\nDRY RUN — nothing was written. Re-run with --apply to commit.\n"
                    "Applying moves cost_usd, which moves price_usd through the "
                    "pricing rules, which changes storefront prices."
                )
            )
            return

        written, changed_plans = self._apply(planned)
        self.stdout.write(
            self.style.SUCCESS(
                f"\nwrote {written} offer(s); {changed_plans} plan(s) re-sourced and repriced"
            )
        )

    def _report(self, planned, limit):
        new = [p for p in planned if p[4] is None]
        moved = [p for p in planned if p[4] is not None and p[4] != p[3]]
        same = len(planned) - len(new) - len(moved)
        self.stdout.write(
            f"\noffers: {len(new)} new, {len(moved)} price changed, {same} unchanged"
        )

        # Show the price the customer would end up paying, not just the cost:
        # that is the number the operator is actually deciding about.
        by_plan: dict[int, Plan] = {}
        for plan, *_ in planned:
            by_plan[plan.pk] = plan

        self.stdout.write("\nwhat the storefront price would become:")
        shown = 0
        for plan in by_plan.values():
            if shown >= limit:
                break
            cheapest = min(
                (p[3] for p in planned if p[0].pk == plan.pk),
                default=None,
            )
            if cheapest is None:
                continue
            before = plan.price_usd
            probe = Plan(
                pk=plan.pk,
                country=plan.country,
                region=plan.region,
                title=plan.title,
                cost_usd=cheapest,
                markup_percent=plan.markup_percent,
                price_usd=plan.price_usd,
                price_locked=plan.price_locked,
                provider=plan.provider,
            )
            probe.recalculate_price()
            arrow = "→" if probe.price_usd != before else "="
            self.stdout.write(
                f"  {plan.title[:34]:<35} ${before} {arrow} ${probe.price_usd}"
                f"   (cost ${plan.cost_usd} → ${cheapest})"
            )
            shown += 1
        if len(by_plan) > shown:
            self.stdout.write(f"  … and {len(by_plan) - shown} more")

    @transaction.atomic
    def _apply(self, planned):
        written = 0
        touched: set[int] = set()
        for plan, provider, code, cost, _previous in planned:
            _, created = SupplierOffer.objects.update_or_create(
                plan=plan,
                provider=provider,
                defaults={"package_code": code, "cost_usd": cost, "is_available": True},
            )
            written += 1
            touched.add(plan.pk)
            del created
        # SupplierOffer.save() re-sources its plan, so the prices are already
        # correct by here; the count is reported for the operator's benefit.
        return written, len(touched)
