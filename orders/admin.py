from django.contrib import admin
from django.db.models import Count
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from .models import ESIM, Order, OrderItem, Payment, PaymeTransaction, PromoCode

_STATUS_COLORS = {
    "pending": "#FFB020",
    "paid": "#00C9A7",
    "active": "#00C9A7",
    "success": "#00C9A7",
    "cancelled": "#5B6478",
    "expired": "#5B6478",
    "refunded": "#E5484D",
    "failed": "#E5484D",
}


def _status_badge(value, label):
    color = _STATUS_COLORS.get(value, "#5B6478")
    return format_html(
        '<span style="display:inline-block;padding:2px 10px;border-radius:999px;'
        'font-size:11px;font-weight:700;color:#fff;background:{};">{}</span>',
        color,
        label,
    )


class OrderItemInline(TabularInline):
    model = OrderItem
    extra = 0
    autocomplete_fields = ("plan",)
    readonly_fields = ("unit_price",)


class ESIMInline(TabularInline):
    model = ESIM
    extra = 0
    fields = ("iccid", "plan", "status", "data_total_mb", "data_used_mb", "expires_at")
    readonly_fields = fields
    can_delete = False
    show_change_link = True


@admin.register(PromoCode)
class PromoCodeAdmin(ModelAdmin):
    list_display = ("code", "discount_type", "discount_value", "usage", "is_active", "valid_until")
    list_filter = ("discount_type", "is_active")
    search_fields = ("code",)
    ordering = ("-created_at",)

    @display(description="Usage")
    def usage(self, obj):
        cap = obj.max_uses or "∞"
        return f"{obj.used_count} / {cap}"


@admin.register(Order)
class OrderAdmin(ModelAdmin):
    list_display = ("id", "customer", "status_badge", "total", "item_count", "created_at", "paid_at")
    list_filter = ("status", "created_at")
    search_fields = ("id", "customer__email")
    autocomplete_fields = ("customer", "promo_code")
    readonly_fields = ("created_at", "paid_at", "subtotal", "discount", "total")
    inlines = (OrderItemInline, ESIMInline)
    ordering = ("-created_at",)
    list_filter_submit = True
    list_select_related = ("customer",)

    def get_queryset(self, request):
        # `obj.items.count()` per row was one query per order — 100 extra
        # queries on a default-sized page.
        return super().get_queryset(request).annotate(_item_count=Count("items"))

    @display(description="Status")
    def status_badge(self, obj):
        return _status_badge(obj.status, obj.get_status_display())

    @display(description="Items", ordering="_item_count")
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

    @display(description="Status")
    def status_badge(self, obj):
        return _status_badge(obj.status, obj.get_status_display())

    @display(description="Data used")
    def usage_bar(self, obj):
        if obj.data_total_mb == 0:
            return "Unlimited"
        pct = min(100, round(obj.data_used_mb / obj.data_total_mb * 100))
        return format_html(
            '<div style="width:120px;background:#E3E8F0;border-radius:999px;height:8px;">'
            '<div style="width:{}%;background:#1B4DFF;height:8px;border-radius:999px;"></div>'
            '</div><span style="font-size:11px;color:#5B6478;">{}%</span>',
            pct,
            pct,
        )

    @display(description="QR code")
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

    @display(description="Status")
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

    @display(description="Amount")
    def amount_display(self, obj):
        return f"{obj.amount_uzs:,.2f} so‘m"

    @display(description="State", ordering="state")
    def state_badge(self, obj):
        colours = {1: "#FFB020", 2: "#00C9A7", -1: "#5B6478", -2: "#E5484D"}
        return format_html(
            '<span style="display:inline-block;padding:2px 10px;border-radius:999px;'
            'font-size:11px;font-weight:700;color:#fff;background:{};">{}</span>',
            colours.get(obj.state, "#5B6478"),
            obj.get_state_display(),
        )
