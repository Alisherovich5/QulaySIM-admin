"""Forms for the supplier price import page."""

from __future__ import annotations

from django import forms

from catalog.models import Country, SupplierOffer

MAX_UPLOAD_BYTES = 8 * 1024 * 1024


class SupplierPriceUploadForm(forms.Form):
    provider = forms.ChoiceField(
        choices=[(c, l) for c, l in SupplierOffer.Provider.choices if c != "mock"],
        label="Wholesaler",
        help_text="Which supplier's price list this file is.",
    )
    csv_file = forms.FileField(
        label="Price list (CSV)",
        help_text=(
            "Needs the columns package_code, location, data_gb, days, cost_usd. "
            "The export from the supplier's portal already has them."
        ),
    )
    dry_run = forms.BooleanField(
        required=False,
        initial=True,
        label="Preview only — do not write anything",
        help_text=(
            "Leave this on the first time. Applying moves supplier costs, which "
            "moves retail prices through the pricing rules."
        ),
    )
    only_countries = forms.ModelMultipleChoiceField(
        queryset=Country.objects.order_by("name"),
        required=False,
        label="Limit to these destinations",
        help_text=(
            "Leave empty to apply the whole file. Choosing destinations is how you "
            "set one up without repricing everything else."
        ),
    )

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
