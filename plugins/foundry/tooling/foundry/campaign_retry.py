"""Explicit effect-bound authorization for one blocked campaign implementation."""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from foundry.campaign_coordinator import CampaignStore


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}\Z")
_ISSUE_ID = re.compile(r"[A-Z][A-Z0-9]{1,15}-[1-9][0-9]*\Z")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Autoriser une reprise manuelle exacte de campagne Foundry",
    )
    parser.add_argument("campaign_id")
    parser.add_argument("issue_id")
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args(argv)
    if (_IDENTIFIER.fullmatch(args.campaign_id) is None
            or _ISSUE_ID.fullmatch(args.issue_id) is None
            or _IDENTIFIER.fullmatch(args.actor) is None
            or args.attempt < 1):
        raise SystemExit("coordonnées de reprise manuelle invalides")
    state = Path(args.state_dir).expanduser().resolve()
    campaign_root = state / "campaigns"
    candidate = campaign_root / args.campaign_id / "campaign.sqlite3"
    if not candidate.is_file() or candidate.is_symlink():
        raise SystemExit("ledger de campagne Foundry absent")
    database = candidate.resolve()
    try:
        database.relative_to(campaign_root)
    except ValueError:
        raise SystemExit("chemin de ledger de campagne invalide") from None
    now_ms = int(time.time() * 1000)
    authorization = CampaignStore(database).authorize_manual_retry(
        args.campaign_id, args.issue_id, args.attempt,
        actor=args.actor, now_ms=now_ms,
    )
    print(json.dumps({
        "contract": "foundry-campaign-manual-retry.v1",
        "campaign_id": args.campaign_id,
        "issue_id": args.issue_id,
        "from_attempt": args.attempt,
        "reason": "manual_retry_approved",
        "authorization_digest": authorization,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
