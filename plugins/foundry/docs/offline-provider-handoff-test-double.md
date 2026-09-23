# Offline provider handoff test double

`tests/offline_provider_handoff.py` is a reusable, standard-library-only test
double for a crash-safe provider handoff. It keeps provider-side idempotency and
capacity in one durable JSON file and local intents and receipts in a different
durable JSON file. Tests supply both paths under pytest's `tmp_path`; the double
does not read or mutate a real workspace, tracker, provider, credential, or
campaign.

The stable effect identity binds issue ID, operation ID, diff SHA-256, acceptance
criteria SHA-256, generation, and the fixture authority ID. A first effect
revalidates the offline capacity immediately before persistence. Missing,
expired, consumed, or context-drifted capacity fails closed. A durable local
intent does not extend capacity lifetime.

The two explicit crash points are `before_effect` and
`after_effect_before_receipt`. After the first crash, a retry still needs live
capacity because no provider effect exists. After the second crash, an exact
retry may read the provider's durable idempotency result and reconstruct one
local receipt even if the capacity has since expired; it never performs a
second effect. Any drift in issue, operation, diff, acceptance criteria,
generation, fixture authority, or capability identity is rejected.

Run the focused recipe from the repository root:

```sh
pytest -q plugins/foundry/tests/test_offline_provider_handoff.py
ruff check --config plugins/foundry/ruff.toml \
  plugins/foundry/tests/offline_provider_handoff.py \
  plugins/foundry/tests/test_offline_provider_handoff.py
```

## Limits

The capability issuer writes synthetic data marked `authoritative: false`. It
does not authenticate a provider, grant migration or campaign authority, or
model a real Linear/YouTrack response. Receipts contain no human verdict and
cannot satisfy a human gate. This recipe proves only the offline double's
freshness, exact identity, crash separation, and idempotent reconstruction. It
does not attest a Linear migration and does not validate the six acceptance
criteria of PAT-21 or replace PAT-21's final independent review.
