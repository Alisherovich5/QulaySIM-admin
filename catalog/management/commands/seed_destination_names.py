"""Fill in the Uzbek and Russian names of destinations and regions.

The catalogue stores English as the base name, so an Uzbek or Russian visitor
was reading "Uzbekistan", "Japan" and "Europe" on an otherwise translated page.
The tables below are the translations; running this makes the database match
them.

Blank translations only, by default: an operator who corrects a name in the
admin should not have it overwritten the next time somebody runs a seed. Pass
--overwrite when the table here is meant to win.

Dry run by default, like `setup_destinations`, because these strings are
customer-facing on every destination card.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from catalog.models import Country, Region

# Keyed by the English base name, which is unique on both models.
# Uzbek uses the Latin okina (‘) in o‘/g‘, as the admin's own translations do.
COUNTRY_NAMES = {
    "Australia": ("Avstraliya", "Австралия"),
    "Azerbaijan": ("Ozarbayjon", "Азербайджан"),
    "Brazil": ("Braziliya", "Бразилия"),
    "China": ("Xitoy", "Китай"),
    "Egypt": ("Misr", "Египет"),
    "France": ("Fransiya", "Франция"),
    "Georgia": ("Gruziya", "Грузия"),
    "Germany": ("Germaniya", "Германия"),
    "Italy": ("Italiya", "Италия"),
    "Japan": ("Yaponiya", "Япония"),
    "Kazakhstan": ("Qozog‘iston", "Казахстан"),
    "Malaysia": ("Malayziya", "Малайзия"),
    "Mexico": ("Meksika", "Мексика"),
    "Qatar": ("Qatar", "Катар"),
    "Saudi Arabia": ("Saudiya Arabistoni", "Саудовская Аравия"),
    "Singapore": ("Singapur", "Сингапур"),
    "South Korea": ("Janubiy Koreya", "Южная Корея"),
    "Spain": ("Ispaniya", "Испания"),
    "Thailand": ("Tailand", "Таиланд"),
    "Turkey": ("Turkiya", "Турция"),
    # The owner asks for Dubai in the label: it is what customers search for,
    # and almost nobody buys "the Emirates" by that name.
    "United Arab Emirates": (
        "Dubay (BAA)",
        "ОАЭ (Дубай)",
    ),
    "United Kingdom": ("Buyuk Britaniya", "Великобритания"),
    "United States": ("Amerika Qo‘shma Shtatlari", "США"),
    "Uzbekistan": ("O‘zbekiston", "Узбекистан"),
    "Vietnam": ("Vietnam", "Вьетнам"),
}

REGION_NAMES = {
    "Africa": ("Afrika", "Африка"),
    "Asia": ("Osiyo", "Азия"),
    "Europe": ("Yevropa", "Европа"),
    "Latin America": ("Lotin Amerikasi", "Латинская Америка"),
    "Middle East": ("Yaqin Sharq", "Ближний Восток"),
    "North America": ("Shimoliy Amerika", "Северная Америка"),
    "Oceania": ("Okeaniya", "Океания"),
}


class Command(BaseCommand):
    help = "Set name_uz / name_ru on countries and regions. Dry run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Replace existing translations too, not just blank ones.",
        )

    def handle(self, *args, **options):
        overwrite: bool = options["overwrite"]

        countries, country_kept, country_absent = self._plan(
            Country, COUNTRY_NAMES, overwrite
        )
        regions, region_kept, region_absent = self._plan(Region, REGION_NAMES, overwrite)

        self._report("destinations", countries, country_kept, country_absent)
        self._report("regions", regions, region_kept, region_absent)

        if not (countries or regions):
            self.stdout.write(self.style.SUCCESS("\nEvery name is already translated."))
            return

        if not options["apply"]:
            self.stdout.write(
                self.style.WARNING("\nDRY RUN — nothing written. Re-run with --apply.")
            )
            return

        for model, rows in ((Country, countries), (Region, regions)):
            if rows:
                model.objects.bulk_update(
                    [row for row, _ in rows], ["name_uz", "name_ru"], batch_size=200
                )

        # bulk_update bypasses post_save, so the signal that normally clears the
        # storefront cache never fires and the site would keep serving the old
        # English names until the TTL expired.
        from config.cache import invalidate_catalogue

        invalidate_catalogue()

        self.stdout.write(
            self.style.SUCCESS(
                f"\napplied: {len(countries)} destination(s), {len(regions)} region(s) named."
            )
        )

    def _plan(self, model, table, overwrite):
        """Return the rows to write, the ones deliberately left alone, and the
        table entries with no row in the database."""
        by_name = {obj.name: obj for obj in model.objects.all()}
        changes: list[tuple[object, str]] = []
        kept: list[str] = []

        for name, (uz, ru) in table.items():
            obj = by_name.get(name)
            if obj is None:
                continue
            # Per field, not per row: guarding on "either is set" meant a
            # country with a hand-written Uzbek name never got its Russian one,
            # and the only way to fill it was --overwrite, which would have
            # thrown away the hand-written Uzbek at the same time.
            wanted_uz = obj.name_uz if (obj.name_uz and not overwrite) else uz
            wanted_ru = obj.name_ru if (obj.name_ru and not overwrite) else ru
            held = [
                f"{name} {label}: {current}"
                for label, current, proposed in (
                    ("uz", obj.name_uz, uz),
                    ("ru", obj.name_ru, ru),
                )
                if current and not overwrite and current != proposed
            ]
            kept.extend(held)
            if (obj.name_uz, obj.name_ru) == (wanted_uz, wanted_ru):
                continue
            obj.name_uz = wanted_uz
            obj.name_ru = wanted_ru
            changes.append((obj, f"{name} → {wanted_uz} / {wanted_ru}"))

        absent = sorted(set(table) - set(by_name))
        return changes, kept, absent

    def _report(self, label, changes, kept, absent):
        self.stdout.write(f"\n{label} to name: {len(changes)}")
        for _, line in changes:
            self.stdout.write(f"  + {line}")
        if kept:
            self.stdout.write(
                self.style.WARNING(
                    f"\nalready translated, left alone ({len(kept)}) — use --overwrite to replace:"
                )
            )
            for line in kept:
                self.stdout.write(f"  = {line}")
        if absent:
            self.stdout.write(
                self.style.WARNING(f"\nnot in the database ({len(absent)}): {', '.join(absent)}")
            )
