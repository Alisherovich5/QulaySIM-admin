from django.db import models


class FAQ(models.Model):
    class Category(models.TextChoices):
        GENERAL = "general", "General"
        SETUP = "setup", "Setup & activation"
        BILLING = "billing", "Billing & payments"
        DEVICE = "device", "Device compatibility"

    # English is the base; _ru/_uz are optional and fall back to the base.
    question = models.CharField(max_length=255)
    answer = models.TextField()
    question_ru = models.CharField(max_length=255, blank=True)
    answer_ru = models.TextField(blank=True)
    question_uz = models.CharField(max_length=255, blank=True)
    answer_uz = models.TextField(blank=True)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.GENERAL)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "content_faq"
        ordering = ["sort_order", "id"]
        verbose_name = "FAQ"
        verbose_name_plural = "FAQs"

    def __str__(self):
        return self.question


class Banner(models.Model):
    title = models.CharField(max_length=120)
    subtitle = models.CharField(max_length=255, blank=True)
    image_url = models.URLField(blank=True)
    cta_text = models.CharField(max_length=60, blank=True)
    cta_link = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "content_banner"
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.title


class Benefit(models.Model):
    """A "why travellers trust us" benefit block on the landing page."""

    icon = models.CharField(
        max_length=40,
        default="Globe2",
        help_text="Lucide icon name, e.g. Globe2, Radio, Zap, Wallet, ShieldCheck.",
    )
    title = models.CharField(max_length=120, help_text="English (base).")
    text = models.TextField(help_text="English (base).")
    title_ru = models.CharField(max_length=120, blank=True)
    text_ru = models.TextField(blank=True)
    title_uz = models.CharField(max_length=120, blank=True)
    text_uz = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "content_benefit"
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
    )

    name = models.CharField(max_length=80)
    location = models.CharField(max_length=120, help_text="English (base), e.g. 'Tashkent → Tokyo'.")
    text = models.TextField(help_text="English (base).")
    location_ru = models.CharField(max_length=120, blank=True)
    text_ru = models.TextField(blank=True)
    location_uz = models.CharField(max_length=120, blank=True)
    text_uz = models.TextField(blank=True)
    rating = models.PositiveSmallIntegerField(default=5)
    moderation_status = models.CharField(
        max_length=10,
        choices=ModerationStatus.choices,
        default=ModerationStatus.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "content_testimonial"
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.name} ({self.rating}★)"


class Device(models.Model):
    """An eSIM-compatible device shown in the compatibility section."""

    name = models.CharField(max_length=120)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "content_device"
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.name


class PromoBanner(models.Model):
    """The welcome-bonus / voucher banner on the landing page.
    The most recently-updated active row is used."""

    eyebrow = models.CharField(max_length=80, help_text="English (base).")
    title = models.CharField(max_length=160, help_text="English (base).")
    text = models.TextField(help_text="English (base). Use {{code}} where the code should appear.")
    eyebrow_ru = models.CharField(max_length=80, blank=True)
    title_ru = models.CharField(max_length=160, blank=True)
    text_ru = models.TextField(blank=True)
    eyebrow_uz = models.CharField(max_length=80, blank=True)
    title_uz = models.CharField(max_length=160, blank=True)
    text_uz = models.TextField(blank=True)
    code = models.CharField(max_length=40, default="WELCOME10")
    cta_link = models.CharField(max_length=255, default="/destinations")
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "content_promobanner"
        ordering = ["-updated_at"]
        verbose_name = "Promo banner"

    def __str__(self):
        return f"{self.title} ({self.code})"
