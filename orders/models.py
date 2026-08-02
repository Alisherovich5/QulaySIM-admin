from django.db import models

from catalog.fields import EncryptedCharField, EncryptedTextField
from catalog.models import Plan
from customers.models import Customer
from django.utils.translation import gettext_lazy as _


class PromoCode(models.Model):
    class DiscountType(models.TextChoices):
        PERCENT = "percent", "Percent"
        FIXED = "fixed", "Fixed amount"

    code = models.CharField(max_length=40, unique=True, verbose_name=_("code"))
    discount_type = models.CharField(
        max_length=10, choices=DiscountType.choices, default=DiscountType.PERCENT,
        verbose_name=_("discount type"),
    )
    discount_value = models.DecimalField(max_digits=8, decimal_places=2, verbose_name=_("discount value"))
    # A fixed discount without a floor is a free-plan coupon: $20 off a $1.99
    # tariff is $0.00, and the discount is capped at the cart so nothing warns
    # you. This is what makes a fixed amount usable at all.
    min_order_usd = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0,
        help_text=_(
            "Smallest order this code applies to. Leave at 0 for no minimum — but "
            "a fixed-amount discount larger than your cheapest plan makes that "
            "plan free."
        ),
        verbose_name=_("min order usd"),
    )
    max_uses = models.PositiveIntegerField(default=0, help_text=_("0 = unlimited"), verbose_name=_("max uses"))
    used_count = models.PositiveIntegerField(default=0, verbose_name=_("used count"))
    valid_until = models.DateTimeField(null=True, blank=True, verbose_name=_("valid until"))
    is_active = models.BooleanField(default=True, verbose_name=_("is active"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("created at"))

    class Meta:
        db_table = "orders_promocode"
        verbose_name = _("promo code")
        verbose_name_plural = _("promo codes")
        ordering = ["-created_at"]

    def __str__(self):
        return self.code

    def clean(self):
        """Normalise the code and refuse the shapes that zero out orders.

        The API uppercases what the customer types and compares it to this row
        verbatim, so a lowercase code here can never match at checkout — the
        banner advertises it while every attempt says "invalid". Normalising in
        clean() (before validate_unique) means the admin's uniqueness check
        runs against the value that will actually be stored.
        """
        from django.core.exceptions import ValidationError

        self.code = (self.code or "").strip().upper()

        if self.discount_value is not None:
            if self.discount_type == self.DiscountType.PERCENT and self.discount_value > 100:
                raise ValidationError(
                    {"discount_value": "A percentage discount cannot exceed 100."}
                )
            if (
                self.discount_type == self.DiscountType.FIXED
                and (self.min_order_usd or 0) <= self.discount_value
            ):
                # A $5-off code with no (or an equal) minimum makes every order
                # at or below $5 free — the cap against the cart total hides
                # the giveaway instead of refusing it.
                raise ValidationError(
                    {
                        "min_order_usd": (
                            "A fixed discount needs a minimum order above the "
                            "discount itself, or it makes cheap plans free."
                        )
                    }
                )

    def save(self, *args, **kwargs):
        # Also normalised here (not only in clean) so existing lowercase rows
        # heal on their next programmatic save, not only through the admin form.
        normalised = (self.code or "").strip().upper()
        if normalised != self.code:
            self.code = normalised
            update_fields = kwargs.get("update_fields")
            if update_fields is not None and "code" not in update_fields:
                kwargs["update_fields"] = sorted(set(update_fields) | {"code"})
        super().save(*args, **kwargs)


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        PAID = "paid", _("Paid")
        CANCELLED = "cancelled", _("Cancelled")
        REFUNDED = "refunded", _("Refunded")

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="orders", verbose_name=_("customer"))
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, verbose_name=_("status"))
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name=_("subtotal"))
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name=_("discount"))
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name=_("total"))
    promo_code = models.ForeignKey(
        PromoCode, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders",
        verbose_name=_("promo code"),
    )
    # The catalogue is priced in USD but Payme charges som, so the som amount
    # is frozen when the order is created. Recomputing it at payment time would
    # let the exchange rate move between the price the customer agreed to and
    # the amount actually charged.
    amount_uzs = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Som amount frozen at checkout. Payme is validated against this."),
        verbose_name=_("amount uzs"),
    )
    exchange_rate = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
        help_text=_("USD→UZS rate used to freeze amount_uzs."),
        verbose_name=_("exchange rate"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("created at"))
    paid_at = models.DateTimeField(null=True, blank=True, verbose_name=_("paid at"))
    provider = models.CharField(max_length=20, default="mock", verbose_name=_("provider"))
    provider_transaction_id = models.CharField(max_length=64, blank=True, verbose_name=_("provider transaction id"))
    provider_order_no = models.CharField(max_length=64, blank=True, db_index=True, verbose_name=_("provider order no"))
    provider_status = models.CharField(max_length=40, blank=True, verbose_name=_("provider status"))

    class Meta:
        db_table = "orders_order"
        verbose_name = _("order")
        verbose_name_plural = _("orders")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Order #{self.pk} — {self.customer.email}"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items", verbose_name=_("order"))
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="order_items", verbose_name=_("plan"))
    unit_price = models.DecimalField(max_digits=8, decimal_places=2, verbose_name=_("unit price"))
    quantity = models.PositiveIntegerField(default=1, verbose_name=_("quantity"))

    class Meta:
        db_table = "orders_orderitem"
        verbose_name = _("order item")
        verbose_name_plural = _("order items")

    def __str__(self):
        return f"{self.quantity}× {self.plan.title}"


class ESIM(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="esims", verbose_name=_("order"))
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="esims", verbose_name=_("plan"))
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="esims", verbose_name=_("customer"))
    # ICCID stays in the clear: it identifies the profile for support lookups
    # and admin search, and on its own it cannot install anything.
    iccid = models.CharField(max_length=22, unique=True, verbose_name=_("iccid"))
    # The activation code is the credential. Together with the ICCID it is
    # enough to install the customer's eSIM elsewhere, so it is encrypted at
    # rest — a database dump alone no longer hands over working profiles.
    qr_payload = EncryptedCharField(max_length=255)
    qr_image = EncryptedTextField(blank=True, help_text=_("Base64 PNG data URL (encrypted at rest)"))
    provider = models.CharField(max_length=20, default="mock", verbose_name=_("provider"))
    provider_esim_tran_no = models.CharField(max_length=64, blank=True, db_index=True, verbose_name=_("provider esim tran no"))
    provider_status = models.CharField(max_length=40, blank=True, verbose_name=_("provider status"))
    provider_qr_url = models.URLField(max_length=500, blank=True, verbose_name=_("provider qr url"))
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, verbose_name=_("status"))
    data_total_mb = models.PositiveIntegerField(default=0, verbose_name=_("data total mb"))
    data_used_mb = models.PositiveIntegerField(default=0, verbose_name=_("data used mb"))
    validity_days = models.PositiveIntegerField(default=7, verbose_name=_("validity days"))
    activated_at = models.DateTimeField(null=True, blank=True, verbose_name=_("activated at"))
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name=_("expires at"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("created at"))

    class Meta:
        db_table = "orders_esim"
        verbose_name = _("eSIM")
        verbose_name_plural = _("eSIMs")
        ordering = ["-created_at"]
        verbose_name = _("eSIM")
        verbose_name_plural = _("eSIMs")

    def __str__(self):
        return f"eSIM {self.iccid}"

    def clean(self):
        """An active eSIM must carry an expiry.

        The API's clean-up job only expires rows whose expires_at has passed;
        an admin who flips one to 'active' without a date creates a profile
        that stays 'active' forever and over-reports on every dashboard.
        """
        from django.core.exceptions import ValidationError

        if self.status == self.Status.ACTIVE and self.expires_at is None:
            raise ValidationError(
                {"expires_at": "An active eSIM needs an expiry date, or it never expires."}
            )


class Payment(models.Model):
    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments", verbose_name=_("order"))
    method = models.CharField(max_length=30, default="mock", verbose_name=_("method"))
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name=_("amount"))
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SUCCESS, verbose_name=_("status"))
    provider_ref = models.CharField(max_length=64, blank=True, verbose_name=_("provider ref"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("created at"))

    class Meta:
        db_table = "orders_payment"
        verbose_name = _("payment")
        verbose_name_plural = _("payments")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Payment {self.provider_ref or self.pk} — {self.amount}"


class PaymeTransaction(models.Model):
    """A Payme (Paycom) transaction against an order.

    Payme drives the lifecycle through its Merchant API and may retry any call,
    so `transaction_id` — the identifier Payme assigns — is unique and every
    handler is written to be replayable.

    States are Payme's, not ours:
        1  created, awaiting confirmation
        2  performed (money captured)
       -1  cancelled before it was performed
       -2  cancelled after it was performed (refund)
    """

    class State(models.IntegerChoices):
        CREATED = 1, "Created"
        PERFORMED = 2, "Performed"
        CANCELLED = -1, "Cancelled"
        CANCELLED_AFTER_PERFORM = -2, "Refunded"

    order = models.ForeignKey(
        Order, on_delete=models.PROTECT, related_name="payme_transactions",
        verbose_name=_("order"),
    )
    transaction_id = models.CharField(max_length=64, unique=True, db_index=True, verbose_name=_("transaction id"))
    # Payme works in tiyin (1 UZS = 100 tiyin) and everything it sends is an
    # integer, so this is never a Decimal.
    amount_tiyin = models.BigIntegerField(verbose_name=_("amount tiyin"))
    account = models.CharField(max_length=64, help_text=_("The account value Payme sent."), verbose_name=_("account"))
    state = models.IntegerField(choices=State.choices, default=State.CREATED, verbose_name=_("state"))
    reason = models.IntegerField(null=True, blank=True, help_text=_("Payme cancellation reason."), verbose_name=_("reason"))
    # Payme timestamps are milliseconds since the epoch, and it compares the
    # values it receives back, so they are stored exactly as given.
    create_time = models.BigIntegerField(default=0, verbose_name=_("create time"))
    perform_time = models.BigIntegerField(default=0, verbose_name=_("perform time"))
    cancel_time = models.BigIntegerField(default=0, verbose_name=_("cancel time"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("created at"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("updated at"))

    class Meta:
        db_table = "orders_paymetransaction"
        verbose_name = _("Payme transaction")
        verbose_name_plural = _("Payme transactions")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["state", "-created_at"]),
            models.Index(fields=["create_time"]),
        ]

    def __str__(self):
        return f"Payme {self.transaction_id} — order #{self.order_id} ({self.get_state_display()})"

    @property
    def amount_uzs(self):
        from decimal import Decimal

        return Decimal(self.amount_tiyin) / 100
