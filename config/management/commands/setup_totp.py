"""Enrol an authenticator app for one staff account.

Run over SSH with a TTY so the secret is printed into the operator's own
terminal. It must not travel any other way: a QR code pasted into a chat, a
ticket or an e-mail is a second factor that a second party now holds, which is
the one property it exists to deny.

    ssh -t <host> 'cd ~/qulaysim && docker compose exec admin \
        python manage.py setup_totp <username>'

The TTY is not only about privacy: the command finishes by asking for a code
from the app and enrols nothing until it gets one. Run without a terminal it
can read, it prints the QR, fails to confirm, and leaves the account exactly as
it found it.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Enrol a TOTP authenticator for a staff account and print its QR code."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Delete existing devices first — for a lost or replaced phone.",
        )
        parser.add_argument(
            "--name", default="phone", help="Label for the device (default: phone)."
        )

    def handle(self, *args, **options):
        from django_otp.plugins.otp_totp.models import TOTPDevice

        User = get_user_model()
        try:
            user = User.objects.get(username=options["username"])
        except User.DoesNotExist as exc:
            raise CommandError(f"no user named {options['username']!r}") from exc
        if not user.is_staff:
            raise CommandError(f"{user.username} is not staff — nothing to protect")

        existing = TOTPDevice.objects.filter(user=user)
        if existing.exists() and not options["replace"]:
            self.stdout.write(
                self.style.WARNING(
                    f"{user.username} already has {existing.count()} device(s): "
                    + ", ".join(f"{d.name} ({'confirmed' if d.confirmed else 'pending'})"
                                for d in existing)
                )
            )
            self.stdout.write("Pass --replace to delete them and enrol a new one.")
            return
        if options["replace"]:
            deleted = existing.delete()[0]
            self.stdout.write(f"removed {deleted} existing device(s)")

        # confirmed=True: there is no second confirmation step in this flow, so a
        # pending device would lock the account out of its own second factor.
        # Created pending, and it stays pending until the operator has typed a
        # code this command itself accepted. That ordering is the whole point.
        #
        # The first version of this command created the device confirmed, on the
        # reasoning that a pending device would lock the account out of its own
        # second factor. It did the opposite: `has_device()` counts confirmed
        # devices, so the login form began demanding a code the instant the row
        # existed — before anyone had scanned the QR. The owner was locked out of
        # his own panel and 2FA was removed altogether.
        #
        # Confirming only after a working code has been shown makes that outcome
        # unreachable. Either the operator proves the app is set up and the
        # device goes live, or nothing was enrolled and the password still works.
        device = TOTPDevice.objects.create(user=user, name=options["name"], confirmed=False)
        uri = device.config_url

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Enrolling an authenticator for {user.username}"))
        self.stdout.write("")
        self.stdout.write("Scan this with Google Authenticator, Authy or 1Password:")
        self.stdout.write("")
        try:
            import qrcode

            qr = qrcode.QRCode(border=1)
            qr.add_data(uri)
            qr.print_ascii(out=self.stdout, invert=True)
        except ImportError:
            self.stdout.write(self.style.WARNING("  (qrcode not installed — type the key by hand)"))
        self.stdout.write("")
        self.stdout.write("If the camera will not read it, enter the key manually:")
        self.stdout.write(f"  {device.key}")
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                "This secret is shown once and is not recoverable. Nobody but the "
                "account holder should ever see it — do not forward it, screenshot "
                "it, or paste it anywhere. Losing it means running this again with "
                "--replace."
            )
        )
        self.stdout.write("")

        if not self._confirm(device):
            device.delete()
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    f"Nothing was enrolled. {user.username} still signs in with the "
                    "password alone — run this again when the app is ready."
                )
            )
            return

        device.confirmed = True
        device.save(update_fields=["confirmed"])
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Authenticator confirmed for {user.username}."))
        self.stdout.write("From now on: username, password, then the six digits from the app.")

    #: Enough tries to survive a mistyped digit or a clock a few seconds out,
    #: few enough that an unattended terminal is not a guessing machine.
    CONFIRM_ATTEMPTS = 3

    def _confirm(self, device) -> bool:
        """Ask for a code from the app and check it against the pending device.

        Returns False on a wrong code, an empty answer, Ctrl-C, or a terminal
        that cannot be read from at all — the caller deletes the device in every
        one of those cases. Refusing to enrol is always safe; enrolling
        something the operator cannot use is not.
        """
        for remaining in range(self.CONFIRM_ATTEMPTS, 0, -1):
            try:
                token = input("Enter the six digits from the app to confirm: ").strip()
            except (EOFError, KeyboardInterrupt):
                self.stdout.write("")
                return False
            if not token:
                return False
            if device.verify_token(token):
                return True
            if remaining > 1:
                self.stdout.write(
                    self.style.WARNING(f"  that code did not match — {remaining - 1} left")
                )
        return False
