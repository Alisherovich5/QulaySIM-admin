"""Dashboard metrics for the QulaySIM Unfold admin index page."""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _


def dashboard_callback(request, context):
    from catalog.models import Country, Plan
    from customers.models import Customer
    from orders.models import ESIM, Order, OrderItem

    paid = Order.objects.filter(status=Order.Status.PAID)
    revenue = paid.aggregate(s=Sum("total"))["s"] or Decimal("0")

    # Revenue and cost for the last 14 days, so the bar chart can show the
    # profit split rather than just turnover.
    today = timezone.localdate()
    start = today - timedelta(days=13)

    revenue_rows = paid.filter(paid_at__date__gte=start).values_list("paid_at", "total")
    revenue_buckets = {start + timedelta(days=i): Decimal("0") for i in range(14)}
    for paid_at, total in revenue_rows:
        if paid_at:
            day = timezone.localtime(paid_at).date()
            if day in revenue_buckets:
                revenue_buckets[day] += total or Decimal("0")

    # Cost of goods sold: what we paid suppliers for the eSIMs on paid orders.
    # unit_cost is the snapshot taken at the time of sale; rows that predate
    # the snapshot fall back to the plan's *current* cost, which is only an
    # estimate — fs_cost_estimated below is what lets the page say so.
    cost_rows = (
        OrderItem.objects.filter(
            order__status=Order.Status.PAID, order__paid_at__date__gte=start
        )
        .values_list("order__paid_at", "quantity", Coalesce("unit_cost", "plan__cost_usd"))
    )
    cost_buckets = {start + timedelta(days=i): Decimal("0") for i in range(14)}
    for paid_at, quantity, unit_cost in cost_rows:
        if paid_at and unit_cost is not None:
            day = timezone.localtime(paid_at).date()
            if day in cost_buckets:
                cost_buckets[day] += unit_cost * quantity

    max_val = max(revenue_buckets.values()) or Decimal("1")
    chart = [
        {
            "label": date_format(day, "d M"),
            "short": date_format(day, "d"),
            "value": float(value),
            "cost": float(cost_buckets[day]),
            "profit": float(value - cost_buckets[day]),
            "pct": int((value / max_val) * 100) if max_val else 0,
            # Share of the bar that is cost, so the bar can be stacked.
            "cost_pct": int((cost_buckets[day] / value) * 100) if value else 0,
        }
        for day, value in sorted(revenue_buckets.items())
    ]

    period_revenue = sum(revenue_buckets.values())
    period_cost = sum(cost_buckets.values())
    period_profit = period_revenue - period_cost

    # Lifetime margin, measured ONLY over orders we can actually cost.
    #
    # An order with no line items (older imports, manual entries) has no
    # traceable supplier cost. Treating that as zero cost would report it as
    # pure profit and inflate the headline margin — so those orders are
    # excluded from the ratio and reported separately instead.
    costed_orders = (
        Order.objects.filter(status=Order.Status.PAID)
        .annotate(item_count=Count("items"))
        .filter(item_count__gt=0)
    )
    costed_revenue = costed_orders.aggregate(s=Sum("total"))["s"] or Decimal("0")
    lifetime_cost = OrderItem.objects.filter(order__status=Order.Status.PAID).aggregate(
        c=Sum(
            ExpressionWrapper(
                # The cost captured at the time of sale, when there is one;
                # today's plan cost otherwise (an estimate, flagged below).
                F("quantity")
                * Coalesce(F("unit_cost"), F("plan__cost_usd"), Decimal("0")),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )
    )["c"] or Decimal("0")
    lifetime_profit = costed_revenue - lifetime_cost
    uncosted_revenue = revenue - costed_revenue
    # True when any sold item predates the unit_cost snapshot: its share of
    # the cost figures above is estimated at today's plan cost, and the page
    # must say so rather than present a rewritten history as fact.
    cost_estimated = OrderItem.objects.filter(
        order__status=Order.Status.PAID, unit_cost__isnull=True
    ).exists()

    # Margin spread across the live catalogue.
    priced = Plan.objects.filter(is_active=True, cost_usd__isnull=False)
    margin_buckets = {"loss": 0, "0-20": 0, "20-40": 0, "40-60": 0, "60+": 0}
    for cost, price in priced.values_list("cost_usd", "price_usd"):
        if not cost:
            continue
        pct = (price - cost) / cost * 100
        if pct <= 0:
            margin_buckets["loss"] += 1
        elif pct < 20:
            margin_buckets["0-20"] += 1
        elif pct < 40:
            margin_buckets["20-40"] += 1
        elif pct < 60:
            margin_buckets["40-60"] += 1
        else:
            margin_buckets["60+"] += 1
    margin_total = sum(margin_buckets.values()) or 1
    margin_spread = [
        {
            "label": label,
            "count": count,
            "pct": int(count / margin_total * 100),
        }
        for label, count in margin_buckets.items()
    ]

    # Where the catalogue comes from — the two supply routes.
    supply_rows = (
        Plan.objects.filter(is_active=True)
        .values("provider")
        .annotate(c=Count("id"))
        .order_by("-c")
    )
    supply_total = sum(row["c"] for row in supply_rows) or 1
    supply_mix = [
        {"provider": row["provider"], "count": row["c"], "pct": int(row["c"] / supply_total * 100)}
        for row in supply_rows
    ]

    # Top destinations by eSIMs sold.
    top_countries = (
        ESIM.objects.values("plan__country__name", "plan__country__iso2")
        .annotate(c=Count("id"))
        .order_by("-c")[:5]
    )

    recent_orders = (
        Order.objects.select_related("customer").order_by("-created_at")[:8]
    )

    # ---- Donut inputs -----------------------------------------------------
    # Pre-computed as stroke offsets so the template stays free of arithmetic:
    # each segment carries its own dash length and offset around one circle.
    def _arcs(rows, circumference=100.0, gap=1.0):
        """Turn (label, value, colour_role) rows into donut segment geometry.

        A 1-unit gap in the surface colour separates touching segments — the
        spacer is what makes neighbouring steps read as distinct, rather than a
        stroke drawn around each one.
        """
        total = sum(max(v, 0) for _, v, _ in rows)
        if not total:
            return [], 0
        out, offset = [], 0.0
        for label, value, role in rows:
            share = max(value, 0) / total
            length = share * circumference
            visible = max(length - gap, 0.4) if share < 1 else circumference
            out.append(
                {
                    "label": label,
                    "value": value,
                    "percent": round(share * 100),
                    "role": role,
                    "dash": round(visible, 3),
                    "rest": round(circumference - visible, 3),
                    "offset": round(-offset, 3),
                }
            )
            offset += length
        return out, total

    status_rows = [
        (_("Paid"), Order.objects.filter(status=Order.Status.PAID).count(), "good"),
        (_("Pending"), Order.objects.filter(status=Order.Status.PENDING).count(), "warning"),
        (_("Cancelled"), Order.objects.filter(status=Order.Status.CANCELLED).count(), "neutral"),
        (_("Refunded"), Order.objects.filter(status=Order.Status.REFUNDED).count(), "critical"),
    ]
    status_arcs, status_total = _arcs([r for r in status_rows if r[1] > 0])

    # Margin buckets are an ordered scale, not identities, so they take one
    # sequential hue light→dark. A loss is a status, not a step on that ramp.
    margin_rows = [
        (_("Loss"), margin_buckets["loss"], "loss"),
        ("0–20%", margin_buckets["0-20"], "s1"),
        ("20–40%", margin_buckets["20-40"], "s2"),
        ("40–60%", margin_buckets["40-60"], "s3"),
        ("60%+", margin_buckets["60+"], "s4"),
    ]
    margin_arcs, margin_total = _arcs([r for r in margin_rows if r[1] > 0])

    context.update(
        {
            # The meter's track length is 100 minus the filled arc, so the
            # template needs no arithmetic.
            "fs_admin_path": settings.ADMIN_URL_PATH,
            "fs_margin_track": 100,
            "fs_status_arcs": status_arcs,
            "fs_status_total": status_total,
            "fs_margin_arcs": margin_arcs,
            "fs_margin_total": margin_total,
            "fs_revenue": revenue,
            "fs_orders_count": Order.objects.count(),
            "fs_paid_count": paid.count(),
            "fs_pending_count": Order.objects.filter(
                status=Order.Status.PENDING
            ).count(),
            "fs_customers_count": Customer.objects.count(),
            "fs_active_esims": ESIM.objects.filter(
                status=ESIM.Status.ACTIVE
            ).count(),
            "fs_total_esims": ESIM.objects.count(),
            "fs_countries_count": Country.objects.filter(is_active=True).count(),
            "fs_chart": chart,
            "fs_top_countries": list(top_countries),
            "fs_recent_orders": recent_orders,
            # Profitability
            "fs_cost": lifetime_cost,
            "fs_cost_estimated": cost_estimated,
            "fs_profit": lifetime_profit,
            "fs_margin_percent": (
                round(lifetime_profit / costed_revenue * 100, 1) if costed_revenue else 0
            ),
            "fs_costed_revenue": costed_revenue,
            "fs_uncosted_revenue": uncosted_revenue,
            "fs_period_revenue": period_revenue,
            "fs_period_cost": period_cost,
            "fs_period_profit": period_profit,
            "fs_margin_spread": margin_spread,
            "fs_supply_mix": supply_mix,
            "fs_unpriced_plans": Plan.objects.filter(
                is_active=True, cost_usd__isnull=True
            ).count(),
        }
    )
    # --- What needs a person, and today's real numbers ----------------------
    #
    # The page used to open with lifetime revenue ($8.56) and three doughnuts.
    # Nobody opens a back office to admire a doughnut: they open it to find out
    # whether anything broke and how today went. Those two answers now come
    # first, and the analytics keep their place further down.
    from config.attention import collect

    context["fs_attention"] = collect()
    context["fs_attention_critical"] = [i for i in context["fs_attention"] if i.is_critical]

    # Money in som, from the frozen amount the card was actually charged rather
    # than a USD figure converted at today's rate. `amount_uzs` is what the
    # customer paid; the dollar total is our own bookkeeping.
    periods = []
    for label, days in ((_("Today"), 0), (_("7 days"), 7), (_("30 days"), 30)):
        since = timezone.now() - timedelta(days=days) if days else timezone.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        window = paid.filter(paid_at__gte=since)
        rows = window.aggregate(
            count=Count("id"),
            som=Coalesce(Sum("amount_uzs"), Decimal("0")),
            usd=Coalesce(Sum("total"), Decimal("0")),
        )
        cost = OrderItem.objects.filter(
            order__status=Order.Status.PAID, order__paid_at__gte=since
        ).aggregate(
            c=Coalesce(
                Sum(
                    ExpressionWrapper(
                        Coalesce("unit_cost", "plan__cost_usd") * F("quantity"),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )
                ),
                Decimal("0"),
            )
        )["c"]
        profit = (rows["usd"] or Decimal("0")) - cost
        periods.append(
            {
                "label": label,
                "count": rows["count"],
                "som": rows["som"] or Decimal("0"),
                "profit": profit,
                # Margin on a day with no sales is not zero, it is undefined —
                # and printing 0% would read as "we sold at cost".
                "margin": (
                    int(profit / rows["usd"] * 100) if rows["usd"] else None
                ),
            }
        )
    context["fs_periods"] = periods

    return context
