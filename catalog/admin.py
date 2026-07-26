from django.contrib import admin
from django.utils.html import format_html
from unfold.admin import ModelAdmin
from unfold.decorators import display

from .models import Country, Plan, Region


@admin.register(Region)
class RegionAdmin(ModelAdmin):
    list_display = ("name", "slug", "country_count", "sort_order")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("sort_order", "name")

    @display(description="Countries")
    def country_count(self, obj):
        return obj.countries.count()


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

    @display(description="")
    def flag(self, obj):
        return format_html(
            '<span style="font-family:monospace;font-weight:700;letter-spacing:1px;'
            'padding:2px 6px;border-radius:4px;background:#EEF2FF;color:#1B4DFF;">{}</span>',
            obj.iso2,
        )

    @display(description="From")
    def from_price(self, obj):
        price = obj.starting_price
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
        "price_badge",
        "is_popular",
        "is_active",
    )
    list_filter = ("scope", "network_type", "is_popular", "is_active", "is_unlimited")
    search_fields = ("title", "country__name", "region__name", "provider_package_code")
    list_editable = ("is_popular", "is_active")
    autocomplete_fields = ("country", "region")
    ordering = ("sort_order", "price_usd")
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
            "Pricing & visibility",
            {"fields": ("price_usd", "is_popular", "is_active", "sort_order")},
        ),
    )

    @display(description="Target")
    def target(self, obj):
        return obj.country or obj.region or "Global"

    @display(description="Data")
    def data_col(self, obj):
        return obj.data_label

    @display(description="Price")
    def price_badge(self, obj):
        return format_html(
            '<span style="font-weight:700;color:#0A1F5C;">${}</span>', obj.price_usd
        )
