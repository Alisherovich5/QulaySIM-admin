"""What each wholesaler costs us, holds for us, and is better at.

Four questions this answers, and one it refuses to pretend it can:

  * How much money is with each supplier — read live from their APIs.
  * How much has gone out — from the purchase ledger, which is the only record
    of money actually spent (a plan's cost is a price list, not a receipt).
  * Who is cheaper, where — computed over the 615 tariffs both of them carry.
  * Which one we sell more from — by the supplier each sold tariff was sourced
    from at the time of sale.

The one it refuses: **topping up from here is not possible.** Neither reseller
API has a deposit endpoint — eSIM Access exposes balance/query, package/list,
esim/order, esim/query and esim/cancel; eSIMCard exposes GET /balance,
POST /package/purchase and GET /esims. Money into a wallet goes through the
supplier's own portal. Rather than hide that, the page shows the balance beside a
link to the place where it can actually be topped up.

Balance reading is a network call on a page load, which is a bad idea made safe
two ways: it is cached, and a supplier that is unreachable degrades to "could not
read" rather than taking the page down. A balance we cannot fetch must never look
like a balance of zero — the two mean opposite things when deciding whether to
switch payments on.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache

REQUEST_TIMEOUT = 20
BALANCE_CACHE_SECONDS = 60

#: Where a human actually tops the wallet up. Shown beside each balance.
PORTALS = {
    "esimaccess": "https://esimaccess.com",
    "esimcard": "https://portal.esimcard.com",
}

LABELS = {"esimaccess": "eSIM Access", "esimcard": "eSIMCard"}


@dataclass
class Balance:
    """What one supplier holds for us, or why we do not know."""

    provider: str
    amount: Decimal | None = None
    error: str = ""
    portal: str = ""

    @property
    def label(self) -> str:
        return LABELS.get(self.provider, self.provider)

    @property
    def known(self) -> bool:
        return self.amount is not None

    @property
    def empty(self) -> bool:
        """True only when we read it and it really is zero."""
        return self.amount is not None and self.amount <= 0


def _esimaccess_balance() -> Balance:
    import requests

    result = Balance(provider="esimaccess", portal=PORTALS["esimaccess"])
    access_code = getattr(settings, "ESIMACCESS_ACCESS_CODE", "")
    secret = getattr(settings, "ESIMACCESS_SECRET_KEY", "")
    base = getattr(settings, "ESIMACCESS_BASE_URL", "https://api.esimaccess.com")
    if not access_code:
        result.error = "not configured"
        return result

    # Same signing contract as the catalogue fetch: compact separators, no ASCII
    # escaping, and the body hashed byte for byte. A stray space authenticates as
    # garbage.
    body = json.dumps({}, separators=(",", ":"), ensure_ascii=False)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "RT-AccessCode": access_code,
    }
    if secret:
        timestamp = str(int(time.time() * 1000))
        request_id = str(uuid.uuid4())
        headers.update(
            {
                "RT-Timestamp": timestamp,
                "RT-RequestID": request_id,
                "RT-Signature": hmac.new(
                    secret.encode(),
                    f"{timestamp}{request_id}{access_code}{body}".encode(),
                    hashlib.sha256,
                )
                .hexdigest()
                .lower(),
            }
        )
    try:
        response = requests.post(
            f"{base}/api/v1/open/balance/query",
            data=body.encode(),
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - shown to the operator verbatim
        result.error = str(exc)[:160]
        return result

    # A refusal arrives as HTTP 200 with success=false, so the status code alone
    # proves nothing.
    if payload.get("success") is not True:
        result.error = str(payload.get("errorMsg") or payload.get("errorCode") or "refused")[:160]
        return result
    raw = (payload.get("obj") or {}).get("balance")
    if raw is None:
        result.error = "no balance in the response"
        return result
    # eSIM Access quotes money in 1/10000 USD everywhere else in its API; the
    # balance endpoint answers in whole dollars, so it is taken as given rather
    # than scaled on a guess.
    result.amount = Decimal(str(raw)).quantize(Decimal("0.01"))
    return result


def _esimcard_balance() -> Balance:
    import requests

    result = Balance(provider="esimcard", portal=PORTALS["esimcard"])
    token = getattr(settings, "ESIMCARD_API_TOKEN", "")
    base = getattr(
        settings, "ESIMCARD_BASE_URL", "https://portal.esimcard.com/api/developer/reseller"
    )
    if not token:
        result.error = "not configured"
        return result
    try:
        response = requests.get(
            f"{base}/balance",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        result.error = str(exc)[:160]
        return result
    if response.status_code != 200:
        result.error = f"HTTP {response.status_code}"
        return result
    raw = payload.get("balance")
    if raw is None:
        result.error = "no balance in the response"
        return result
    result.amount = Decimal(str(raw)).quantize(Decimal("0.01"))
    return result


READERS = {"esimaccess": _esimaccess_balance, "esimcard": _esimcard_balance}


def balances(*, refresh: bool = False) -> list[Balance]:
    """Both wallets, cached for a minute.

    Cached because this is two HTTP calls on a page load and a balance does not
    move between refreshes; `refresh=True` is the button that skips the cache.
    """
    out: list[Balance] = []
    for provider, reader in READERS.items():
        key = f"supplier-balance:{provider}"
        cached = None if refresh else cache.get(key)
        if cached is not None:
            amount, error = cached
            out.append(
                Balance(
                    provider=provider,
                    amount=Decimal(amount) if amount is not None else None,
                    error=error,
                    portal=PORTALS.get(provider, ""),
                )
            )
            continue
        result = reader()
        cache.set(
            key,
            (str(result.amount) if result.amount is not None else None, result.error),
            BALANCE_CACHE_SECONDS,
        )
        out.append(result)
    return out


@dataclass
class ProviderStats:
    """One supplier's side of the comparison."""

    provider: str
    offers: int = 0
    plans: int = 0
    exclusive_countries: int = 0
    cheaper_on: int = 0
    sourced_plans: int = 0
    sourced_cost: Decimal = Decimal("0")
    sold_units: int = 0
    sold_cost: Decimal = Decimal("0")
    purchases: int = 0
    spent: Decimal = Decimal("0")

    @property
    def label(self) -> str:
        return LABELS.get(self.provider, self.provider)


@dataclass
class CountryRow:
    """One destination, and which supplier wins it."""

    name: str
    iso2: str
    access_plans: int = 0
    card_plans: int = 0
    access_wins: int = 0
    card_wins: int = 0
    best_saving: Decimal = Decimal("0")
    cheapest: Decimal | None = None
    cheapest_provider: str = ""

    @property
    def both(self) -> bool:
        return bool(self.access_plans and self.card_plans)

    @property
    def only(self) -> str:
        if self.access_plans and not self.card_plans:
            return "esimaccess"
        if self.card_plans and not self.access_plans:
            return "esimcard"
        return ""


def comparison() -> dict:
    """Who is cheaper where, and what we actually buy from each.

    One important thing this does *not* report as an opportunity: the difference
    between the two suppliers' prices. Sourcing already buys the cheaper of the
    two on every tariff that has both, so that money is captured, not waiting to
    be captured. What the comparison is for is the opposite question — how much
    we depend on each supplier, and which one it would hurt to lose.
    """
    from catalog.models import Country, Plan
    from orders.models import OrderItem, SupplierPurchase

    stats = {p: ProviderStats(provider=p) for p in ("esimaccess", "esimcard")}
    rows: dict[int, CountryRow] = {}
    both_plans = 0
    mis_sourced = 0

    plans = (
        Plan.objects.filter(is_active=True)
        .select_related("country")
        .prefetch_related("offers")
    )
    for plan in plans:
        offers = list(plan.offers.all())
        by_provider: dict[str, Decimal] = {}
        for offer in offers:
            current = by_provider.get(offer.provider)
            if current is None or offer.cost_usd < current:
                by_provider[offer.provider] = offer.cost_usd

        for provider, cost in by_provider.items():
            if provider in stats:
                stats[provider].offers += 1
                stats[provider].plans += 1

        if plan.provider in stats:
            stats[plan.provider].sourced_plans += 1
            stats[plan.provider].sourced_cost += plan.cost_usd or Decimal("0")

        access = by_provider.get("esimaccess")
        card = by_provider.get("esimcard")

        if plan.country_id:
            row = rows.setdefault(
                plan.country_id,
                CountryRow(
                    name=plan.country.name_uz or plan.country.name,
                    iso2=plan.country.iso2,
                ),
            )
            if access is not None:
                row.access_plans += 1
            if card is not None:
                row.card_plans += 1
            best = min([c for c in (access, card) if c is not None], default=None)
            if best is not None and (row.cheapest is None or best < row.cheapest):
                row.cheapest = best
                row.cheapest_provider = "esimaccess" if best == access else "esimcard"

        if access is not None and card is not None:
            both_plans += 1
            winner = "esimaccess" if access < card else "esimcard" if card < access else ""
            if winner:
                stats[winner].cheaper_on += 1
                if plan.country_id:
                    if winner == "esimaccess":
                        rows[plan.country_id].access_wins += 1
                    else:
                        rows[plan.country_id].card_wins += 1
                    gap = abs(access - card)
                    if gap > rows[plan.country_id].best_saving:
                        rows[plan.country_id].best_saving = gap
            # Sourcing should already be on the cheaper one. When it is not, that
            # is a fact worth surfacing rather than an average worth hiding.
            cheapest_provider = "esimaccess" if access <= card else "esimcard"
            if plan.provider != cheapest_provider:
                mis_sourced += 1

    for row in rows.values():
        only = row.only
        if only in stats:
            stats[only].exclusive_countries += 1

    # What actually left the wallet. The ledger, not the price list.
    for row in (
        SupplierPurchase.objects.values("provider")
        .annotate(n=__import__("django.db.models", fromlist=["Count"]).Count("id"))
    ):
        if row["provider"] in stats:
            stats[row["provider"]].purchases = row["n"]

    for item in (
        OrderItem.objects.filter(
            order__status__in=("paid", "fulfilled", "completed"), plan__isnull=False
        )
        .select_related("plan")
        .only("quantity", "plan__provider", "plan__cost_usd")
    ):
        provider = item.plan.provider
        if provider in stats:
            stats[provider].sold_units += item.quantity
            stats[provider].sold_cost += (item.plan.cost_usd or Decimal("0")) * item.quantity

    ordered = sorted(rows.values(), key=lambda r: (-r.best_saving, r.name))
    return {
        "stats": [stats["esimaccess"], stats["esimcard"]],
        "countries": ordered,
        "both_countries": sum(1 for r in rows.values() if r.both),
        "both_plans": both_plans,
        "mis_sourced": mis_sourced,
        "total_countries": len(rows),
        "cheapest_overall": min(
            (r for r in rows.values() if r.cheapest is not None),
            key=lambda r: r.cheapest,
            default=None,
        ),
    }
