"""Explicit local human-test receipt for a suspended bounded campaign."""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from foundry.campaign_runtime import CampaignEffectStore


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}\Z")
_ISSUE_ID = re.compile(r"[A-Z][A-Z0-9]{1,15}-[1-9][0-9]*\Z")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Enregistrer un gate humain exact de campagne Foundry",
    )
    parser.add_argument("campaign_id")
    parser.add_argument("issue_id")
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--valid-seconds", type=int, default=3_600)
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args(argv)
    if not 60 <= args.valid_seconds <= 86_400:
        raise SystemExit("--valid-seconds doit être compris entre 60 et 86400")
    if (_IDENTIFIER.fullmatch(args.campaign_id) is None
            or _ISSUE_ID.fullmatch(args.issue_id) is None
            or _IDENTIFIER.fullmatch(args.actor) is None):
        raise SystemExit("coordonnées de gate humain invalides")
    now_ms = int(time.time() * 1000)
    state = Path(args.state_dir).expanduser().resolve()
    campaign_root = state / "campaigns"
    candidate = (
        state / "campaigns" / args.campaign_id / "primitive-receipts.sqlite3"
    )
    if not candidate.is_file() or candidate.is_symlink():
        raise SystemExit("ledger de campagne Foundry absent")
    database = candidate.resolve()
    try:
        database.relative_to(campaign_root)
    except ValueError:
        raise SystemExit("chemin de ledger de campagne invalide") from None
    store = CampaignEffectStore(database)
    approval = store.approve_pending_human_gate(
        args.campaign_id, args.issue_id, args.attempt, actor=args.actor,
        expires_at=now_ms + args.valid_seconds * 1_000, now_ms=now_ms,
    )
    print(json.dumps({
        "contract": "foundry-campaign-human-gate.v1",
        "campaign_id": args.campaign_id,
        "issue_id": args.issue_id,
        "attempt": args.attempt,
        "approval_digest": approval,
        "expires_at": now_ms + args.valid_seconds * 1_000,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
