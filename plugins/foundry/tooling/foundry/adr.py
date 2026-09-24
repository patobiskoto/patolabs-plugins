"""Create / advance an ADR in the tracker's knowledge base.

Ticker comes from the resolved project (fixes the old bug where it was hardcoded
ORFEO-ADR and scanned articles across all projects). Title on argv, body on stdin.

CLI:
  echo "<body>" | python3 -m foundry.adr create "<title>" [status]
  python3 -m foundry.adr accept <ADR-ID>
  python3 -m foundry.adr edit <ADR-ID> <expected-body.md> <updated-body.md>
"""

from __future__ import annotations

import sys
from pathlib import Path

import foundry
from foundry import registry, write

_SKELETON = """## Contexte
<Pourquoi cette décision se pose.>

## Décision
<Ce qu'on choisit.>

## Conséquences
<Ce qui devient possible / interdit / plus coûteux.>

## Alternatives écartées
- **<Alt>** — <pourquoi rejetée>
"""


def _project(tr):
    binding = write.mutation_project(tr)
    return (
        binding if binding is not None else tr.resolve_project(registry.repo_basename())
    )


def create(title, status="proposed"):
    tr = foundry.tracker()
    body = sys.stdin.read().strip() or _SKELETON
    adr = tr.create_adr(_project(tr), title, body, status=status)
    print(f"📐 {adr.id} créé ({status}) — article {adr.ref}")


def accept(adr_id):
    tr = foundry.tracker()
    p = _project(tr)
    match = next((a for a in tr.list_adrs(p) if a.id == adr_id), None)
    if not match:
        raise SystemExit(f"ADR introuvable : {adr_id}")
    write.set_adr_status(tr, match, "accepted")
    print(f"✅ {adr_id} → accepted")


def _body_file(path: str) -> str:
    with Path(path).open(encoding="utf-8", newline="") as source:
        return source.read()


def edit(adr_id, expected_path, updated_path):
    changed = write.update_adr_body(
        foundry.tracker(),
        adr_id,
        _body_file(expected_path),
        _body_file(updated_path),
    )
    print(f"✏️  {adr_id} · corps {'mis à jour' if changed else 'inchangé'}")


def supersede(adr_id, replacement_id):
    tr = foundry.tracker()
    p = _project(tr)
    current = next((a for a in tr.list_adrs(p) if a.id == adr_id), None)
    if current is None:
        raise SystemExit(f"ADR introuvable : {adr_id}")
    write.supersede_adr(tr, current, replacement_id)
    print(f"↪️  {adr_id} → superseded by {replacement_id}")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "create":
        create(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "proposed")
    elif cmd == "accept":
        accept(sys.argv[2])
    elif cmd == "edit":
        edit(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "supersede":
        supersede(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit(
            "usage: adr.py <create '<title>' [status] | accept <ADR-ID> | "
            "edit <ADR-ID> <expected-body.md> <updated-body.md> | "
            "supersede <ADR-ID> <replacement-ADR-ID>>"
        )
