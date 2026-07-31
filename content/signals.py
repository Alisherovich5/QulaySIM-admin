"""Clear the landing-page cache whenever CMS content changes.

Previously each receiver was a lambda registered in a loop. Django holds signal
receivers with a *weak* reference by default, and a lambda created in a loop has
no other reference — so once the garbage collector ran, the receivers silently
disappeared and CMS edits stopped clearing the cache. Nothing failed; the site
just kept serving the old content until the key expired, which reads as "my edit
did not save".

A module-level function plus weak=False is the fix: the function is referenced by
the module, and the flag says so explicitly rather than relying on that.
"""

from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from config.cache import invalidate_content
from content.models import FAQ, Benefit, Device, PromoBanner, Testimonial
from orders.models import PromoCode

# PromoCode is here even though it lives in another app: the landing payload
# reads the advertised discount from it, so changing 10% to a fixed $20 has to
# clear this cache too — otherwise the bar keeps advertising the old figure
# while checkout applies the new one.
_CACHED_MODELS = (Benefit, Testimonial, Device, FAQ, PromoBanner, PromoCode)


def _clear_content_cache(sender, **kwargs) -> None:
    del sender, kwargs
    invalidate_content()


for _model in _CACHED_MODELS:
    receiver(post_save, sender=_model, weak=False)(_clear_content_cache)
    receiver(post_delete, sender=_model, weak=False)(_clear_content_cache)
