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

from django.db import models, transaction

from catalog.models import Country, Plan, SupplierOffer

# The tariff ladder a destination gets when one is generated for it.
#
# Not invented: every rung is a shape both wholesalers actually stock in almost
# every country, measured against the live eSIM Access catalogue (2 908 packages,
# 193 countries). The count after each rung is how many countries offer it.
#
# The ladder exists because the suppliers between them list 24 different shapes,
# including 1-day packages and a 0.49 GB oddity, and putting all of them on a
# destination page gives a customer a wall of near-identical choices instead of a
# decision. Seven rungs is a menu; twenty-four is a spreadsheet.
#
# What it must never do is drop a shape silently, which is what it used to do —
# `ParsedPrices.off_ladder` now records every package the ladder had no rung for,
# so widening it is a decision someone makes from evidence.
#: The ladder as it shipped, and now only the seed for the table that replaced it.
#:
#: Kept in code for one reason: an empty table must not silently stop the
#: catalogue from importing. `rungs()` falls back to this, so a fresh database
#: behaves exactly as this file used to before the data migration runs.
DEFAULT_LADDER = [
    (1024, 7, "4G"),     # 1 GB / 7 days   — 204 countries
    (3072, 15, "5G"),    # 3 GB / 15 days  — 204
    (3072, 30, "5G"),    # 3 GB / 30 days  — 194
    (5120, 30, "5G"),    # 5 GB / 30 days  — 202
    (10240, 30, "5G"),   # 10 GB / 30 days — 197
    (20480, 30, "5G"),   # 20 GB / 30 days — 196
    (51200, 30, "5G"),   # 50 GB / 30 days — 40, the heavy-user rung
]

#: The one region whose page carries every shape the wholesalers sell rather
#: than the ladder a destination page gets.
GLOBAL_SLUG = "global"


#: One read of a tiny table per country per import, instead of one per package.
#: A supplier file carries thousands of rows; the table has a handful. Cleared
#: whenever a rung is edited (see catalog/signals.py) so the admin's change
#: takes effect on the next import rather than the next restart.
_RUNGS_CACHE: dict[str | None, list[tuple[int, int, str]]] = {}


def reset_rungs_cache() -> None:
    _RUNGS_CACHE.clear()


def rungs(iso2: str | None = None) -> list[tuple[int, int, str]]:
    """The shapes we are willing to sell, in the order the customer reads them.

    Read from `SellableShape` so widening the ladder is a row in the admin rather
    than a commit and a deploy — see that model for why. A rung naming a country
    applies only there; a rung naming none applies everywhere.

    Cached per import run by the caller, not here: this is called once per
    package and the table is tiny, but a supplier file has thousands of rows.
    """
    from catalog.models import SellableShape

    key = iso2.upper() if iso2 else None
    if key in _RUNGS_CACHE:
        return _RUNGS_CACHE[key]

    query = SellableShape.objects.filter(is_active=True)
    if iso2:
        query = query.filter(models.Q(country__isnull=True) | models.Q(country__iso2=iso2.upper()))
    else:
        query = query.filter(country__isnull=True)

    found = [
        (shape.data_mb, shape.days, shape.network)
        for shape in query.order_by("sort_order", "data_mb", "days")
    ]
    if not found:
        # "Not configured yet" and "configured to sell less" are different
        # answers, and the fallback must only cover the first. Deactivating a
        # rung used to silently restore the whole shipped ladder — caught by
        # test_an_inactive_rung_stops_being_sold_without_being_deleted, which is
        # the operator's expectation: what they switch off stays off.
        found = [] if SellableShape.objects.exists() else list(DEFAULT_LADDER)

    _RUNGS_CACHE[key] = found
    return found


def on_ladder(megabytes: int, days: int, iso2: str | None = None) -> bool:
    """Whether a package shape has a rung, and so becomes a sellable plan."""
    return any((mb, d) == (megabytes, days) for mb, d, _ in rungs(iso2))


def ladder_rung(megabytes: int, days: int, iso2: str | None = None) -> tuple[int, str] | None:
    """(sort order, network) for a shape, or None when it has no rung."""
    for order, (mb, plan_days, network) in enumerate(rungs(iso2)):
        if (mb, plan_days) == (megabytes, days):
            return order, network
    return None


REQUIRED_COLUMNS = {"package_code", "location", "data_gb", "days", "cost_usd"}


@dataclass
class ParsedPrices:
    """The cheapest package per (country, GB, days), plus what was unusable."""

    best: dict[tuple[str, float, int], tuple[str, Decimal]] = field(default_factory=dict)
    rows_read: int = 0
    rows_skipped: int = 0
    missing_columns: set[str] = field(default_factory=set)
    # (GB, days) -> how many packages had that shape and no rung to sit on.
    # Reported rather than discarded: a supplier adding a popular new size used
    # to vanish here without a trace.
    off_ladder: dict[tuple[float, int], int] = field(default_factory=dict)


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
        if not on_ladder(mb, days, loc):
            # Only the rungs the catalogue actually sells; a wholesaler lists
            # two dozen shapes, most of them near-duplicates. What was dropped is
            # recorded in prices.off_ladder rather than lost.
            key = (gb, days)
            prices.off_ladder[key] = prices.off_ladder.get(key, 0) + 1
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


def apply_regional(
    regional: dict, provider: str
) -> dict[str, int]:
    """Write the multi-country tariffs a supplier sells, one per region.

    Separate from apply() because the shape is genuinely different: these hang
    off a region rather than a country, and there is no per-country ladder to
    match — a "Europe 5GB / 30 days" is the whole product.

    Same ladder rule though. A wholesaler lists two dozen regional shapes and
    putting all of them on one page gives a customer a wall of near-identical
    choices; the rungs are the ones almost every destination already offers, so
    the regional menu reads like the local one.

    `coverage_count` goes into the title because it is the reason to buy: "41
    countries" is the product, and a plan called just "Europe 5GB" makes a
    customer guess whether their stop is included.
    """
    from catalog.models import Region

    regions = {r.slug: r for r in Region.objects.all()}
    ladder_index = {(mb, days): (order, network) for order, (mb, days, network) in enumerate(rungs())}
    made_plans = made_offers = 0

    for (region_slug, gb, days), offer in sorted(regional.items()):
        code, cost, coverage = offer.package_code, offer.cost_usd, offer.coverage
        region = regions.get(region_slug)
        if region is None:
            continue
        mb = int(round(gb * 1024))
        rung = ladder_index.get((mb, days))
        is_global = region_slug == GLOBAL_SLUG

        # The ladder is a per-destination idea: every country offers roughly the
        # same seven shapes, so seven rungs keep a destination page a menu rather
        # than a spreadsheet. The worldwide page is the opposite case — it is one
        # page with one product family, the shapes are genuinely different
        # (1 to 100 GB, 1 to 365 days, 66 to 167 countries), and a customer
        # arrives already knowing roughly what they need. Filtering, not
        # pre-selection, is the right tool there, so global takes every shape the
        # wholesalers sell: 24 instead of 7.
        if rung is None:
            if not is_global:
                continue
            network = "5G" if mb >= 3072 else "4G"
        else:
            network = rung[1]

        # Global rows sort by size then duration so the page reads as a ladder of
        # its own. Using the rung order here would put the seven ladder shapes
        # first and scatter the rest, which is worse than no order at all.
        order = (int(gb) * 1000 + min(days, 999)) if is_global else rung[0]

        # A worldwide 1 GB is deliberately not sold — the owner took it off the
        # page: at global cost it prices close to a 3 GB and reads as a trap.
        # Skipped rather than created-and-disabled, so it cannot be switched on
        # by accident later.
        if is_global and mb <= 1024:
            continue

        plan, created = Plan.objects.get_or_create(
            region=region,
            country=None,
            data_amount_mb=mb,
            validity_days=days,
            defaults={
                "scope": Plan.Scope.GLOBAL if region_slug == "global" else Plan.Scope.REGIONAL,
                "title": f"{region.name} {gb:g} GB · {days} days",
                "network_type": network,
                "price_usd": Decimal("0"),  # recalculated from cost on save
                "cost_usd": cost,
                "sort_order": order,
                "is_popular": False,
                # Regional tariffs stay off until someone looks at them, same as
                # a new destination. Global is the exception: the worldwide page
                # is meant to carry the whole range with filters, so a shape that
                # arrives switched off would simply never appear. The below-cost
                # guard still refuses to sell anything whose cost has overtaken
                # its price, so "on by default" cannot mean "sold at a loss".
                "is_active": region_slug == GLOBAL_SLUG,
            },
        )
        made_plans += int(created)
        # The count can grow as a supplier adds countries to a bundle, so it is
        # refreshed rather than frozen at creation.
        note = f"{coverage} ta davlat"
        # The list, not only the count: a bundle covering 106 of 200 countries
        # is a fine product, but only if the customer can check their stop
        # before paying. Refreshed on every sync for the same reason the count
        # is — a wholesaler adds and drops countries from a bundle over time.
        coverage_codes = ",".join(offer.codes)
        changed = []
        if plan.price_note != note:
            plan.price_note = note
            changed.append("price_note")
        if plan.coverage_iso2 != coverage_codes:
            plan.coverage_iso2 = coverage_codes
            changed.append("coverage_iso2")
        if changed:
            plan.save(update_fields=changed)

        _, offer_created = SupplierOffer.objects.update_or_create(
            plan=plan,
            provider=provider,
            defaults={"package_code": code, "cost_usd": cost, "is_available": True},
        )
        made_offers += int(offer_created)

    return {"plans_created": made_plans, "offers_created": made_offers}


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
    ladder_index = {(mb, days): (order, network) for order, (mb, days, network) in enumerate(rungs())}
    # Same rule as the preview, via the same helper — see on_ladder().
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
            key = (gb, days)
            prices.off_ladder[key] = prices.off_ladder.get(key, 0) + 1
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
