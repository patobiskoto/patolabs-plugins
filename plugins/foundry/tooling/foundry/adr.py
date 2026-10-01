"""Create / advance an ADR in the tracker's knowledge base.

Ticker comes from the resolved project (fixes the old bug where it was hardcoded
ORFEO-ADR and scanned articles across all projects). Title on argv, body on stdin.

CLI:
  echo "<body>" | python3 -m foundry.adr create "<title>" [status]
  python3 -m foundry.adr accept <ADR-ID> [--framed-by <ISSUE-ID>]
  python3 -m foundry.adr edit <ADR-ID> <expected-body.md> <updated-body.md>
"""

from __future__ import annotations

import re
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

_FRAME_LINE = re.compile(r"^\*\*Cadre \(ADR\) :\*\*\s*(?P<refs>.*?)\s*$")
_FRAME_CANDIDATE = re.compile(r"^\s*\*\*Cadre\s*\(ADR\)")
_ADR_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]*-ADR-[0-9]+")
_FENCE_OPEN = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})")


def _visible_markdown_lines(body: str):
    """Yield body lines that are outside fenced code blocks and HTML comments."""
    fence: str | None = None
    in_comment = False
    for line in body.splitlines():
        if fence is not None:
            closing = re.compile(
                rf"^ {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*$"
            )
            if closing.match(line):
                fence = None
            continue
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        if "<!--" in line:
            if "-->" not in line:
                in_comment = True
            continue
        if match := _FENCE_OPEN.match(line):
            fence = match.group("fence")
            continue
        yield line


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


def _framed_adr_ids(issue_id: str, body: str | None) -> tuple[str, ...]:
    """Return the one portable, explicit ADR frame declared by an issue body.

    Tracker ``query issue.adrs`` data is a project-level retrieval index. It is not
    evidence that this delivery issue framed any ADR, so automated promotion uses the
    body convention emitted by :mod:`foundry.frame` instead. Native relations remain
    useful provider evidence, but they are not portable enough to replace this exact
    citation at the shared CLI boundary.
    """
    candidates = [line for line in _visible_markdown_lines(body or "")
                  if _FRAME_CANDIDATE.match(line)]
    if len(candidates) != 1 or not (match := _FRAME_LINE.fullmatch(candidates[0])):
        raise SystemExit(
            f"⛔ Acceptation ADR automatique refusée — {issue_id} doit contenir "
            "un unique marqueur `**Cadre (ADR) :** <ADR-ID>[, ...]`."
        )
    refs = tuple(part.strip() for part in match.group("refs").split(","))
    if (
        not refs
        or any(not _ADR_ID.fullmatch(ref) for ref in refs)
        or len(set(refs)) != len(refs)
    ):
        raise SystemExit(
            f"⛔ Acceptation ADR automatique refusée — le cadre ADR de {issue_id} "
            "est ambigu ou mal formé."
        )
    return refs


def accept(adr_id, *, framed_by: str | None = None):
    tr = foundry.tracker()
    if framed_by is not None:
        p = write.issue_binding(tr, framed_by)
        if p is None:
            p = tr.resolve_project(registry.repo_basename())
        try:
            issue = tr.get_issue(framed_by)
        except Exception as exc:
            raise SystemExit(
                f"⛔ Acceptation ADR automatique refusée — issue de cadrage "
                f"{framed_by} illisible."
            ) from exc
        if issue is None or adr_id not in _framed_adr_ids(framed_by, issue.body):
            raise SystemExit(
                f"⛔ Acceptation ADR automatique refusée — {framed_by} ne cadre pas "
                f"explicitement {adr_id}."
            )
    else:
        p = _project(tr)
    match = write.adr_for_mutation(tr, p, adr_id)
    if not match:
        raise SystemExit(f"ADR introuvable : {adr_id}")
    if framed_by is not None and match.status != "proposed":
        raise SystemExit(
            f"⛔ Acceptation ADR automatique refusée — {adr_id} doit être proposed "
            f"(état actuel : {match.status})."
        )
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
    current = write.adr_for_mutation(tr, p, adr_id)
    if current is None:
        raise SystemExit(f"ADR introuvable : {adr_id}")
    write.supersede_adr(tr, current, replacement_id)
    print(f"↪️  {adr_id} → superseded by {replacement_id}")


def link_issue(adr_id, issue_id):
    tr = foundry.tracker()
    p = _project(tr)
    current = write.adr_for_mutation(tr, p, adr_id)
    if current is None:
        raise SystemExit(f"ADR introuvable : {adr_id}")
    linked = write.link_adr_issue(tr, current, issue_id)
    print(f"🔗 {linked.id} ↔ {issue_id}")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "create":
        create(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "proposed")
    elif cmd == "accept":
        args = sys.argv[2:]
        if len(args) == 1:
            accept(args[0])
        elif len(args) == 3 and args[1] == "--framed-by":
            accept(args[0], framed_by=args[2])
        else:
            raise SystemExit(
                "usage: adr.py accept <ADR-ID> [--framed-by <ISSUE-ID>]"
            )
    elif cmd == "edit":
        edit(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "supersede":
        supersede(sys.argv[2], sys.argv[3])
    elif cmd == "link-issue":
        link_issue(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit(
            "usage: adr.py <create '<title>' [status] | accept <ADR-ID> "
            "[--framed-by <ISSUE-ID>] | "
            "edit <ADR-ID> <expected-body.md> <updated-body.md> | "
            "supersede <ADR-ID> <replacement-ADR-ID> | "
            "link-issue <ADR-ID> <ISSUE-ID>>"
        )
