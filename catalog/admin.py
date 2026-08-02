from django.contrib import admin, messages
from django import forms
from django.db import models
from django.db.models import Count, ExpressionWrapper, F, Max, Min
from django.utils.html import format_html, format_html_join
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from .models import Country, Plan, PricingRule, Region, SupplierOffer
from django.utils.translation import gettext_lazy as _


@admin.register(Region)
class RegionAdmin(ModelAdmin):
    list_display = ("name", "name_uz", "name_ru", "slug", "country_count", "sort_order")
    # Staff type the name they see on the site, which is the translated one.
    search_fields = ("name", "name_uz", "name_ru")
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("sort_order", "name")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_country_count=Count("countries"))

    @display(description=_("Countries"), ordering="_country_count")
    def country_count(self, obj):
        return obj._country_count


class PromotionFilter(admin.SimpleListFilter):
    """Is this destination on the landing page — and can it carry a card?

    A plain `is_popular` yes/no answers the first half. The third choice answers
    the half nobody thinks to ask: promoted destinations with nothing to sell,
    which are exactly the ones rendering as empty cards right now.
    """

    title = _("Landing page")
    parameter_name = "promoted"

    def lookups(self, request, model_admin):
        return (
            ("yes", _("Promoted")),
            ("no", _("Not promoted")),
            ("empty", _("Promoted with no plans")),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == "yes":
            return queryset.filter(is_popular=True)
        if value == "no":
            return queryset.filter(is_popular=False)
        if value == "empty":
            # Reuses the count CountryAdmin.get_queryset already annotates, so
            # the answer costs no extra query.
            return queryset.filter(is_popular=True, _active_plan_count=0)
        return queryset


@admin.register(Country)
class CountryAdmin(ModelAdmin):
    """Destinations, and the handful promoted on the landing page.

    The promoted set is managed from this list rather than from nine change
    pages: whether a destination is promoted, where it sits in the row, and
    whether it has anything to sell are one decision, and it can only be taken
    with all three side by side.
    """

    list_display = (
        "flag",
        "name",
        "iso2",
        "region",
        "promotion",
        "is_popular",
        "sort_order",
        "plan_count",
        "is_active",
        "from_price",
    )
    list_display_links = ("flag", "name")
    list_filter = (PromotionFilter, "region", "is_active")
    search_fields = ("name", "name_uz", "name_ru", "iso2")
    prepopulated_fields = {"slug": ("name",)}
    # sort_order is the order the storefront renders the promoted row in, so it
    # is edited here: reshuffling one destination used to mean opening as many
    # change pages as there are countries between its old and new place.
    list_editable = ("is_popular", "sort_order", "is_active")
    autocomplete_fields = ("region",)
    # Promoted first, then in site order. Everything else shares sort_order 0,
    # so ordering by sort_order alone buried the nine that matter at the bottom
    # of the first page.
    ordering = ("-is_popular", "sort_order", "name")
    list_select_related = ("region",)
    actions = ("promote", "demote")

    def get_queryset(self, request):
        # Annotate instead of hitting `starting_price` per row: the property
        # issues one query each, so a 100-row page cost 100 extra queries.
        queryset = (
            super()
            .get_queryset(request)
            .annotate(
                _starting_price=Min(
                    "plans__price_usd", filter=models.Q(plans__is_active=True)
                ),
                # Second aggregate over the same join, so still no extra query:
                # a promoted country with no active plans is an empty card on
                # the site, and this list is the only place to notice it.
                _active_plan_count=Count(
                    "plans", filter=models.Q(plans__is_active=True), distinct=True
                ),
            )
        )
        # Landing-page position, as an annotation rather than a dict on `self`:
        # a ModelAdmin is instantiated once at registration and shared across
        # threads, so per-request state on it races. It also removes the extra
        # query this used to run on every autocomplete keystroke, since Country
        # is an autocomplete target on both PlanAdmin and PricingRuleAdmin.
        #
        # is_active is part of the filter because the storefront only ever lists
        # active countries: deactivating a promoted one used to leave every
        # country after it labelled one place too high.
        promoted = Country.objects.filter(is_popular=True, is_active=True)
        earlier = promoted.filter(
            models.Q(sort_order__lt=models.OuterRef("sort_order"))
            | models.Q(
                sort_order=models.OuterRef("sort_order"),
                name__lt=models.OuterRef("name"),
            )
        )
        return queryset.annotate(
            _landing_position=models.Subquery(
                earlier.order_by()
                .values(dummy=models.Value(1))
                .annotate(n=Count("pk"))
                .values("n"),
                output_field=models.IntegerField(),
            ),
            _landing_total=models.Subquery(
                promoted.order_by()
                .values(dummy=models.Value(1))
                .annotate(n=Count("pk"))
                .values("n"),
                output_field=models.IntegerField(),
            ),
        )

    @display(description="")
    def flag(self, obj):
        return format_html(
            '<span style="font-family:monospace;font-weight:700;letter-spacing:1px;'
            'padding:2px 6px;border-radius:4px;background:var(--qs-surface-2);color:var(--qs-blue-text);">{}</span>',
            obj.iso2,
        )

    @display(description=_("On the site"), ordering="sort_order")
    def promotion(self, obj):
        if not obj.is_popular:
            return format_html('<span style="color:var(--qs-ink-mute);">—</span>')
        if not obj.is_active:
            # Promoted but hidden: the storefront never lists it, so it has no
            # position. Saying so is more use than a number that is not real.
            return format_html(
                '<span style="color:var(--qs-bad-text);font-weight:600;">{}</span>',
                _("not shown — inactive"),
            )
        return format_html(
            '<span style="color:var(--qs-blue-text);font-weight:700;">★ {}</span>'
            '<span style="color:var(--qs-ink-mute);font-size:11px;"> / {}</span>',
            (obj._landing_position or 0) + 1,
            obj._landing_total or 1,
        )

    @display(description=_("Active plans"), ordering="_active_plan_count")
    def plan_count(self, obj):
        count = obj._active_plan_count
        if count:
            return count
        # Named for what a customer would see, not for the number, because that
        # is what makes it obvious this one must not stay promoted.
        label = (
            _("empty card on the site")
            if obj.is_popular and obj.is_active
            else _("no active plans")
        )
        return format_html(
            '<span style="color:var(--qs-bad-text);font-weight:600;">{}</span>', label
        )

    @display(description=_("From"), ordering="_starting_price")
    def from_price(self, obj):
        price = obj._starting_price
        return f"${price}" if price is not None else "—"

    def save_model(self, request, obj, form, change):
        """Promotion from the changelist checkbox lands at the end of the row.

        The promote action already did this, but ticking `is_popular` in the
        list is the more obvious path and it saved the row as typed — leaving
        sort_order at 0, which sorts ahead of every promoted country and makes
        the new destination the *first* card on the landing page. Same rule,
        whichever way it was promoted.
        """
        if obj.is_popular and obj.sort_order == 0:
            last = Country.objects.filter(is_popular=True).exclude(pk=obj.pk).aggregate(
                Max("sort_order")
            )["sort_order__max"]
            obj.sort_order = (last or 0) + 1
        super().save_model(request, obj, form, change)

    # --- Landing-page promotion --------------------------------------------

    @admin.action(description=_("Promote to the landing page"), permissions=["change"])
    def promote(self, request, queryset):
        added = [country for country in queryset if not country.is_popular]
        last = (
            Country.objects.filter(is_popular=True).aggregate(Max("sort_order"))[
                "sort_order__max"
            ]
            or 0
        )
        for country in added:
            country.is_popular = True
            if country.sort_order == 0:
                # Land at the end of the row instead of tying with every other
                # unpromoted country at 0, which would leave the new position
                # up to the alphabet.
                last += 1
                country.sort_order = last
            # Saved one at a time rather than queryset.update(): post_save is
            # what clears the storefront cache, and a bulk update fires none, so
            # the site would keep serving the old row until the TTL ran out.
            country.save(update_fields=["is_popular", "sort_order"])

        if added:
            self.message_user(
                request,
                _("{count} destination(s) added to the landing page.").format(
                    count=len(added)
                ),
            )
        # Reporting the untouched ones too, so a selection that changed nothing
        # says so instead of looking like the action did not run.
        already = queryset.count() - len(added)
        if already:
            self.message_user(
                request,
                _("{count} were already promoted and were left where they are.").format(
                    count=already
                ),
            )
        self._warn_about_empty_cards(request, added)

    @admin.action(description=_("Remove from the landing page"), permissions=["change"])
    def demote(self, request, queryset):
        removed = 0
        for country in queryset.filter(is_popular=True):
            country.is_popular = False
            # sort_order is deliberately kept: it is the place the destination
            # returns to when it is promoted again, and zeroing it here would
            # quietly send it to the front of the row next time.
            country.save(update_fields=["is_popular"])
            removed += 1
        self.message_user(
            request,
            _("{count} destination(s) removed from the landing page.").format(
                count=removed
            ),
        )

    def _warn_about_empty_cards(self, request, countries):
        """Name the destinations that will render as a card with no price.

        Promotion is not refused — promoting a destination before loading its
        plans is a normal order of work — but it must not happen quietly.
        """
        if not countries:
            return
        # One query for the whole selection; asking each country would be the
        # per-row queries the changelist annotation exists to avoid.
        stocked = set(
            Plan.objects.filter(country__in=countries, is_active=True).values_list(
                "country_id", flat=True
            )
        )
        empty = [country.name for country in countries if country.pk not in stocked]
        if not empty:
            return
        self.message_user(
            request,
            _(
                "No active plans yet: {names}. They will show as empty cards on "
                "the site until a plan is added."
            ).format(names=", ".join(empty)),
            level=messages.WARNING,
        )


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
        "fulfilment_warning",
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
    search_fields = (
        "title",
        "country__name",
        "country__name_uz",
        "country__name_ru",
        "region__name",
        "provider_package_code",
    )
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
        #
        # The margin annotation exists so the column header sorts. The displayed
        # figure still comes from catalog.pricing via the model properties —
        # this expression is price minus cost and nothing more, which matches
        # the property for every plan that has both numbers and sorts NULLs
        # (no supplier cost) to the bottom rather than crashing the ordering.
        return (
            super()
            .get_queryset(request)
            .prefetch_related("offers")
            .annotate(
                _margin_usd=ExpressionWrapper(
                    F("price_usd") - F("cost_usd"),
                    output_field=models.DecimalField(max_digits=10, decimal_places=2),
                )
            )
        )


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

        winner = obj.winning_offer
        if winner is None:
            # Offers exist but none from a connected supplier: there is nothing
            # to source from, and the fulfilment column carries the warning.
            return format_html('<span style="color:var(--qs-bad-text);font-weight:600;">—</span>')

        # By pk, not identity: without a prefetch each ranked_offers call
        # builds fresh instances, and an identity check would call the winner
        # a different offer from itself.
        if winner != offers[0]:
            # A cheaper offer exists, but its supplier is not connected, so the
            # dearer connected one is what orders are actually placed with.
            # Naming the cheaper source keeps the saving visible for the day
            # its integration lands.
            return format_html(
                '<span style="font-weight:600;">{}</span>'
                '<span style="color:var(--qs-ink-mute);font-size:11px;"> · {} ${} {}</span>',
                winner.get_provider_display(),
                offers[0].get_provider_display(),
                offers[0].cost_usd,
                _("not connected"),
            )

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

    # An empty heading, like the flag column: the badge explains itself and a
    # header would label the many rows that rightly show nothing.
    @display(description="")
    def fulfilment_warning(self, obj):
        """Red badge for plans on sale that no connected supplier can deliver.

        The sourcing engine refuses to route these (see Plan.winning_offer), so
        a paid order for one strands the customer. The list is where an
        operator can actually notice that before a customer does.
        """
        if not obj.unfulfillable_only:
            return ""
        return format_html(
            '<span style="display:inline-block;padding:2px 10px;border-radius:999px;'
            'font-size:11px;font-weight:700;color:var(--qs-bad-text);background:var(--qs-bad-wash);">{}</span>',
            _("no connected supplier"),
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

        from catalog.models import fulfillable_providers

        rows = []
        usable = fulfillable_providers()
        winner = obj.winning_offer
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
            elif offer.provider not in usable:
                # On file for comparison, but fulfilment cannot order there, so
                # calling it a fallback would promise a route that is not real.
                rows.append(
                    format_html(
                        '<li style="color:var(--qs-ink-mute);">{} — ${} · {}</li>',
                        offer.get_provider_display(),
                        offer.cost_usd,
                        _("not connected"),
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

    @display(description=_("Price"), ordering="price_usd")
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

    @display(description=_("Margin"), ordering="_margin_usd")
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
    search_fields = ("provider", "country__name", "country__name_uz", "country__name_ru", "note")
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

    def delete_queryset(self, request, queryset):
        """Bulk delete reprices what each rule governed, like a single delete.

        The changelist action deletes through the queryset and skips
        PricingRule.delete(), so the catalogue would keep the dead rules'
        prices until every affected plan happened to be saved again.
        """
        rules = list(queryset)
        super().delete_queryset(request, queryset)
        for rule in rules:
            rule.apply_to_plans()

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

    def delete_queryset(self, request, queryset):
        """Bulk delete must re-decide sourcing, exactly like a single delete.

        The changelist action deletes through the queryset, which never calls
        SupplierOffer.delete() — so removing a winning offer left its plan
        still routing to the deleted supplier, with the deleted package code
        and cost. Plans are re-sourced after the rows are gone, handing each
        one to its surviving runner-up.
        """
        plan_ids = list(queryset.values_list("plan_id", flat=True).distinct())
        super().delete_queryset(request, queryset)
        for plan in Plan.objects.filter(pk__in=plan_ids):
            plan.resolve_sourcing(save=True)

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
