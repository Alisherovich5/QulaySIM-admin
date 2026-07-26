from django.contrib import admin
from django import forms
from django.db import models
from django.db.models import Count, Min
from django.utils.html import format_html
from unfold.admin import ModelAdmin
from unfold.decorators import display

from .models import Country, Plan, PricingRule, Region


@admin.register(Region)
class RegionAdmin(ModelAdmin):
    list_display = ("name", "slug", "country_count", "sort_order")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("sort_order", "name")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_country_count=Count("countries"))

    @display(description="Countries", ordering="_country_count")
    def country_count(self, obj):
        return obj._country_count


@admin.register(Country)
class CountryAdmin(ModelAdmin):
    list_display = ("flag", "name", "iso2", "region", "is_popular", "is_active", "from_price")
    list_display_links = ("flag", "name")
    list_filter = ("region", "is_popular", "is_active")
    search_fields = ("name", "iso2")
    prepopulated_fields = {"slug": ("name",)}
    list_editable = ("is_popular", "is_active")
    autocomplete_fields = ("region",)
    ordering = ("sort_order", "name")
    list_select_related = ("region",)

    def get_queryset(self, request):
        # Annotate instead of hitting `starting_price` per row: the property
        # issues one query each, so a 100-row page cost 100 extra queries.
        return (
            super()
            .get_queryset(request)
            .annotate(
                _starting_price=Min(
                    "plans__price_usd", filter=models.Q(plans__is_active=True)
                )
            )
        )

    @display(description="")
    def flag(self, obj):
        return format_html(
            '<span style="font-family:monospace;font-weight:700;letter-spacing:1px;'
            'padding:2px 6px;border-radius:4px;background:#EEF2FF;color:#1B4DFF;">{}</span>',
            obj.iso2,
        )

    @display(description="From", ordering="_starting_price")
    def from_price(self, obj):
        price = obj._starting_price
        return f"${price}" if price is not None else "—"


@admin.register(Plan)
class PlanAdmin(ModelAdmin):
    list_display = (
        "title",
        "scope",
        "target",
        "data_col",
        "validity_days",
        "network_type",
        "provider",
        "cost_col",
        "price_badge",
        "margin_col",
        "is_popular",
        "is_active",
    )
    list_filter = (
        "scope",
        "network_type",
        "provider",
        "price_locked",
        "is_popular",
        "is_active",
        "is_unlimited",
    )
    search_fields = ("title", "country__name", "region__name", "provider_package_code")
    list_editable = ("is_popular", "is_active")
    autocomplete_fields = ("country", "region")
    ordering = ("sort_order", "price_usd")
    list_select_related = ("country", "region")
    actions = ("recalculate_prices",)
    fieldsets = (
        ("Plan", {"fields": ("title", "scope", "country", "region")}),
        (
            "Data & validity",
            {
                "fields": (
                    "data_amount_mb",
                    "is_unlimited",
                    "validity_days",
                    "network_type",
                    "supports_hotspot",
                    "provider",
                    "provider_package_code",
                )
            },
        ),
        (
            "Pricing",
            {
                "fields": (
                    "cost_usd",
                    "markup_percent",
                    "price_usd",
                    "price_locked",
                    "margin_readout",
                ),
                "description": (
                    "Enter the supplier cost and the price is calculated on save. "
                    "Leave the markup empty to inherit a pricing rule; fill it in to "
                    "override every rule for this plan alone. Tick 'price locked' to "
                    "type a price by hand and stop it being recalculated."
                ),
            },
        ),
        ("Visibility", {"fields": ("is_popular", "is_active", "sort_order")}),
    )
    readonly_fields = ("margin_readout",)

    @display(description="Target")
    def target(self, obj):
        return obj.country or obj.region or "Global"

    @display(description="Data")
    def data_col(self, obj):
        return obj.data_label

    @display(description="Cost")
    def cost_col(self, obj):
        return f"${obj.cost_usd}" if obj.cost_usd is not None else "—"

    @display(description="Price")
    def price_badge(self, obj):
        lock = " 🔒" if obj.price_locked else ""
        return format_html(
            '<span style="font-weight:700;color:#0A1F5C;">${}</span>{}', obj.price_usd, lock
        )

    @display(description="Margin")
    def margin_col(self, obj):
        amount = obj.margin_usd
        if amount is None:
            return "—"
        percent = obj.margin_percent
        colour = "#00A37A" if amount > 0 else "#E5484D"
        return format_html(
            '<span style="color:{};font-weight:600;">${}</span>'
            '<span style="color:#5B6478;font-size:11px;"> ({}%)</span>',
            colour,
            amount,
            percent,
        )

    @display(description="Calculated margin")
    def margin_readout(self, obj):
        if obj.pk is None or obj.cost_usd is None:
            return "Enter a supplier cost to see the margin."
        rule = obj.markup_percent
        source = (
            f"this plan's own {rule}% markup"
            if rule is not None
            else "the governing pricing rule"
        )
        return format_html(
            "Cost ${} → price ${} · profit <b>${}</b> ({}%), using {}.",
            obj.cost_usd,
            obj.price_usd,
            obj.margin_usd,
            obj.margin_percent,
            source,
        )

    @admin.action(description="Recalculate prices from cost and rules")
    def recalculate_prices(self, request, queryset):
        rules = list(PricingRule.objects.filter(is_active=True))
        changed = 0
        for plan in queryset:
            if plan.recalculate_price(rules):
                plan.save(update_fields=["price_usd"])
                changed += 1
        skipped = queryset.count() - changed
        self.message_user(
            request, f"{changed} price(s) updated, {skipped} unchanged or locked."
        )


@admin.register(PricingRule)
class PricingRuleAdmin(ModelAdmin):
    """Markup rules. The most specific active rule wins:
    plan override → destination → supplier → everything."""

    list_display = ("scope_label", "markup_badge", "min_margin_usd", "rounding", "affected", "is_active")
    list_filter = ("scope", "is_active", "rounding")
    list_editable = ("is_active",)
    autocomplete_fields = ("country",)
    search_fields = ("provider", "country__name", "note")
    actions = ("apply_to_catalogue",)
    fieldsets = (
        (
            "Applies to",
            {
                "fields": ("scope", "provider", "country"),
                "description": (
                    "Pick one. 'Everything' is the house default; a supplier or "
                    "destination rule overrides it for the plans it covers."
                ),
            },
        ),
        ("Markup", {"fields": ("markup_percent", "min_margin_usd", "rounding")}),
        ("Admin", {"fields": ("is_active", "note")}),
    )

    @display(description="Applies to", ordering="scope")
    def scope_label(self, obj):
        return str(obj).rsplit(" +", 1)[0]

    @display(description="Markup", ordering="markup_percent")
    def markup_badge(self, obj):
        return format_html(
            '<span style="font-weight:700;color:#0A1F5C;">+{}%</span>', obj.markup_percent
        )

    def get_queryset(self, request):
        # Counting plans per row would be one query per rule. Tally the whole
        # catalogue once and look the numbers up in memory instead.
        qs = super().get_queryset(request).select_related("country")
        plans = Plan.objects.filter(is_active=True)
        self._plan_total = plans.count()
        self._by_provider = dict(
            plans.values_list("provider").annotate(c=Count("id")).values_list("provider", "c")
        )
        self._by_country = dict(
            plans.values_list("country_id").annotate(c=Count("id")).values_list("country_id", "c")
        )
        return qs

    @display(description="Plans covered")
    def affected(self, obj):
        if obj.scope == PricingRule.Scope.PROVIDER:
            return self._by_provider.get(obj.provider, 0)
        if obj.scope == PricingRule.Scope.COUNTRY:
            return self._by_country.get(obj.country_id, 0)
        return self._plan_total

    @admin.action(description="Recalculate every affected plan now")
    def apply_to_catalogue(self, request, queryset):
        rules = list(PricingRule.objects.filter(is_active=True))
        plans = Plan.objects.filter(price_locked=False, cost_usd__isnull=False)
        changed = 0
        for plan in plans:
            if plan.recalculate_price(rules):
                plan.save(update_fields=["price_usd"])
                changed += 1
        self.message_user(request, f"{changed} price(s) recalculated across the catalogue.")
