"""Encrypt activation credentials written before encryption existed.

Rows are re-saved one at a time so the field's own encrypt step runs; values
already encrypted are skipped, so the command is safe to re-run.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from config.crypto import is_encrypted
from orders.models import ESIM


class Command(BaseCommand):
    help = "Encrypt any eSIM activation credentials still stored in the clear."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        # values_list still runs from_db_value, so ask the raw cursor instead.
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("SELECT id, qr_payload FROM orders_esim")
            rows = cursor.fetchall()

        plaintext_ids = [pk for pk, value in rows if value and not is_encrypted(value)]

        if not plaintext_ids:
            self.stdout.write(self.style.SUCCESS("Every credential is already encrypted."))
            return

        if options["dry_run"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run: {len(plaintext_ids)} credential(s) would be encrypted."
                )
            )
            return

        for esim in ESIM.objects.filter(id__in=plaintext_ids).iterator():
            esim.save(update_fields=["qr_payload", "qr_image"])

        self.stdout.write(self.style.SUCCESS(f"Encrypted {len(plaintext_ids)} credential(s)."))
