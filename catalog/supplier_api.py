"""Reading both wholesalers' catalogues straight from their APIs.

This replaces the CSV upload as the way prices get in. The upload is still there
and still works, but it was never the right primary path: a human downloading a
file, remembering which supplier it came from, and clicking apply is a step that
gets skipped, and the catalogue drifts the moment it is skipped. Worse, a country
that had not already been created by hand was silently dropped, which is why the
site offered 25 destinations while the APIs offered 193.

Deliberately produces the same `ParsedPrices` shape the CSV parser produces, so
everything downstream — the comparison between suppliers, the pricing rules, the
preview that shows what would change — is the code that already exists and is
already tested. Only the source of the rows is new.

Both suppliers are read through their own paginated endpoints:

  eSIM Access  POST /api/v1/open/package/list — one response, ~2900 packages,
               each with a `locationNetworkList` naming every country it covers.
               Prices are in ten-thousandths of a dollar.
  eSIMCard     GET /packages — ~6500 packages, each with a `coverage` list.
               Prices are plain dollars. Two things the doc does not say: the
               page size is settable with `per_page` (100 turns 324 requests
               into 65, and a nightly sync that takes six minutes gets skipped),
               and the host is portal.esimcard.com — esimcard.com answers every
               path with HTTP 410.

Both kinds of package are read. A single-country one becomes a destination
tariff; a multi-country one becomes a regional tariff attached to whichever
region its coverage mostly falls in — a real product we were leaving on the
table, since a traveller doing Vienna, Prague and Budapest wants one eSIM, not
three. Which region is decided from the coverage list rather than the supplier's
name for it, because "Global139" and "Asia Pacific 12" are marketing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

import requests
from django.conf import settings

from catalog.supplier_import import ParsedPrices

REQUEST_TIMEOUT = 60

# eSIM Access quotes in units of 1/10000 USD: 16000 means $1.60.
ESIMACCESS_PRICE_DIVISOR = Decimal("10000")


class SupplierApiError(RuntimeError):
    """A wholesaler's catalogue could not be read."""


@dataclass
class FetchedCatalogue:
    """One supplier's catalogue, ready for the existing apply() path.

    `countries` is what makes auto-creation possible: the supplier's own code and
    English name for every destination it can actually sell, so the sync creates
    the ones we are missing instead of skipping their packages.
    """

    prices: ParsedPrices = field(default_factory=ParsedPrices)
    # iso2 -> the supplier's English name for it.
    countries: dict[str, str] = field(default_factory=dict)
    # (region slug, GB, days) -> (package code, cost, how many countries it
    # covers). The cheapest wins, same rule as the per-country prices.
    regional: dict[tuple[str, float, int], tuple[str, Decimal, int]] = field(
        default_factory=dict
    )
    packages_read: int = 0
    multi_country: int = 0
    # Multi-country packages too narrow to honestly call regional.
    too_narrow: int = 0
    unusable: int = 0


def _gb_from_mb(megabytes: float) -> float:
    return round(megabytes / 1024, 4)


# A multi-country package has to cover at least this many countries before it is
# sold as a regional tariff.
#
# Both suppliers list two- and three-country bundles, and the region rule files a
# Poland+Czechia pair under "Europe". Sold as "Yevropa 5 GB" that is misleading
# to the point of being a complaint: the customer buys a continent and lands in
# the third country with no data. Below the floor the package is simply not a
# regional product, and it is reported rather than sold.
MIN_REGIONAL_COVERAGE = 8


def _add_regional(
    catalogue: FetchedCatalogue,
    codes: list[str],
    gb: float,
    days: int,
    code: str,
    cost: Decimal,
):
    """Record one multi-country package against the region it mostly covers.

    Ranked by coverage first and price second — the opposite of the per-country
    rule, and deliberately. For a local tariff the product is fixed and the only
    variable is what we pay; for a regional one the coverage IS the product, and
    a cheaper bundle that drops half the continent is not the same thing sold
    for less. Saving forty cents by shipping "Europe, 12 countries" instead of
    "Europe, 41 countries" is not a saving.
    """
    from catalog.geo import region_for_coverage

    if gb <= 0 or days <= 0 or cost <= 0 or not code:
        catalogue.unusable += 1
        return
    if len(codes) < MIN_REGIONAL_COVERAGE:
        catalogue.too_narrow += 1
        return
    key = (region_for_coverage(codes), gb, days)
    existing = catalogue.regional.get(key)
    # Widest first, then cheapest.
    if existing is None or (-len(codes), cost) < (-existing[2], existing[1]):
        catalogue.regional[key] = (code, cost, len(codes))


def _add(catalogue: FetchedCatalogue, iso2: str, gb: float, days: int, code: str, cost: Decimal):
    """Record one package, keeping the cheapest per (country, GB, days).

    Same rule the CSV parser uses: a supplier often lists several packages for
    the same shape and we want the one that costs least.
    """
    if not iso2 or gb <= 0 or days <= 0 or cost <= 0 or not code:
        catalogue.unusable += 1
        return
    key = (iso2.upper(), gb, days)
    existing = catalogue.prices.best.get(key)
    if existing is None or cost < existing[1]:
        catalogue.prices.best[key] = (code, cost)


# --------------------------------------------------------------------------- #
# eSIM Access
# --------------------------------------------------------------------------- #


def fetch_esimaccess(*, max_pages: int | None = None) -> FetchedCatalogue:
    # max_pages is accepted and ignored: eSIM Access returns its whole catalogue
    # in one response, so there is nothing to bound.
    """Read the eSIM Access catalogue.

    Uses the backend's signing scheme rather than reimplementing it: the request
    is a plain POST with the access code and a HMAC of the body, and getting that
    subtly wrong would look like an empty catalogue rather than an error.
    """
    access_code = getattr(settings, "ESIMACCESS_ACCESS_CODE", "")
    secret = getattr(settings, "ESIMACCESS_SECRET_KEY", "")
    base = getattr(settings, "ESIMACCESS_BASE_URL", "https://api.esimaccess.com")
    if not access_code:
        raise SupplierApiError("ESIMACCESS_ACCESS_CODE is not configured")

    import hashlib
    import hmac
    import json
    import time
    import uuid

    # Byte-for-byte what the backend client sends, because the signature covers
    # the body: compact separators and ensure_ascii=False are part of the
    # contract, and a stray space would authenticate as garbage.
    body = json.dumps(
        {"locationCode": "", "type": "BASE", "slug": "", "iccid": ""},
        separators=(",", ":"),
        ensure_ascii=False,
    )
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

    response = requests.post(
        f"{base}/api/v1/open/package/list",
        data=body.encode(),
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise SupplierApiError(f"eSIM Access answered HTTP {response.status_code}")
    payload = response.json()
    if not payload.get("success"):
        raise SupplierApiError(
            f"eSIM Access {payload.get('errorCode', 'unknown')}: "
            f"{payload.get('errorMessage', 'refused the request')}"
        )

    catalogue = FetchedCatalogue()
    for package in (payload.get("obj") or {}).get("packageList") or []:
        catalogue.packages_read += 1
        locations = package.get("locationNetworkList") or []
        for location in locations:
            code = (location.get("locationCode") or "").upper()
            if code:
                catalogue.countries.setdefault(code, location.get("locationName") or code)
        # volume is bytes; duration is in durationUnit, which is DAY in practice.
        gb = _gb_from_mb(int(package.get("volume") or 0) / (1024 * 1024))
        days = int(package.get("duration") or 0)
        if str(package.get("durationUnit") or "DAY").upper() != "DAY":
            catalogue.unusable += 1
            continue
        cost = (Decimal(str(package.get("price") or 0)) / ESIMACCESS_PRICE_DIVISOR).quantize(
            Decimal("0.01")
        )
        code = str(package.get("packageCode") or "")

        if len(locations) != 1:
            catalogue.multi_country += 1
            _add_regional(
                catalogue,
                [(loc.get("locationCode") or "").upper() for loc in locations],
                gb,
                days,
                code,
                cost,
            )
            continue

        _add(catalogue, (locations[0].get("locationCode") or "").upper(), gb, days, code, cost)

    catalogue.prices.rows_read = catalogue.packages_read
    return catalogue


# --------------------------------------------------------------------------- #
# eSIMCard
# --------------------------------------------------------------------------- #

# "eSIM Data For 1GB in 3 Days, Taiwan" — the structured fields are the source
# of truth; this only exists as a fallback when they are missing.
_ESIMCARD_NAME = re.compile(r"([\d.]+)\s*(GB|MB)\b.*?(\d+)\s*Day", re.IGNORECASE)


# The largest page eSIMCard honours. `perPage` and `limit` are ignored; only
# `per_page` moves it, and anything above 100 is capped there.
ESIMCARD_PAGE_SIZE = 100


def fetch_esimcard(*, max_pages: int = 120) -> FetchedCatalogue:
    """Read the eSIMCard catalogue, walking its pagination.

    Bounded so a supplier that starts reporting a nonsensical `lastPage` cannot
    turn a nightly sync into an endless one.
    """
    token = getattr(settings, "ESIMCARD_API_TOKEN", "")
    base = getattr(
        settings, "ESIMCARD_BASE_URL", "https://portal.esimcard.com/api/developer/reseller"
    )
    if not token:
        raise SupplierApiError("ESIMCARD_API_TOKEN is not configured")

    catalogue = FetchedCatalogue()
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    page, last_page = 1, 1
    while page <= min(last_page, max_pages):
        response = session.get(
            f"{base}/packages",
            params={"page": page, "per_page": ESIMCARD_PAGE_SIZE},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 410:
            raise SupplierApiError(
                "eSIMCard answered HTTP 410 — the base URL is stale "
                "(the API moved to portal.esimcard.com)"
            )
        if response.status_code != 200:
            raise SupplierApiError(f"eSIMCard answered HTTP {response.status_code} on page {page}")
        payload = response.json()
        # A refusal arrives inside a 200 with status false, so the field is what
        # decides, not the status line.
        if payload.get("status") is not True:
            raise SupplierApiError(str(payload.get("message") or "eSIMCard refused the request"))

        last_page = int((payload.get("meta") or {}).get("lastPage") or 1)
        for package in payload.get("data") or []:
            catalogue.packages_read += 1
            coverage = package.get("coverage") or []
            for entry in coverage:
                code = (entry.get("code") or "").upper()
                if len(code) == 2:
                    catalogue.countries.setdefault(code, entry.get("country_name") or code)
            gb = _quantity_in_gb(package)
            days = _validity_in_days(package)
            cost = Decimal(str(package.get("price") or 0)).quantize(Decimal("0.01"))
            code = str(package.get("id") or "")

            if len(coverage) != 1:
                catalogue.multi_country += 1
                _add_regional(
                    catalogue,
                    [(entry.get("code") or "").upper() for entry in coverage],
                    gb,
                    days,
                    code,
                    cost,
                )
                continue

            _add(catalogue, (coverage[0].get("code") or "").upper(), gb, days, code, cost)
        page += 1

    catalogue.prices.rows_read = catalogue.packages_read
    return catalogue


def _quantity_in_gb(package: dict) -> float:
    """Data allowance in GB. Unlimited packages report 0 and are skipped.

    An unlimited eSIM is a real product but it has no place on a GB ladder, and
    pretending it is 0 GB would put a plan on the site that says "0 GB".
    """
    if package.get("unlimited"):
        return 0.0
    quantity = float(package.get("data_quantity") or 0)
    unit = str(package.get("data_unit") or "GB").upper()
    if unit == "MB":
        return _gb_from_mb(quantity)
    if unit == "GB":
        return round(quantity, 4)
    match = _ESIMCARD_NAME.search(str(package.get("name") or ""))
    if match:
        amount = float(match.group(1))
        return _gb_from_mb(amount) if match.group(2).upper() == "MB" else amount
    return 0.0


def _validity_in_days(package: dict) -> int:
    validity = int(package.get("package_validity") or 0)
    unit = str(package.get("package_validity_unit") or "Day").lower()
    if unit.startswith("day"):
        return validity
    if unit.startswith("week"):
        return validity * 7
    if unit.startswith("month"):
        return validity * 30
    return 0


FETCHERS = {
    "esimaccess": fetch_esimaccess,
    "esimcard": fetch_esimcard,
}


def fetch(provider: str, *, max_pages: int | None = None) -> FetchedCatalogue:
    fetcher = FETCHERS.get(provider)
    if fetcher is None:
        raise SupplierApiError(f"no catalogue fetcher for provider {provider!r}")
    return fetcher(max_pages=max_pages) if max_pages else fetcher()
