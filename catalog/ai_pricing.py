"""Turning a sentence in Uzbek into pricing rules a human then approves.

The division of labour here is the whole design, so it is worth stating plainly:

    Claude reads the sentence and proposes rule *parameters*.
    This module validates them against the real catalogue.
    catalog/pricing.py computes every price, exactly as it does in production.
    A person looks at what would change and clicks approve.
    Only then is anything written.

Claude never computes a price and never writes to the database. That is not
caution for its own sake — a markup applies to 1377 live tariffs, and a plausible
wrong number would be charged to real customers before anyone noticed. Language
models are good at "1 GB dan arzon tariflarga 200% qo'y" → `{scope: tier,
tier_data_mb: 1024, markup_percent: 200}`. They are the wrong tool for
`0.46 × 3.00 = 1.38`, which Python does correctly every time.

The preview is not an estimate. It builds the rule set that *would* exist after
approval and runs `calculate_price` over every affected plan — the same function
`Plan.save()` calls. What the preview shows is what the prices become.

Three rails the model cannot talk its way past, because they are checked after it
has spoken and in code it does not see:

  * a markup outside 0–`AI_PRICING_MAX_MARKUP` is refused outright;
  * a destination, supplier or traffic size that is not in the catalogue is
    refused, so a hallucinated "MC" or a 2 TB tier cannot create a dead rule;
  * at most `AI_PRICING_MAX_RULES` rules per instruction, so one confused
    answer cannot reprice the catalogue in a single click.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.conf import settings
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

CENTS = Decimal("0.01")

MODEL = getattr(settings, "ANTHROPIC_MODEL", "claude-opus-5")
MAX_RULES = int(getattr(settings, "AI_PRICING_MAX_RULES", 25))
MAX_MARKUP = Decimal(str(getattr(settings, "AI_PRICING_MAX_MARKUP", 1000)))
MAX_MIN_MARGIN = Decimal("100")


class AiUnavailable(RuntimeError):
    """No API key, no network, or the API refused. Never a silent failure."""


# --------------------------------------------------------------------------
# What comes back
# --------------------------------------------------------------------------


@dataclass
class Proposal:
    """One pricing rule the model suggests. Not yet a database row."""

    scope: str
    markup_percent: Decimal
    country_iso2: str = ""
    provider: str = ""
    tier_data_mb: int = 0
    tier_days: int = 0
    min_margin_usd: Decimal = Decimal("0")
    rounding: str = "none"
    note: str = ""
    reason: str = ""
    # Filled in by validation, not by the model.
    country_id: int | None = None
    country_name: str = ""
    problem: str = ""

    @property
    def ok(self) -> bool:
        return not self.problem

    @property
    def target(self) -> str:
        """How this rule reads to a person, in the admin's own vocabulary."""
        from catalog.margin import size_label

        size = size_label(self.tier_data_mb) if self.tier_data_mb else ""
        days = f" · {self.tier_days} kun" if self.tier_days else ""
        if self.scope == "global":
            return str(_("Everything (house default)"))
        if self.scope == "provider":
            return f"{self.provider}"
        if self.scope == "country":
            return f"{self.country_name}{(' ' + size + days) if size else ''}"
        if self.scope == "tier":
            return f"{size}{days} — {_('every destination')}"
        return self.scope


@dataclass
class Change:
    """One tariff whose price the proposal would move."""

    plan_id: int
    title: str
    destination: str
    cost: Decimal
    before: Decimal
    after: Decimal

    @property
    def delta(self) -> Decimal:
        return (self.after - self.before).quantize(CENTS, rounding=ROUND_HALF_UP)

    @property
    def margin_before(self) -> Decimal:
        return (self.before - self.cost).quantize(CENTS, rounding=ROUND_HALF_UP)

    @property
    def margin_after(self) -> Decimal:
        return (self.after - self.cost).quantize(CENTS, rounding=ROUND_HALF_UP)


@dataclass
class Preview:
    """Exactly what approving would do."""

    proposals: list[Proposal] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    understood: str = ""
    question: str = ""
    confident: bool = True
    unchanged: int = 0
    locked_skipped: int = 0
    sample_limit: int = 60

    @property
    def usable(self) -> list[Proposal]:
        return [p for p in self.proposals if p.ok]

    @property
    def rejected(self) -> list[Proposal]:
        return [p for p in self.proposals if not p.ok]

    @property
    def moved(self) -> int:
        return len(self.changes)

    @property
    def sample(self) -> list[Change]:
        """Biggest movers first — the ones worth checking by eye."""
        return sorted(self.changes, key=lambda c: -abs(c.delta))[: self.sample_limit]

    @property
    def margin_delta(self) -> Decimal:
        """Added margin per one unit of each affected tariff, summed.

        Not a revenue forecast — it says nothing about how many will sell. It is
        the per-unit profit change across the tariffs that move, which is the
        figure that tells you whether a markup change is worth making.
        """
        total = sum((c.margin_after - c.margin_before for c in self.changes), Decimal("0"))
        return total.quantize(CENTS, rounding=ROUND_HALF_UP)

    @property
    def raised(self) -> int:
        return sum(1 for c in self.changes if c.delta > 0)

    @property
    def lowered(self) -> int:
        return sum(1 for c in self.changes if c.delta < 0)


# --------------------------------------------------------------------------
# Asking Claude
# --------------------------------------------------------------------------


PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "understood": {
            "type": "string",
            "description": (
                "Restate in Uzbek, in one sentence, what pricing change you "
                "understood the operator to be asking for."
            ),
        },
        "confident": {
            "type": "boolean",
            "description": (
                "False when the instruction is ambiguous enough that guessing "
                "could reprice the wrong tariffs."
            ),
        },
        "question": {
            "type": "string",
            "description": (
                "When not confident, the single clarifying question to ask, in "
                "Uzbek. Empty string otherwise."
            ),
        },
        "rules": {
            "type": "array",
            "description": "The pricing rules that express the instruction.",
            "items": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["global", "provider", "country", "tier"],
                        "description": (
                            "global = the house default for everything; "
                            "provider = one supplier; country = one destination "
                            "(optionally narrowed to one traffic size); "
                            "tier = one traffic size across every destination."
                        ),
                    },
                    "country_iso2": {
                        "type": "string",
                        "description": (
                            "ISO2 code, e.g. TR. Required for scope=country, "
                            "empty string otherwise."
                        ),
                    },
                    "provider": {
                        "type": "string",
                        "description": (
                            "Supplier key. Required for scope=provider, empty "
                            "string otherwise."
                        ),
                    },
                    "tier_data_mb": {
                        "type": "integer",
                        "description": (
                            "Traffic size in MB (1024 = 1 GB). Required for "
                            "scope=tier; optional on scope=country to narrow the "
                            "rule to one tariff. Use 0 for 'any size'."
                        ),
                    },
                    "tier_days": {
                        "type": "integer",
                        "description": (
                            "Validity in days. Use 0 to cover every duration of "
                            "that size, which is almost always what is wanted."
                        ),
                    },
                    "markup_percent": {
                        "type": "number",
                        "description": (
                            "Percentage added on top of supplier cost. 50 means "
                            "cost x 1.5. This is a markup on cost, NOT a margin "
                            "share of the selling price."
                        ),
                    },
                    "min_margin_usd": {
                        "type": "number",
                        "description": (
                            "Profit floor in dollars. Use 0 unless the operator "
                            "asked for one."
                        ),
                    },
                    "rounding": {
                        "type": "string",
                        "enum": ["none", "charm", "half", "whole"],
                        "description": (
                            "none = exact cents; charm = end in .99; half = up "
                            "to 0.50; whole = up to a whole dollar."
                        ),
                    },
                    "note": {
                        "type": "string",
                        "description": "Short label for the rule, in Uzbek.",
                    },
                    "reason": {
                        "type": "string",
                        "description": (
                            "One short sentence in Uzbek: why this rule follows "
                            "from what the operator said."
                        ),
                    },
                },
                "required": [
                    "scope",
                    "country_iso2",
                    "provider",
                    "tier_data_mb",
                    "tier_days",
                    "markup_percent",
                    "min_margin_usd",
                    "rounding",
                    "note",
                    "reason",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["understood", "confident", "question", "rules"],
    "additionalProperties": False,
}


SYSTEM = """You turn a QulaySIM operator's plain-language pricing instruction into \
pricing rules. QulaySIM resells eSIM data plans in Uzbekistan; operators write in \
Uzbek, sometimes mixing Russian or English words.

You propose rules. You never compute a price — the system does that arithmetic \
itself and shows the operator every price that would move before anything is \
saved. So propose the rule that expresses the instruction and let the preview \
speak for the consequences.

How rules resolve: the narrowest rule wins. A destination-and-size rule beats a \
size rule, which beats a destination rule, which beats a supplier rule, which \
beats the house default. A tariff whose price an operator has typed by hand is \
locked and no rule touches it.

markup_percent is a markup on supplier COST, not a share of the selling price. \
"50%" on a $2 cost gives a $3 price, earning $1. If an operator asks for a \
margin expressed as a share of the sale price ("sotuvdan 30% foyda"), convert it: \
a 30% share of the price is a 42.86% markup on cost. Say so in `reason`.

Prefer one rule over several. "1 GB tariflarga 200%" is a single tier rule, not \
208 country rules. Only propose per-destination rules when the operator named \
destinations.

Set confident=false when acting on a guess could reprice the wrong tariffs — an \
instruction that names no target at all, a number that could be either a markup \
or a final price, or a size the catalogue does not carry. Ask one question rather \
than guessing. An unclear instruction with confident=false and an empty rules \
list is a good answer; a confident wrong one is not.

Write `understood`, `note` and `reason` in Uzbek."""


def _client():
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - packaging problem, not logic
        raise AiUnavailable(
            _("The anthropic package is not installed on the server.")
        ) from exc

    key = getattr(settings, "ANTHROPIC_API_KEY", "")
    if not key:
        raise AiUnavailable(
            _(
                "ANTHROPIC_API_KEY is not set on the server, so the assistant "
                "cannot be reached. Everything else on this page works without it."
            )
        )
    return anthropic.Anthropic(api_key=key)


def catalogue_facts() -> str:
    """What the model needs to know to name a real destination or size.

    Kept stable and put behind a cache breakpoint: it is a few thousand tokens
    that do not change between one instruction and the next, so after the first
    call it is billed at roughly a tenth of the price.
    """
    from catalog.margin import size_label
    from catalog.models import Country, Plan

    sizes = (
        Plan.objects.filter(is_active=True, is_unlimited=False)
        .values_list("data_amount_mb", flat=True)
        .distinct()
        .order_by("data_amount_mb")
    )
    size_text = ", ".join(
        f"{size_label(mb)}={mb}" for mb in sizes if mb
    ) or "(katalog bo'sh)"

    countries = Country.objects.filter(is_active=True).order_by("name").values_list(
        "iso2", "name", "name_uz"
    )
    country_text = ", ".join(
        f"{iso2}={uz or name}" for iso2, name, uz in countries
    ) or "(katalog bo'sh)"

    providers = (
        Plan.objects.filter(is_active=True)
        .exclude(provider="")
        .values_list("provider", flat=True)
        .distinct()
        .order_by("provider")
    )

    return (
        "Katalogdagi trafik o'lchamlari (yorliq=MB):\n"
        f"{size_text}\n\n"
        "Ta'minotchilar:\n"
        f"{', '.join(providers) or '(yoq)'}\n\n"
        "Yo'nalishlar (ISO2=nom):\n"
        f"{country_text}\n\n"
        f"Bitta ko'rsatma uchun ko'pi bilan {MAX_RULES} qoida. "
        f"markup_percent 0 dan {MAX_MARKUP} gacha bo'lishi shart."
    )


def ask(instruction: str) -> dict:
    """Send one instruction to Claude and return the validated JSON it produced.

    Structured outputs, so the shape is guaranteed by the API rather than by a
    regex over prose. Medium effort: this is translation from one vocabulary to
    another, not a problem that rewards long deliberation.
    """
    client = _client()
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": PROPOSAL_SCHEMA},
            },
            system=[
                {"type": "text", "text": SYSTEM},
                {
                    "type": "text",
                    "text": catalogue_facts(),
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            messages=[{"role": "user", "content": instruction}],
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator verbatim
        logger.exception("AI pricing request failed")
        raise AiUnavailable(str(exc)) from exc

    # A safety decline is a normal HTTP 200 with an empty content list, so the
    # stop reason has to be read before the content is indexed.
    if getattr(response, "stop_reason", None) == "refusal":
        raise AiUnavailable(
            _("The assistant declined to answer this instruction. Rephrase it.")
        )

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        raise AiUnavailable(_("The assistant returned an empty answer."))
    return json.loads(text)


# --------------------------------------------------------------------------
# Validation — after the model has spoken, in code it never sees
# --------------------------------------------------------------------------


def _decimal(value, default="0") -> Decimal:
    try:
        return Decimal(str(value)).quantize(CENTS, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def validate(payload: dict) -> list[Proposal]:
    """Turn the model's JSON into Proposals, marking any that cannot stand.

    A bad proposal is kept and labelled rather than dropped: an operator who
    asked for five destinations and sees four should be told the fifth was
    refused and why, not left to count.
    """
    from catalog.models import Country, Plan, PricingRule

    raw = payload.get("rules") or []
    proposals: list[Proposal] = []

    known_sizes = set(
        Plan.objects.filter(is_active=True).values_list("data_amount_mb", flat=True)
    )
    known_providers = set(
        Plan.objects.exclude(provider="").values_list("provider", flat=True)
    )
    roundings = {choice for choice, _label in PricingRule.Rounding.choices}
    scopes = {choice for choice, _label in PricingRule.Scope.choices}

    for index, item in enumerate(raw):
        if index >= MAX_RULES:
            break
        proposal = Proposal(
            scope=str(item.get("scope") or "").strip(),
            markup_percent=_decimal(item.get("markup_percent")),
            country_iso2=str(item.get("country_iso2") or "").strip().upper(),
            provider=str(item.get("provider") or "").strip(),
            tier_data_mb=int(item.get("tier_data_mb") or 0),
            tier_days=int(item.get("tier_days") or 0),
            min_margin_usd=_decimal(item.get("min_margin_usd")),
            rounding=str(item.get("rounding") or "none").strip(),
            note=str(item.get("note") or "")[:200],
            reason=str(item.get("reason") or "")[:300],
        )

        if proposal.scope not in scopes:
            proposal.problem = _("Unknown rule type.")
        elif not (Decimal("0") <= proposal.markup_percent <= MAX_MARKUP):
            proposal.problem = _("Markup %(value)s%% is outside the allowed 0–%(max)s%%.") % {
                "value": proposal.markup_percent,
                "max": MAX_MARKUP,
            }
        elif not (Decimal("0") <= proposal.min_margin_usd <= MAX_MIN_MARGIN):
            proposal.problem = _("Profit floor is outside the allowed range.")
        elif proposal.rounding not in roundings:
            proposal.problem = _("Unknown rounding mode.")
        elif proposal.scope == PricingRule.Scope.COUNTRY:
            country = Country.objects.filter(iso2__iexact=proposal.country_iso2).first()
            if country is None:
                proposal.problem = _("There is no destination %(code)s in the catalogue.") % {
                    "code": proposal.country_iso2 or "—"
                }
            else:
                proposal.country_id = country.id
                proposal.country_name = country.name_uz or country.name
        elif proposal.scope == PricingRule.Scope.PROVIDER:
            if proposal.provider not in known_providers:
                proposal.problem = _("There is no supplier %(name)s in the catalogue.") % {
                    "name": proposal.provider or "—"
                }
        elif proposal.scope == PricingRule.Scope.TIER and not proposal.tier_data_mb:
            proposal.problem = _("A size rule has to name a size.")

        # A size nothing in the catalogue carries would create a rule that
        # silently governs nothing.
        if proposal.ok and proposal.tier_data_mb and proposal.tier_data_mb not in known_sizes:
            proposal.problem = _("No tariff has a size of %(mb)s MB.") % {
                "mb": proposal.tier_data_mb
            }

        proposals.append(proposal)

    return proposals


# --------------------------------------------------------------------------
# The preview — the real pricing code, on the real catalogue, in memory
# --------------------------------------------------------------------------


def _identity(rule) -> tuple:
    """What makes two rules the same rule, matching the unique constraints."""
    from catalog.models import PricingRule

    scope = rule.scope
    if scope == PricingRule.Scope.GLOBAL:
        return ("global",)
    if scope == PricingRule.Scope.PROVIDER:
        return ("provider", rule.provider)
    if scope == PricingRule.Scope.COUNTRY:
        return ("country", rule.country_id, rule.tier_data_mb or None, rule.tier_days or None)
    return ("tier", rule.tier_data_mb, rule.tier_days or None)


def _as_rule(proposal: Proposal):
    """An unsaved PricingRule carrying the proposal, for the preview to use."""
    from catalog.models import PricingRule

    return PricingRule(
        scope=proposal.scope,
        provider=proposal.provider if proposal.scope == PricingRule.Scope.PROVIDER else "",
        country_id=proposal.country_id if proposal.scope == PricingRule.Scope.COUNTRY else None,
        tier_data_mb=proposal.tier_data_mb or None,
        tier_days=proposal.tier_days or None,
        markup_percent=proposal.markup_percent,
        min_margin_usd=proposal.min_margin_usd,
        rounding=proposal.rounding,
        is_active=True,
        note=proposal.note,
    )


def _priceable_plans():
    """Every plan a rule could reprice, with the columns pricing reads.

    One queryset used by both the preview and the save, so the two can never
    disagree about which tariffs are in scope.
    """
    from catalog.models import Plan

    return (
        Plan.objects.filter(is_active=True, cost_usd__isnull=False)
        .select_related("country", "region")
        .only(
            "id",
            "title",
            "cost_usd",
            "price_usd",
            "price_locked",
            "markup_percent",
            "data_amount_mb",
            "validity_days",
            "provider",
            "country__name",
            "country__name_uz",
            "region__name",
            "region__name_uz",
        )
    )


def _movements(rules: list) -> tuple[list, int, int]:
    """Which plans the rule set moves, and to what. Writes nothing.

    Returns `(moved, unchanged, locked_skipped)` where `moved` is a list of
    `(plan, new_price)` — the plan objects carry the new price on `price_usd`
    already, so the caller can bulk-update them directly.

    This function is the single definition of "what these rules do", called by
    the preview to render and by the save to persist. That is not tidiness: when
    the two were separate, the preview priced every plan while the save only
    repriced the ones its rule's scope selected, so a tariff whose stored price
    was stale for any other reason was promised a new price the approval never
    delivered. Sharing the computation makes the promise true by construction.
    """
    from catalog.pricing import calculate_price

    moved: list = []
    unchanged = 0
    locked = 0

    for plan in _priceable_plans():
        if plan.price_locked:
            # A hand-typed price outranks every rule, so it is neither a change
            # to approve nor a row to write.
            locked += 1
            continue
        new_price = calculate_price(plan, rules)
        if new_price is None or new_price == plan.price_usd:
            unchanged += 1
            continue
        moved.append((plan, new_price))

    return moved, unchanged, locked


def _destination(plan) -> str:
    if plan.country_id:
        return plan.country.name_uz or plan.country.name
    if plan.region_id:
        return plan.region.name_uz or plan.region.name
    return "—"


def _rules_after(usable: list[Proposal]) -> list:
    """The active rule set as it would be once these proposals are saved."""
    from catalog.models import PricingRule

    existing = list(PricingRule.objects.filter(is_active=True))
    proposed = [_as_rule(p) for p in usable]
    replaced = {_identity(rule) for rule in proposed}
    return [rule for rule in existing if _identity(rule) not in replaced] + proposed


def preview(proposals: list[Proposal], *, sample_limit: int = 60) -> Preview:
    """What the catalogue's prices become if these rules are approved.

    Runs `calculate_price` — the same function `Plan.save()` calls — over every
    active plan against the rule set that would exist afterwards. Nothing is
    written and nothing is approximated.
    """
    result = Preview(proposals=proposals, sample_limit=sample_limit)
    usable = result.usable
    if not usable:
        return result

    moved, result.unchanged, result.locked_skipped = _movements(_rules_after(usable))
    result.changes = [
        Change(
            plan_id=plan.id,
            title=plan.title,
            destination=_destination(plan),
            cost=plan.cost_usd,
            before=plan.price_usd,
            after=new_price,
        )
        for plan, new_price in moved
    ]
    return result


def apply(proposals: list[Proposal], *, actor: str = "") -> int:
    """Write the approved rules and every price the preview promised.

    Two steps, and the second is the one that makes this feature honest. Saving
    a rule reprices the plans that rule's own scope selects — which is right for
    the admin's rule form and not enough here, because the operator approved a
    list of prices, not a rule. So after the rules are written the movements are
    recomputed with `_movements` (the same function that drew the preview) and
    persisted, so every price shown is a price delivered.

    One transaction: a half-applied instruction would leave the catalogue in a
    state nobody asked for and nobody could describe.
    """
    from django.db import transaction

    from catalog.models import Plan, PricingRule

    usable = [p for p in proposals if p.ok]
    if not usable:
        return 0

    with transaction.atomic():
        for proposal in usable:
            rule = _as_rule(proposal)
            lookup = {"scope": proposal.scope, "is_active": True}
            if proposal.scope == PricingRule.Scope.PROVIDER:
                lookup["provider"] = proposal.provider
            elif proposal.scope == PricingRule.Scope.COUNTRY:
                lookup["country_id"] = proposal.country_id
                lookup["tier_data_mb"] = proposal.tier_data_mb or None
                lookup["tier_days"] = proposal.tier_days or None
            elif proposal.scope == PricingRule.Scope.TIER:
                lookup["tier_data_mb"] = proposal.tier_data_mb
                lookup["tier_days"] = proposal.tier_days or None

            PricingRule.objects.update_or_create(
                **lookup,
                defaults={
                    "markup_percent": rule.markup_percent,
                    "min_margin_usd": rule.min_margin_usd,
                    "rounding": rule.rounding,
                    "note": (proposal.note or "")[:200],
                },
            )

        # Read the rules back rather than reusing the proposed ones: saving may
        # have replaced an existing row, and what has to be applied now is the
        # catalogue's real rule set, not the request's idea of it.
        moved, _unchanged, _locked = _movements(
            list(PricingRule.objects.filter(is_active=True))
        )
        if moved:
            for plan, new_price in moved:
                plan.price_usd = new_price
            Plan.objects.bulk_update(
                [plan for plan, _ in moved], ["price_usd"], batch_size=500
            )
            # bulk_update fires no post_save, so the cache signal never sees
            # these moves and the storefront would serve the old prices for the
            # whole TTL. on_commit, so the keys clear only once the new prices
            # are actually visible to the API.
            from config.cache import invalidate_catalogue

            transaction.on_commit(invalidate_catalogue)

    logger.info(
        "AI pricing applied by %s: %s (%s prices moved)",
        actor or "unknown",
        "; ".join(f"{p.scope}:{p.target}={p.markup_percent}%" for p in usable),
        len(moved),
    )
    return len(usable)


# --------------------------------------------------------------------------
# Written commentary over the margin report
# --------------------------------------------------------------------------


REPORT_SYSTEM = """You write a short margin commentary for QulaySIM's owner, in \
Uzbek. You are given figures that were already computed from the database — treat \
them as given and never recompute or adjust one.

Three or four sentences. Lead with the thing that costs money: which traffic \
sizes earn too little in absolute dollars, and which destinations sit far below \
the rest. Name specific figures from the data. If the numbers look healthy, say \
that plainly instead of manufacturing a concern.

No headings, no bullet lists, no greeting. Plain sentences."""


def explain(report: dict) -> str:
    """A few sentences of commentary over figures this module did not compute.

    Deliberately narrow. The report's numbers come from catalog/margin.py and are
    passed in already calculated; the model is asked to read them, not to produce
    them, so a wrong sentence here is a wrong sentence and never a wrong price.
    """
    client = _client()

    sizes = "\n".join(
        f"- {row.label}: {row.count} ta tarif, o'rtacha ustama {row.markup_percent}%, "
        f"donasiga foyda ${row.margin_each}, ozgina foydalilar {row.thin} ta"
        for row in report["sizes"]
    )
    worst = "\n".join(
        f"- {row.label}: {row.count} ta, ustama {row.markup_percent}%, "
        f"donasiga ${row.margin_each}"
        for row in report["destinations"][:12]
    )
    overall = report["overall"]
    sales = report["sales"]

    facts = (
        f"Jami {report['counted']} ta faol tarif hisobga olindi; "
        f"{report['no_cost']} tasida tannarx yo'q.\n"
        f"Umumiy ustama: {overall.markup_percent}%. "
        f"Jami tannarx ${overall.cost_total}, jami sotuv narxi ${overall.price_total}, "
        f"farqi ${overall.margin_total}.\n"
        f"${report['thin_margin_usd']} dan kam foyda keltiradigan tariflar: "
        f"{report['thin_total']} ta.\n\n"
        f"Trafik o'lchami bo'yicha:\n{sizes}\n\n"
        f"Eng past ustamali yo'nalishlar:\n{worst}\n\n"
        f"Haqiqiy sotuvlar: {sales['units']} dona, tushum ${sales['revenue']}, "
        f"tannarx ${sales['cost']}, foyda ${sales['margin']}."
    )

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            output_config={"effort": "low"},
            system=REPORT_SYSTEM,
            messages=[{"role": "user", "content": facts}],
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("AI report commentary failed")
        raise AiUnavailable(str(exc)) from exc

    if getattr(response, "stop_reason", None) == "refusal":
        raise AiUnavailable(_("The assistant declined to write the commentary."))
    return next((b.text for b in response.content if b.type == "text"), "").strip()
