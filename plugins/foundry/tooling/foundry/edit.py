"""Single-issue writes for intake / groom fixes.

Every command here is a deterministic write. The DECISION to call it belongs to a
judgment skill AND to the human confirming it — never auto-invoked from reasoning.

CLI:
  python3 -m foundry.edit create-issue '<json:{title,body,fields,parent}>'
  python3 -m foundry.edit set-field <ISSUE-ID> "<Field>" "<value>"
  python3 -m foundry.edit transition <ISSUE-ID> <state>
  python3 -m foundry.edit link <SRC-ID> <link-type> <DST-ID>
  python3 -m foundry.edit comment <ISSUE-ID> < note.md   (progress note, body on stdin)
  python3 -m foundry.edit body <ISSUE-ID> <expected-body.md> <updated-body.md>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import foundry
from foundry import registry, write


def _project(tr):
    binding = write.mutation_project(tr)
    return binding if binding is not None else tr.resolve_project(registry.repo_basename())


def create_issue(spec_json):
    tr = foundry.tracker()
    spec = json.loads(spec_json)
    it = tr.create_issue(_project(tr), spec["title"], spec.get("body", ""),
                         fields=spec.get("fields"), parent=spec.get("parent"))
    print(f"🆕 {it.id} — {it.title}")


def set_field(issue_id, field, value):
    tr = foundry.tracker()
    # numeric coercion for Estimate
    if field == "Estimate":
        value = int(value)
    write.set_field(tr, issue_id, field, value)
    print(f"✏️  {issue_id} · {field} = {value}")


def transition(issue_id, state):
    write.transition(foundry.tracker(), issue_id, state)
    print(f"🔀 {issue_id} → {state}")


def link(src, link_type, dst):
    write.link(foundry.tracker(), src, link_type, dst)
    print(f"🔗 {src} {link_type} {dst}")


def comment(issue_id):
    text = sys.stdin.read().strip()
    if not text:
        raise SystemExit("⛔ Note vide — passe le corps sur stdin (`< note.md`).")
    write.add_comment(foundry.tracker(), issue_id, text)
    print(f"📝 note d'avancement posée sur {issue_id}")


def _body_file(path: str) -> str:
    with Path(path).open(encoding="utf-8", newline="") as source:
        return source.read()


def body(issue_id, expected_path, updated_path):
    changed = write.update_issue_body(
        foundry.tracker(), issue_id, _body_file(expected_path), _body_file(updated_path),
    )
    print(f"✏️  {issue_id} · corps {'mis à jour' if changed else 'inchangé'}")


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    {"create-issue": create_issue, "set-field": set_field,
     "transition": transition, "link": link, "comment": comment, "body": body}[cmd](*args)
