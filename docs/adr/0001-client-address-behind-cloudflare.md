# ADR-0001 — Which address is the visitor, behind Cloudflare

The decision is recorded once, in the API repo, because it binds both services
and one copy cannot drift from the other:

**`QulaySIM-backend/docs/adr/0001-client-address-behind-cloudflare.md`**

What it means here, in short: django-axes must judge a failed login by the
person's address, not by the Cloudflare edge that delivered the request. That is
what `config/client_address.py` and its middleware do, driven by the same
`TRUST_CLOUDFLARE_CLIENT_IP` switch the API reads. Tests:
`config/tests_client_address.py`.

Change one service's copy of this rule and you must change the other's.
