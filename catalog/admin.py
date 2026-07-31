from django.contrib import admin
from django import forms
from django.db import models
from django.db.models import Count, Min
from django.utils.html import format_html, format_html_join
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from .models import Country, Plan, PricingRule, Region, SupplierOffer
from django.utils.translation import gettext_lazy as _


@admin.register(Region)
class RegionAdmin(ModelAdmin):
    list_display = ("name", "slug", "country_count", "sort_order")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("sort_order", "name")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_country_count=Count("countries"))

    @display(description=_("Countries"), ordering="_country_count")
    def country_count(self, obj):
        return obj._country_count


@admin.register(Country)
class CountryAdmin(ModelAdmin):
    list_display = ("flag", "name", "iso2", "region", "is_popular", "is_active", "from_price")
    list_display_links = ("flag", "name")
    list_filter = ("region", "is_popular", "is_active")
    search_fields = ("name", "iso2")
    prepopulated_fields = {"slug": ("name",)}
    list_editable = ("is_popular", "is_active")
    autocomplete_fields = ("region",)
    ordering = ("sort_order", "name")
    list_select_related = ("region",)

    def get_queryset(self, request):
        # Annotate instead of hitting `starting_price` per row: the property
        # issues one query each, so a 100-row page cost 100 extra queries.
        return (
            super()
            .get_queryset(request)
            .annotate(
                _starting_price=Min(
                    "plans__price_usd", filter=models.Q(plans__is_active=True)
                )
            )
        )

    @display(description="")
    def flag(self, obj):
        return format_html(
            '<span style="font-family:monospace;font-weight:700;letter-spacing:1px;'
            'padding:2px 6px;border-radius:4px;background:var(--qs-surface-2);color:var(--qs-blue-text);">{}</span>',
            obj.iso2,
        )

    @display(description=_("From"), ordering="_starting_price")
    def from_price(self, obj):
        price = obj._starting_price
        return f"${price}" if price is not None else "—"


class SupplierOfferInline(TabularInline):
    """Each supplier's price for this plan, side by side.

    Editing here is the whole comparison workflow: add both suppliers' prices
    and the cheapest one takes over the plan's cost and fulfilment route on
    save. Nothing has to be chosen by hand.
    """

    model = SupplierOffer
    extra = 1
    fields = ("provider", "package_code", "cost_usd", "is_available", "unavailable_reason")
    ordering = ("cost_usd",)


@admin.register(Plan)
class PlanAdmin(ModelAdmin):
    """Plans, plus the supplier-price import page.

    Setting up one destination by hand means creating eleven objects — a
    country, four plans and six supplier offers — which is why the import lives
    here: upload the wholesaler's export, see exactly what would change, then
    commit it.
    """

    inlines = (SupplierOfferInline,)
    list_display = (
        "title",
        "scope",
        "target",
        "data_col",
        "validity_days",
        "network_type",
        "sourcing_col",
        "cost_col",
        "price_badge",
        "margin_col",
        "is_popular",
        "is_active",
    )
    list_filter = (
        "scope",
        "network_type",
        "provider",
        "price_locked",
        "is_popular",
        "is_active",
        "is_unlimited",
    )
    search_fields = ("title", "country__name", "region__name", "provider_package_code")
    list_editable = ("is_popular", "is_active")
    autocomplete_fields = ("country", "region")
    ordering = ("sort_order", "price_usd")
    list_select_related = ("country", "region")
    actions = ("recalculate_prices",)
    fieldsets = (
        (_("Plan"), {"fields": ("title", "scope", "country", "region")}),
        (
            _("Data & validity"),
            {
                "fields": (
                    "data_amount_mb",
                    "is_unlimited",
                    "validity_days",
                    "network_type",
                    "supports_hotspot",
                    "provider",
                    "provider_package_code",
                )
            },
        ),
        (
            _("Supplier sourcing"),
            {
                "fields": ("sourcing_readout",),
                "description": _(
                    "Add each supplier's wholesale price under 'Supplier offers' at the "
                    "bottom of this page. On save the cheapest available one sets the "
                    "cost, the price, and where the order is placed; the rest become "
                    "fallbacks used when the winner is out of stock."
                ),
            },
        ),
        (
            _("Pricing"),
            {
                "fields": (
                    "cost_usd",
                    "markup_percent",
                    "price_usd",
                    "price_note",
                    "price_locked",
                    "margin_readout",
                ),
                "description": _(
                    "Enter the supplier cost and the price is calculated on save. "
                    "Leave the markup empty to inherit a pricing rule; fill it in to "
                    "override every rule for this plan alone. Tick 'price locked' to "
                    "type a price by hand and stop it being recalculated. "
                    "Use the note for anything the number cannot say on its own, "
                    "such as '+ deposit' — it is shown to customers beside the price."
                ),
            },
        ),
        (_("Visibility"), {"fields": ("is_popular", "is_active", "sort_order")}),
    )
    readonly_fields = ("margin_readout", "sourcing_readout")

    def get_queryset(self, request):
        # `ranked_offers` walks the related set, so without this every row on
        # the changelist would fetch its own offers — the same N+1 the country
        # and order lists were already fixed for.
        return super().get_queryset(request).prefetch_related("offers")


    # --- Supplier price import ---------------------------------------------

    def get_urls(self):
        from django.urls import path

        return [
            path(
                "import-prices/",
                self.admin_site.admin_view(self.import_prices_view),
                name="catalog_plan_import_prices",
            ),
            *super().get_urls(),
        ]

    def import_prices_view(self, request):
        """Upload a wholesaler price list; preview first, write only on confirm.

        Wrapped in `admin_site.admin_view`, so it is behind the same login,
        permission and CSRF protection as every other admin page rather than
        being a second, weaker door into the catalogue.
        """
        from django.contrib import messages
        from django.shortcuts import redirect, render
        from django.urls import reverse

        from catalog import supplier_import
        from catalog.forms import SupplierPriceUploadForm

        if not self.has_change_permission(request):
            messages.error(request, "You do not have permission to change plans.")
            return redirect(reverse("admin:catalog_plan_changelist"))

        context = {
            **self.admin_site.each_context(request),
            "title": "Import supplier prices",
            "opts": self.model._meta,
        }

        if request.method != "POST":
            context["form"] = SupplierPriceUploadForm()
            return render(request, "admin/catalog/import_prices.html", context)

        form = SupplierPriceUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            context["form"] = form
            return render(request, "admin/catalog/import_prices.html", context)

        provider = form.cleaned_data["provider"]
        countries = form.cleaned_data["only_countries"]
        iso2 = [c.iso2 for c in countries] if countries else None
        prices = supplier_import.parse(form.cleaned_data["csv_file"])

        if prices.missing_columns:
            messages.error(
                request,
                "That file is missing the columns: "
                + ", ".join(sorted(prices.missing_columns)),
            )
            context["form"] = form
            return render(request, "admin/catalog/import_prices.html", context)

        changes = supplier_import.plan_changes(prices, provider, iso2=iso2)

        if form.cleaned_data["dry_run"]:
            # Two uploads rather than carrying the file through a confirm step:
            # an 8 MB CSV does not belong in a hidden field or a session, and
            # this matches how the management command works.
            context.update(
                form=form,
                prices=prices,
                changes=changes,
                # Separate names rather than a dict: Django templates cannot
                # index a dict by a hyphenated key without a custom filter.
                count_new_plans=sum(1 for c in changes if c.kind == "new-plan"),
                count_new_offers=sum(1 for c in changes if c.kind == "new-offer"),
                count_price_changes=sum(1 for c in changes if c.kind == "price-change"),
                count_unchanged=sum(1 for c in changes if c.kind == "unchanged"),
                previewed=True,
            )
            return render(request, "admin/catalog/import_prices.html", context)

        result = supplier_import.apply(prices, provider, iso2=iso2)
        messages.success(
            request,
            f"{provider}: {result['plans_created']} plan(s) created, "
            f"{result['offers_created']} offer(s) added. Prices were recalculated.",
        )
        return redirect(reverse("admin:catalog_plan_changelist"))

    @display(description=_("Target"))
    def target(self, obj):
        return obj.country or obj.region or "Global"

    @display(description=_("Sourced from"))
    def sourcing_col(self, obj):
        offers = obj.ranked_offers
        if not offers:
            # No offers at all means the cost was typed in by hand, which is
            # worth distinguishing from a comparison that only has one entrant.
            return format_html(
                '<span style="color:var(--qs-ink-soft);">{}</span>'
                '<span style="color:var(--qs-ink-mute);font-size:11px;"> {}</span>',
                obj.provider,
                _("manual"),
            )

        winner = offers[0]
        saving = obj.sourcing_saving_usd
        if saving is None:
            return format_html(
                '<span style="font-weight:600;">{}</span>'
                '<span style="color:var(--qs-ink-mute);font-size:11px;"> {}</span>',
                winner.get_provider_display(),
                _("only source"),
            )
        return format_html(
            '<span style="font-weight:600;">{}</span>'
            '<span style="color:var(--qs-teal-text);font-size:11px;"> −${} {} {}</span>',
            winner.get_provider_display(),
            saving,
            # "vs" as a word so a translator can render the comparison naturally.
            _("vs"),
            offers[1].get_provider_display(),
        )

    @display(description=_("Supplier comparison"))
    def sourcing_readout(self, obj):
        if obj.pk is None:
            return _("Save the plan, then add supplier prices below to compare them.")

        offers = list(obj.offers.all())
        if not offers:
            return format_html(
                _(
                    "No supplier offers yet, so the cost above is used as typed "
                    "and orders route to <b>{}</b>. Add two offers below to have "
                    "the cheaper one chosen automatically."
                ),
                obj.provider,
            )

        rows = []
        ranked = obj.ranked_offers
        winner = ranked[0] if ranked else None
        for offer in sorted(offers, key=lambda o: o.cost_usd):
            if not offer.is_available:
                note = offer.unavailable_reason or _("unavailable")
                rows.append(
                    format_html(
                        '<li style="color:var(--qs-ink-mute);">{} — <s>${}</s> · {}</li>',
                        offer.get_provider_display(),
                        offer.cost_usd,
                        note,
                    )
                )
            elif offer == winner:
                rows.append(
                    format_html(
                        '<li><b>{} — ${}</b> '
                        '<span style="color:var(--qs-teal-text);font-weight:600;">← {}</span></li>',
                        offer.get_provider_display(),
                        offer.cost_usd,
                        _("ordered from here"),
                    )
                )
            else:
                rows.append(
                    format_html(
                        '<li>{} — ${} <span style="color:var(--qs-ink-soft);">· {}</span></li>',
                        offer.get_provider_display(),
                        offer.cost_usd,
                        _("fallback"),
                    )
                )
        return format_html(
            '<ul style="margin:0;padding-left:18px;">{}</ul>',
            # Joining and re-formatting would treat the markup as a format
            # string; this keeps each already-escaped row intact.
            format_html_join("", "{}", ((row,) for row in rows)),
        )

    @display(description=_("Data"))
    def data_col(self, obj):
        return obj.data_label

    @display(description=_("Cost"))
    def cost_col(self, obj):
        return f"${obj.cost_usd}" if obj.cost_usd is not None else "—"

    @display(description=_("Price"))
    def price_badge(self, obj):
        lock = " 🔒" if obj.price_locked else ""
        note = (
            format_html('<br><span style="color:var(--qs-ink-soft);font-size:11px;">{}</span>', obj.price_note)
            if obj.price_note
            else ""
        )
        return format_html(
            '<span style="font-weight:700;color:var(--qs-ink);">${}</span>{}{}',
            obj.price_usd,
            lock,
            note,
        )

    @display(description=_("Margin"))
    def margin_col(self, obj):
        amount = obj.margin_usd
        if amount is None:
            return "—"
        percent = obj.margin_percent
        colour = "var(--qs-teal-text)" if amount > 0 else "var(--qs-bad-text)"
        return format_html(
            '<span style="color:{};font-weight:600;">${}</span>'
            '<span style="color:var(--qs-ink-soft);font-size:11px;"> ({}%)</span>',
            colour,
            amount,
            percent,
        )

    @display(description=_("Calculated margin"))
    def margin_readout(self, obj):
        if obj.pk is None or obj.cost_usd is None:
            return "Enter a supplier cost to see the margin."
        rule = obj.markup_percent
        source = (
            f"this plan's own {rule}% markup"
            if rule is not None
            else "the governing pricing rule"
        )
        return format_html(
            "Cost ${} → price ${} · profit <b>${}</b> ({}%), using {}.",
            obj.cost_usd,
            obj.price_usd,
            obj.margin_usd,
            obj.margin_percent,
            source,
        )

    @admin.action(description=_("Recalculate prices from cost and rules"))
    def recalculate_prices(self, request, queryset):
        rules = list(PricingRule.objects.filter(is_active=True))
        changed = 0
        for plan in queryset:
            if plan.recalculate_price(rules):
                plan.save(update_fields=["price_usd"])
                changed += 1
        skipped = queryset.count() - changed
        self.message_user(
            request, f"{changed} price(s) updated, {skipped} unchanged or locked."
        )


@admin.register(PricingRule)
class PricingRuleAdmin(ModelAdmin):
    """Markup rules. The most specific active rule wins:
    plan override → destination → supplier → everything."""

    list_display = ("scope_label", "markup_badge", "min_margin_usd", "rounding", "affected", "is_active")
    list_filter = ("scope", "is_active", "rounding")
    list_editable = ("is_active",)
    autocomplete_fields = ("country",)
    search_fields = ("provider", "country__name", "note")
    actions = ("apply_to_catalogue",)
    fieldsets = (
        (
            _("Applies to"),
            {
                "fields": ("scope", "provider", "country"),
                "description": _(
                    "Pick one. 'Everything' is the house default; a supplier or "
                    "destination rule overrides it for the plans it covers."
                ),
            },
        ),
        (_("Markup"), {"fields": ("markup_percent", "min_margin_usd", "rounding")}),
        (_("Admin"), {"fields": ("is_active", "note")}),
    )

    @display(description=_("Applies to"), ordering="scope")
    def scope_label(self, obj):
        return str(obj).rsplit(" +", 1)[0]

    @display(description=_("Markup"), ordering="markup_percent")
    def markup_badge(self, obj):
        return format_html(
            '<span style="font-weight:700;color:var(--qs-ink);">+{}%</span>', obj.markup_percent
        )

    def get_queryset(self, request):
        # Counting plans per row would be one query per rule. Tally the whole
        # catalogue once and look the numbers up in memory instead.
        qs = super().get_queryset(request).select_related("country")
        plans = Plan.objects.filter(is_active=True)
        self._plan_total = plans.count()
        self._by_provider = dict(
            plans.values_list("provider").annotate(c=Count("id")).values_list("provider", "c")
        )
        self._by_country = dict(
            plans.values_list("country_id").annotate(c=Count("id")).values_list("country_id", "c")
        )
        return qs

    @display(description=_("Plans covered"))
    def affected(self, obj):
        if obj.scope == PricingRule.Scope.PROVIDER:
            return self._by_provider.get(obj.provider, 0)
        if obj.scope == PricingRule.Scope.COUNTRY:
            return self._by_country.get(obj.country_id, 0)
        return self._plan_total

    @admin.action(description=_("Recalculate every affected plan now"))
    def apply_to_catalogue(self, request, queryset):
        rules = list(PricingRule.objects.filter(is_active=True))
        plans = Plan.objects.filter(price_locked=False, cost_usd__isnull=False)
        changed = 0
        for plan in plans:
            if plan.recalculate_price(rules):
                plan.save(update_fields=["price_usd"])
                changed += 1
        self.message_user(request, f"{changed} price(s) recalculated across the catalogue.")


@admin.register(SupplierOffer)
class SupplierOfferAdmin(ModelAdmin):
    """Every supplier price in one list — the catalogue-wide comparison.

    The plan page shows one plan's suppliers; this shows the whole shape of the
    sourcing decision, which is what answers "where are we overpaying?".
    """

    list_display = (
        "plan_col",
        "provider",
        "package_code",
        "cost_badge",
        "verdict",
        "is_available",
        "last_synced_at",
    )
    list_filter = ("provider", "is_available", "plan__scope", "plan__is_active")
    search_fields = ("plan__title", "package_code", "plan__country__name")
    list_select_related = ("plan", "plan__country")
    autocomplete_fields = ("plan",)
    ordering = ("plan__title", "cost_usd")
    actions = ("mark_available", "mark_unavailable")

    def get_queryset(self, request):
        # `verdict` compares this offer against its plan's other offers, so the
        # sibling set is prefetched rather than re-queried per row.
        return super().get_queryset(request).prefetch_related("plan__offers")

    @display(description=_("Plan"), ordering="plan__title")
    def plan_col(self, obj):
        target = obj.plan.country or obj.plan.region or "Global"
        return format_html(
            "{}<br><span style=\"color:var(--qs-ink-mute);font-size:11px;\">{}</span>", obj.plan.title, target
        )

    @display(description=_("Cost"), ordering="cost_usd")
    def cost_badge(self, obj):
        return format_html('<span style="font-weight:600;">${}</span>', obj.cost_usd)

    @display(description=_("Verdict"))
    def verdict(self, obj):
        if not obj.is_available:
            return format_html(
                '<span style="color:var(--qs-ink-mute);">{} — {}</span>',
                _("out"),
                obj.unavailable_reason or _("unavailable"),
            )

        ranked = obj.plan.ranked_offers
        if len(ranked) < 2:
            return format_html(
                '<span style="color:var(--qs-ink-soft);">{}</span>', _("only source")
            )

        if ranked[0].pk == obj.pk:
            return format_html(
                '<span style="color:var(--qs-teal-text);font-weight:600;">{}</span>'
                '<span style="color:var(--qs-ink-soft);font-size:11px;"> · {}</span>',
                _("cheapest"),
                # Formatted here rather than inside the markup so the phrase, not
                # the layout, is what a translator sees.
                _("saves ${amount}").format(amount=ranked[1].cost_usd - obj.cost_usd),
            )
        return format_html(
            '<span style="color:var(--qs-ink-soft);">{}</span>'
            '<span style="color:var(--qs-ink-mute);font-size:11px;"> · {}</span>',
            _("fallback"),
            _("+${amount} vs {provider}").format(
                amount=obj.cost_usd - ranked[0].cost_usd,
                provider=ranked[0].get_provider_display(),
            ),
        )

    @admin.action(description=_("Mark available (put back in the running)"))
    def mark_available(self, request, queryset):
        count = 0
        for offer in queryset:
            offer.is_available = True
            offer.unavailable_reason = ""
            # Saved one at a time on purpose: SupplierOffer.save() re-decides
            # the plan's sourcing, which a bulk update would skip entirely.
            offer.save()
            count += 1
        self.message_user(request, f"{count} offer(s) back in the running; sourcing re-decided.")

    @admin.action(description=_("Mark unavailable (take out of the running)"))
    def mark_unavailable(self, request, queryset):
        count = 0
        for offer in queryset:
            offer.is_available = False
            offer.unavailable_reason = offer.unavailable_reason or "taken out by an admin"
            offer.save()
            count += 1
        self.message_user(request, f"{count} offer(s) removed; plans handed to their fallback.")
