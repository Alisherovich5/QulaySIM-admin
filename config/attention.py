"""What needs a person right now, and nothing else.

The dashboard used to open with lifetime revenue and three doughnut charts. An
operator opening this at nine in the morning is not asking "how are we doing" —
they are asking "did anything break overnight", and the answer to that was
nowhere on the page. Meanwhile the fact that twenty-six of twenty-nine orders
were unpaid sat in grey subtitle text.

Each check here answers one question that has a consequence, carries the link to
the rows behind it, and is deliberately silent when there is nothing to say. A
dashboard that always shows six warnings trains people to ignore six warnings.

The definitions match the ones the Telegram report uses, on purpose: two places
counting "paid but undelivered" differently is how an operator ends up trusting
neither.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.db.models import F
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

#: A supplier order normally returns a profile in seconds. Past this, something
#: is stuck rather than slow.
STUCK_AFTER = timedelta(minutes=30)

#: How soon an expiring eSIM is worth mentioning. Short enough to be actionable,
#: long enough that a customer can still be told.
EXPIRING_WITHIN = timedelta(days=3)

#: Below this, a wholesaler wallet can no longer cover a normal day of sales.
LOW_BALANCE_USD = Decimal("25")


@dataclass(frozen=True)
class Item:
    """One thing that may need attention."""

    key: str
    label: str
    count: int
    detail: str
    url: str
    #: "critical" costs money right now; "warn" will cost money soon; "info" is
    #: worth knowing and nothing more.
    level: str = "warn"

    @property
    def is_critical(self) -> bool:
        return self.level == "critical"


def _url(name: str, query: str = "") -> str:
    try:
        return reverse(name) + query
    except Exception:  # noqa: BLE001 — a missing page must not break the page
        return ""


def paid_but_undelivered() -> Item | None:
    """Money taken, no eSIM issued. The one failure that cannot wait.

    Same condition the rescue sweep and the Telegram report use: a paid order
    with no eSIM row. Anything under five minutes old is still in flight and is
    not a problem, so it is excluded — otherwise every sale would raise an alarm
    for its first minute.
    """
    from orders.models import ESIM, Order

    cutoff = timezone.now() - timedelta(minutes=5)
    qs = (
        Order.objects.filter(status=Order.Status.PAID)
        .exclude(id__in=ESIM.objects.values("order_id"))
        .filter(paid_at__lt=cutoff)
    )
    count = qs.count()
    if not count:
        return None
    return Item(
        key="undelivered",
        label=str(_("Paid, no eSIM")),
        count=count,
        detail=str(_("The customer paid and has nothing. Check the supplier and the worker.")),
        url=_url("admin:orders_order_changelist", "?status__exact=paid"),
        level="critical",
    )


def stuck_esims() -> Item | None:
    """Issued on our side, never confirmed by the wholesaler."""
    from orders.models import ESIM

    cutoff = timezone.now() - STUCK_AFTER
    qs = ESIM.objects.filter(status=ESIM.Status.PENDING, created_at__lt=cutoff)
    count = qs.count()
    if not count:
        return None
    return Item(
        key="stuck",
        label=str(_("eSIMs waiting on a supplier")),
        count=count,
        detail=str(_("Ordered more than half an hour ago and still without a profile.")),
        url=_url("admin:orders_esim_changelist", "?status__exact=pending"),
        level="critical",
    )


def underwater_plans() -> Item | None:
    """Plans whose supplier cost has caught up with our price.

    Checkout refuses to sell these, so the customer sees a plan they cannot buy
    until somebody reprices it — a silent hole in the catalogue.
    """
    from catalog.models import Plan

    qs = Plan.objects.filter(is_active=True, cost_usd__gte=F("price_usd")).exclude(cost_usd=None)
    count = qs.count()
    if not count:
        return None
    return Item(
        key="underwater",
        label=str(_("Plans priced below cost")),
        count=count,
        detail=str(_("Not sellable until repriced. Checkout refuses them.")),
        url=_url("admin:catalog_plan_margin_report"),
        level="critical",
    )


def unpaid_orders() -> Item | None:
    """Carts that reached checkout and stopped there.

    Not a fault — most are somebody changing their mind — but the count is the
    honest read on how well checkout is working, and it belongs where it can be
    seen rather than in a subtitle.
    """
    from orders.models import Order

    cutoff = timezone.now() - timedelta(hours=1)
    qs = Order.objects.filter(status=Order.Status.PENDING, created_at__lt=cutoff)
    count = qs.count()
    if not count:
        return None
    return Item(
        key="unpaid",
        label=str(_("Unpaid orders")),
        count=count,
        detail=str(_("Reached the payment page over an hour ago and never paid.")),
        url=_url("admin:orders_order_changelist", "?status__exact=pending"),
        level="info",
    )


def expiring_esims() -> Item | None:
    """Customers whose data is about to run out — a reason to sell, not a fault."""
    from orders.models import ESIM

    now = timezone.now()
    qs = ESIM.objects.filter(
        status=ESIM.Status.ACTIVE, expires_at__gt=now, expires_at__lt=now + EXPIRING_WITHIN
    )
    count = qs.count()
    if not count:
        return None
    return Item(
        key="expiring",
        label=str(_("eSIMs expiring within three days")),
        count=count,
        detail=str(_("A good moment to offer a top-up.")),
        url=_url("admin:orders_esim_changelist", "?status__exact=active"),
        level="info",
    )


def low_supplier_balance() -> Item | None:
    """A wallet too thin to cover the day.

    Read from the cached balance rather than the live API: this runs on every
    dashboard load, and a wholesaler being slow must not make the admin slow.
    """
    try:
        from catalog.suppliers import balances
    except Exception:  # noqa: BLE001
        return None

    low = []
    try:
        for balance in balances(refresh=False):
            if balance.amount is not None and Decimal(str(balance.amount)) < LOW_BALANCE_USD:
                low.append(f"{balance.label}: ${balance.amount}")
    except Exception:  # noqa: BLE001 — see the docstring
        return None
    if not low:
        return None
    return Item(
        key="balance",
        label=str(_("Supplier balance is low")),
        count=len(low),
        detail=" · ".join(low) + ". " + str(_("A sale fails when the wallet is empty.")),
        url=_url("admin:catalog_supplieroffer_board"),
        level="critical",
    )


#: Order matters: the reader should meet the expensive problems first.
CHECKS = (
    paid_but_undelivered,
    stuck_esims,
    low_supplier_balance,
    underwater_plans,
    unpaid_orders,
    expiring_esims,
)


def collect() -> list[Item]:
    """Run every check, skipping any that fails.

    One broken check must not take the dashboard with it — the operator would
    lose the other five warnings along with it, which is the opposite of what
    this page is for.
    """
    items: list[Item] = []
    for check in CHECKS:
        try:
            item = check()
        except Exception:  # noqa: BLE001 — see the docstring
            continue
        if item is not None:
            items.append(item)
    return items
