"""Reading a wholesaler price list and turning it into plans and offers.

Shared by the `sync_supplier_prices` command and the admin import page, so the
two cannot disagree about what a price file means.

Everything here is a pure calculation over rows plus one explicit `apply`
function. That split is what lets the admin show a preview before writing:
`plan()` answers "what would change" without touching the database.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Iterable

from django.db import transaction

from catalog.models import Country, Plan, SupplierOffer

# The tariff ladder a destination gets when one is generated for it, matching
# what every hand-built destination already offers.
LADDER = [(1024, 7, "4G"), (3072, 15, "5G"), (5120, 30, "5G"), (10240, 30, "5G")]

REQUIRED_COLUMNS = {"package_code", "location", "data_gb", "days", "cost_usd"}


@dataclass
class ParsedPrices:
    """The cheapest package per (country, GB, days), plus what was unusable."""

    best: dict[tuple[str, float, int], tuple[str, Decimal]] = field(default_factory=dict)
    rows_read: int = 0
    rows_skipped: int = 0
    missing_columns: set[str] = field(default_factory=set)


def parse(text: str) -> ParsedPrices:
    reader = csv.DictReader(StringIO(text))
    out = ParsedPrices()
    out.missing_columns = REQUIRED_COLUMNS - set(reader.fieldnames or [])
    if out.missing_columns:
        return out

    for row in reader:
        out.rows_read += 1
        try:
            key = (
                row["location"].strip().upper(),
                round(float(row["data_gb"]), 2),
                int(float(row["days"] or 0)),
            )
            cost = Decimal(row["cost_usd"]).quantize(Decimal("0.01"))
            code = row["package_code"].strip()
        except (KeyError, ValueError, TypeError, InvalidOperation):
            out.rows_skipped += 1
            continue
        if not code or cost < 0:
            out.rows_skipped += 1
            continue
        # Cheapest wins: there is no reason to source from a wholesaler's own
        # dearer duplicate of the same package.
        if key not in out.best or cost < out.best[key][1]:
            out.best[key] = (code, cost)
    return out


@dataclass
class Change:
    country: str
    label: str
    kind: str  # "new-plan" | "new-offer" | "price-change" | "unchanged"
    package_code: str
    cost_usd: Decimal
    previous_cost: Decimal | None = None
    price_before: Decimal | None = None
    price_after: Decimal | None = None


def plan_changes(prices: ParsedPrices, provider: str, *, iso2: Iterable[str] | None = None) -> list[Change]:
    """What applying this file would do. Touches nothing.

    Restricted to `iso2` when given, so a destination can be set up on its own
    without repricing the whole catalogue as a side effect.
    """
    wanted = {code.upper() for code in iso2} if iso2 else None
    countries = {c.iso2.upper(): c for c in Country.objects.all() if c.iso2}
    changes: list[Change] = []

    for (loc, gb, days), (code, cost) in sorted(prices.best.items()):
        if wanted is not None and loc not in wanted:
            continue
        country = countries.get(loc)
        if country is None:
            continue
        mb = int(round(gb * 1024))
        if (mb, days, "5G") not in [(m, d, n) for m, d, n in LADDER] and (mb, days, "4G") not in [
            (m, d, n) for m, d, n in LADDER
        ]:
            # Only the rungs the catalogue actually sells; a wholesaler lists
            # hundreds of shapes nobody has a plan for.
            continue

        existing_plan = country.plans.filter(data_amount_mb=mb, validity_days=days).first()
        label = f"{country.name} {gb:g} GB · {days} days"
        if existing_plan is None:
            changes.append(Change(country.name, label, "new-plan", code, cost))
            continue

        offer = existing_plan.offers.filter(provider=provider).first()
        probe = _price_probe(existing_plan, provider, cost)
        if offer is None:
            changes.append(
                Change(country.name, label, "new-offer", code, cost,
                       price_before=existing_plan.price_usd, price_after=probe)
            )
        elif offer.cost_usd != cost:
            changes.append(
                Change(existing_plan.country.name, label, "price-change", code, cost,
                       previous_cost=offer.cost_usd,
                       price_before=existing_plan.price_usd, price_after=probe)
            )
        else:
            changes.append(Change(country.name, label, "unchanged", code, cost))
    return changes


def sourcing_after_uploads(plan: Plan, uploads: dict[str, Decimal]):
    """The (cost, provider) sourcing would settle on once these offers land.

    The uploaded prices are only entrants: on apply, resolve_sourcing picks
    the cheapest *available, connected* offer across every supplier, so a
    preview probing the uploaded cost alone promised price changes that apply
    then refused to make — a cheaper surviving offer keeps winning, and an
    upload for an unconnected supplier changes nothing at all.

    Takes a mapping rather than one (provider, cost) because
    sync_supplier_prices can feed both wholesalers to one plan in a single
    run, and its forecast has to weigh them together, exactly as apply will.

    Returns None when nothing connected would remain to source from, in which
    case apply leaves the plan's cost and price exactly as they are.
    """
    from catalog.models import fulfillable_providers

    usable = fulfillable_providers()
    candidates = [
        (offer.cost_usd, offer.provider)
        for offer in plan.offers.all()
        if offer.provider not in uploads and offer.is_available and offer.provider in usable
    ]
    candidates.extend(
        (cost, provider) for provider, cost in uploads.items() if provider in usable
    )
    if not candidates:
        return None
    # (cost, provider) — the same stable tie-break ranked_offers uses.
    return min(candidates)


def _sourcing_after_apply(plan: Plan, provider: str, cost: Decimal):
    return sourcing_after_uploads(plan, {provider: cost})


def price_probe_for_uploads(plan: Plan, uploads: dict[str, Decimal]) -> Decimal:
    """What the retail price would become after apply, without saving."""
    sourcing = sourcing_after_uploads(plan, uploads)
    if sourcing is None:
        return plan.price_usd

    winning_cost, winning_provider = sourcing
    probe = Plan(
        pk=plan.pk,
        country=plan.country,
        region=plan.region,
        title=plan.title,
        cost_usd=winning_cost,
        markup_percent=plan.markup_percent,
        price_usd=plan.price_usd,
        price_locked=plan.price_locked,
        # The provider matters too: a provider-scoped pricing rule prices the
        # plan by whoever actually wins, not by whoever was uploaded.
        provider=winning_provider,
    )
    probe.recalculate_price()
    return probe.price_usd


def _price_probe(plan: Plan, provider: str, cost: Decimal) -> Decimal:
    return price_probe_for_uploads(plan, {provider: cost})


@transaction.atomic
def apply(prices: ParsedPrices, provider: str, *, iso2: Iterable[str] | None = None) -> dict[str, int]:
    """Write the plans and offers this file describes."""
    wanted = {code.upper() for code in iso2} if iso2 else None
    countries = {c.iso2.upper(): c for c in Country.objects.all() if c.iso2}
    ladder_index = {(mb, days): (order, network) for order, (mb, days, network) in enumerate(LADDER)}
    made_plans = made_offers = 0

    for (loc, gb, days), (code, cost) in sorted(prices.best.items()):
        if wanted is not None and loc not in wanted:
            continue
        country = countries.get(loc)
        if country is None:
            continue
        mb = int(round(gb * 1024))
        rung = ladder_index.get((mb, days))
        if rung is None:
            continue
        order, network = rung

        plan, created = Plan.objects.get_or_create(
            country=country,
            data_amount_mb=mb,
            validity_days=days,
            defaults={
                "title": f"{country.name} {gb:g} GB · {days} days",
                "network_type": network,
                "price_usd": Decimal("0"),  # recalculated from cost on save
                "cost_usd": cost,
                "sort_order": order,
                # The middle rung is what most people buy and what the landing
                # page shows for a destination.
                "is_popular": (mb, days) == (3072, 15),
                "is_active": True,
            },
        )
        made_plans += int(created)
        _, offer_created = SupplierOffer.objects.update_or_create(
            plan=plan,
            provider=provider,
            defaults={"package_code": code, "cost_usd": cost, "is_available": True},
        )
        made_offers += int(offer_created)

    return {"plans_created": made_plans, "offers_created": made_offers}
