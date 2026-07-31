from django.contrib import admin
from django.utils.html import format_html
from unfold.admin import ModelAdmin
from unfold.decorators import display

from .models import FAQ, Banner, Benefit, Device, PromoBanner, Testimonial
from django.utils.translation import gettext_lazy as _


@admin.register(FAQ)
class FAQAdmin(ModelAdmin):
    list_display = ("question", "category", "sort_order", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("question", "answer")
    list_editable = ("sort_order", "is_active")
    ordering = ("sort_order", "id")
    fieldsets = (
        (None, {"fields": ("category", "sort_order", "is_active")}),
        (_("English"), {"fields": ("question", "answer")}),
        (_("Русский"), {"fields": ("question_ru", "answer_ru")}),
        (_("Oʻzbekcha"), {"fields": ("question_uz", "answer_uz")}),
    )


@admin.register(Benefit)
class BenefitAdmin(ModelAdmin):
    list_display = ("title", "icon", "sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("title", "text")
    list_editable = ("sort_order", "is_active")
    ordering = ("sort_order", "id")
    fieldsets = (
        (None, {"fields": ("icon", "sort_order", "is_active")}),
        (_("English"), {"fields": ("title", "text")}),
        (_("Русский"), {"fields": ("title_ru", "text_ru")}),
        (_("Oʻzbekcha"), {"fields": ("title_uz", "text_uz")}),
    )


@admin.register(Testimonial)
class TestimonialAdmin(ModelAdmin):
    list_display = ("name", "location", "rating", "moderation_status", "created_at", "is_active")
    list_filter = ("moderation_status", "is_active", "rating")
    search_fields = ("name", "text", "customer__email")
    list_editable = ("moderation_status", "is_active")
    ordering = ("-created_at", "id")
    actions = ("approve_reviews", "reject_reviews")
    fieldsets = (
        (None, {"fields": ("customer", "name", "rating", "moderation_status", "sort_order", "is_active")}),
        (_("English"), {"fields": ("location", "text")}),
        (_("Русский"), {"fields": ("location_ru", "text_ru")}),
        (_("Oʻzbekcha"), {"fields": ("location_uz", "text_uz")}),
    )

    @admin.action(description=_("Approve selected reviews"))
    def approve_reviews(self, request, queryset):
        queryset.update(moderation_status=Testimonial.ModerationStatus.APPROVED, is_active=True)

    @admin.action(description=_("Reject selected reviews"))
    def reject_reviews(self, request, queryset):
        queryset.update(moderation_status=Testimonial.ModerationStatus.REJECTED)


@admin.register(Device)
class DeviceAdmin(ModelAdmin):
    list_display = ("name", "sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)
    list_editable = ("sort_order", "is_active")
    ordering = ("sort_order", "id")


def _money_label(value) -> str:
    """Drop trailing zeros without falling into scientific notation.

    Decimal("10.00") formats as "10.00" under :g and as "1e+1" after normalize(),
    so neither shortcut works. This is the number an operator reads when deciding
    what to advertise, and "10.00%" reads like a mistake.
    """
    text = f"{value:f}".rstrip("0").rstrip(".")
    return text or "0"


@admin.register(PromoBanner)
class PromoBannerAdmin(ModelAdmin):
    """The offer bar pinned above the navigation, and the landing-page banner.

    Link a promo code and the discount shown on the site is read from it, so
    changing 10% to 20% — or to a fixed $20 — is one edit in one place. Typing
    the figure into the copy as well is how a site ends up advertising a discount
    checkout does not give.
    """

    list_display = ("title", "code_col", "discount_col", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("title", "code")
    autocomplete_fields = ("promo_code",)
    list_select_related = ("promo_code",)
    readonly_fields = ("discount_readout",)
    fieldsets = (
        (
            _("Offer"),
            {
                "fields": ("promo_code", "discount_readout", "code", "cta_link", "is_active"),
                "description": _(
                    "Pick the promo code this advertises. The discount comes from that "
                    "code, so the bar and the checkout always agree. The plain 'code' "
                    "field is only used when nothing is linked."
                ),
            },
        ),
        (
            _("Bar text (short)"),
            {
                "fields": ("strip_text", "strip_text_ru", "strip_text_uz"),
                "description": _(
                    "Optional. Leave empty and the bar writes itself from the "
                    "discount — \"20% off\" or \"$20 off\"."
                ),
            },
        ),
        (_("English"), {"fields": ("eyebrow", "title", "text")}),
        (_("Русский"), {"fields": ("eyebrow_ru", "title_ru", "text_ru")}),
        (_("Oʻzbekcha"), {"fields": ("eyebrow_uz", "title_uz", "text_uz")}),
    )

    @display(description=_("Code"), ordering="promo_code__code")
    def code_col(self, obj):
        if obj.promo_code:
            return obj.promo_code.code
        return format_html(
            '<span style="color:var(--qs-ink-mute);">{} ({})</span>',
            obj.code,
            _("not linked"),
        )

    @display(description=_("Discount"))
    def discount_col(self, obj):
        code = obj.promo_code
        if code is None:
            return format_html(
                '<span style="color:var(--qs-bad-text);">{}</span>', _("no code linked")
            )
        if not code.is_active:
            return format_html(
                '<span style="color:var(--qs-bad-text);">{}</span>', _("code disabled")
            )
        value = (
            f"{_money_label(code.discount_value)}%"
            if code.discount_type == "percent"
            else f"${_money_label(code.discount_value)}"
        )
        return format_html('<b>{}</b>', value)

    @display(description=_("What the site will show"))
    def discount_readout(self, obj):
        code = obj.promo_code
        if code is None:
            return _(
                "No promo code linked, so the bar falls back to its built-in wording "
                "and the site shows no figure. Link one above."
            )
        if not code.is_active:
            return format_html(
                '<span style="color:var(--qs-bad-text);">{}</span>',
                _(
                    "%(code)s is disabled, so nothing is advertised. An expired "
                    "offer must not stay on the bar."
                )
                % {"code": code.code},
            )
        shown = (
            _("%(value)s%% off") % {"value": _money_label(code.discount_value)}
            if code.discount_type == "percent"
            else _("$%(value)s off") % {"value": _money_label(code.discount_value)}
        )
        # The values stay as format_html arguments rather than being interpolated
        # into the format string: a promo code is operator-entered text, and
        # pre-interpolating it would put it into the markup unescaped.
        return format_html(
            _("Bar: <b>{shown}</b> · code <b>{code}</b> — the same discount checkout applies."),
            shown=shown,
            code=code.code,
        )


@admin.register(Banner)
class BannerAdmin(ModelAdmin):
    list_display = ("title", "subtitle", "is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("title", "subtitle")
    list_editable = ("is_active", "sort_order")
    ordering = ("sort_order", "id")
