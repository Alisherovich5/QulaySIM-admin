"""Giving an eSIM away at our own cost, from the admin.

The shop owner wanted to hand a friend a 5 GB Vietnam eSIM without going through
checkout, at what it costs us rather than at the retail price.

The whole point of the design is that this goes through the *same* machinery as a
sale. Writing an eSIM row directly would repeat the top-up mistake: a number in
our database that the wholesaler has never heard of, and a friend abroad holding
a QR code that installs nothing. So a grant creates a real order and asks the
fulfilment worker to buy a real profile.

The worker lives in the API service, not here, so the task is dispatched by name
over the shared Redis broker. Django and Celery agree on the name and the
argument; nothing else is shared.
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

#: Must match the task registered in the API's app/workers/tasks/provisioning.py.
FULFIL_TASK = "provisioning.fulfil_paid_order"


class FulfilmentNotQueued(RuntimeError):
    """The order exists but nobody was asked to buy the profile."""


def _dispatch(order_id: int) -> None:
    """Ask the API's worker to fulfil this order.

    Raises rather than logging quietly. A grant whose order was created but never
    fulfilled looks finished in the admin and delivers nothing, which is the one
    outcome worth failing the save for — the operator can retry, but only if they
    are told.
    """
    try:
        from celery import Celery
    except ImportError as exc:  # pragma: no cover - celery is a declared dep
        raise FulfilmentNotQueued("celery is not installed in the admin image") from exc

    broker = os.environ.get("REDIS_URL")
    if not broker:
        raise FulfilmentNotQueued("REDIS_URL is not set, so the worker cannot be reached")

    try:
        Celery(broker=broker).send_task(FULFIL_TASK, args=[order_id])
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator
        raise FulfilmentNotQueued(f"could not reach the worker: {exc}") from exc


def issue(grant) -> None:
    """Create the order behind a grant and queue its fulfilment.

    Called from the admin's save. Runs in a transaction so a grant row never
    survives without its order: half a giveaway is harder to find than none.
    """
    from orders.models import Order, OrderItem

    plan = grant.plan
    cost = Decimal(plan.cost_usd or 0)

    with transaction.atomic():
        order = Order.objects.create(
            customer=grant.customer,
            status=Order.Status.PAID,
            # Priced at what it cost us, not at retail. `total` is what the
            # reports read as the value of the order, and this one was not sold.
            subtotal=cost,
            discount=Decimal("0"),
            total=cost,
            # No som amount and no exchange rate: nobody was charged, and
            # inventing a figure here would put a payment that never happened
            # into the day's takings.
            amount_uzs=None,
            exchange_rate=None,
            paid_at=timezone.now(),
            is_complimentary=True,
        )
        OrderItem.objects.create(
            order=order,
            plan=plan,
            unit_price=cost,
            unit_cost=cost,
            quantity=1,
        )
        grant.cost_usd = cost
        grant.order = order
        grant.save(update_fields=["cost_usd", "order"])

        # Inside the transaction, but deferred: the worker must not be handed an
        # order id that a rollback is about to erase.
        transaction.on_commit(lambda: _dispatch(order.id))

    logger.info(
        "complimentary grant issued: order=%s customer=%s plan=%s cost=%s",
        order.id,
        grant.customer_id,
        plan.id,
        cost,
    )
