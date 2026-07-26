from django.db import models


class Region(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=80, unique=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "catalog_region"
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Country(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    iso2 = models.CharField(max_length=2, help_text="ISO 3166-1 alpha-2 code, e.g. UZ")
    region = models.ForeignKey(
        Region, on_delete=models.SET_NULL, null=True, blank=True, related_name="countries"
    )
    is_popular = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "catalog_country"
        ordering = ["sort_order", "name"]
        verbose_name_plural = "countries"

    def __str__(self):
        return f"{self.name} ({self.iso2})"

    @property
    def starting_price(self):
        cheapest = self.plans.filter(is_active=True).order_by("price_usd").first()
        return cheapest.price_usd if cheapest else None


class Plan(models.Model):
    class Scope(models.TextChoices):
        LOCAL = "local", "Local"
        REGIONAL = "regional", "Regional"
        GLOBAL = "global", "Global"

    class Network(models.TextChoices):
        LTE = "4G", "4G / LTE"
        FIVE_G = "5G", "5G"

    scope = models.CharField(max_length=10, choices=Scope.choices, default=Scope.LOCAL)
    country = models.ForeignKey(
        Country, on_delete=models.CASCADE, null=True, blank=True, related_name="plans"
    )
    region = models.ForeignKey(
        Region, on_delete=models.CASCADE, null=True, blank=True, related_name="plans"
    )
    title = models.CharField(max_length=120)
    data_amount_mb = models.PositiveIntegerField(default=1024, help_text="Ignored when unlimited")
    is_unlimited = models.BooleanField(default=False)
    validity_days = models.PositiveIntegerField(default=7)
    price_usd = models.DecimalField(max_digits=8, decimal_places=2)
    network_type = models.CharField(max_length=2, choices=Network.choices, default=Network.LTE)
    supports_hotspot = models.BooleanField(default=True)
    provider = models.CharField(
        max_length=20,
        default="mock",
        help_text="Supplier key. Set to esimaccess only after package mapping is verified.",
    )
    provider_package_code = models.CharField(
        max_length=120,
        blank=True,
        help_text="Supplier package slug/code, e.g. JP_1_7 for eSIM Access.",
    )
    is_popular = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "catalog_plan"
        ordering = ["sort_order", "price_usd"]

    def __str__(self):
        return f"{self.title} — ${self.price_usd}"

    @property
    def data_label(self):
        if self.is_unlimited:
            return "Unlimited"
        if self.data_amount_mb % 1024 == 0:
            return f"{self.data_amount_mb // 1024} GB"
        return f"{self.data_amount_mb} MB"
