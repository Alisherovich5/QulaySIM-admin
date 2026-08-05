from django import forms
from django.contrib import admin
from django.db.models import Count
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from .models import (
    ESIM,
    AtmosTransaction,
    Order,
    OrderItem,
    Payment,
    PaymeTransaction,
    PromoCode,
    SupplierPurchase,
)
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
    list_display = ("id", "customer", "status_badge", "total", "item_count", "created_at", "paid_at")
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
        # queries on a default-sized page.
        return super().get_queryset(request).annotate(_item_count=Count("items"))

    @display(description=_("Status"))
    def status_badge(self, obj):
        return _status_badge(obj.status, obj.get_status_display())

    @display(description=_("Items"), ordering="_item_count")
    def item_count(self, obj):
        return obj._item_count


@admin.register(ESIM)
class ESIMAdmin(ModelAdmin):
    list_display = ("iccid", "customer", "plan", "status_badge", "usage_bar", "expires_at")
    list_filter = ("status", "plan__network_type")
    search_fields = ("iccid", "customer__email")
    autocomplete_fields = ("order", "plan", "customer")
    readonly_fields = ("qr_preview", "iccid", "qr_payload", "created_at")
    ordering = ("-created_at",)

    @display(description=_("Status"))
    def status_badge(self, obj):
        return _status_badge(obj.status, obj.get_status_display())

    @display(description=_("Data used"))
    def usage_bar(self, obj):
        if obj.data_total_mb == 0:
            return "Unlimited"
        pct = min(100, round(obj.data_used_mb / obj.data_total_mb * 100))
        return format_html(
            '<div style="width:120px;background:var(--qs-line);border-radius:999px;height:8px;">'
            '<div style="width:{}%;background:var(--qs-blue);height:8px;border-radius:999px;"></div>'
            '</div><span style="font-size:11px;color:var(--qs-ink-soft);">{}%</span>',
            pct,
            pct,
        )

    @display(description=_("QR code"))
    def qr_preview(self, obj):
        if obj.qr_image:
            return format_html('<img src="{}" style="width:160px;height:160px;" />', obj.qr_image)
        return "—"


@admin.register(Payment)
class PaymentAdmin(ModelAdmin):
    list_display = ("provider_ref", "order", "method", "amount", "status_badge", "created_at")
    list_filter = ("status", "method", "created_at")
    search_fields = ("provider_ref", "order__id")
    readonly_fields = ("created_at",)
    ordering = ("-created_at",)

    @display(description=_("Status"))
    def status_badge(self, obj):
        return _status_badge(obj.status, obj.get_status_display())


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
