"""Exact human authorization for one first blocked campaign review."""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from foundry.campaign_coordinator import CampaignStore
from foundry.campaign_runtime import CampaignEffectStore, FoundryPrimitiveRunner


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}\Z")
_ISSUE_ID = re.compile(r"[A-Z][A-Z0-9]{1,15}-[1-9][0-9]*\Z")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Autoriser la correction d'une première review bloquée",
    )
    parser.add_argument("campaign_id")
    parser.add_argument("issue_id")
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--valid-seconds", type=int, default=3_600)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument(
        "--root", help="racine du depot; utilise le repertoire courant par defaut",
    )
    args = parser.parse_args(argv)
    if not 60 <= args.valid_seconds <= 86_400:
        raise SystemExit("--valid-seconds doit être compris entre 60 et 86400")
    if (_IDENTIFIER.fullmatch(args.campaign_id) is None
            or _ISSUE_ID.fullmatch(args.issue_id) is None
            or _IDENTIFIER.fullmatch(args.actor) is None
            or args.attempt < 1):
        raise SystemExit("coordonnées de remédiation review invalides")

    state = Path(args.state_dir).expanduser().resolve()
    root = Path(args.root or ".").expanduser().resolve()
    if not root.is_dir():
        raise SystemExit("racine de depot Foundry absente")
    campaign_root = state / "campaigns"
    campaign_directory = campaign_root / args.campaign_id
    candidate = campaign_directory / "campaign.sqlite3"
    if not candidate.is_file() or candidate.is_symlink():
        raise SystemExit("ledger de campagne Foundry absent")
    database = candidate.resolve()
    try:
        database.relative_to(campaign_root)
    except ValueError:
        raise SystemExit("chemin de ledger de campagne invalide") from None

    primitive_candidate = campaign_directory / "primitive-receipts.sqlite3"
    if not primitive_candidate.is_file() or primitive_candidate.is_symlink():
        raise SystemExit("ledger de preuves primitives Foundry absent")
    primitive_database = primitive_candidate.resolve()
    try:
        primitive_database.relative_to(campaign_root)
    except ValueError:
        raise SystemExit("chemin de preuves primitives invalide") from None

    primitive_store = CampaignEffectStore(primitive_database)
    primitives = FoundryPrimitiveRunner(
        root, worktree_directory=state / "worktrees", receipts=primitive_store,
    )

    authorization = CampaignStore(database).authorize_review_remediation(
        args.campaign_id, args.issue_id, args.attempt, actor=args.actor,
        valid_seconds=args.valid_seconds, now_ms=int(time.time() * 1_000),
        revalidate=primitives.revalidate_review_remediation,
    )
    print(json.dumps({
        "contract": "foundry-campaign-first-review-remediation.v1",
        "campaign_id": authorization.campaign_id,
        "issue_id": authorization.issue_id,
        "from_attempt": authorization.attempt,
        "effect_id": authorization.effect_id,
        "proof_digest": authorization.proof_digest,
        "role": authorization.role,
        "current_tier": authorization.current_tier,
        "actor": authorization.actor,
        "expires_at": authorization.expires_at,
        "authorization_digest": authorization.authorization_digest,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
