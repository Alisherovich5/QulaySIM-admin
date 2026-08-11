"""Take the pages nobody opens out of the admin sidebar.

Twenty-nine pages had accumulated, and a sidebar that long costs something real:
the pages that matter are harder to find, and every extra row is a place to click
by mistake on a live database.

Only the *page* is removed. The model, the table and the data stay exactly as they
are, and every entry here comes back by deleting one line — so this is reversible
in a way that a migration would not be.

Each removal is justified below. Anything with a reason to exist stays, including
tables that are empty today but will fill: testimonials, complimentary grants and
supplier offers all have working features behind them.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: (app_label, model_name) → why it is not worth a page.
UNREGISTER = {
    # Permission groups. There is one administrator, so a group is a layer of
    # indirection over a permission set nobody has needed to create.
    ("auth", "Group"): "no groups in use — a single administrator",
    # Payme is not the payment provider (PAYMENT_PROVIDER=atmos) and its merchant
    # id is blank, so this table can only ever be empty. The model stays because
    # the integration code still imports it.
    ("orders", "PaymeTransaction"): "Payme is not the active provider",
    # Nothing writes this table. It is read programmatically by the supplier
    # comparison page, which does not need an admin screen of its own.
    ("orders", "SupplierPurchase"): "never written; read only by the suppliers page",
    # Every successful admin login and logout. Fifty-five rows of "signed in",
    # and no decision depends on any of them.
    ("axes", "AccessLog"): "login history nobody acts on",
    # The permanent record of lockouts. `AccessAttempt` is the one worth keeping —
    # it is what an operator reads when someone cannot get in, and what they clear
    # to unlock them. Two pages for the same question is one too many.
    ("axes", "AccessFailureLog"): "duplicate of AccessAttempt for the same question",
}


def install() -> None:
    """Unregister the pages above. Called once, after admin autodiscovery.

    A model that is already absent — because an app was removed or a library
    changed what it registers — is skipped rather than raising: an admin that
    refuses to load is a far worse outcome than a stale line in this dictionary.
    """
    from django.apps import apps
    from django.contrib import admin

    for (app_label, model_name), reason in UNREGISTER.items():
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            logger.debug("admin cleanup: %s.%s is not installed", app_label, model_name)
            continue
        try:
            admin.site.unregister(model)
            logger.debug("admin cleanup: hid %s.%s (%s)", app_label, model_name, reason)
        except admin.sites.NotRegistered:
            logger.debug("admin cleanup: %s.%s was not registered", app_label, model_name)
