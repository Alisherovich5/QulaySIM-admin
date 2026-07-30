from django.contrib import admin
from django.db.models import Count
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from unfold.admin import ModelAdmin
from unfold.decorators import display
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from .models import Customer, Referral, SocialAccount

# Re-register Django's built-in auth models with Unfold styling.
admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass


@admin.register(Customer)
class CustomerAdmin(ModelAdmin):
    list_display = ("email", "full_name", "order_count", "referral_code", "is_active", "created_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("email", "full_name", "referral_code")
    readonly_fields = ("hashed_password", "created_at", "referral_code")
    autocomplete_fields = ("referred_by",)
    ordering = ("-created_at",)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_order_count=Count("orders"))

    @display(description="Orders", ordering="_order_count")
    def order_count(self, obj):
        return obj._order_count


@admin.register(Referral)
class ReferralAdmin(ModelAdmin):
    list_display = ("referrer", "referred_email", "status", "reward_code", "created_at", "completed_at")
    list_filter = ("status", "created_at")
    search_fields = ("referrer__email", "referred_email", "reward_code")
    readonly_fields = ("created_at", "completed_at")
    ordering = ("-created_at",)


@admin.register(SocialAccount)
class SocialAccountAdmin(ModelAdmin):
    """Read-only view of provider links, for answering "why can't I sign in?".

    Everything is read-only on purpose: editing a provider uid by hand would
    silently hand one person's account to another, and nothing in support needs
    that. Unlinking is a delete, which is auditable.
    """

    list_display = ("customer_email", "provider", "uid_tail", "created_at", "last_login_at")
    list_filter = ("provider",)
    search_fields = ("customer__email", "email", "provider_uid")
    list_select_related = ("customer",)
    ordering = ("-created_at",)
    readonly_fields = ("customer", "provider", "provider_uid", "email", "created_at", "last_login_at")

    def has_add_permission(self, request):
        # A link is only ever created by a verified sign-in.
        return False

    @display(description="Customer", ordering="customer__email")
    def customer_email(self, obj):
        return obj.customer.email

    @display(description="Provider id")
    def uid_tail(self, obj):
        # The full id is not secret, but it is long and identifies a person at
        # the provider; the tail is enough to match up a support request.
        return f"…{obj.provider_uid[-8:]}" if len(obj.provider_uid) > 8 else obj.provider_uid
