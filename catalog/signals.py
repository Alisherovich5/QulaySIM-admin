"""Clear the storefront cache whenever catalogue data changes."""

from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from catalog.models import Country, Plan, PricingRule, Region, SupplierOffer
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
def _clear_catalogue_cache(sender, **kwargs):
    invalidate_catalogue()
