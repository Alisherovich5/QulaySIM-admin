from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _


def fulfillable_providers() -> frozenset:
    """Suppliers order fulfilment can actually buy from.

    Read at call time, not import time, so tests and deployments can change
    FULFILLABLE_PROVIDERS without re-importing this module.
    """
    from django.conf import settings

    return frozenset(getattr(settings, "FULFILLABLE_PROVIDERS", ["esimaccess"]))


class Region(models.Model):
    # English is the base; _ru/_uz are optional and fall back to the base.
    name = models.CharField(max_length=80, unique=True, verbose_name=_("name"))
    name_ru = models.CharField(
        max_length=80,
        blank=True,
        verbose_name=_("name ru"),
        help_text=_("Russian name. Leave blank to show the English one."),
    )
    name_uz = models.CharField(
        max_length=80,
        blank=True,
        verbose_name=_("name uz"),
        help_text=_("Uzbek name. Leave blank to show the English one."),
    )
    slug = models.SlugField(max_length=80, unique=True, verbose_name=_("slug"))
    sort_order = models.PositiveIntegerField(default=0, verbose_name=_("sort order"))

    class Meta:
        db_table = "catalog_region"
        verbose_name = _("region")
        verbose_name_plural = _("regions")
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Country(models.Model):
    # English is the base; _ru/_uz are optional and fall back to the base.
    # The slug is deliberately not translated: it is the URL the storefront
    # links to, and it must stay the same whichever language is showing.
    name = models.CharField(max_length=120, unique=True, verbose_name=_("name"))
    name_ru = models.CharField(
        max_length=120,
        blank=True,
        verbose_name=_("name ru"),
        help_text=_("Russian name. Leave blank to show the English one."),
    )
    name_uz = models.CharField(
        max_length=120,
        blank=True,
        verbose_name=_("name uz"),
        help_text=_("Uzbek name. Leave blank to show the English one."),
    )
    slug = models.SlugField(max_length=120, unique=True, verbose_name=_("slug"))
    iso2 = models.CharField(max_length=2, help_text=_("ISO 3166-1 alpha-2 code, e.g. UZ"), verbose_name=_("iso2"))
    region = models.ForeignKey(
        Region, on_delete=models.SET_NULL, null=True, blank=True, related_name="countries",
        verbose_name=_("region"),
    )
    is_popular = models.BooleanField(default=False, verbose_name=_("is popular"))
    is_active = models.BooleanField(default=True, verbose_name=_("is active"))
    sort_order = models.PositiveIntegerField(default=0, verbose_name=_("sort order"))

    class Meta:
        db_table = "catalog_country"
        verbose_name = _("country")
        verbose_name_plural = _("countries")
        ordering = ["sort_order", "name"]
        verbose_name_plural = _("countries")

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

    scope = models.CharField(max_length=10, choices=Scope.choices, default=Scope.LOCAL, verbose_name=_("scope"))
    country = models.ForeignKey(
        Country, on_delete=models.CASCADE, null=True, blank=True, related_name="plans",
        verbose_name=_("country"),
    )
    region = models.ForeignKey(
        Region, on_delete=models.CASCADE, null=True, blank=True, related_name="plans",
        verbose_name=_("region"),
    )
    title = models.CharField(max_length=120, verbose_name=_("title"))
    data_amount_mb = models.PositiveIntegerField(default=1024, help_text=_("Ignored when unlimited"), verbose_name=_("data amount mb"))
    is_unlimited = models.BooleanField(default=False, verbose_name=_("is unlimited"))
    validity_days = models.PositiveIntegerField(default=7, verbose_name=_("validity days"))

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
        help_text=_("Supplier cost. Set automatically from the cheapest supplier offer, if any."),
        verbose_name=_("cost usd"),
    )
    # What the customer pays. Recalculated from cost + markup on save unless
    # price_locked is set.
    price_usd = models.DecimalField(max_digits=12, decimal_places=2, verbose_name=_("price usd"))
    # Free text shown next to the price, for the part an amount cannot express:
    # "+ deposit", "first month only", "per device". Kept separate from
    # price_usd on purpose — margin, currency conversion and the Payme amount
    # all read that column, and none of them can divide by "500 + deposit".
    price_note = models.CharField(
        max_length=120,
        blank=True,
        help_text=_(
            "Optional text shown beside the price, e.g. '+ deposit'. "
            "The number itself stays in the price field."
        ),
        verbose_name=_("price note"),
    )
    markup_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        # The same floor PricingRule enforces with a check constraint: a
        # negative override (a typo for a positive one) prices below cost and,
        # past -100%, writes a negative price that breaks checkout.
        validators=[MinValueValidator(0)],
        help_text=_("Overrides every pricing rule for this plan only. Leave empty to inherit."),
        verbose_name=_("markup percent"),
    )
    price_locked = models.BooleanField(
        default=False,
        help_text=_("Keep the price exactly as typed; never recalculate it from cost."),
        verbose_name=_("price locked"),
    )

    network_type = models.CharField(max_length=2, choices=Network.choices, default=Network.LTE, verbose_name=_("network type"))
    supports_hotspot = models.BooleanField(default=True, verbose_name=_("supports hotspot"))
    # The supplier this plan is currently sourced from, and its code there.
    # Denormalised from the winning SupplierOffer so that order fulfilment and
    # the per-supplier pricing rules can filter on a single column.
    provider = models.CharField(
        max_length=20,
        default="mock",
        help_text=_("Winning supplier. Managed by the offers below when there are any."),
        verbose_name=_("provider"),
    )
    provider_package_code = models.CharField(
        max_length=120,
        blank=True,
        help_text=_("Supplier package slug/code, e.g. JP_1_7 for eSIM Access."),
        verbose_name=_("provider package code"),
    )
    is_popular = models.BooleanField(default=False, verbose_name=_("is popular"))
    is_active = models.BooleanField(default=True, verbose_name=_("is active"))
    sort_order = models.PositiveIntegerField(default=0, verbose_name=_("sort order"))

    class Meta:
        db_table = "catalog_plan"
        verbose_name = _("plan")
        verbose_name_plural = _("plans")
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
        """Available supplier offers, cheapest first.

        This is the price comparison, not the sourcing decision on its own:
        `winning_offer` picks the head of this list *among the suppliers the
        API can actually order from*, and the fulfillable tail is the fallback
        order when the head fails.
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
        """Cheapest available offer from a supplier fulfilment can buy from.

        An unfulfillable supplier's offer must never win: its cost would set a
        retail price the real (dearer) route then eats through, or — with no
        fulfillable route at all — sell a plan nobody can provision. Offers
        from suppliers outside FULFILLABLE_PROVIDERS stay on file purely for
        comparison until their integration is connected.
        """
        usable = fulfillable_providers()
        for offer in self.ranked_offers:
            if offer.provider in usable:
                return offer
        return None

    @property
    def unfulfillable_only(self):
        """True when the plan has supplier offers but none we can order from.

        This is the stranded state the changelist badge exists for: the plan
        is on sale, yet a paid order for it has no route to a real eSIM.
        """
        offers = list(self.offers.all())
        if not offers:
            return False
        usable = fulfillable_providers()
        return all(offer.provider not in usable for offer in offers)

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

    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name="offers", verbose_name=_("plan"))
    provider = models.CharField(max_length=20, choices=Provider.choices, verbose_name=_("provider"))
    package_code = models.CharField(
        max_length=120, help_text=_("This supplier's own code for the package, e.g. TR_5_30."),
        verbose_name=_("package code"),
    )
    cost_usd = models.DecimalField(
        max_digits=12, decimal_places=2, help_text=_("Wholesale price this supplier charges us."),
        verbose_name=_("cost usd"),
    )
    is_available = models.BooleanField(
        default=True,
        help_text=_("Uncheck to take a supplier out of the running without losing its price."),
        verbose_name=_("is available"),
    )
    unavailable_reason = models.CharField(
        max_length=200, blank=True, help_text=_("Why it is out — e.g. 'out of stock', 'no balance'."),
        verbose_name=_("unavailable reason"),
    )
    last_synced_at = models.DateTimeField(
        null=True, blank=True, help_text=_("When a price sync last confirmed this offer."),
        verbose_name=_("last synced at"),
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("updated at"))

    class Meta:
        db_table = "catalog_supplieroffer"
        verbose_name = _("supplier offer")
        verbose_name_plural = _("supplier offers")
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

    scope = models.CharField(max_length=10, choices=Scope.choices, default=Scope.GLOBAL, verbose_name=_("scope"))
    provider = models.CharField(
        max_length=20,
        blank=True,
        help_text=_("Required when the scope is 'One supplier', e.g. esimaccess."),
        verbose_name=_("provider"),
    )
    country = models.ForeignKey(
        Country,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="pricing_rules",
        help_text=_("Required when the scope is 'One destination'."),
        verbose_name=_("country"),
    )
    markup_percent = models.DecimalField(
        max_digits=6, decimal_places=2, default=30, help_text=_("Added on top of supplier cost."),
        verbose_name=_("markup percent"),
    )
    min_margin_usd = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0,
        help_text=_("Profit floor. A percentage on a cheap plan can be pennies; this prevents that."),
        verbose_name=_("min margin usd"),
    )
    rounding = models.CharField(
        max_length=10, choices=Rounding.choices, default=Rounding.NONE,
        verbose_name=_("rounding"),
    )
    is_active = models.BooleanField(default=True, verbose_name=_("is active"))
    note = models.CharField(max_length=200, blank=True, verbose_name=_("note"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("updated at"))

    class Meta:
        db_table = "catalog_pricingrule"
        verbose_name = _("pricing rule")
        verbose_name_plural = _("pricing rules")
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
        # Remembered before the write so a retargeted rule can reprice the
        # plans it *stopped* governing, not only the ones it now covers.
        previous = PricingRule.objects.filter(pk=self.pk).first() if self.pk else None
        super().save(*args, **kwargs)
        # A rule that does not move any price is a rule the admin cannot trust.
        # Recalculating here makes the change visible immediately instead of
        # waiting for someone to remember the bulk action.
        self.apply_to_plans()
        if previous is not None and (
            previous.scope != self.scope
            or previous.provider != self.provider
            or previous.country_id != self.country_id
        ):
            # Moving the rule from Japan to Turkey used to reprice Turkey only,
            # leaving Japan's plans priced by a rule that no longer names them.
            previous.apply_to_plans()

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        # Deactivating a rule reprices through save(); deleting one used to
        # reprice nothing, so the catalogue kept the dead rule's prices until
        # each plan happened to be saved for some other reason.
        self.apply_to_plans()
        return result

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
            # bulk_update fires no post_save, so the cache signal never sees
            # these price moves; without this the storefront keeps the old
            # prices for the whole TTL. on_commit, so the keys are cleared only
            # once the new prices are actually visible to the API.
            from config.cache import invalidate_catalogue

            transaction.on_commit(invalidate_catalogue)
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
