"""Rebuild the catalogue from the wholesalers' own APIs.

This is the command that is supposed to run on a schedule so nobody ever types a
destination in by hand again. Before it existed, adding one country meant
creating the country, four plans and their supplier offers — eleven objects — and
a country nobody had created yet had its packages silently dropped. That is why
the site sold 25 destinations while the two APIs between them offered 193.

What it does, in order:

  1. Reads both suppliers' full catalogues.
  2. Creates the regions and countries the APIs cover and we are missing, with
     Uzbek and Russian names from CLDR rather than from a list we maintain.
  3. Hands each supplier's prices to the existing `supplier_import.apply()`, so
     the comparison between suppliers, the pricing rules and the margin floors
     are the code that was already there and already tested.

`--dry-run` is the default posture for a first look: it reports exactly what
would change and writes nothing. Applying moves supplier costs, and costs move
retail prices through the pricing rules.

One supplier failing does not stop the other. A wholesaler having a bad minute
should cost us that wholesaler's updates, not the whole sync.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from catalog import geo, supplier_api, supplier_import
from catalog.models import CatalogSyncRun, Country, Region, SupplierOffer

PROVIDERS = ("esimaccess", "esimcard")


class Command(BaseCommand):
    help = "Sync countries, plans and supplier prices from the wholesalers' APIs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--provider",
            action="append",
            choices=PROVIDERS,
            help="Sync only this supplier. Repeatable. Default: all of them.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the changes. Without it nothing is written.",
        )
        parser.add_argument(
            "--no-new-countries",
            action="store_true",
            help="Update prices only; do not create destinations the APIs cover.",
        )
        parser.add_argument(
            "--max-pages",
            type=int,
            default=0,
            help=(
                "Stop after this many pages per supplier. 0 means all of them. "
                "eSIMCard answers a page in about 4.4 seconds and has 65 of "
                "them, so a full read takes ~5 minutes — fine for the nightly "
                "job, useless for a quick check."
            ),
        )
        parser.add_argument(
            "--activate-new",
            action="store_true",
            help=(
                "Put newly created destinations on sale immediately. Off by "
                "default: a destination appears on the site the moment it is "
                "active, and a fresh one has had nobody look at its prices."
            ),
        )

    def handle(self, *args, **options):
        providers = options.get("provider") or list(PROVIDERS)
        write = options["apply"]
        # Recorded before anything is fetched, so a run that dies mid-flight is
        # still visible as a run that died rather than as a run that never
        # happened. Automation nobody can see is the same as automation that
        # stopped.
        self.run = CatalogSyncRun.objects.create(
            provider=",".join(providers) if len(providers) < len(PROVIDERS) else "all",
            dry_run=not write,
        )
        self._log_lines: list[str] = []
        try:
            self._sync(providers, write=write, options=options)
        except Exception as exc:
            self._finish(CatalogSyncRun.Status.FAILED, str(exc))
            raise
        self._finish(CatalogSyncRun.Status.OK)

    def _finish(self, status, error: str = ""):
        from django.utils import timezone as tz

        if error:
            self._log_lines.append(f"XATO: {error}")
        self.run.status = status
        self.run.finished_at = tz.now()
        self.run.log = "\n".join(self._log_lines)[:20000]
        self.run.save()

    def note(self, line: str):
        """Write to the console and to the run record at once."""
        self._log_lines.append(line)
        self.stdout.write(line)

    def _sync(self, providers, *, write: bool, options: dict):
        self.note(
            "KO‘RIB CHIQISH — hech narsa yozilmaydi"
            if not write
            else "QO‘LLANADI — tannarx va chakana narxlar o‘zgaradi"
        )

        catalogues = {}
        for provider in providers:
            try:
                catalogue = supplier_api.fetch(provider, max_pages=options["max_pages"] or None)
            except supplier_api.SupplierApiError as exc:
                # Reported, not raised: the other supplier's update is still worth
                # having, and a nightly sync that dies on one outage silently
                # stops maintaining everything.
                self._log_lines.append(f"  {provider}: XATO — {exc}")
                self.stderr.write(self.style.ERROR(f"{provider}: {exc}"))
                continue
            catalogues[provider] = catalogue
            self.run.packages_read += catalogue.packages_read
            self.note(
                f"  {provider}: {catalogue.packages_read} paket, "
                f"{len(catalogue.countries)} davlat qamrovda, "
                f"{len(catalogue.prices.best)} sotiladigan shakl, "
                f"{catalogue.multi_country} ko‘p davlatli → "
                f"{len(catalogue.regional)} hududiy tarif "
                f"({catalogue.too_narrow} tasi juda tor)"
            )

        if not catalogues:
            raise CommandError("no supplier catalogue could be read")

        if not options["no_new_countries"]:
            self._provision_destinations(catalogues, write=write, activate=options["activate_new"])

        for provider, catalogue in catalogues.items():
            self._apply_prices(provider, catalogue, write=write)

        if not write:
            self.note("\nHech narsa yozilmadi. Qo‘llash uchun: --apply")

    # ---------------------------------------------------------------- regions #

    def _provision_destinations(self, catalogues: dict, *, write: bool, activate: bool):
        """Create the regions and countries the APIs cover and we lack."""
        covered: dict[str, str] = {}
        for catalogue in catalogues.values():
            for iso2, name in catalogue.countries.items():
                covered.setdefault(iso2, name)
        # Only destinations someone can actually buy: a country that appears in a
        # coverage list but has no single-country package of its own would become
        # an empty card on the site.
        sellable = {iso2 for catalogue in catalogues.values() for (iso2, _, _) in catalogue.prices.best}
        candidates = {iso2: covered[iso2] for iso2 in sorted(sellable) if iso2 in covered}

        existing = {c.iso2.upper() for c in Country.objects.all() if c.iso2}
        missing = {iso2: name for iso2, name in candidates.items() if iso2 not in existing}

        self.note(
            f"\nYo‘nalishlar: {len(existing)} bor, {len(candidates)} API sotadi, "
            f"{len(missing)} yangi"
        )
        self._ensure_regions(write=write)
        if not missing:
            return

        # Every region in the map, not only the ones the new destinations need:
        # regional tariffs attach to a region too, and "global" belongs to no
        # country at all, so waiting for a country to conjure it would leave the
        # 128-country bundle with nowhere to sit.
        regions = {r.slug: r for r in Region.objects.all()}

        preview = sorted(missing.items())
        for iso2, supplier_name in preview[:12]:
            names = geo.names_for(iso2, supplier_name)
            self.note(f"  + {iso2}  {names['name']} / {names['name_uz']}")
        if len(preview) > 12:
            self.note(f"  … va yana {len(preview) - 12} ta")

        if not write:
            return

        with transaction.atomic():
            for iso2, supplier_name in preview:
                names = geo.names_for(iso2, supplier_name)
                region = regions.get(geo.region_slug_for(iso2))
                # get_or_create on the slug too: two different codes can produce
                # the same slug for territories CLDR names identically, and a
                # duplicate-slug crash would abort the whole sync.
                Country.objects.get_or_create(
                    iso2=iso2,
                    defaults={
                        **names,
                        "slug": _unique_slug(names["slug"], iso2),
                        "region": region,
                        # Off by default — see --activate-new.
                        "is_active": activate,
                        "sort_order": 0,
                    },
                )
        self.run.countries_created = len(preview)
        self.note(
            f"  {len(preview)} yo‘nalish yaratildi"
            + ("" if activate else " (faol emas — ko‘rib chiqib yoqing)")
        )


    def _ensure_regions(self, *, write: bool) -> dict:
        """Create any region in the map that the database is missing."""
        regions = {r.slug: r for r in Region.objects.all()}
        for slug in sorted(set(geo.REGION_NAMES) - set(regions)):
            name, name_uz, name_ru, order = geo.REGION_NAMES[slug]
            self.note(f"  + hudud: {name}")
            if write:
                regions[slug] = Region.objects.create(
                    slug=slug, name=name, name_uz=name_uz, name_ru=name_ru, sort_order=order
                )
        return regions

    # ----------------------------------------------------------------- prices #

    def _apply_prices(self, provider: str, catalogue, *, write: bool):
        changes = supplier_import.plan_changes(catalogue.prices, provider)
        kinds: dict[str, int] = {}
        for change in changes:
            kinds[change.kind] = kinds.get(change.kind, 0) + 1
        summary = ", ".join(f"{kind}: {count}" for kind, count in sorted(kinds.items())) or "—"
        self.note(f"\n{provider} narxlari: {summary}")

        # A dry run counts only countries that exist right now, so without this
        # the preview understates itself by every plan the new destinations would
        # get — which is most of them on a first run, and exactly the number
        # somebody needs before deciding to apply.
        if not write:
            projected = self._projected_new_plans(catalogue)
            if projected:
                self.note(
                    f"  + yangi yo‘nalishlar qo‘shilsa yana ~{projected} tarif "
                    "(hozir bazada yo‘q davlatlar uchun)"
                )
            if catalogue.regional:
                on_rungs = sum(
                    1
                    for (_, gb, days) in catalogue.regional
                    if supplier_import.on_ladder(int(round(gb * 1024)), days)
                )
                self.note(
                    f"  + {on_rungs} hududiy tarif ({len(catalogue.regional)} shakldan)"
                )
            if catalogue.prices.off_ladder:
                worst = sorted(
                    catalogue.prices.off_ladder.items(), key=lambda kv: -kv[1]
                )[:4]
                shapes = ", ".join(f"{gb:g}GB/{days}kun ×{n}" for (gb, days), n in worst)
                self.note(
                    f"  pog‘onada joyi yo‘q: {sum(catalogue.prices.off_ladder.values())} paket "
                    f"({shapes})"
                )
            return
        result = supplier_import.apply(catalogue.prices, provider)
        regional = supplier_import.apply_regional(catalogue.regional, provider)
        # Stamp the sync so the admin can show how fresh a price is, and so a
        # supplier that quietly stopped answering is visible as staleness rather
        # than as prices that merely look plausible.
        SupplierOffer.objects.filter(provider=provider).update(last_synced_at=timezone.now())
        self.run.plans_created += result.get("plans_created", 0) + regional.get("plans_created", 0)
        self.run.offers_written += result.get("offers_created", 0) + regional.get(
            "offers_created", 0
        )
        self.note(
            f"  {result.get('plans_created', 0)} yangi tarif, "
            f"{result.get('offers_created', 0)} yangi taklif"
        )
        if regional.get("plans_created") or regional.get("offers_created"):
            self.note(
                f"  {regional.get('plans_created', 0)} hududiy tarif, "
                f"{regional.get('offers_created', 0)} hududiy taklif (faol emas)"
            )


    def _projected_new_plans(self, catalogue) -> int:
        """How many plans the destinations we do not have yet would receive."""
        existing = {c.iso2.upper() for c in Country.objects.all() if c.iso2}
        return sum(
            1
            for (iso2, gb, days) in catalogue.prices.best
            if iso2 not in existing and supplier_import.on_ladder(int(round(gb * 1024)), days)
        )


def _unique_slug(slug: str, iso2: str) -> str:
    """A slug nothing else is using, suffixed with the country code if needed."""
    if not Country.objects.filter(slug=slug).exists():
        return slug
    candidate = f"{slug}-{iso2.lower()}"
    return candidate if not Country.objects.filter(slug=candidate).exists() else f"{slug}-{iso2.lower()}-2"
