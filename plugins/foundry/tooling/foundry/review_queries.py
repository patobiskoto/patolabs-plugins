"""Bounded local queries over regular blobs of an actively claimed review HEAD."""
from __future__ import annotations

import re
import subprocess

from foundry.routing import RoutingConfigError, claimed_review_diff

MAX_TEXT_CHARS = 512
MAX_BLOB_BYTES = 1_048_576
MAX_MATCHES = 1_000


def _git(root, *args):
    result = subprocess.run(
        ["git", "--no-replace-objects", *args], cwd=root, capture_output=True,
    )
    if result.returncode:
        raise RoutingConfigError("lecture des objets Git de review impossible.")
    return result.stdout


def query_review(expected_hash, repository, *, claim_id, root, base,
                 text=None, state_dir=None, consume):
    """Emit under the existing claim lock; never read working files or execute text."""
    if text is not None and (not isinstance(text, str) or not text
                            or len(text) > MAX_TEXT_CHARS or "\n" in text or "\r" in text
                            or "\x00" in text):
        raise RoutingConfigError("recherche de review : texte littéral non vide, une ligne, 512 caractères maximum.")

    def snapshot(_diff, head):
        if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head):
            raise RoutingConfigError("recherche de review : HEAD immuable du claim requis.")
        entries = _git(root, "ls-tree", "-r", "-z", "--full-tree", head).split(b"\0")
        paths, matches, skipped = [], [], []
        truncated = False
        for entry in filter(None, entries):
            metadata, path_bytes = entry.split(b"\t", 1)
            mode, kind, oid = metadata.split()
            if mode not in (b"100644", b"100755") or kind != b"blob":
                continue  # never follow a symlink or traverse a submodule
            path = path_bytes.decode("utf-8", errors="surrogateescape")
            paths.append(path)
            if text is None:
                continue
            object_id = oid.decode("ascii")
            if not re.fullmatch(r"[0-9a-f]{40}", object_id):
                raise RoutingConfigError("objet Git de review invalide.")
            if int(_git(root, "cat-file", "-s", object_id)) > MAX_BLOB_BYTES:
                skipped.append({"path": path, "reason": "oversize"})
                continue
            blob = _git(root, "cat-file", "blob", object_id)
            try:
                content = blob.decode("utf-8")
            except UnicodeDecodeError:
                skipped.append({"path": path, "reason": "non_utf8"})
                continue
            if "\x00" in content:
                skipped.append({"path": path, "reason": "binary"})
                continue
            for number, line in enumerate(content.splitlines(), 1):
                if text in line:
                    if len(matches) == MAX_MATCHES:
                        truncated = True
                    else:
                        matches.append({"path": path, "line": number, "text": line})
        return consume({"source": "claimed_head", "head": head,
                        "diff_hash": expected_hash, "paths": paths} if text is None else
                       {"source": "claimed_head", "head": head, "diff_hash": expected_hash,
                        "matches": matches, "skipped": skipped, "truncated": truncated})

    return claimed_review_diff(
        expected_hash, repository, claim_id=claim_id, root=root, base=base,
        state_dir=state_dir, consume_snapshot=snapshot,
    )
