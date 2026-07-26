"""Model fields that encrypt their contents at rest."""

from __future__ import annotations

from django.db import models

from config.crypto import decrypt, encrypt


class EncryptedTextField(models.TextField):
    """Transparently encrypted TextField.

    The value is ciphertext in the database and plaintext everywhere in Python,
    so admin forms and templates need no special handling. It cannot be
    filtered or searched on — which is the point: the database never sees the
    plaintext.
    """

    def get_prep_value(self, value):
        return encrypt(super().get_prep_value(value))

    def from_db_value(self, value, expression, connection):
        return decrypt(value)

    def to_python(self, value):
        return decrypt(super().to_python(value))


class EncryptedCharField(models.CharField):
    """As above, for shorter values. Ciphertext is longer than plaintext, so
    the column is widened well past the declared max_length."""

    def db_type(self, connection):
        return "text"

    def get_prep_value(self, value):
        return encrypt(super().get_prep_value(value))

    def from_db_value(self, value, expression, connection):
        return decrypt(value)

    def to_python(self, value):
        return decrypt(super().to_python(value))
