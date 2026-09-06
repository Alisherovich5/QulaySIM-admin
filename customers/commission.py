"""Referal komissiyasining pog'onalari.

Bu modul QulaySIM-backend'dagi `app/domain/referral.py` ning nusxasi. Ikki
loyiha alohida repo va alohida konteynerda ishlaydi, shuning uchun kodni
import qilib bo'lmaydi -- lekin ikkalasi HAM bir xil muhit o'zgaruvchisini
(`REFERRAL_COMMISSION_TIERS`) o'qiydi, ya'ni raqam bitta joyda turadi.
Formula o'zgarsa, ikkala fayl birga o'zgarishi kerak; shuning uchun bu yerda
ham xuddi o'sha testlar bor (`customers/tests_commission.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings


@dataclass(frozen=True)
class CommissionTier:
    """Shu sondan boshlab amal qiladigan stavka."""

    from_count: int
    percent: Decimal | None = None
    flat_uzs: int | None = None

    def amount_for(self, order_uzs) -> int:
        if self.flat_uzs is not None:
            return int(self.flat_uzs)
        if self.percent is None:
            return 0
        value = (Decimal(order_uzs) * self.percent / Decimal(100)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        return int(value)

    @property
    def label(self) -> str:
        """Ekranga chiqadigan ko'rinish; mingliklar bo'sh joy bilan ajratiladi."""

        if self.flat_uzs is not None:
            return f"{self.flat_uzs:,}".replace(",", "\u00a0") + " so'm"
        return f"{_trim(self.percent)}%"


def _trim(value: Decimal | None) -> str:
    if value is None:
        return "0"
    return format(value.normalize(), "f")


def parse_tiers(raw: str) -> list[CommissionTier]:
    """`"0:5%,100:6%,300:6.5%"` yoki `"0:5000,100:6000"` ni o'qiydi."""

    tiers: list[CommissionTier] = []
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise ValueError(f"pog'ona noto'g'ri: {chunk!r}")
        head, value = (part.strip() for part in chunk.split(":", 1))
        if not head.isdigit():
            raise ValueError(f"pog'ona boshi son bo'lishi kerak: {chunk!r}")
        if value.endswith("%"):
            tiers.append(CommissionTier(int(head), percent=Decimal(value[:-1])))
        else:
            tiers.append(CommissionTier(int(head), flat_uzs=int(value)))
    if not tiers:
        return [CommissionTier(0, percent=Decimal(0))]
    return sorted(tiers, key=lambda t: t.from_count)


def tier_for(tiers: list[CommissionTier], index: int) -> CommissionTier:
    """`index` -- bu nechanchi mijoz (0 dan boshlanadi).

    Stavka mijoz olib kelingan paytdagi pog'ona bo'yicha olinadi va keyin
    o'zgarmaydi -- panelda ko'rsatilgan summa hisobotda boshqacha chiqmasligi
    uchun.
    """

    chosen = tiers[0]
    for tier in tiers:
        if index + 1 > tier.from_count:
            chosen = tier
        else:
            break
    return chosen


def current_tiers() -> list[CommissionTier]:
    """Sozlamadagi pog'onalar; xato yozilgan bo'lsa eski bir pog'onali sozlama.

    Adminka ochilmay qolishi hisobotni to'g'rilamaydi -- xato jurnalga tushadi
    va summa eski sozlamaga, odatda nolga, tushadi.
    """

    legacy = f"0:{int(settings.REFERRAL_COMMISSION_UZS)}"
    raw = (settings.REFERRAL_COMMISSION_TIERS or "").strip() or legacy
    try:
        return parse_tiers(raw)
    except ValueError:
        import logging

        logging.getLogger(__name__).error("referral tiers invalid: %r", raw)
        return parse_tiers(legacy)
