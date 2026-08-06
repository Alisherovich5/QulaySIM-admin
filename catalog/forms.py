"""Forms for the supplier price import page."""

from __future__ import annotations

from django import forms

from catalog.models import Country, SupplierOffer
from django.utils.translation import gettext_lazy as _

MAX_UPLOAD_BYTES = 8 * 1024 * 1024


class SupplierPriceUploadForm(forms.Form):
    provider = forms.ChoiceField(
        choices=[(c, l) for c, l in SupplierOffer.Provider.choices if c != "mock"],
        label=_("Wholesaler"),
        help_text=_("Which supplier's price list this file is."),
    )
    csv_file = forms.FileField(
        label=_("Price list (CSV)"),
        help_text=_(
            "Needs the columns package_code, location, data_gb, days, cost_usd. "
            "The export from the supplier's portal already has them."
        ),
    )
    dry_run = forms.BooleanField(
        required=False,
        initial=True,
        label=_("Preview only — do not write anything"),
        help_text=_(
            "Leave this on the first time. Applying moves supplier costs, which "
            "moves retail prices through the pricing rules."
        ),
    )
    # Typed codes, not a picker.
    #
    # This was a multi-select over every destination. At 25 countries that was a
    # list; at 208 the browser renders an unreadable wall that fills the screen
    # and buries the rest of the form. Nobody scrolls 208 options to find Turkey —
    # they know it is TR.
    only_countries = forms.CharField(
        required=False,
        label=_("Limit to these destinations"),
        widget=forms.TextInput(attrs={"placeholder": "TR, AE, UZ"}),
        help_text=_(
            "ISO codes, comma-separated. Leave empty to apply the whole file — "
            "naming destinations is how you set one up without repricing the rest."
        ),
    )

    def clean_only_countries(self):
        """Codes to Country rows, naming anything that does not exist.

        Silently dropping an unknown code would mean an operator asks for
        "TR, AA" and gets a preview covering only Turkey with no hint that half
        their request was ignored.
        """
        raw = (self.cleaned_data.get("only_countries") or "").strip()
        if not raw:
            return Country.objects.none()
        codes = {
            part.strip().upper()
            for part in raw.replace(";", ",").replace(" ", ",").split(",")
            if part.strip()
        }
        found = Country.objects.filter(iso2__in=codes)
        missing = codes - {c.iso2.upper() for c in found}
        if missing:
            raise forms.ValidationError(
                _("Not a destination we have: %(codes)s")
                % {"codes": ", ".join(sorted(missing))}
            )
        return found

    def clean_csv_file(self):
        upload = self.cleaned_data["csv_file"]
        if upload.size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError(
                f"That file is {upload.size // 1024 // 1024} MB; the limit is "
                f"{MAX_UPLOAD_BYTES // 1024 // 1024} MB."
            )
        raw = upload.read()
        try:
            # Supplier exports are sometimes UTF-8 with a BOM.
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise forms.ValidationError(
                "Could not read that file as text. Export it as CSV, not XLSX."
            ) from None
