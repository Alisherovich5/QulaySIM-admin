"""Dashboard metrics for the FastSIM Unfold admin index page."""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.utils import timezone


def dashboard_callback(request, context):
    from catalog.models import Country
    from customers.models import Customer
    from orders.models import ESIM, Order

    paid = Order.objects.filter(status=Order.Status.PAID)
    revenue = paid.aggregate(s=Sum("total"))["s"] or Decimal("0")

    # Sales for the last 14 days (bar chart).
    today = timezone.localdate()
    start = today - timedelta(days=13)
    rows = (
        paid.filter(paid_at__date__gte=start)
        .values_list("paid_at", "total")
    )
    buckets = {start + timedelta(days=i): Decimal("0") for i in range(14)}
    for paid_at, total in rows:
        if paid_at:
            d = timezone.localtime(paid_at).date()
            if d in buckets:
                buckets[d] += total or Decimal("0")
    max_val = max(buckets.values()) or Decimal("1")
    chart = [
        {
            "label": d.strftime("%d %b"),
            "short": d.strftime("%d"),
            "value": float(v),
            "pct": int((v / max_val) * 100) if max_val else 0,
        }
        for d, v in sorted(buckets.items())
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

    context.update(
        {
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
        }
    )
    return context
