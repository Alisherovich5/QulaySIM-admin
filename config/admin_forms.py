"""Stop the browser drawing its password-manager panel over the login form.

The operator kept seeing a white rounded box sitting where the username input
should be — wider than the form, overlapping both edges, and not going away. It
is not the input and it is not a stylesheet: it is the browser's own autofill
suggestion panel, anchored to the username field and drawn on top of the page.
No CSS we ship can move it, because it is not part of the document.

What summons it is the field's own markup. Django's admin login sets
`autofocus` on the username and `autocomplete="username"` on it, which is an
explicit invitation for the browser to open its saved-credentials list the moment
the page loads. Removing both stops the invitation.

The cost is small and worth naming: the cursor no longer lands in the username
field by itself, and the browser will not offer to fill either field. On a panel
one person signs into, typing eight characters beats a panel you cannot dismiss.
"""

from __future__ import annotations

from django.contrib.admin.forms import AdminAuthenticationForm


class QuietAdminLoginForm(AdminAuthenticationForm):
    """The admin login, without anything that triggers browser autofill UI."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("username", "password"):
            widget = self.fields[name].widget
            # `off` alone is widely ignored by password managers; a token that
            # names no known field type is what actually stops the suggestion
            # panel from being offered.
            widget.attrs["autocomplete"] = "off"
            widget.attrs["data-1p-ignore"] = "true"
            widget.attrs["data-lpignore"] = "true"
            widget.attrs["data-form-type"] = "other"
            widget.attrs.pop("autofocus", None)
        # Django sets autofocus on the form's first field via the class
        # attribute too, not only the widget.
        self.fields["username"].widget.attrs.pop("autofocus", None)


def install() -> None:
    """Point the admin login at the quiet form.

    Called from config/urls.py, which Django imports once while resolving
    ROOT_URLCONF — late enough that `admin.site` exists, early enough that no
    request has been served.
    """
    from django.contrib import admin

    admin.site.login_form = QuietAdminLoginForm
