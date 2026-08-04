"""Generate demo orders spread across the last 14 days so the dashboard has data.

Run:  ./venv/bin/python seed_orders.py
"""

import os
import random
from datetime import timedelta

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.utils import timezone  # noqa: E402

from catalog.models import Plan  # noqa: E402
from customers.models import Customer  # noqa: E402
from orders.models import ESIM, Order, OrderItem, Payment  # noqa: E402

DEMO_NAMES = [
    ("aziz@example.com", "Aziz Karimov"),
    ("dilnoza@example.com", "Dilnoza Yusupova"),
    ("john@example.com", "John Carter"),
    ("mei@example.com", "Mei Tanaka"),
    ("sara@example.com", "Sara Lopez"),
    ("omar@example.com", "Omar Said"),
]


def run():
    # Ensure some customers exist (password is a dummy bcrypt-like placeholder).
    customers = []
    for email, name in DEMO_NAMES:
        c, _ = Customer.objects.get_or_create(
            email=email,
            defaults={"full_name": name, "hashed_password": "x", "is_active": True},
        )
        customers.append(c)

    plans = list(Plan.objects.filter(is_active=True))
    if not plans:
        print("No plans — run backend-api/seed.py first.")
        return

    created = 0
    now = timezone.now()
    for day in range(14):
        # 1-4 orders per day
        for _ in range(random.randint(1, 4)):
            when = now - timedelta(days=day, hours=random.randint(0, 20))
            customer = random.choice(customers)
            plan = random.choice(plans)
            qty = random.randint(1, 2)
            total = plan.price_usd * qty

            order = Order.objects.create(
                customer=customer,
                status=Order.Status.PAID,
                subtotal=total,
                discount=0,
                total=total,
                paid_at=when,
            )
            # Backdate created_at too.
            Order.objects.filter(pk=order.pk).update(created_at=when)

            OrderItem.objects.create(
                order=order,
                plan=plan,
                unit_price=plan.price_usd,
                # Snapshot the cost at "sale" time, as real fulfilment does:
                # demo margins must not drift when supplier prices move later.
                unit_cost=plan.cost_usd,
                quantity=qty,
            )
            Payment.objects.create(
                order=order,
                method="mock",
                amount=total,
                status=Payment.Status.SUCCESS,
                provider_ref=f"MOCK-{random.randint(100000, 999999)}",
            )
            for n in range(qty):
                ESIM.objects.create(
                    order=order,
                    plan=plan,
                    customer=customer,
                    iccid=f"8998{random.randint(10**14, 10**15 - 1)}",
                    qr_payload="LPA:1$fastsim.rsp.example.com$demo",
                    qr_image="",
                    status=random.choice(
                        [ESIM.Status.ACTIVE, ESIM.Status.PENDING, ESIM.Status.ACTIVE]
                    ),
                    data_total_mb=0 if plan.is_unlimited else plan.data_amount_mb,
                    data_used_mb=random.randint(0, max(1, plan.data_amount_mb // 2)),
                    validity_days=plan.validity_days,
                    expires_at=when + timedelta(days=plan.validity_days),
                )
            created += 1

    print(f"Created {created} demo orders. Total orders now: {Order.objects.count()}")


if __name__ == "__main__":
    run()
