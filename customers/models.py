from django.db import models


class Customer(models.Model):
    """Storefront customer. Passwords are hashed by the FastAPI service
    (passlib/bcrypt) and stored here; Django admin treats this read-mostly."""

    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=150, blank=True)
    hashed_password = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Referral program
    referral_code = models.CharField(max_length=12, unique=True, null=True, blank=True)
    referred_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="referrals_made",
    )

    class Meta:
        db_table = "customers_customer"
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
        Customer, on_delete=models.CASCADE, related_name="referrals"
    )
    referred = models.ForeignKey(
        Customer,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="referred_via",
    )
    referred_email = models.EmailField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    reward_code = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "customers_referral"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.referrer_id} → {self.referred_email or self.referred_id} ({self.status})"
