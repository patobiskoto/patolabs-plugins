"""PAT-296: fail-closed gate of the live DevHub conformance suite.

The suite writes to the DevHub it targets and deletes nothing it creates, so it must
never reach a production instance.  This module decides, from an explicit
environment mapping only, whether the suite may run.  It never reads the user's
configuration, keychain, registry or ``config.env``: every coordinate comes from the
``DEVHUB_LIVE_*`` variables documented in ``docs/devhub-requirements.md``.

Outcomes
- not opted in (a variable is missing or the confirmation is not the exact phrase):
  :func:`read_environment` returns a skip reason, the suite is skipped;
- opted in but unsafe (production-looking coordinates, malformed URL, instance that holds
  projects the suite did not create): :class:`GateRefusal`, the suite errors out and
  nothing is written.

Limit of the instance marker: ``assert_disposable_instance`` only sees the projects the
credential can see.  On an instance with local accounts (observed: ``authorizeProject`` of
DevHub limits a service credential to the projects granted to it) it proves nothing: 0
visible projects does not mean an empty instance.  Use a disposable instance without local
accounts, and a credential created for it (``docs/devhub-requirements.md``).  This holds for
the 41 tests that use the ``suite`` fixture of the live suite.  DHN-42 is the exception: it
needs a target with local accounts, where the marker proves nothing, so that target must be
disposable by construction (an instance and a database made for this run), not by the check;
the confirmation phrase is its only other safeguard.
"""
from __future__ import annotations

import os
import secrets
import string
from dataclasses import dataclass, field
from typing import Mapping

from foundry.trackers import devhub

CONFIRM = "I_UNDERSTAND_DEVHUB_LIVE_SUITE_WRITES_TO_A_DISPOSABLE_INSTANCE"
KEY_PREFIX = "LV"

ENV_URL = "DEVHUB_LIVE_URL"
ENV_CONFIRM = "DEVHUB_LIVE_CONFIRM"
ENV_TOKEN = "DEVHUB_LIVE_TOKEN"
ENV_PROOF_SECRET = "DEVHUB_LIVE_PROOF_SECRET"
# Declaration (never detection) that the target runs with local accounts: only DHN-42 reads it.
ENV_LOCAL_ACCOUNTS = "DEVHUB_LIVE_LOCAL_ACCOUNTS"

# Names of the variables the everyday adapter reads (``DevHubTracker.__init__``).  The
# live suite compares against them, never the other way round, so that a production
# coordinate exported in the same shell is refused instead of reused.
_PRODUCTION_URL = "DEVHUB_URL"
_PRODUCTION_TOKEN = "DEVHUB_TRACKER_TOKEN"
_PRODUCTION_PROOF_SECRET = "DEVHUB_TRACKER_PROOF_SECRET"


class GateRefusal(RuntimeError):
    """The suite was opted in but the target cannot be shown to be disposable."""


@dataclass(frozen=True)
class LiveConfig:
    url: str
    token: str = field(repr=False)
    proof_secret: str = field(repr=False)


def read_environment(environ: Mapping[str, str] | None = None):
    """Return ``(config, None)`` to run, ``(None, reason)`` to skip; raise to refuse."""
    env = os.environ if environ is None else environ
    url = env.get(ENV_URL, "")
    if not url:
        return None, f"{ENV_URL} not set: live DevHub conformance suite not requested"
    if env.get(ENV_CONFIRM) != CONFIRM:
        return None, (
            f"{ENV_CONFIRM} is missing or is not the exact confirmation phrase of "
            "docs/devhub-requirements.md"
        )
    missing = [name for name in (ENV_TOKEN, ENV_PROOF_SECRET) if not env.get(name)]
    if missing:
        return None, f"{', '.join(missing)} not set"

    try:
        devhub.validate_base_url(url.rstrip("/"))
    except ValueError:
        raise GateRefusal(f"{ENV_URL} is not a valid DevHub origin") from None
    token, secret = env[ENV_TOKEN], env[ENV_PROOF_SECRET]
    if len(token) < 24 or len(secret) < 32:
        raise GateRefusal(f"{ENV_TOKEN}/{ENV_PROOF_SECRET} are too short for DevHubTracker")

    production_url = env.get(_PRODUCTION_URL)
    if production_url:
        try:
            same = devhub._origin(url.rstrip("/")) == devhub._origin(production_url.rstrip("/"))
        except ValueError:
            same = True  # an unreadable production coordinate cannot be told apart: refuse
        if same:
            raise GateRefusal(f"{ENV_URL} designates the origin of {_PRODUCTION_URL}")
    if token == env.get(_PRODUCTION_TOKEN) or secret == env.get(_PRODUCTION_PROOF_SECRET):
        raise GateRefusal(
            f"{ENV_TOKEN}/{ENV_PROOF_SECRET} reuse a production credential; "
            "use credentials dedicated to the disposable instance"
        )
    return LiveConfig(url=url.rstrip("/"), token=token, proof_secret=secret), None


def local_accounts_declared(environ: Mapping[str, str] | None = None) -> bool:
    """True only for the exact value ``1``: the suite cannot read the instance mode."""
    env = os.environ if environ is None else environ
    return env.get(ENV_LOCAL_ACCOUNTS) == "1"


def build_tracker(config: LiveConfig) -> devhub.DevHubTracker:
    """Explicit coordinates only: ``DevHubTracker`` then reads no configuration."""
    return devhub.DevHubTracker(
        url=config.url, token=config.token, proof_secret=config.proof_secret,
    )


def assert_disposable_instance(tracker) -> None:
    """Instance marker: every project visible to the token carries the suite prefix.

    A populated production instance holds projects whose key does not start with
    :data:`KEY_PREFIX`; an unreadable catalogue cannot establish the marker.  Both
    refuse.  Only what the token can see is checked: the credentials must be dedicated
    to the disposable instance (see ``docs/devhub-requirements.md``).  With a credential
    that only sees the projects granted to it (local accounts), this check proves nothing.
    """
    try:
        catalog = tracker.list_project_catalog()
    except Exception as error:  # fail closed on any read problem, never print details
        raise GateRefusal(
            f"cannot read the project catalogue to establish the instance marker "
            f"({type(error).__name__})"
        ) from None
    foreign = sorted(
        item["key"] for item in catalog if not item["key"].startswith(KEY_PREFIX)
    )
    if foreign:
        raise GateRefusal(
            f"the instance holds {len(foreign)} project(s) whose key does not start with "
            f"{KEY_PREFIX!r}; it is not a disposable instance of this suite"
        )


def new_project_key() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return KEY_PREFIX + "".join(secrets.choice(alphabet) for _ in range(8))
