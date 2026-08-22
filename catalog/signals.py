"""Clear the storefront cache whenever catalogue data changes."""

from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from catalog.models import Country, Plan, PricingRule, Region, SellableShape, SupplierOffer
from config.cache import invalidate_catalogue


@receiver(post_save, sender=Plan)
@receiver(post_save, sender=Country)
@receiver(post_save, sender=Region)
@receiver(post_save, sender=PricingRule)
# A supplier price change usually reaches the cache through the plan it
# reprices, but only when the winner actually moved. Listening directly means
# the guarantee does not depend on that.
@receiver(post_save, sender=SupplierOffer)
@receiver(post_delete, sender=SupplierOffer)
@receiver(post_delete, sender=Plan)
@receiver(post_delete, sender=Country)
@receiver(post_delete, sender=Region)
# Deleting a rule now reprices the plans it governed (PricingRule.delete), so
# the delete has to clear the cache exactly like a save does.
@receiver(post_delete, sender=PricingRule)
def _clear_catalogue_cache(sender, **kwargs):
    # on_commit, not inline: the admin wraps every save in a transaction, and
    # clearing Redis *before* COMMIT invites the API to re-cache the old rows
    # during the gap — after which nothing clears them until the TTL runs out.
    # Outside a transaction the callback runs immediately, so nothing changes
    # for plain saves.
    transaction.on_commit(invalidate_catalogue)


@receiver(post_save, sender=SellableShape)
@receiver(post_delete, sender=SellableShape)
def _forget_the_ladder(sender, **kwargs):
    """A rung edited in the admin has to reach the next import, not the next
    restart. The importer caches the table once per country per run — see
    supplier_import.rungs — so the cache is dropped here."""
    from catalog.supplier_import import reset_rungs_cache

    reset_rungs_cache()
