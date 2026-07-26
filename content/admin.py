from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import FAQ, Banner, Benefit, Device, PromoBanner, Testimonial


@admin.register(FAQ)
class FAQAdmin(ModelAdmin):
    list_display = ("question", "category", "sort_order", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("question", "answer")
    list_editable = ("sort_order", "is_active")
    ordering = ("sort_order", "id")
    fieldsets = (
        (None, {"fields": ("category", "sort_order", "is_active")}),
        ("English", {"fields": ("question", "answer")}),
        ("Русский", {"fields": ("question_ru", "answer_ru")}),
        ("Oʻzbekcha", {"fields": ("question_uz", "answer_uz")}),
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
        ("English", {"fields": ("title", "text")}),
        ("Русский", {"fields": ("title_ru", "text_ru")}),
        ("Oʻzbekcha", {"fields": ("title_uz", "text_uz")}),
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
        ("English", {"fields": ("location", "text")}),
        ("Русский", {"fields": ("location_ru", "text_ru")}),
        ("Oʻzbekcha", {"fields": ("location_uz", "text_uz")}),
    )

    @admin.action(description="Approve selected reviews")
    def approve_reviews(self, request, queryset):
        queryset.update(moderation_status=Testimonial.ModerationStatus.APPROVED, is_active=True)

    @admin.action(description="Reject selected reviews")
    def reject_reviews(self, request, queryset):
        queryset.update(moderation_status=Testimonial.ModerationStatus.REJECTED)


@admin.register(Device)
class DeviceAdmin(ModelAdmin):
    list_display = ("name", "sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)
    list_editable = ("sort_order", "is_active")
    ordering = ("sort_order", "id")


@admin.register(PromoBanner)
class PromoBannerAdmin(ModelAdmin):
    list_display = ("title", "code", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("title", "code")
    fieldsets = (
        (None, {"fields": ("code", "cta_link", "is_active")}),
        ("English", {"fields": ("eyebrow", "title", "text")}),
        ("Русский", {"fields": ("eyebrow_ru", "title_ru", "text_ru")}),
        ("Oʻzbekcha", {"fields": ("eyebrow_uz", "title_uz", "text_uz")}),
    )


@admin.register(Banner)
class BannerAdmin(ModelAdmin):
    list_display = ("title", "subtitle", "is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("title", "subtitle")
    list_editable = ("is_active", "sort_order")
    ordering = ("sort_order", "id")
