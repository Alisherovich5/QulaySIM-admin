from django.db import models

from catalog.fields import EncryptedCharField, EncryptedTextField
from catalog.models import Plan
from customers.models import Customer


class PromoCode(models.Model):
    class DiscountType(models.TextChoices):
        PERCENT = "percent", "Percent"
        FIXED = "fixed", "Fixed amount"

    code = models.CharField(max_length=40, unique=True)
    discount_type = models.CharField(
        max_length=10, choices=DiscountType.choices, default=DiscountType.PERCENT
    )
    discount_value = models.DecimalField(max_digits=8, decimal_places=2)
    max_uses = models.PositiveIntegerField(default=0, help_text="0 = unlimited")
    used_count = models.PositiveIntegerField(default=0)
    valid_until = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "orders_promocode"
        ordering = ["-created_at"]

    def __str__(self):
        return self.code


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"
        REFUNDED = "refunded", "Refunded"

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="orders")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    promo_code = models.ForeignKey(
        PromoCode, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    provider = models.CharField(max_length=20, default="mock")
    provider_transaction_id = models.CharField(max_length=64, blank=True)
    provider_order_no = models.CharField(max_length=64, blank=True, db_index=True)
    provider_status = models.CharField(max_length=40, blank=True)

    class Meta:
        db_table = "orders_order"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Order #{self.pk} — {self.customer.email}"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="order_items")
    unit_price = models.DecimalField(max_digits=8, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "orders_orderitem"

    def __str__(self):
        return f"{self.quantity}× {self.plan.title}"


class ESIM(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="esims")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="esims")
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="esims")
    # ICCID stays in the clear: it identifies the profile for support lookups
    # and admin search, and on its own it cannot install anything.
    iccid = models.CharField(max_length=22, unique=True)
    # The activation code is the credential. Together with the ICCID it is
    # enough to install the customer's eSIM elsewhere, so it is encrypted at
    # rest — a database dump alone no longer hands over working profiles.
    qr_payload = EncryptedCharField(max_length=255)
    qr_image = EncryptedTextField(blank=True, help_text="Base64 PNG data URL (encrypted at rest)")
    provider = models.CharField(max_length=20, default="mock")
    provider_esim_tran_no = models.CharField(max_length=64, blank=True, db_index=True)
    provider_status = models.CharField(max_length=40, blank=True)
    provider_qr_url = models.URLField(max_length=500, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    data_total_mb = models.PositiveIntegerField(default=0)
    data_used_mb = models.PositiveIntegerField(default=0)
    validity_days = models.PositiveIntegerField(default=7)
    activated_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "orders_esim"
        ordering = ["-created_at"]
        verbose_name = "eSIM"
        verbose_name_plural = "eSIMs"

    def __str__(self):
        return f"eSIM {self.iccid}"


class Payment(models.Model):
    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    method = models.CharField(max_length=30, default="mock")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SUCCESS)
    provider_ref = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "orders_payment"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Payment {self.provider_ref or self.pk} — {self.amount}"
