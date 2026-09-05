from django.db import models
from django.utils.translation import gettext_lazy as _


class Customer(models.Model):
    """Storefront customer. Passwords are hashed by the FastAPI service
    (passlib/bcrypt) and stored here; Django admin treats this read-mostly."""

    email = models.EmailField(unique=True, verbose_name=_("email"))
    full_name = models.CharField(max_length=150, blank=True, verbose_name=_("full name"))
    # Blank for accounts that only ever sign in through a provider. The API's
    # verify_password rejects an empty hash with a constant-time decoy, so a
    # blank value is not a password anyone can guess — it is the absence of one.
    hashed_password = models.CharField(max_length=255, blank=True, verbose_name=_("hashed password"))
    is_active = models.BooleanField(default=True, verbose_name=_("is active"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("created at"))
    # The avatar lives in the database rather than a media volume: at this size
    # it is a few kilobytes, and keeping it here means the nightly pg_dump
    # already backs it up, with no volume to mount, no extra route to serve and
    # no cross-origin image for the storefront's CSP to allow.
    #
    # Always re-encoded WebP written by the API, never the bytes the customer
    # uploaded — that is what strips EXIF and anything hidden in the original.
    avatar_webp = models.BinaryField(null=True, blank=True, editable=False, verbose_name=_("avatar webp"))
    avatar_updated_at = models.DateTimeField(null=True, blank=True, editable=False, verbose_name=_("avatar updated at"))
    # Referral program
    referral_code = models.CharField(max_length=12, unique=True, null=True, blank=True, verbose_name=_("referral code"))
    referred_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="referrals_made",
        verbose_name=_("referred by"),
    )

    class Meta:
        db_table = "customers_customer"
        verbose_name = _("customer")
        verbose_name_plural = _("customers")
        ordering = ["-created_at"]

    def __str__(self):
        return self.email


class Referral(models.Model):
    """Tracks one invited customer and the reward granted to the referrer
    once the invitee makes their first purchase."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"

    referrer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="referrals",
        verbose_name=_("referrer"),
    )
    referred = models.ForeignKey(
        Customer,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="referred_via",
        verbose_name=_("referred"),
    )
    referred_email = models.EmailField(blank=True, verbose_name=_("referred email"))
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, verbose_name=_("status"))
    reward_code = models.CharField(max_length=40, blank=True, verbose_name=_("reward code"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("created at"))
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name=_("completed at"))

    class Meta:
        db_table = "customers_referral"
        verbose_name = _("referral")
        verbose_name_plural = _("referrals")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.referrer_id} → {self.referred_email or self.referred_id} ({self.status})"


class SocialAccount(models.Model):
    """A provider identity linked to a customer.

    Its own table rather than columns on Customer, so one person can sign in
    with Google today and add another provider later without the model growing
    a pair of fields per provider.

    Linking is keyed on the provider's own user id, never on the e-mail: an
    address can change hands at the provider, and matching on it would hand the
    old owner's orders to the new one.
    """

    class Provider(models.TextChoices):
        GOOGLE = "google", "Google"
        TELEGRAM = "telegram", "Telegram"

    customer = models.ForeignKey(
        "customers.Customer", on_delete=models.CASCADE, related_name="social_accounts",
        verbose_name=_("customer"),
    )
    provider = models.CharField(max_length=20, choices=Provider.choices, verbose_name=_("provider"))
    provider_uid = models.CharField(
        max_length=191, help_text=_("The provider's own immutable id for this user."),
        verbose_name=_("provider uid"),
    )
    email = models.EmailField(
        blank=True, help_text=_("Address as the provider reported it, for support only."),
        verbose_name=_("email"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("created at"))
    last_login_at = models.DateTimeField(null=True, blank=True, verbose_name=_("last login at"))

    class Meta:
        db_table = "customers_socialaccount"
        verbose_name = _("social account")
        verbose_name_plural = _("social accounts")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_uid"], name="one_link_per_provider_identity"
            ),
            models.UniqueConstraint(
                fields=["provider", "customer"], name="one_link_per_customer_per_provider"
            ),
        ]

    def __str__(self):
        return f"{self.get_provider_display()} — {self.customer_id}"


class ReferralAgent(Customer):
    """Taklif havolasi orqali odam olib kelganlar -- alohida sahifa sifatida.

    Proksi, chunki bu boshqa jadval emas, o'sha mijozlarning bir qismi. Alohida
    sahifa kerak, chunki mijozlarning aksariyati hech kimni taklif qilmagan va
    ularni umumiy ro'yxatda qidirish agentni topishni qiyinlashtiradi.
    """

    class Meta:
        proxy = True
        verbose_name = "Agent (referal)"
        verbose_name_plural = "Agentlar va referallar"
