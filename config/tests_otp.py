"""Two-factor admin login.

The properties worth testing are the ones that would silently not hold:

  * a correct password with no code, or a wrong code, must not establish a
    session — otherwise the second factor is decoration;
  * the failure message must not say *which* factor failed, or the form becomes
    an oracle that tells an attacker their password is right;
  * a session that never passed the factor must not reach an admin page, however
    it was created;
  * and while OTP_REQUIRED is off, an account with no device must still be able
    to log in — because the alternative is this change locking the only
    superuser out of the panel on deploy.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

PASSWORD = "Pw-1234-abcd-efgh"


def current_token(device: TOTPDevice) -> str:
    """The code the user's phone would be showing right now."""
    return f"{totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits):0{device.digits}d}"


@override_settings(SECURE_SSL_REDIRECT=False, AXES_ENABLED=False)
class LoginTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "otpadmin", "o@x.uz", PASSWORD
        )
        self.url = reverse("admin:login")
        self.index = reverse("admin:index")

    def _post(self, **extra):
        data = {"username": "otpadmin", "password": PASSWORD}
        data.update(extra)
        return self.client.post(self.url, data, follow=False)

    def _enrol(self):
        return TOTPDevice.objects.create(user=self.user, name="phone", confirmed=True)

    # --- with a device enrolled ------------------------------------------

    def test_the_right_password_without_a_code_is_refused(self):
        self._enrol()
        response = self._post()
        self.assertEqual(response.status_code, 200)  # re-rendered form, not a redirect
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_a_wrong_code_is_refused(self):
        self._enrol()
        response = self._post(otp_token="000000")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_the_right_code_logs_in(self):
        device = self._enrol()
        response = self._post(otp_token=current_token(device))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(self.index).status_code, 200)

    def test_spaces_in_the_code_are_tolerated(self):
        # Some apps display "123 456"; a person copying that should not be locked
        # out over the space.
        device = self._enrol()
        token = current_token(device)
        response = self._post(otp_token=f"{token[:3]} {token[3:]}")
        self.assertEqual(response.status_code, 302)

    def test_the_error_does_not_reveal_which_factor_failed(self):
        """A wrong code and a wrong password must be indistinguishable.

        Asserted on the form's errors rather than on the rendered HTML: the page
        is translated, so a text assertion passes or fails on whether a msgid has
        a translation yet — which has nothing to do with the property under test.
        """
        self._enrol()
        wrong_code = self._post(otp_token="000000")
        wrong_password = self.client.post(
            self.url, {"username": "otpadmin", "password": "nope", "otp_token": "000000"}
        )

        code_errors = list(wrong_code.context["form"].non_field_errors())
        password_errors = list(wrong_password.context["form"].non_field_errors())

        # Both must complain, and neither may say which half was wrong.
        self.assertTrue(code_errors)
        self.assertTrue(password_errors)
        for errors in (code_errors, password_errors):
            joined = " ".join(errors).lower()
            self.assertNotIn("authenticator code is", joined)
            self.assertNotIn("invalid code", joined)
            self.assertNotIn("wrong code", joined)
        # Neither session was established.
        self.assertFalse(wrong_code.wsgi_request.user.is_authenticated)
        self.assertFalse(wrong_password.wsgi_request.user.is_authenticated)

    # --- with no device enrolled -----------------------------------------

    def test_without_a_device_a_password_still_works_by_default(self):
        # Deliberate: deploying 2FA must not lock out the only superuser before
        # anyone has had a chance to enrol.
        response = self._post()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(self.index).status_code, 200)

    @override_settings(OTP_REQUIRED=True)
    def test_with_otp_required_an_unenrolled_account_is_refused(self):
        response = self._post()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)
        # The error code, not its rendered text: the message is translated, so a
        # text assertion would break the moment a translation lands — which is
        # unrelated to whether the account was refused.
        codes = [
            e.code
            for e in response.context["form"].errors.as_data().get("__all__", [])
        ]
        self.assertIn("otp_required", codes)


@override_settings(SECURE_SSL_REDIRECT=False, AXES_ENABLED=False)
class MiddlewareTests(TestCase):
    """The window-check behind the login form's door."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("mw", "m@x.uz", PASSWORD)
        self.index = reverse("admin:index")

    def test_a_session_that_skipped_the_factor_gets_no_admin_page(self):
        # force_login bypasses the form entirely — exactly the shape of session
        # the middleware exists to catch.
        TOTPDevice.objects.create(user=self.user, name="phone", confirmed=True)
        self.client.force_login(self.user)

        response = self.client.get(self.index)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response["Location"])

    def test_a_user_with_no_device_is_not_gated(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.index).status_code, 200)

    def test_a_gated_session_can_still_reach_the_login_form(self):
        """Being gated must not mean there is no way back in.

        Django redirects an already-authenticated staff user away from the login
        page, and the middleware bounces them off the index — so the two could in
        principle ping-pong. They do not, because the middleware logs the session
        out before redirecting: the second hop arrives unauthenticated and gets
        the form. This test walks the redirects to prove the loop terminates.
        """
        TOTPDevice.objects.create(user=self.user, name="phone", confirmed=True)
        self.client.force_login(self.user)

        response = self.client.get(reverse("admin:login"), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(response.redirect_chain), 4, response.redirect_chain)
        # Landed on a login form, logged out.
        self.assertFalse(response.wsgi_request.user.is_authenticated)
        self.assertIn("form", response.context)

    def test_the_gate_does_not_loop_when_entering_at_the_index(self):
        TOTPDevice.objects.create(user=self.user, name="phone", confirmed=True)
        self.client.force_login(self.user)

        response = self.client.get(self.index, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(response.redirect_chain), 4, response.redirect_chain)
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_a_verified_login_reaches_the_admin(self):
        device = TOTPDevice.objects.create(user=self.user, name="phone", confirmed=True)
        self.client.post(
            reverse("admin:login"),
            {"username": "mw", "password": PASSWORD, "otp_token": current_token(device)},
        )
        self.assertEqual(self.client.get(self.index).status_code, 200)

    def test_the_storefront_side_is_untouched(self):
        # The gate is scoped to the admin prefix; a non-admin path must not be
        # swept up by it.
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)


class EnrolmentCommandTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("cmd", "c@x.uz", PASSWORD)

    def _run(self, *args):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("setup_totp", *args, stdout=out)
        return out.getvalue()

    def test_it_enrols_a_confirmed_device_and_prints_a_key(self):
        output = self._run("cmd")
        device = TOTPDevice.objects.get(user=self.user)
        self.assertTrue(device.confirmed)
        self.assertIn(device.key, output)

    def test_it_refuses_to_silently_replace_an_existing_device(self):
        self._run("cmd")
        first = TOTPDevice.objects.get(user=self.user).key
        output = self._run("cmd")
        self.assertIn("already has", output)
        self.assertEqual(TOTPDevice.objects.get(user=self.user).key, first)

    def test_replace_swaps_the_device_for_a_new_secret(self):
        self._run("cmd")
        first = TOTPDevice.objects.get(user=self.user).key
        self._run("cmd", "--replace")
        self.assertEqual(TOTPDevice.objects.filter(user=self.user).count(), 1)
        self.assertNotEqual(TOTPDevice.objects.get(user=self.user).key, first)

    def test_an_unknown_user_is_an_error_not_a_silent_no_op(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            self._run("nobody")

    def test_a_non_staff_account_is_refused(self):
        get_user_model().objects.create_user("shopper", "s@x.uz", PASSWORD)
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            self._run("shopper")
