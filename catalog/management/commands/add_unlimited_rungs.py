"""Open the ladder to unlimited packages, one rung per duration a supplier sells.

The ladder in `SellableShape` is what decides which shapes become plans, and
every rung on it names a traffic size. Unlimited has none — the supplier reports
`data_quantity: -1` — so no rung could ever match it and 3,377 unlimited
packages were counted as unusable on every sync and thrown away.

A rung with `data_mb = 0` is the unlimited rung. That keeps the decision where
the rest of the ladder keeps it: which durations we are willing to sell is a row
in the admin, not a constant in the code, and switching one off stops that
duration being offered without deleting anything.

This reads the supplier's own catalogue rather than taking a list of days on
faith, so the rungs created are exactly the durations that exist to sell. Dry
run unless --apply, same posture as sync_catalog.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from catalog import supplier_api
from catalog.models import SellableShape

#: Unlimited rungs sort after every gigabyte rung. The ladder is read top to
#: bottom on a destination page and unlimited is the upsell, not the opener.
UNLIMITED_SORT_BASE = 100


class Command(BaseCommand):
    help = "Create the unlimited rungs (data_mb=0) for the durations a supplier offers."

    def add_arguments(self, parser):
        parser.add_argument("--provider", default="esimcard", choices=sorted(supplier_api.FETCHERS))
        parser.add_argument("--apply", action="store_true", help="Write. Otherwise dry run.")
        parser.add_argument(
            "--network",
            default=SellableShape.Network.FIVE_G,
            choices=[c for c, _ in SellableShape.Network.choices],
        )
        parser.add_argument(
            "--max-days",
            type=int,
            default=0,
            help="Ignore durations longer than this. 0 means no limit.",
        )

    def handle(self, *args, **options):
        provider = options["provider"]
        try:
            catalogue = supplier_api.fetch(provider)
        except supplier_api.SupplierApiError as exc:
            raise CommandError(str(exc)) from exc

        # Durations that actually reached the catalogue as unlimited, which is
        # to say: a single destination, a real price, and a duration in days.
        days_seen: dict[int, int] = {}
        for (_iso2, gb, days) in catalogue.prices.best:
            if gb > 0:
                continue
            days_seen[days] = days_seen.get(days, 0) + 1

        if options["max_days"]:
            days_seen = {d: n for d, n in days_seen.items() if d <= options["max_days"]}

        if not days_seen:
            self.stdout.write(
                self.style.WARNING(
                    f"{provider}: unlimited package reached the catalogue for no duration. "
                    "Nothing to do."
                )
            )
            return

        self.stdout.write(f"{provider}: {len(days_seen)} ta cheksiz muddat topildi")
        created = existing = 0
        for order, days in enumerate(sorted(days_seen)):
            count = days_seen[days]
            rung = SellableShape.objects.filter(data_mb=0, days=days, country=None).first()
            if rung is not None:
                existing += 1
                state = "bor" if rung.is_active else "bor (o'chirilgan)"
            else:
                state = "yangi"
                created += 1
                if options["apply"]:
                    SellableShape.objects.create(
                        data_mb=0,
                        days=days,
                        network=options["network"],
                        sort_order=UNLIMITED_SORT_BASE + order,
                        note=f"Cheksiz · {provider} da {count} ta yo'nalishda bor",
                    )
            self.stdout.write(f"  {days:>3} kun · {count:>4} yo'nalish · {state}")

        if options["apply"]:
            self.stdout.write(self.style.SUCCESS(f"{created} ta rung yaratildi, {existing} tasi bor edi"))
            self.stdout.write("Endi: manage.py sync_catalog --apply")
        else:
            self.stdout.write(
                self.style.WARNING(f"Quruq yugurish — {created} ta rung yaratilardi. --apply qo'shing.")
            )
