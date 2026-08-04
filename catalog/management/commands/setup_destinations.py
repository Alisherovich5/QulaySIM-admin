"""Bring the catalogue in line with the destinations the business wants to sell.

Declarative and idempotent: the list below is the intent, and running this makes
the database match it. Countries and plans that already exist are left alone
apart from the popular flag, so this is safe to re-run after the list changes.

Dry run by default, like `sync_supplier_prices`, because it creates customer-
facing plans and moves which destinations the landing page promotes.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from catalog.models import Country, Plan, PricingRule, Region, SupplierOffer

# The nine destinations to promote, in the order they should appear. Names are
# the catalogue's English names; the Uzbek label is only here so this list can be
# checked against the request it came from.
POPULAR = [
    ("Turkey", "Turkiya", "europe", "TR"),
    ("Georgia", "Gruziya", "europe", "GE"),
    ("Vietnam", "Vietnam", "asia", "VN"),
    ("Thailand", "Tailand", "asia", "TH"),
    ("Malaysia", "Malayziya", "asia", "MY"),
    ("China", "Xitoy", "asia", "CN"),
    ("Azerbaijan", "Azarbayjon", "asia", "AZ"),
    ("United Arab Emirates", "Dubay (BAA)", "middle-east", "AE"),
    ("Qatar", "Qatar", "middle-east", "QA"),
]

# The shape of a destination's tariff ladder, matching what every existing
# country already offers so a new one does not look different.
LADDER = [
    (1024, 7, "4G"),
    (3072, 15, "5G"),
    (5120, 30, "5G"),
    (10240, 30, "5G"),
]


def _read_prices(path: Path) -> dict[tuple[str, float, int], tuple[str, Decimal]]:
    """Cheapest package per (country, GB, days)."""
    best: dict[tuple[str, float, int], tuple[str, Decimal]] = {}
    with path.open() as handle:
        for row in csv.DictReader(handle):
            try:
                key = (
                    row["location"].strip().upper(),
                    round(float(row["data_gb"]), 2),
                    int(float(row["days"] or 0)),
                )
                cost = Decimal(row["cost_usd"]).quantize(Decimal("0.01"))
            except (KeyError, ValueError, TypeError):
                continue
            if key not in best or cost < best[key][1]:
                best[key] = (row["package_code"].strip(), cost)
    return best


class Command(BaseCommand):
    help = "Create the promoted destinations and set which are popular. Dry run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("--esimaccess", type=Path, required=True)
        parser.add_argument("--esimcard", type=Path)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        access = _read_prices(options["esimaccess"])
        card = _read_prices(options["esimcard"]) if options.get("esimcard") else {}
        if not access:
            raise CommandError("no usable rows in the eSIM Access price file")

        regions = {r.slug: r for r in Region.objects.all()}
        missing_regions = {slug for _, _, slug, _ in POPULAR} - set(regions)
        if missing_regions:
            raise CommandError(f"regions not in the database: {sorted(missing_regions)}")

        plan_rules = list(PricingRule.objects.filter(is_active=True))
        del plan_rules  # recalculation reads them itself; this is just a presence check

        created_countries: list[str] = []
        created_plans: list[str] = []
        offers: list[str] = []
        promoted: list[str] = []
        skipped: list[str] = []

        for name, uz, region_slug, iso2 in POPULAR:
            country = Country.objects.filter(name__iexact=name).first()
            if country is None:
                created_countries.append(f"{name} ({iso2}) → {region_slug}")

            for mb, days, network in LADDER:
                gb = round(mb / 1024, 2)
                supplier = access.get((iso2, gb, days))
                if supplier is None:
                    skipped.append(f"{name} {gb}GB/{days}d — no eSIM Access package")
                    continue
                exists = (
                    country
                    and country.plans.filter(data_amount_mb=mb, validity_days=days).exists()
                )
                if not exists:
                    created_plans.append(
                        f"{name} {gb:g} GB · {days} days  cost ${supplier[1]}"
                    )
                for provider, table in (("esimaccess", access), ("esimcard", card)):
                    if (iso2, gb, days) in table:
                        offers.append(f"{name} {gb:g}GB/{days}d {provider} ${table[(iso2, gb, days)][1]}")

            promoted.append(name)

        self._report(created_countries, created_plans, offers, promoted, skipped)

        if not options["apply"]:
            self.stdout.write(
                self.style.WARNING(
                    "\nDRY RUN — nothing written. Re-run with --apply.\n"
                    "New plans are priced from real supplier cost, so they will sit well "
                    "below the existing catalogue until sync_supplier_prices is applied to "
                    "the rest of it."
                )
            )
            return

        self._apply(access, card, regions)

        # The demotion step inside _apply is a queryset.update(), which fires
        # no post_save — and every signal the saves did fire ran inside the
        # transaction. Cleared here, after commit, so the storefront cannot
        # keep serving demoted destinations for the rest of the cache TTL.
        from config.cache import invalidate_catalogue

        invalidate_catalogue()
        self.stdout.write("storefront cache cleared.")

    def _report(self, countries, plans, offers, promoted, skipped):
        self.stdout.write(f"\ncountries to create: {len(countries)}")
        for row in countries:
            self.stdout.write(f"  + {row}")
        self.stdout.write(f"\nplans to create: {len(plans)}")
        for row in plans[:20]:
            self.stdout.write(f"  + {row}")
        if len(plans) > 20:
            self.stdout.write(f"  … and {len(plans) - 20} more")
        self.stdout.write(f"\nsupplier offers to attach: {len(offers)}")
        self.stdout.write(f"\ndestinations promoted ({len(promoted)}): {', '.join(promoted)}")
        if skipped:
            self.stdout.write(self.style.WARNING(f"\nno supplier package ({len(skipped)}):"))
            for row in skipped:
                self.stdout.write(f"  ! {row}")

    @transaction.atomic
    def _apply(self, access, card, regions):
        wanted_ids: set[int] = set()

        for order, (name, _uz, region_slug, iso2) in enumerate(POPULAR, start=1):
            # The same case-insensitive match the dry run reports on: an exact
            # get_or_create next to a "turkey" already in the database would
            # try to create a second "Turkey" and die on the slug constraint,
            # rolling the whole apply back after the dry run said "exists".
            country = Country.objects.filter(name__iexact=name).first()
            made = country is None
            if made:
                country = Country(
                    name=name,
                    slug=slugify(name),
                    iso2=iso2,
                    region=regions[region_slug],
                    is_active=True,
                )
            if not made and not country.region_id:
                country.region = regions[region_slug]
            country.is_popular = True
            country.sort_order = order
            country.save()
            wanted_ids.add(country.id)

            for index, (mb, days, network) in enumerate(LADDER):
                gb = round(mb / 1024, 2)
                supplier = access.get((iso2, gb, days))
                if supplier is None:
                    continue
                label = f"{name} {gb:g} GB · {days} days"
                plan, _ = Plan.objects.get_or_create(
                    country=country,
                    data_amount_mb=mb,
                    validity_days=days,
                    defaults={
                        "title": label,
                        "network_type": network,
                        # Recalculated from cost by Plan.save(); a placeholder is
                        # needed only because the column is not nullable.
                        "price_usd": Decimal("0"),
                        "cost_usd": supplier[1],
                        "sort_order": index,
                        # The middle rung is what most people buy, and it is what
                        # the landing page shows for this destination.
                        "is_popular": (mb, days) == (3072, 15),
                        "is_active": True,
                    },
                )
                for provider, table in (("esimaccess", access), ("esimcard", card)):
                    entry = table.get((iso2, gb, days))
                    if entry is None:
                        continue
                    SupplierOffer.objects.update_or_create(
                        plan=plan,
                        provider=provider,
                        defaults={
                            "package_code": entry[0],
                            "cost_usd": entry[1],
                            "is_available": True,
                        },
                    )

        # Demote anything not on the list, or the landing page keeps promoting
        # destinations the business has moved on from.
        demoted = (
            Country.objects.filter(is_popular=True).exclude(id__in=wanted_ids).update(is_popular=False)
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"\napplied: {len(wanted_ids)} destinations promoted, {demoted} demoted"
            )
        )
