"""Selling-price calculation.

Supplier cost comes in from two places — the eSIM Access sync and manual
entry — and what we charge on top of it is a business decision, not a
supplier one. This module resolves that markup.

Rules cascade from most specific to most general; the first match wins:

    1. the plan's own `markup_percent`   (one-off exception)
    2. a rule for the plan's country     (e.g. Japan sells at +45%)
    3. a rule for the plan's provider    (e.g. everything from eSIM Access +30%)
    4. the global rule                   (the house default)
    5. no rule at all                    -> DEFAULT_MARKUP_PERCENT

A rule may also enforce a floor in absolute dollars, so a percentage markup on
a cheap plan cannot leave us with a few cents of margin, and may round the
result to a tidier number.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENTS = Decimal("0.01")
DEFAULT_MARKUP_PERCENT = Decimal("30")


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def _apply_rounding(price: Decimal, mode: str) -> Decimal:
    """Nudge the computed price to a retail-looking number.

    Always rounds *up*: rounding down would quietly eat the margin the rule
    just calculated.
    """
    if mode == "none" or price <= 0:
        return _money(price)
    if mode == "whole":
        return _money(price.to_integral_value(rounding="ROUND_CEILING"))
    if mode == "half":
        steps = (price / Decimal("0.5")).to_integral_value(rounding="ROUND_CEILING")
        return _money(steps * Decimal("0.5"))
    if mode == "charm":
        # 4.12 -> 4.99, 5.00 -> 5.99
        whole = price.to_integral_value(rounding="ROUND_FLOOR")
        candidate = whole + Decimal("0.99")
        if candidate < price:
            candidate = whole + Decimal("1.99")
        return _money(candidate)
    return _money(price)


def resolve_rule(plan, rules: list | None = None):
    """Return the PricingRule that governs this plan, or None.

    `rules` may be pre-fetched by the caller to avoid a query per plan when
    recalculating a whole catalogue.
    """
    from catalog.models import PricingRule

    if rules is None:
        rules = list(PricingRule.objects.filter(is_active=True))

    country_id = plan.country_id
    provider = plan.provider

    # Most specific first. A tariff can be named four ways and the narrowest
    # statement about it has to win, or an operator's exception is silently
    # overruled by a broader rule they set weeks earlier.
    def matches_tier(rule) -> bool:
        if not rule.tier_data_mb:
            return False
        if rule.tier_data_mb != plan.data_amount_mb:
            return False
        return rule.tier_days in (None, plan.validity_days)

    # 1. this destination AND this size — "Japan 1 GB"
    exact = [
        r
        for r in rules
        if r.scope == PricingRule.Scope.COUNTRY
        and r.country_id == country_id
        and matches_tier(r)
    ]
    if exact:
        return exact[0]

    # 2. this size, everywhere — "1 GB is priced this way"
    by_tier = [r for r in rules if r.scope == PricingRule.Scope.TIER and matches_tier(r)]
    if by_tier:
        return by_tier[0]

    # 3. this destination, any size
    by_country = [
        r
        for r in rules
        if r.scope == PricingRule.Scope.COUNTRY
        and r.country_id == country_id
        and not r.tier_data_mb
    ]
    if by_country:
        return by_country[0]

    by_provider = [r for r in rules if r.scope == PricingRule.Scope.PROVIDER and r.provider == provider]
    if by_provider:
        return by_provider[0]

    by_global = [r for r in rules if r.scope == PricingRule.Scope.GLOBAL]
    return by_global[0] if by_global else None


def calculate_price(plan, rules: list | None = None) -> Decimal | None:
    """Selling price for `plan`, or None when it cannot be computed.

    Returns None when there is no supplier cost to mark up — the admin is then
    expected to set the price by hand.
    """
    cost = plan.cost_usd
    if cost is None or cost <= 0:
        return None

    rule = resolve_rule(plan, rules)

    # An individual markup on the plan always wins over any rule.
    if plan.markup_percent is not None:
        percent = plan.markup_percent
    elif rule is not None:
        percent = rule.markup_percent
    else:
        percent = DEFAULT_MARKUP_PERCENT

    price = cost * (Decimal("1") + percent / Decimal("100"))

    # A percentage on a $0.40 plan is pennies; the floor keeps it worthwhile.
    min_margin = rule.min_margin_usd if rule else Decimal("0")
    if min_margin and price - cost < min_margin:
        price = cost + min_margin

    # New markups are validated to be >= 0, but a legacy negative one can
    # still be on a row. A price of zero or below is never right to *write*,
    # so it is treated like a missing cost: left for the admin to set by hand.
    if price <= 0:
        return None

    return _apply_rounding(price, rule.rounding if rule else "none")


def margin(plan) -> Decimal | None:
    """Absolute profit per unit, or None when cost is unknown."""
    if plan.cost_usd is None or plan.price_usd is None:
        return None
    return _money(plan.price_usd - plan.cost_usd)


def margin_percent(plan) -> Decimal | None:
    """Profit as a percentage of cost, or None when cost is unknown/zero."""
    if not plan.cost_usd or plan.price_usd is None:
        return None
    return _money((plan.price_usd - plan.cost_usd) / plan.cost_usd * Decimal("100"))
