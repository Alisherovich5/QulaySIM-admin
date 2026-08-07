"""Enrol an authenticator app for one staff account.

Run over SSH with a TTY so the secret is printed into the operator's own
terminal. It must not travel any other way: a QR code pasted into a chat, a
ticket or an e-mail is a second factor that a second party now holds, which is
the one property it exists to deny.

    ssh -t <host> 'cd ~/qulaysim && docker compose exec admin \
        python manage.py setup_totp <username>'
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
        # The operator confirms by successfully logging in.
        device = TOTPDevice.objects.create(user=user, name=options["name"], confirmed=True)
        uri = device.config_url

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Authenticator enrolled for {user.username}"))
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
        self.stdout.write("Now log in: username, password, then the six digits from the app.")
