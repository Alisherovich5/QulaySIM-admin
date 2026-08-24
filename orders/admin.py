from datetime import timedelta

from django import forms
from django.contrib import admin
from django.db.models import Case, Count, F, FloatField, IntegerField, Value, When
from django.db.models.functions import Cast
from django.utils import timezone
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from .models import (
    ESIM,
    AtmosTransaction,
    ComplimentaryGrant,
    Order,
    OrderItem,
    Payment,
    PaymeTransaction,
    PromoCode,
    SupplierPurchase,
    TelegramRecipient,
)
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

# (ink, wash) per status. A wash behind coloured ink reads in both themes;
# white text on a mid-tone fill was 2.4:1 on gold and 3.0:1 on teal, and
# darkening the fill enough to fix that would make the dark theme unreadable.
_STATUS_STYLES = {
    "pending": ("var(--qs-accent-text)", "var(--qs-warn-wash)"),
    "paid": ("var(--qs-teal-text)", "var(--qs-good-wash)"),
    "active": ("var(--qs-teal-text)", "var(--qs-good-wash)"),
    "success": ("var(--qs-teal-text)", "var(--qs-good-wash)"),
    "cancelled": ("var(--qs-ink-soft)", "var(--qs-neutral-wash)"),
    "expired": ("var(--qs-ink-soft)", "var(--qs-neutral-wash)"),
    "refunded": ("var(--qs-bad-text)", "var(--qs-bad-wash)"),
    "failed": ("var(--qs-bad-text)", "var(--qs-bad-wash)"),
}
_NEUTRAL_STYLE = ("var(--qs-ink-soft)", "var(--qs-neutral-wash)")


def _status_badge(value, label):
    ink, wash = _STATUS_STYLES.get(value, _NEUTRAL_STYLE)
    return format_html(
        '<span style="display:inline-block;padding:2px 10px;border-radius:999px;'
        'font-size:11px;font-weight:700;color:{};background:{};">{}</span>',
        ink,
        wash,
        label,
    )


class OrderItemInline(TabularInline):
    """Read-only, like ESIMInline: the line items are the money.

    Editing them here never recomputed the frozen subtotal/discount/total, so
    the order's totals stopped matching its items; and a *new* row was a crash,
    because the readonly unit_price never reached the form and hit the NOT NULL
    constraint. The API is the only writer of order lines.
    """

    model = OrderItem
    extra = 0
    fields = ("plan", "unit_price", "quantity")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class ESIMInline(TabularInline):
    model = ESIM
    extra = 0
    fields = ("iccid", "plan", "status", "data_total_mb", "data_used_mb", "expires_at")
    readonly_fields = fields
    can_delete = False
    show_change_link = True


class PromoCodeForm(forms.ModelForm):
    """Uppercases the code before validation.

    The model's clean() does this too; doing it in the form as well means the
    admin's uniqueness check and error messages all speak about the value that
    will be stored, and "sale" vs an existing "SALE" is a form error rather
    than a surprise at save time.
    """

    class Meta:
        model = PromoCode
        fields = "__all__"

    def clean_code(self):
        return (self.cleaned_data.get("code") or "").strip().upper()


@admin.register(PromoCode)
class PromoCodeAdmin(ModelAdmin):
    form = PromoCodeForm
    list_display = ("code", "discount_type", "discount_value", "min_order_usd", "usage", "is_active", "valid_until")
    list_filter = ("discount_type", "is_active")
    search_fields = ("code",)
    ordering = ("-created_at",)

    @display(description=_("Usage"))
    def usage(self, obj):
        cap = obj.max_uses or "∞"
        return f"{obj.used_count} / {cap}"


@admin.register(Order)
class OrderAdmin(ModelAdmin):

    # Fifty rows, not Django's hundred. At 1377 tariffs a full page is 666 KB of
    # HTML, most of it scrolled past unread, and this admin gets used over a phone
    # tether where bytes are the slow part. Fifty still fills a screen several
    # times over.
    list_per_page = 50
    # The columns an operator actually reads down this page.
    #
    # What was here: total (a bare "1,00" — dollars, but nothing said so, and it
    # read like a quantity), a line count that is 1 on every order, and two long
    # date columns. What was missing is the question this page exists to answer:
    # has this paid order got its eSIM? That is the difference between a sale and
    # a complaint, and it was invisible.
    list_display = (
        "id",
        "customer",
        "what",
        "status_badge",
        "delivery",
        "charged",
        "when",
    )
    list_filter = ("status", "created_at")
    search_fields = ("id", "customer__email")
    autocomplete_fields = ("customer", "promo_code")
    # status, amount_uzs and exchange_rate are read-only alongside the totals:
    # flipping an order to "paid" here provisions nothing (fulfilment only runs
    # off Payme's PerformTransaction), "refunded" refunds nothing at Payme, and
    # editing the frozen som amount makes Payme reject the checkout link the
    # customer already holds. The payment provider owns this state.
    readonly_fields = (
        "created_at",
        "paid_at",
        "subtotal",
        "discount",
        "total",
        "status",
        "amount_uzs",
        "exchange_rate",
    )
    inlines = (OrderItemInline, ESIMInline)
    ordering = ("-created_at",)
    list_filter_submit = True
    list_select_related = ("customer",)

    def get_queryset(self, request):
        # `obj.items.count()` per row was one query per order — 100 extra
        # queries on a default-sized page. The same applies to the eSIM count and
        # to naming what was bought, so both are annotated and prefetched rather
        # than fetched per row.
        return (
            super()
            .get_queryset(request)
            .annotate(_item_count=Count("items", distinct=True), _esim_count=Count("esims", distinct=True))
            .prefetch_related("items__plan__country")
        )

    @display(description=_("Status"))
    def status_badge(self, obj):
        return _status_badge(obj.status, obj.get_status_display())

    @display(description=_("Items"), ordering="_item_count")
    def item_count(self, obj):
        return obj._item_count

    @display(description=_("Bought"))
    def what(self, obj):
        """Destination and size, which is how an operator recognises an order.

        A row identified only by an id and an email means opening it to find out
        what it was — and this page is read while somebody is on the phone.
        """
        first = next(iter(obj.items.all()), None)
        if first is None or first.plan is None:
            return "—"
        plan = first.plan
        # The Uzbek name when there is one: the admin runs in Uzbek, and an
        # operator reading "Turkey" in a column of Uzbek text has to translate it
        # back before recognising the order.
        if plan.country_id:
            country = plan.country
            where = get_language() == "ru" and country.name_ru or country.name_uz or country.name
        elif plan.region_id:
            where = plan.region.name_uz or plan.region.name
        else:
            where = ""
        size = _("Unlimited") if plan.is_unlimited else f"{plan.data_amount_mb / 1024:g} GB"
        extra = f" +{obj._item_count - 1}" if obj._item_count > 1 else ""
        return format_html(
            '<span style="white-space:nowrap">{}</span>',
            f"{where} · {size}{extra}" if where else f"{size}{extra}",
        )

    @display(description=_("eSIM"), ordering="_esim_count")
    def delivery(self, obj):
        """Whether the thing the customer paid for exists.

        Only meaningful once money has changed hands: an unpaid order having no
        eSIM is correct, and colouring it red would train the reader to ignore
        the colour.
        """
        if obj._esim_count:
            return format_html(
                '<span style="color:var(--qs-good-deep);font-weight:600">✓ {}</span>',
                obj._esim_count if obj._esim_count > 1 else _("issued"),
            )
        if obj.status != Order.Status.PAID:
            return format_html('<span style="color:var(--qs-ink-mute)">—</span>')
        return format_html(
            '<span style="color:var(--qs-bad-text);font-weight:600">{}</span>', _("missing")
        )

    @display(description=_("Charged"), ordering="amount_uzs")
    def charged(self, obj):
        """What the card was charged, in som.

        `amount_uzs` is the frozen figure the customer actually paid. The dollar
        total is our bookkeeping and belongs on the detail page, not in the column
        somebody scans to find a payment.
        """
        if obj.amount_uzs:
            return format_html(
                '<span style="font-variant-numeric:tabular-nums;white-space:nowrap">{} <span style="color:var(--qs-ink-mute)">{}</span></span>',
                f"{int(obj.amount_uzs):,}".replace(",", " "),
                _("so‘m"),
            )
        return format_html(
            '<span style="font-variant-numeric:tabular-nums;color:var(--qs-ink-soft)">${}</span>',
            f"{obj.total:.2f}",
        )

    @display(description=_("Date"), ordering="-created_at")
    def when(self, obj):
        """One date column, and the one that matters for this row's state.

        Two columns of "16-Avgust, 2026-yil 14:53" took a third of the width to
        say what a short date says, and for an unpaid order the paid column was
        always a dash.
        """
        stamp = obj.paid_at or obj.created_at
        if stamp is None:
            return "—"
        local = timezone.localtime(stamp)
        label = _("paid") if obj.paid_at else _("created")
        return format_html(
            '<span style="white-space:nowrap">{}</span><br><span style="color:var(--qs-ink-mute);font-size:11px">{}</span>',
            local.strftime("%d.%m.%Y %H:%M"),
            label,
        )


def _human_mb(mb: int) -> str:
    """MB or GB, whichever a person would say out loud.

    1948 MB is what the database holds; "1.9 GB" is what the answer sounds like.
    Below a gigabyte MB stays, because "0.2 GB" reads as less precise than the
    number it came from.
    """
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{mb} MB"


def _human_gb(mb: int) -> str:
    """Totals are always in GB: a page-wide sum is never a two-digit MB."""
    return f"{mb / 1024:.1f} GB"


class DataLeftFilter(admin.SimpleListFilter):
    """How much of the allowance is gone.

    The question this page exists to answer is not "which eSIMs are active" but
    "which ones are about to stop working", and those are two different sets: a
    profile at 98% is still `active` and still reads as fine everywhere else.
    "Finished" is separated from "almost gone" because they need different
    actions -- one is a sale you can still make, the other is a support call
    that already happened.
    """

    title = _("Data left")
    parameter_name = "data_left"

    def lookups(self, request, model_admin):
        return (
            ("finished", _("Finished (0 left)")),
            ("low", _("Almost gone (90%+ used)")),
            ("half", _("Past half (50%+ used)")),
            ("unused", _("Never used")),
            ("unlimited", _("Unlimited")),
        )

    def queryset(self, request, queryset):
        value = self.value()
        # data_total_mb == 0 means unlimited in this schema, so every threshold
        # below has to exclude it: a percentage of zero is not a small number,
        # it is not a number.
        if value == "finished":
            return queryset.filter(data_total_mb__gt=0, data_used_mb__gte=F("data_total_mb"))
        if value == "low":
            return queryset.filter(data_total_mb__gt=0, data_used_mb__gte=F("data_total_mb") * 0.9)
        if value == "half":
            return queryset.filter(data_total_mb__gt=0, data_used_mb__gte=F("data_total_mb") * 0.5)
        if value == "unused":
            return queryset.filter(data_used_mb=0)
        if value == "unlimited":
            return queryset.filter(data_total_mb=0)
        return queryset


class ExpiryFilter(admin.SimpleListFilter):
    """When the profile stops working, whatever is left on it.

    Validity runs from purchase, not from first use, so a customer who has not
    installed the eSIM yet is still losing days. That makes "expires soon" a
    thing worth seeing on its own, separately from how much data is left.
    """

    title = _("Expiry")
    parameter_name = "expiry"

    def lookups(self, request, model_admin):
        return (
            ("expired", _("Expired")),
            ("d3", _("Expires within 3 days")),
            ("d7", _("Expires within 7 days")),
            ("live", _("Still valid")),
        )

    def queryset(self, request, queryset):
        value = self.value()
        now = timezone.now()
        if value == "expired":
            return queryset.filter(expires_at__lt=now)
        if value == "d3":
            return queryset.filter(expires_at__gte=now, expires_at__lt=now + timedelta(days=3))
        if value == "d7":
            return queryset.filter(expires_at__gte=now, expires_at__lt=now + timedelta(days=7))
        if value == "live":
            return queryset.filter(expires_at__gte=now)
        return queryset


@admin.register(ESIM)
class ESIMAdmin(ModelAdmin):

    # Fifty rows, not Django's hundred. At 1377 tariffs a full page is 666 KB of
    # HTML, most of it scrolled past unread, and this admin gets used over a phone
    # tether where bytes are the slow part. Fifty still fills a screen several
    # times over.
    list_per_page = 50
    # Ordered as the question is asked: which sale, how much is LEFT, how long
    # has it got. Remaining comes before used because "1.9 GB left" is the
    # answer; "36% used" is the same fact in the form that needs arithmetic.
    list_display = (
        "iccid",
        "customer",
        "plan",
        "status_badge",
        "data_left",
        "usage_bar",
        "days_left",
        "expires_at",
    )
    list_filter = (DataLeftFilter, ExpiryFilter, "status", "plan__network_type")
    search_fields = ("iccid", "customer__email")
    autocomplete_fields = ("order", "plan", "customer")
    readonly_fields = ("qr_preview", "iccid", "qr_payload", "created_at")
    ordering = ("-created_at",)

    def get_queryset(self, request):
        """Annotate remaining and percentage so the columns can be SORTED.

        A computed column that cannot be ordered is half a monitoring page: the
        whole point is to put the nearly-empty profiles at the top. Unlimited
        (total 0) annotates to NULL rather than to a number, so it sorts to one
        end instead of pretending to be the emptiest profile on the page.
        """
        used = Cast(F("data_used_mb"), FloatField())
        total = Cast(F("data_total_mb"), FloatField())
        return (
            super()
            .get_queryset(request)
            .annotate(
                left_mb=Case(
                    When(data_total_mb=0, then=Value(None)),
                    default=F("data_total_mb") - F("data_used_mb"),
                    output_field=IntegerField(),
                ),
                used_pct=Case(
                    When(data_total_mb=0, then=Value(None)),
                    default=used * Value(100.0) / total,
                    output_field=FloatField(),
                ),
            )
        )

    def changelist_view(self, request, extra_context=None):
        """Put the totals in the page title, where they cannot be missed.

        The totals are for the CURRENT filter, so "Finished" answers "how much
        did we sell that is now spent" without a second query anywhere. Done in
        the title rather than a template block because overriding an unfold
        template to show three numbers is a maintenance cost with no upside.
        """
        from django.db.models import Sum

        rows = self.get_changelist_instance(request).get_queryset(request)
        totals = rows.filter(data_total_mb__gt=0).aggregate(
            sold=Sum("data_total_mb"), used=Sum("data_used_mb")
        )
        sold_mb = totals["sold"] or 0
        used_mb = min(totals["used"] or 0, sold_mb)
        extra_context = {
            **(extra_context or {}),
            "title": _("eSIMs — sold %(sold)s, used %(used)s, left %(left)s")
            % {
                "sold": _human_gb(sold_mb),
                "used": _human_gb(used_mb),
                "left": _human_gb(sold_mb - used_mb),
            },
        }
        return super().changelist_view(request, extra_context)

    @display(description=_("Status"))
    def status_badge(self, obj):
        return _status_badge(obj.status, obj.get_status_display())

    @display(description=_("Left"), ordering="left_mb")
    def data_left(self, obj):
        if obj.data_total_mb == 0:
            return format_html('<span style="color:var(--qs-ink-soft);">{}</span>', _("Unlimited"))
        # Clamped at zero: the wholesaler's figure can exceed the allowance, and
        # "-40 MB left" is a number no one can act on.
        left = max(0, obj.data_total_mb - obj.data_used_mb)
        colour = "var(--qs-ink)" if left else "var(--qs-red, #b3261e)"
        return format_html(
            '<strong style="color:{};">{}</strong>', colour, _human_mb(left)
        )

    @display(description=_("Data used"), ordering="used_pct")
    def usage_bar(self, obj):
        if obj.data_total_mb == 0:
            return "Unlimited"
        pct = min(100, round(obj.data_used_mb / obj.data_total_mb * 100))
        return format_html(
            '<div style="width:120px;background:var(--qs-line);border-radius:999px;height:8px;">'
            '<div style="width:{}%;background:var(--qs-blue);height:8px;border-radius:999px;"></div>'
            '</div><span style="font-size:11px;color:var(--qs-ink-soft);">{}% · {}</span>',
            pct,
            pct,
            _human_mb(obj.data_used_mb),
        )

    @display(description=_("Days left"), ordering="expires_at")
    def days_left(self, obj):
        if not obj.expires_at:
            return "—"
        # Rounded UP: a profile with 10 days and 23 hours on it has eleven
        # calendar days of use left, and truncating told the customer ten. The
        # support desk reads this number out loud, so it has to be the one the
        # customer will experience.
        from math import ceil

        seconds = (obj.expires_at - timezone.now()).total_seconds()
        remaining = ceil(seconds / 86400)
        if remaining < 0:
            return format_html('<span style="color:var(--qs-red, #b3261e);">{}</span>', _("expired"))
        return format_html("{}", remaining)

    @display(description=_("QR code"))
    def qr_preview(self, obj):
        if obj.qr_image:
            return format_html('<img src="{}" style="width:160px;height:160px;" />', obj.qr_image)
        return "—"


@admin.register(Payment)
class PaymentAdmin(ModelAdmin):
    """Payments, each with a printable confirmation.

    The confirmation is deliberately NOT called a fiscal receipt. ATMOS is
    registered as our commissioner and issues the fiscal receipt itself, so this
    document is the sale as we recorded it — what was bought, for how much, and
    against which transaction. Labelling it as the fiscal receipt would present a
    non-fiscal document as one, which is the kind of thing a tax inspection
    notices.
    """

    list_display = (
        "provider_ref",
        "order",
        "method",
        "amount",
        "status_badge",
        "receipt_link",
        "created_at",
    )
    list_filter = ("status", "method", "created_at")
    search_fields = ("provider_ref", "order__id", "order__customer__email")
    readonly_fields = ("created_at", "receipt_link")
    ordering = ("-created_at",)
    list_select_related = ("order", "order__customer")
    list_per_page = 50

    def get_urls(self):
        from django.urls import path

        return [
            path(
                "<int:payment_id>/receipt/",
                self.admin_site.admin_view(self.receipt_view),
                name="orders_payment_receipt",
            ),
            *super().get_urls(),
        ]

    def receipt_view(self, request, payment_id: int):
        """One payment as a printable document.

        Reads the order's items rather than re-deriving a total: the amount the
        card was charged is frozen on the order, and a receipt that recomputes it
        from today's prices would disagree with the customer's bank statement the
        moment a price moves.
        """
        from django.conf import settings
        from django.shortcuts import get_object_or_404, render

        payment = get_object_or_404(
            Payment.objects.select_related("order", "order__customer"), pk=payment_id
        )
        order = payment.order
        items = list(
            order.items.select_related("plan", "plan__country", "plan__region").all()
        )
        seller = {
            "name": settings.COMPANY_NAME,
            "inn": settings.COMPANY_INN,
            "address": settings.COMPANY_ADDRESS,
            "phone": settings.COMPANY_PHONE,
            "email": settings.COMPANY_EMAIL,
            "bank": settings.COMPANY_BANK,
        }
        context = {
            **self.admin_site.each_context(request),
            "title": _("Payment confirmation"),
            "opts": self.model._meta,
            "payment": payment,
            "order": order,
            "items": items,
            "seller": seller,
            # Named so the template can say which fields the business still owes
            # us instead of printing blanks.
            "seller_missing": [key for key, value in seller.items() if not value],
        }
        return render(request, "admin/orders/receipt.html", context)

    @display(description=_("Status"))
    def status_badge(self, obj):
        return _status_badge(obj.status, obj.get_status_display())

    @display(description=_("Confirmation"))
    def receipt_link(self, obj):
        if obj.pk is None:
            return "—"
        from django.urls import reverse

        return format_html(
            '<a href="{}" target="_blank" rel="noopener">{}</a>',
            reverse("admin:orders_payment_receipt", args=[obj.pk]),
            _("open"),
        )


@admin.register(PaymeTransaction)
class PaymeTransactionAdmin(ModelAdmin):
    """Read-only by design: Payme owns this lifecycle. Editing a state here
    would desynchronise us from the payment provider's record."""

    list_display = ("transaction_id", "order", "amount_display", "state_badge", "created_at")
    list_filter = ("state", "created_at")
    search_fields = ("transaction_id", "order__id", "account")
    ordering = ("-created_at",)
    list_select_related = ("order",)
    readonly_fields = tuple(f.name for f in PaymeTransaction._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Payme replays CheckTransaction/GetStatement against this table; a
        # deleted row answers "not found" for a transaction Payme knows it
        # performed, which desynchronises reconciliation. Nothing here is ours
        # to remove.
        return False

    @display(description=_("Amount"))
    def amount_display(self, obj):
        return f"{obj.amount_uzs:,.2f} so‘m"

    @display(description=_("State"), ordering="state")
    def state_badge(self, obj):
        styles = {
            1: ("var(--qs-accent-text)", "var(--qs-warn-wash)"),
            2: ("var(--qs-teal-text)", "var(--qs-good-wash)"),
            -1: _NEUTRAL_STYLE,
            -2: ("var(--qs-bad-text)", "var(--qs-bad-wash)"),
        }
        ink, wash = styles.get(obj.state, _NEUTRAL_STYLE)
        return format_html(
            '<span style="display:inline-block;padding:2px 10px;border-radius:999px;'
            'font-size:11px;font-weight:700;color:{};background:{};">{}</span>',
            ink,
            wash,
            obj.get_state_display(),
        )


@admin.register(AtmosTransaction)
class AtmosTransactionAdmin(ModelAdmin):
    """Read-only for the same reason PaymeTransactionAdmin is: the gateway
    reconciles against these rows, and an edited or deleted one answers its
    retries and statements with a record it no longer recognises."""

    # Fifty rows, not Django's hundred. At 1377 tariffs a full page is 666 KB of
    # HTML, most of it scrolled past unread, and this admin gets used over a phone
    # tether where bytes are the slow part. Fifty still fills a screen several
    # times over.
    list_per_page = 50

    list_display = ("transaction_id", "order", "amount_display", "status_badge", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("transaction_id", "order__id", "account")
    ordering = ("-created_at",)
    list_select_related = ("order",)
    readonly_fields = tuple(f.name for f in AtmosTransaction._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @display(description=_("Amount"))
    def amount_display(self, obj):
        return f"{obj.amount_uzs:,.2f} so‘m"

    @display(description=_("Status"), ordering="status")
    def status_badge(self, obj):
        styles = {
            AtmosTransaction.Status.CONFIRMED: ("var(--qs-teal-text)", "var(--qs-good-wash)"),
            AtmosTransaction.Status.REJECTED: ("var(--qs-bad-text)", "var(--qs-bad-wash)"),
        }
        ink, wash = styles.get(obj.status, _NEUTRAL_STYLE)
        return format_html(
            '<span style="display:inline-block;padding:2px 10px;border-radius:999px;'
            'font-size:11px;font-weight:700;color:{};background:{};">{}</span>',
            ink,
            wash,
            obj.get_status_display(),
        )


@admin.register(SupplierPurchase)
class SupplierPurchaseAdmin(ModelAdmin):
    """The retry ledger. Read-only, and the one place a stuck purchase shows up.

    Editing a row here would defeat the constraint it exists to enforce: setting
    a claimed row to "done" by hand tells fulfilment an eSIM was delivered that
    nobody has, and deleting one lets the next retry buy a second copy.

    A row sitting in "Claimed" is the thing worth watching — it means money may
    have left the account without an eSIM to show for it, and it needs checking
    against the supplier's own list rather than guessing.
    """

    # Fifty rows, not Django's hundred. At 1377 tariffs a full page is 666 KB of
    # HTML, most of it scrolled past unread, and this admin gets used over a phone
    # tether where bytes are the slow part. Fifty still fills a screen several
    # times over.
    list_per_page = 50

    list_display = ("created_at", "order", "provider", "line_key", "state_badge", "supplier_ref")
    list_filter = ("provider", "state", "created_at")
    search_fields = ("order__id", "supplier_ref", "iccid", "package_code")
    ordering = ("-created_at",)
    list_select_related = ("order",)
    readonly_fields = tuple(f.name for f in SupplierPurchase._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @display(description=_("State"), ordering="state")
    def state_badge(self, obj):
        styles = {
            SupplierPurchase.State.DONE: ("var(--qs-teal-text)", "var(--qs-good-wash)"),
            SupplierPurchase.State.CLAIMED: ("var(--qs-accent-text)", "var(--qs-warn-wash)"),
            SupplierPurchase.State.FAILED: ("var(--qs-bad-text)", "var(--qs-bad-wash)"),
        }
        ink, wash = styles.get(obj.state, _NEUTRAL_STYLE)
        return format_html(
            '<span style="display:inline-block;padding:2px 10px;border-radius:999px;'
            'font-size:11px;font-weight:700;color:{};background:{};">{}</span>',
            ink,
            wash,
            obj.get_state_display(),
        )

@admin.register(ComplimentaryGrant)
class ComplimentaryGrantAdmin(ModelAdmin):
    """The page for handing someone an eSIM at our cost.

    Add-only by design. A grant is a thing that happened; editing one afterwards
    would rewrite a record of stock given away, and the order it created would
    not change to match. Delete stays available for a mistyped row, which is why
    the order id is shown — the order itself outlives the grant and is what the
    reports read.
    """

    list_display = ("created_at", "customer", "plan", "cost_usd", "reason", "order", "granted_by")
    list_filter = ("created_at",)
    search_fields = ("customer__email", "plan__title", "reason")
    autocomplete_fields = ("customer", "plan")
    readonly_fields = ("cost_usd", "order", "granted_by", "created_at")
    fields = ("customer", "plan", "reason", "cost_usd", "order", "granted_by", "created_at")

    def has_change_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        """Record who did it, then create the order and queue the purchase.

        A failure to reach the worker is raised to the operator rather than
        logged: a grant whose order was never fulfilled looks finished here and
        delivers nothing.
        """
        from django.contrib import messages

        from orders.complimentary import FulfilmentNotQueued, issue

        obj.granted_by = request.user
        super().save_model(request, obj, form, change)
        try:
            issue(obj)
        except FulfilmentNotQueued as exc:
            messages.error(
                request,
                _(
                    "The order was created but the wholesaler was not contacted: %(reason)s. "
                    "Nothing has been delivered — check the worker and try again."
                )
                % {"reason": exc},
            )
            return
        messages.success(
            request,
            _("eSIM ordered at cost ($%(cost)s). It appears under the customer's profile once the wholesaler answers.")
            % {"cost": obj.cost_usd},
        )


@admin.register(TelegramRecipient)
class TelegramRecipientAdmin(ModelAdmin):
    """The list of people and groups the bot writes to.

    Deliberately plain. The only thing that needs saying beyond the fields is why
    a new person hears nothing: Telegram does not let a bot open a conversation,
    so they have to press Start on the bot before their id exists at all.
    """

    list_display = ("label", "chat_id", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("label", "chat_id")
    list_editable = ("is_active",)
    fields = ("label", "chat_id", "is_active")
