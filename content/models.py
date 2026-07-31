from django.db import models
from django.utils.translation import gettext_lazy as _


class FAQ(models.Model):
    class Category(models.TextChoices):
        GENERAL = "general", "General"
        SETUP = "setup", "Setup & activation"
        BILLING = "billing", "Billing & payments"
        DEVICE = "device", "Device compatibility"

    # English is the base; _ru/_uz are optional and fall back to the base.
    question = models.CharField(max_length=255, verbose_name=_("question"))
    answer = models.TextField(verbose_name=_("answer"))
    question_ru = models.CharField(max_length=255, blank=True, verbose_name=_("question ru"))
    answer_ru = models.TextField(blank=True, verbose_name=_("answer ru"))
    question_uz = models.CharField(max_length=255, blank=True, verbose_name=_("question uz"))
    answer_uz = models.TextField(blank=True, verbose_name=_("answer uz"))
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.GENERAL, verbose_name=_("category"))
    sort_order = models.PositiveIntegerField(default=0, verbose_name=_("sort order"))
    is_active = models.BooleanField(default=True, verbose_name=_("is active"))

    class Meta:
        db_table = "content_faq"
        verbose_name = _("FAQ")
        verbose_name_plural = _("FAQs")
        ordering = ["sort_order", "id"]
        verbose_name = _("FAQ")
        verbose_name_plural = _("FAQs")

    def __str__(self):
        return self.question


class Banner(models.Model):
    title = models.CharField(max_length=120, verbose_name=_("title"))
    subtitle = models.CharField(max_length=255, blank=True, verbose_name=_("subtitle"))
    image_url = models.URLField(blank=True, verbose_name=_("image url"))
    cta_text = models.CharField(max_length=60, blank=True, verbose_name=_("cta text"))
    cta_link = models.CharField(max_length=255, blank=True, verbose_name=_("cta link"))
    is_active = models.BooleanField(default=True, verbose_name=_("is active"))
    sort_order = models.PositiveIntegerField(default=0, verbose_name=_("sort order"))

    class Meta:
        db_table = "content_banner"
        verbose_name = _("banner")
        verbose_name_plural = _("banners")
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.title


class Benefit(models.Model):
    """A "why travellers trust us" benefit block on the landing page."""

    icon = models.CharField(
        max_length=40,
        default="Globe2",
        help_text=_("Lucide icon name, e.g. Globe2, Radio, Zap, Wallet, ShieldCheck."),
        verbose_name=_("icon"),
    )
    title = models.CharField(max_length=120, help_text=_("English (base)."), verbose_name=_("title"))
    text = models.TextField(help_text=_("English (base)."), verbose_name=_("text"))
    title_ru = models.CharField(max_length=120, blank=True, verbose_name=_("title ru"))
    text_ru = models.TextField(blank=True, verbose_name=_("text ru"))
    title_uz = models.CharField(max_length=120, blank=True, verbose_name=_("title uz"))
    text_uz = models.TextField(blank=True, verbose_name=_("text uz"))
    sort_order = models.PositiveIntegerField(default=0, verbose_name=_("sort order"))
    is_active = models.BooleanField(default=True, verbose_name=_("is active"))

    class Meta:
        db_table = "content_benefit"
        verbose_name = _("benefit")
        verbose_name_plural = _("benefits")
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.title


class Testimonial(models.Model):
    """A customer review shown on the landing page."""

    class ModerationStatus(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    customer = models.OneToOneField(
        "customers.Customer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="testimonial",
        verbose_name=_("customer"),
    )

    name = models.CharField(max_length=80, verbose_name=_("name"))
    location = models.CharField(max_length=120, help_text=_("English (base), e.g. 'Tashkent → Tokyo'."), verbose_name=_("location"))
    text = models.TextField(help_text=_("English (base)."), verbose_name=_("text"))
    location_ru = models.CharField(max_length=120, blank=True, verbose_name=_("location ru"))
    text_ru = models.TextField(blank=True, verbose_name=_("text ru"))
    location_uz = models.CharField(max_length=120, blank=True, verbose_name=_("location uz"))
    text_uz = models.TextField(blank=True, verbose_name=_("text uz"))
    rating = models.PositiveSmallIntegerField(default=5, verbose_name=_("rating"))
    moderation_status = models.CharField(
        max_length=10,
        choices=ModerationStatus.choices,
        default=ModerationStatus.PENDING,
        verbose_name=_("moderation status"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("created at"))
    sort_order = models.PositiveIntegerField(default=0, verbose_name=_("sort order"))
    is_active = models.BooleanField(default=True, verbose_name=_("is active"))

    class Meta:
        db_table = "content_testimonial"
        verbose_name = _("testimonial")
        verbose_name_plural = _("testimonials")
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.name} ({self.rating}★)"


class Device(models.Model):
    """An eSIM-compatible device shown in the compatibility section."""

    name = models.CharField(max_length=120, verbose_name=_("name"))
    sort_order = models.PositiveIntegerField(default=0, verbose_name=_("sort order"))
    is_active = models.BooleanField(default=True, verbose_name=_("is active"))

    class Meta:
        db_table = "content_device"
        verbose_name = _("device")
        verbose_name_plural = _("devices")
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.name


class PromoBanner(models.Model):
    """The welcome-bonus / voucher banner on the landing page.
    The most recently-updated active row is used."""

    eyebrow = models.CharField(max_length=80, help_text=_("English (base)."), verbose_name=_("eyebrow"))
    title = models.CharField(max_length=160, help_text=_("English (base)."), verbose_name=_("title"))
    text = models.TextField(help_text=_("English (base). Use {{code}} where the code should appear."), verbose_name=_("text"))
    eyebrow_ru = models.CharField(max_length=80, blank=True, verbose_name=_("eyebrow ru"))
    title_ru = models.CharField(max_length=160, blank=True, verbose_name=_("title ru"))
    text_ru = models.TextField(blank=True, verbose_name=_("text ru"))
    eyebrow_uz = models.CharField(max_length=80, blank=True, verbose_name=_("eyebrow uz"))
    title_uz = models.CharField(max_length=160, blank=True, verbose_name=_("title uz"))
    text_uz = models.TextField(blank=True, verbose_name=_("text uz"))
    # Free-text fallback, kept for banners created before the link existed.
    code = models.CharField(max_length=40, default="WELCOME10", verbose_name=_("code"))
    # The discount the banner advertises is read from the code that actually
    # applies it, so the two cannot disagree. Advertising 20% while checkout
    # takes 10% off is the kind of mistake a second copy of the number invites.
    promo_code = models.ForeignKey(
        "orders.PromoCode",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="banners",
        help_text=_(
            "Link the promo code this banner advertises. Its discount — percent "
            "or fixed amount — is what the site shows, so there is one number to "
            "change, not two."
        ),
        verbose_name=_("promo code"),
    )
    # Shown in the thin bar above the navigation, where there is no room for the
    # full sentence. Left blank, the site falls back to its built-in wording.
    strip_text = models.CharField(
        max_length=60, blank=True, help_text=_("Short bar text, English. e.g. '20$ chegirma'"),
        verbose_name=_("strip text"),
    )
    strip_text_ru = models.CharField(max_length=60, blank=True, verbose_name=_("strip text ru"))
    strip_text_uz = models.CharField(max_length=60, blank=True, verbose_name=_("strip text uz"))
    cta_link = models.CharField(max_length=255, default="/destinations", verbose_name=_("cta link"))
    is_active = models.BooleanField(default=True, verbose_name=_("is active"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("updated at"))

    class Meta:
        db_table = "content_promobanner"
        verbose_name = _("Promo banner")
        verbose_name_plural = _("Promo banners")
        ordering = ["-updated_at"]
        verbose_name = _("Promo banner")

    def __str__(self):
        return f"{self.title} ({self.code})"
