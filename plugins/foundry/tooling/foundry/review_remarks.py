"""Local receipt of the remarks of a validated review (PAT-141, PAT-ADR-0028).

The receipt is distinct from the acceptance proof: it is bound to the proof identifier, the
diff hash and the review generation of that proof, it is written by ``routing
record-review-remarks`` next to ``record-review-proof``, and the merge gate never reads it
(``issue merge`` and ``write.py`` do not import this module; ``tests/test_review_remarks.py``
checks it).  It lists the non-blocking remarks of one review round, each marked deferrable
or "fix before merge".  Append-only: a receipt is written once per proof; an identical replay
is accepted, a different one is refused.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from foundry import registry

SCHEMA_VERSION = 1
STORE_DIR = "review-remarks"
MAX_REMARKS = 1000
MAX_REMARK_TEXT = 2000
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class ReviewRemarksError(ValueError):
    """The remarks or their receipt are refused (closed failure)."""


def validate_remarks(remarks: object) -> list[dict]:
    """Closed shape: a bounded list of {text, fix_before_merge}; returns a normalized copy."""
    if not isinstance(remarks, list) or len(remarks) > MAX_REMARKS:
        raise ReviewRemarksError("remarques hors contrat")
    normalized = []
    for remark in remarks:
        if (not isinstance(remark, dict) or set(remark) != {"text", "fix_before_merge"}
                or not isinstance(remark["text"], str) or not remark["text"].strip()
                or len(remark["text"]) > MAX_REMARK_TEXT
                or type(remark["fix_before_merge"]) is not bool):
            raise ReviewRemarksError("remarque hors contrat")
        normalized.append({"text": remark["text"].strip(),
                           "fix_before_merge": remark["fix_before_merge"]})
    return normalized


class ReviewRemarksStore:
    """Private append-only receipts under the Foundry data directory, per repository."""

    def __init__(self, repository: str, state_dir: str | os.PathLike | None = None):
        if not isinstance(repository, str) or not repository.strip():
            raise ReviewRemarksError("namespace de dépôt requis")
        namespace = hashlib.sha256(repository.encode("utf-8")).hexdigest()
        base = Path(state_dir) if state_dir is not None else Path(registry.data_dir())
        self.directory = base / STORE_DIR / namespace

    def record(self, proof: dict, remarks: object, *, issue: str, diff_hash: str,
               base: str) -> dict:
        """Write the receipt of ``proof`` (already validated by the proof store)."""
        remarks = validate_remarks(remarks)
        if proof.get("quality") != "mergeable":
            raise ReviewRemarksError("reçu de remarques : seule une relecture validée en porte")
        coordinates = proof["coordinates"]
        if (proof["issue"]["id"] != issue or coordinates["diff_hash"] != diff_hash
                or coordinates["base"] != base):
            raise ReviewRemarksError("reçu de remarques : coordonnées divergentes de la preuve")
        receipt = {
            "schema_version": SCHEMA_VERSION, "proof_id": proof["proof_id"], "issue": issue,
            "diff_hash": diff_hash, "base": base,
            "generation": proof["review"]["generation"], "remarks": remarks,
        }
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = self.directory / proof["proof_id"]
        text = json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n"
        if target.exists():
            if target.read_text(encoding="utf-8") != text:
                raise ReviewRemarksError("reçu de remarques déjà présent et différent")
            return receipt
        descriptor, temporary = tempfile.mkstemp(prefix=".remarks.", dir=self.directory)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return receipt

    def find(self, issue: str, diff_hash: str) -> dict | None:
        """The receipt of the latest generation for this issue and diff hash, else ``None``."""
        found = None
        if not self.directory.is_dir():
            return None
        for path in sorted(self.directory.iterdir()):
            if not _HEX64.match(path.name):
                continue
            try:
                receipt = json.loads(path.read_text(encoding="utf-8"))
                remarks = validate_remarks(receipt["remarks"])
            except (OSError, ValueError, KeyError, TypeError):
                raise ReviewRemarksError("reçu de remarques illisible ; refus fermé") from None
            if (receipt.get("proof_id") != path.name or receipt.get("schema_version")
                    != SCHEMA_VERSION):
                raise ReviewRemarksError("reçu de remarques malformé ; refus fermé")
            if receipt["issue"] == issue and receipt["diff_hash"] == diff_hash and (
                    found is None or receipt["generation"] >= found["generation"]):
                found = {**receipt, "remarks": remarks}
        return found
