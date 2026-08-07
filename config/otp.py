"""Second factor for the admin, and the reason it matters more than the URL.

A secret admin path is worth having — it stops the scanners that probe /admin/
and /wp-admin/ all day, which is real noise reduction. But it is obscurity, not
a lock: the path travels in browser history, in a Referer header, on the screen
of anyone standing behind you, and in the memory of every person who has ever
been shown it. Once it is known, the whole defence is one password.

So the password stops being sufficient. With a TOTP device enrolled, a stolen or
guessed password gets an attacker to a form asking for a six-digit code that
changes every thirty seconds and lives only on the phone in your pocket.

Two things guard the door, deliberately overlapping:

  * the login form asks for the code in the same POST as the password, so a
    correct password alone never establishes a session;
  * a middleware refuses every admin page to a session that is authenticated but
    not verified. Belt and braces: the form is the door, the middleware is the
    check that nobody climbed through a window — a session created before 2FA
    existed, or by any future code path that logs a user in without the form.

Staff with no device enrolled are let through, and the admin says so loudly.
Locking out the only superuser the moment this deploys would be a self-inflicted
outage; a visible warning that survives until someone enrols is the safer trade.
Set `OTP_REQUIRED=True` once everyone has a device, and the gap closes.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import admin
from django.contrib.admin.forms import AdminAuthenticationForm
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _


def has_device(user) -> bool:
    """Does this user have a confirmed TOTP device?"""
    from django_otp import devices_for_user

    return any(devices_for_user(user, confirmed=True))


def verify_token(user, token: str) -> bool:
    """True when `token` is valid for one of the user's confirmed devices.

    django-otp's own throttling lives on the device, so a wrong code costs the
    attacker an increasing delay. Every device is tried because a person may
    carry two (a phone and a backup).
    """
    from django_otp import devices_for_user

    for device in devices_for_user(user, confirmed=True):
        if device.verify_token(token):
            return True
    return False


class OtpAdminAuthenticationForm(AdminAuthenticationForm):
    """The admin login, with the code asked for alongside the password.

    Checked *after* the password, on purpose: a wrong password and a wrong code
    have to be indistinguishable from the outside, or the form becomes an oracle
    telling an attacker which half they got right.
    """

    from django import forms as _forms

    otp_token = _forms.CharField(
        label=_("Authenticator code"),
        required=False,
        strip=True,
        widget=_forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "pattern": "[0-9]*",
                "placeholder": "123456",
            }
        ),
        help_text=_("The six digits from your authenticator app."),
    )

    def clean(self):
        cleaned = super().clean()
        user = self.get_user()
        if user is None:
            return cleaned

        if not has_device(user):
            # No device enrolled. Allowed unless the deployment has declared
            # that everyone must have one.
            if getattr(settings, "OTP_REQUIRED", False):
                raise ValidationError(
                    _(
                        "Two-factor authentication is required and this account has "
                        "no authenticator enrolled. Ask an administrator to run "
                        "`manage.py setup_totp <username>`."
                    ),
                    code="otp_required",
                )
            return cleaned

        token = (cleaned.get("otp_token") or "").replace(" ", "")
        if not token or not verify_token(user, token):
            # Deliberately the same wording the password error uses, so the form
            # never reveals which factor failed.
            raise ValidationError(
                _("Please enter the correct username, password and authenticator code."),
                code="otp_invalid",
            )
        # Mark the session so the middleware below can tell a verified login
        # from a merely authenticated one.
        self.request.session["otp_verified_user"] = user.pk
        return cleaned


class RequireOtpMiddleware:
    """Refuse admin pages to a session that never passed the second factor.

    The login form is the door; this is the check that nobody came through a
    window. It matters because a session established by any other means — code
    that predates this file, a shell-created session, a future feature that logs
    someone in directly — would otherwise carry full admin rights.

    Only requests under the admin prefix are gated, and the login and logout
    pages are exempt or there would be no way back in.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        prefix = getattr(settings, "ADMIN_URL_PATH", "admin").strip("/")
        self.prefix = f"/{prefix}/" if prefix else "/"

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            user is not None
            and user.is_authenticated
            and request.path.startswith(self.prefix)
            and not self._exempt(request.path)
            and has_device(user)
            and request.session.get("otp_verified_user") != user.pk
        ):
            from django.contrib.auth import logout

            # Logged out rather than shown a second form: the session was
            # created without the factor this account requires, and the honest
            # remedy is to make them log in properly.
            logout(request)
            return redirect(f"{reverse('admin:login')}?next={request.path}")
        return self.get_response(request)

    def _exempt(self, path: str) -> bool:
        tail = path[len(self.prefix) :] if path.startswith(self.prefix) else path
        return tail.startswith(("login", "logout", "jsi18n", "i18n/"))


def install() -> None:
    """Point the admin login at the two-factor form.

    Called from AppConfig.ready so it runs after the admin site exists and
    before the first request, and so nothing has to remember to import it.
    """
    admin.site.login_form = OtpAdminAuthenticationForm
    admin.site.login_template = "admin/otp_login.html"
