"""Fill in the Uzbek and Russian wording of the landing-page FAQ.

The FAQ is admin-owned content, and the English rows shipped without their
translations — so an Uzbek visitor read the whole section in English, and one
answer still named the project by a brand it no longer uses.

Matched on the English question rather than the primary key, so this survives a
reimport of the content and can be run against production without knowing the
ids there. Dry run by default, like the other seed commands in this project,
because it writes customer-facing copy.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from config.cache import invalidate_content
from content.models import FAQ

# English question -> the corrected English, then Uzbek and Russian.
# The English is included because one row still said "FastSIM".
TRANSLATIONS: dict[str, dict[str, tuple[str, str]]] = {
    "What is an eSIM?": {
        "question": (
            "What is an eSIM?",
            "eSIM nima?",
            "Что такое eSIM?",
        ),
        "answer": (
            "An eSIM is a digital SIM that lets you activate a mobile data plan "
            "without a physical card. You scan a QR code and you are connected.",
            "eSIM — bu raqamli SIM. U plastik karta olmasdan mobil internet "
            "tarifini ulash imkonini beradi: QR kodni skanerlaysiz va internet "
            "ishlaydi.",
            "eSIM — это цифровая SIM-карта: тариф мобильного интернета "
            "подключается без пластика. Сканируете QR-код — и вы в сети.",
        ),
    },
    "How do I install my FastSIM eSIM?": {
        "question": (
            "How do I install my QulaySIM eSIM?",
            "QulaySIM eSIM’ini qanday o‘rnataman?",
            "Как установить eSIM от QulaySIM?",
        ),
        "answer": (
            "After purchase, open My eSIMs, scan the QR code with your phone "
            "camera or add it in Settings > Cellular > Add eSIM. Activate it "
            "when you arrive at your destination.",
            "Xariddan so‘ng «Mening eSIM’larim» bo‘limini oching va QR kodni "
            "telefon kamerasi bilan skanerlang yoki Sozlamalar > Uyali aloqa > "
            "eSIM qo‘shish orqali qo‘shing. Borgan joyingizga yetganingizda "
            "faollashtiring.",
            "После покупки откройте «Мои eSIM» и отсканируйте QR-код камерой "
            "телефона либо добавьте его через Настройки > Сотовая связь > "
            "Добавить eSIM. Активируйте по прибытии.",
        ),
    },
    "Is my phone compatible?": {
        "question": (
            "Is my phone compatible?",
            "Telefonim mos keladimi?",
            "Подойдёт ли мой телефон?",
        ),
        "answer": (
            "Most phones released after 2018 support eSIM (iPhone XS+, Pixel 3+, "
            "recent Samsung Galaxy S/Note/Z). Your device must also be "
            "carrier-unlocked.",
            "2018-yildan keyingi aksariyat telefonlar eSIM’ni qo‘llab-quvvatlaydi "
            "(iPhone XS va undan yangi, Pixel 3+, so‘nggi Samsung Galaxy "
            "S/Note/Z). Qurilmangiz operatorga bog‘lanmagan bo‘lishi ham kerak.",
            "Большинство телефонов, вышедших после 2018 года, поддерживают eSIM "
            "(iPhone XS и новее, Pixel 3+, свежие Samsung Galaxy S/Note/Z). "
            "Устройство также не должно быть привязано к оператору.",
        ),
    },
    "Which payment methods do you accept?": {
        "question": (
            "Which payment methods do you accept?",
            "Qanday to‘lov usullari qabul qilinadi?",
            "Какие способы оплаты принимаются?",
        ),
        "answer": (
            "Payme, Uzcard and Humo are being connected. Until they are live we "
            "will confirm your order with you directly.",
            "Payme, Uzcard va Humo ulanish jarayonida. Ular ishga tushgunicha "
            "buyurtmangizni siz bilan to‘g‘ridan-to‘g‘ri tasdiqlaymiz.",
            "Payme, Uzcard и Humo подключаются. Пока они не заработали, мы "
            "подтвердим ваш заказ напрямую.",
        ),
    },
    "Can I top up my plan?": {
        "question": (
            "Can I top up my plan?",
            "Tarifni to‘ldirish mumkinmi?",
            "Можно ли пополнить тариф?",
        ),
        "answer": (
            "Yes — when your data runs low you can purchase an add-on without "
            "changing your eSIM.",
            "Ha — trafik tugay deganda eSIM’ni almashtirmasdan qo‘shimcha paket "
            "sotib olishingiz mumkin.",
            "Да — когда трафик заканчивается, можно докупить пакет, не меняя eSIM.",
        ),
    },
}


class Command(BaseCommand):
    help = "Translate the landing FAQ into Uzbek and Russian. Dry run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the changes. Without this the command only reports them.",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Replace translations that have already been edited by hand.",
        )

    def handle(self, *args, **options):
        overwrite = options["overwrite"]
        planned: list[tuple[FAQ, list[str]]] = []
        kept: list[str] = []
        absent: list[str] = []

        by_question = {faq.question: faq for faq in FAQ.objects.all()}
        for english, fields in TRANSLATIONS.items():
            faq = by_question.get(english)
            if faq is None:
                absent.append(english)
                continue
            changes: list[str] = []
            for base, (en, uz, ru) in fields.items():
                for suffix, value in (("", en), ("_uz", uz), ("_ru", ru)):
                    attr = f"{base}{suffix}"
                    current = getattr(faq, attr)
                    # Per field: a hand-edited Uzbek answer must not block the
                    # Russian one from being filled in.
                    if current and current != value and not overwrite:
                        kept.append(f"{english[:40]} · {attr}")
                        continue
                    if current == value:
                        continue
                    setattr(faq, attr, value)
                    changes.append(attr)
            if changes:
                planned.append((faq, changes))

        self.stdout.write(f"\nrows to update: {len(planned)}")
        for faq, changes in planned:
            self.stdout.write(f"  + {faq.question[:52]}  ({', '.join(changes)})")
        if kept:
            self.stdout.write(
                self.style.WARNING(f"\nalready written, left alone ({len(kept)}):")
            )
            for row in kept:
                self.stdout.write(f"  = {row}")
        if absent:
            self.stdout.write(
                self.style.WARNING(f"\nnot in the database ({len(absent)}):")
            )
            for row in absent:
                self.stdout.write(f"  ! {row}")

        if not planned:
            self.stdout.write(self.style.SUCCESS("\nnothing to do."))
            return

        if not options["apply"]:
            self.stdout.write(
                self.style.WARNING("\nDRY RUN — nothing written. Re-run with --apply.")
            )
            return

        with transaction.atomic():
            for faq, changes in planned:
                # Saved one at a time rather than bulk_update: post_save is what
                # clears the landing cache, and a bulk update fires none.
                faq.save(update_fields=changes)
        invalidate_content()
        self.stdout.write(self.style.SUCCESS(f"\napplied: {len(planned)} row(s) translated."))
