"""What we add on top of supplier cost — counted, not estimated.

This module is deliberately free of any AI. A margin report is arithmetic over
rows we already hold, and a language model asked to do arithmetic will
occasionally produce a number that looks right and is not. Numbers a business
prices from have to be reproducible: run this twice on the same catalogue and
you get the same figures, every time, with no network call in between.

The AI in this feature writes the *commentary* over these figures and proposes
pricing rules for a human to approve (catalog/ai_pricing.py). It never computes
a number that appears in the report and never writes a price.

Two things the report is careful about:

Cost at the time of a sale is not stored. `OrderItem` freezes the unit price but
the supplier cost lives on the plan and moves with every nightly sync, so
realised margin is measured against *today's* cost and the report says so rather
than presenting it as historical truth.

A blended average across the whole catalogue is close to meaningless when costs
run from $0.46 to $222 — the expensive tariffs drown out the cheap ones. So the
headline is per traffic size, which is the axis the margin problem actually lives
on, and the thin-margin list is by absolute dollars, because a card fee is
charged in dollars and not in percent.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENTS = Decimal("0.01")

#: Below this, a sale earns less than a typical card fee takes.
THIN_MARGIN_USD = Decimal("0.50")


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def _markup_percent(cost: Decimal | None, price: Decimal | None) -> Decimal | None:
    """What we added, as a percentage of what we paid. None when unknowable."""
    if not cost or price is None:
        return None
    return _money((price - cost) / cost * Decimal("100"))


def size_label(data_mb: int | None, *, unlimited: bool = False) -> str:
    """"3 GB", "512 MB", "Cheksiz" — the way the size reads on a tariff."""
    if unlimited:
        return "∞"
    if not data_mb:
        return "—"
    if data_mb % 1024 == 0:
        return f"{data_mb // 1024} GB"
    return f"{data_mb} MB"


@dataclass
class Row:
    """One line of the report: a size, a destination, or a supplier."""

    key: str
    label: str
    count: int = 0
    cost_total: Decimal = Decimal("0")
    price_total: Decimal = Decimal("0")
    thin: int = 0
    lowest_markup: Decimal | None = None
    highest_markup: Decimal | None = None
    locked: int = 0

    def add(self, cost: Decimal, price: Decimal, *, locked: bool = False) -> None:
        self.count += 1
        self.cost_total += cost
        self.price_total += price
        if price - cost < THIN_MARGIN_USD:
            self.thin += 1
        if locked:
            self.locked += 1
        percent = _markup_percent(cost, price)
        if percent is None:
            return
        if self.lowest_markup is None or percent < self.lowest_markup:
            self.lowest_markup = percent
        if self.highest_markup is None or percent > self.highest_markup:
            self.highest_markup = percent

    @property
    def margin_total(self) -> Decimal:
        return _money(self.price_total - self.cost_total)

    @property
    def markup_percent(self) -> Decimal | None:
        """Blended markup: total added over total paid, not a mean of means.

        Averaging per-tariff percentages would weight a $0.46 tariff the same as
        a $222 one, which is how a catalogue reports a healthy margin while
        losing money on volume.
        """
        return _markup_percent(self.cost_total, self.price_total)

    @property
    def margin_each(self) -> Decimal | None:
        if not self.count:
            return None
        return _money(self.margin_total / self.count)

    @property
    def spread(self) -> Decimal | None:
        """How far apart the best and worst markup in this row are."""
        if self.lowest_markup is None or self.highest_markup is None:
            return None
        return _money(self.highest_markup - self.lowest_markup)


@dataclass
class ThinPlan:
    """A tariff earning less than a card fee takes."""

    plan_id: int
    title: str
    destination: str
    size: str
    cost: Decimal
    price: Decimal
    locked: bool

    @property
    def margin(self) -> Decimal:
        return _money(self.price - self.cost)

    @property
    def markup_percent(self) -> Decimal | None:
        return _markup_percent(self.cost, self.price)


def build_report(*, thin_limit: int = 40) -> dict:
    """Every priced tariff, rolled up three ways plus the outliers.

    One query for the catalogue, one for sales. Everything else is arithmetic in
    Python, so the report costs the same whether it is opened once a day or
    refreshed while someone edits prices next to it.
    """
    from django.utils import timezone

    from catalog.models import Plan

    plans = (
        Plan.objects.select_related("country", "region")
        .only(
            "id",
            "title",
            "cost_usd",
            "price_usd",
            "price_locked",
            "data_amount_mb",
            "is_unlimited",
            "validity_days",
            "provider",
            "is_active",
            "country__name",
            "country__name_uz",
            "region__name",
            "region__name_uz",
        )
        .order_by("data_amount_mb", "validity_days")
    )

    overall = Row(key="all", label="Butun katalog")
    by_size: dict[str, Row] = {}
    by_destination: dict[str, Row] = {}
    by_provider: dict[str, Row] = {}
    thin: list[ThinPlan] = []

    counted = 0
    no_cost = 0
    inactive = 0

    for plan in plans:
        if not plan.is_active:
            inactive += 1
            continue
        cost, price = plan.cost_usd, plan.price_usd
        # A tariff with no supplier cost has no markup to report. Counting it as
        # zero margin would drag every average down and invent a problem that is
        # really just a missing number.
        if cost is None or cost <= 0 or price is None:
            no_cost += 1
            continue

        counted += 1
        locked = plan.price_locked
        destination = (
            (plan.country.name_uz or plan.country.name)
            if plan.country_id
            else (plan.region.name_uz or plan.region.name)
            if plan.region_id
            else "—"
        )
        size = size_label(plan.data_amount_mb, unlimited=plan.is_unlimited)

        overall.add(cost, price, locked=locked)
        by_size.setdefault(size, Row(key=size, label=size)).add(cost, price, locked=locked)
        by_destination.setdefault(
            destination, Row(key=destination, label=destination)
        ).add(cost, price, locked=locked)
        by_provider.setdefault(
            plan.provider or "—", Row(key=plan.provider or "—", label=plan.provider or "—")
        ).add(cost, price, locked=locked)

        if price - cost < THIN_MARGIN_USD:
            thin.append(
                ThinPlan(
                    plan_id=plan.id,
                    title=plan.title,
                    destination=destination,
                    size=size,
                    cost=cost,
                    price=price,
                    locked=locked,
                )
            )

    # Sizes read in ascending order, not alphabetically: "10 GB" before "3 GB"
    # is the sort a computer wants and a human never does.
    def size_sort(row: Row) -> tuple:
        text = row.key
        if text == "∞":
            return (2, 0)
        if text == "—":
            return (3, 0)
        number, _, unit = text.partition(" ")
        try:
            value = int(number)
        except ValueError:
            return (3, 0)
        return (1, value * 1024 if unit == "GB" else value)

    thin.sort(key=lambda item: item.margin)

    return {
        "generated_at": timezone.now(),
        "overall": overall,
        "sizes": sorted(by_size.values(), key=size_sort),
        "destinations": sorted(
            by_destination.values(), key=lambda row: (row.markup_percent or Decimal("0"))
        ),
        "providers": sorted(by_provider.values(), key=lambda row: -row.count),
        "thin": thin[:thin_limit],
        "thin_total": len(thin),
        "thin_limit": thin_limit,
        "counted": counted,
        "no_cost": no_cost,
        "inactive": inactive,
        "thin_margin_usd": THIN_MARGIN_USD,
        "sales": _realised_margin(),
    }


def _realised_margin() -> dict:
    """What we actually earned on tariffs that sold.

    Measured against today's supplier cost, because the cost at the moment of
    sale is not stored anywhere — `OrderItem` freezes the price the customer
    paid, and the cost moves with every nightly sync. The report labels this
    rather than presenting it as history.
    """
    from orders.models import OrderItem

    sold = (
        OrderItem.objects.filter(
            order__status__in=("paid", "fulfilled", "completed"),
            plan__cost_usd__isnull=False,
        )
        .select_related("plan")
        .values_list("unit_price", "quantity", "plan__cost_usd")
    )

    revenue = Decimal("0")
    cost = Decimal("0")
    units = 0
    for unit_price, quantity, unit_cost in sold:
        revenue += unit_price * quantity
        cost += unit_cost * quantity
        units += quantity

    return {
        "units": units,
        "revenue": _money(revenue),
        "cost": _money(cost),
        "margin": _money(revenue - cost),
        "markup_percent": _markup_percent(cost, revenue),
    }
