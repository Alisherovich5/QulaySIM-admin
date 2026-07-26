"""Clear the landing-page cache whenever CMS content changes."""

from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from config.cache import invalidate_content
from content.models import FAQ, Benefit, Device, PromoBanner, Testimonial

for model in (Benefit, Testimonial, Device, FAQ, PromoBanner):
    receiver(post_save, sender=model)(lambda sender, **kw: invalidate_content())
    receiver(post_delete, sender=model)(lambda sender, **kw: invalidate_content())
