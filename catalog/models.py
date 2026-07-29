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

    # --- Pricing -----------------------------------------------------------
    # What the supplier charges us; never shown to customers. Derived from the
    # cheapest SupplierOffer when this plan has any, otherwise entered by hand.
    # 12 digits, not 8: the old ceiling was 999,999.99, which is a limit nobody
    # asked for on a field an operator should be able to type any figure into.
    cost_usd = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Supplier cost. Set automatically from the cheapest supplier offer, if any.",
    )
    # What the customer pays. Recalculated from cost + markup on save unless
    # price_locked is set.
    price_usd = models.DecimalField(max_digits=12, decimal_places=2)
    # Free text shown next to the price, for the part an amount cannot express:
    # "+ deposit", "first month only", "per device". Kept separate from
    # price_usd on purpose — margin, currency conversion and the Payme amount
    # all read that column, and none of them can divide by "500 + deposit".
    price_note = models.CharField(
        max_length=120,
        blank=True,
        help_text=(
            "Optional text shown beside the price, e.g. '+ deposit'. "
            "The number itself stays in the price field."
        ),
    )
    markup_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Overrides every pricing rule for this plan only. Leave empty to inherit.",
    )
    price_locked = models.BooleanField(
        default=False,
        help_text="Keep the price exactly as typed; never recalculate it from cost.",
    )

    network_type = models.CharField(max_length=2, choices=Network.choices, default=Network.LTE)
    supports_hotspot = models.BooleanField(default=True)
    # The supplier this plan is currently sourced from, and its code there.
    # Denormalised from the winning SupplierOffer so that order fulfilment and
    # the per-supplier pricing rules can filter on a single column.
    provider = models.CharField(
        max_length=20,
        default="mock",
        help_text="Winning supplier. Managed by the offers below when there are any.",
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

    @property
    def margin_usd(self):
        from catalog.pricing import margin

        return margin(self)

    @property
    def margin_percent(self):
        from catalog.pricing import margin_percent

        return margin_percent(self)

    # --- Supplier sourcing -------------------------------------------------

    @property
    def ranked_offers(self):
        """Usable supplier offers, cheapest first.

        This ordering *is* the sourcing decision: the head is where the plan is
        bought, and the tail is the fallback order when the head fails.
        """
        return sorted(
            (offer for offer in self.offers.all() if offer.is_available),
            # Provider breaks ties so the winner is stable rather than
            # depending on row order — an unstable winner would reroute
            # fulfilment on every save for no reason.
            key=lambda offer: (offer.cost_usd, offer.provider),
        )

    @property
    def winning_offer(self):
        offers = self.ranked_offers
        return offers[0] if offers else None

    @property
    def sourcing_saving_usd(self):
        """Per-unit saving from the cheapest supplier versus the runner-up.

        None when there is nothing to compare, which is the honest answer:
        a single offer is not a comparison.
        """
        offers = self.ranked_offers
        if len(offers) < 2:
            return None
        return offers[1].cost_usd - offers[0].cost_usd

    def resolve_sourcing(self, *, save=False) -> bool:
        """Point cost and fulfilment route at the cheapest available supplier.

        A plan with no usable offers keeps whatever cost and provider were
        entered by hand, so this runs safely over a catalogue that predates
        supplier comparison, and a supplier outage that empties the offer list
        does not silently zero out a plan's cost.
        """
        if not self.pk:
            return False

        winner = self.winning_offer
        if winner is None:
            return False

        if (
            self.cost_usd == winner.cost_usd
            and self.provider == winner.provider
            and self.provider_package_code == winner.package_code
        ):
            return False

        self.cost_usd = winner.cost_usd
        self.provider = winner.provider
        self.provider_package_code = winner.package_code
        if save:
            self.save()
        return True

    def recalculate_price(self, rules=None) -> bool:
        """Recompute price_usd from cost and the governing rule.

        Returns True when the price actually changed. A locked plan or one
        with no supplier cost is left alone.
        """
        if self.price_locked:
            return False

        from catalog.pricing import calculate_price

        new_price = calculate_price(self, rules)
        if new_price is None or new_price == self.price_usd:
            return False

        self.price_usd = new_price
        return True

    def save(self, *args, **kwargs):
        # Sourcing first: the winning offer sets the cost that pricing reads,
        # so the other order would price against the previous supplier.
        sourced = self.resolve_sourcing()
        # Recalculate on every save so an edited cost or markup takes effect
        # immediately, rather than waiting for someone to run a bulk action.
        repriced = self.recalculate_price()

        # A caller that narrowed update_fields could not have known that saving
        # would also move the cost or the route. Widening it keeps those writes
        # from being silently dropped.
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and (sourced or repriced):
            fields = set(update_fields)
            if sourced:
                fields |= {"cost_usd", "provider", "provider_package_code"}
            if repriced:
                fields.add("price_usd")
            kwargs["update_fields"] = sorted(fields)

        super().save(*args, **kwargs)


class SupplierOffer(models.Model):
    """One supplier's wholesale price for one plan.

    The same eSIM package is resold by several suppliers at prices that differ
    per destination — eSIM Access may be cheaper for Turkey while eSIMCard wins
    for Japan. Holding each supplier's price as its own row is what makes them
    comparable: the cheapest available offer becomes the plan's cost and its
    fulfilment route, and the losing offers stay on file as fallbacks for when
    the winner is out of stock or its API is down.

    Customers never see any of this. They see one plan at one price.
    """

    class Provider(models.TextChoices):
        ESIMACCESS = "esimaccess", "eSIM Access"
        ESIMCARD = "esimcard", "eSIMCard"
        MOCK = "mock", "Mock (no real supplier)"

    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name="offers")
    provider = models.CharField(max_length=20, choices=Provider.choices)
    package_code = models.CharField(
        max_length=120, help_text="This supplier's own code for the package, e.g. TR_5_30."
    )
    cost_usd = models.DecimalField(
        max_digits=12, decimal_places=2, help_text="Wholesale price this supplier charges us."
    )
    is_available = models.BooleanField(
        default=True,
        help_text="Uncheck to take a supplier out of the running without losing its price.",
    )
    unavailable_reason = models.CharField(
        max_length=200, blank=True, help_text="Why it is out — e.g. 'out of stock', 'no balance'."
    )
    last_synced_at = models.DateTimeField(
        null=True, blank=True, help_text="When a price sync last confirmed this offer."
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "catalog_supplieroffer"
        ordering = ["cost_usd"]
        constraints = [
            models.UniqueConstraint(
                fields=["plan", "provider"], name="one_offer_per_plan_per_provider"
            ),
            models.CheckConstraint(
                condition=models.Q(cost_usd__gte=0), name="offer_cost_not_negative"
            ),
        ]

    def __str__(self):
        return f"{self.get_provider_display()} — ${self.cost_usd}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # A price that does not move sourcing is a price nobody can trust, so
        # the plan is re-decided here rather than on a schedule.
        self.plan.resolve_sourcing(save=True)

    def delete(self, *args, **kwargs):
        plan = self.plan
        super().delete(*args, **kwargs)
        # Losing the winning offer must hand the plan to the runner-up
        # immediately, or fulfilment keeps routing to a supplier we removed.
        plan.resolve_sourcing(save=True)


class PricingRule(models.Model):
    """A markup rule. The most specific active rule wins — see catalog/pricing.py.

    Three scopes are supported so the same catalogue can be priced globally,
    tuned per supplier, or tuned per destination, without touching individual
    plans.
    """

    class Scope(models.TextChoices):
        GLOBAL = "global", "Everything (house default)"
        PROVIDER = "provider", "One supplier"
        COUNTRY = "country", "One destination"

    class Rounding(models.TextChoices):
        NONE = "none", "Exact cents"
        CHARM = "charm", "End in .99"
        HALF = "half", "Round up to 0.50"
        WHOLE = "whole", "Round up to whole dollar"

    scope = models.CharField(max_length=10, choices=Scope.choices, default=Scope.GLOBAL)
    provider = models.CharField(
        max_length=20,
        blank=True,
        help_text="Required when the scope is 'One supplier', e.g. esimaccess.",
    )
    country = models.ForeignKey(
        Country,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="pricing_rules",
        help_text="Required when the scope is 'One destination'.",
    )
    markup_percent = models.DecimalField(
        max_digits=6, decimal_places=2, default=30, help_text="Added on top of supplier cost."
    )
    min_margin_usd = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0,
        help_text="Profit floor. A percentage on a cheap plan can be pennies; this prevents that.",
    )
    rounding = models.CharField(
        max_length=10, choices=Rounding.choices, default=Rounding.NONE
    )
    is_active = models.BooleanField(default=True)
    note = models.CharField(max_length=200, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "catalog_pricingrule"
        ordering = ["scope", "provider", "country__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["scope"],
                condition=models.Q(scope="global"),
                name="one_global_pricing_rule",
            ),
            models.UniqueConstraint(
                fields=["provider"],
                condition=models.Q(scope="provider"),
                name="one_rule_per_provider",
            ),
            models.UniqueConstraint(
                fields=["country"],
                condition=models.Q(scope="country"),
                name="one_rule_per_country",
            ),
            models.CheckConstraint(
                condition=models.Q(markup_percent__gte=0), name="markup_not_negative"
            ),
            models.CheckConstraint(
                condition=models.Q(min_margin_usd__gte=0), name="min_margin_not_negative"
            ),
        ]

    def __str__(self):
        if self.scope == self.Scope.PROVIDER:
            target = self.provider or "?"
        elif self.scope == self.Scope.COUNTRY:
            target = self.country.name if self.country else "?"
        else:
            target = "everything"
        return f"{target} +{self.markup_percent}%"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # A rule that does not move any price is a rule the admin cannot trust.
        # Recalculating here makes the change visible immediately instead of
        # waiting for someone to remember the bulk action.
        self.apply_to_plans()

    def apply_to_plans(self) -> int:
        """Recalculate every plan this rule could govern; returns how many moved."""
        rules = list(PricingRule.objects.filter(is_active=True))
        plans = Plan.objects.filter(price_locked=False, cost_usd__isnull=False)
        if self.scope == self.Scope.PROVIDER:
            plans = plans.filter(provider=self.provider)
        elif self.scope == self.Scope.COUNTRY:
            plans = plans.filter(country_id=self.country_id)

        changed = [plan for plan in plans if plan.recalculate_price(rules)]
        if changed:
            Plan.objects.bulk_update(changed, ["price_usd"], batch_size=500)
        return len(changed)

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.scope == self.Scope.PROVIDER and not self.provider:
            raise ValidationError({"provider": "Choose the supplier this rule applies to."})
        if self.scope == self.Scope.COUNTRY and not self.country:
            raise ValidationError({"country": "Choose the destination this rule applies to."})
        if self.scope == self.Scope.GLOBAL:
            self.provider = ""
            self.country = None
