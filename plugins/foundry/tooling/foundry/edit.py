"""Single-issue writes for intake / groom fixes.

Except for read-only ``reconcile-create``, commands here are deterministic writes.
The DECISION to call a write belongs to a
judgment skill AND to the human confirming it — never auto-invoked from reasoning.
Exceptions, made without a confirmation: the four writes that the grouped deferral rule
of the `foundry:merge-pr` skill authorizes in advance (create the companion Epic, create
an issue in it, add a comment to such an issue, attach to the companion Epic with `link`
an issue whose parent link was not set); and the writes that a skill orders as a step of
its own procedure (for example the progress note `comment` of `foundry:open-pr` and the
`transition` back to `in-progress` of `foundry:merge-pr`). The second group does not
cover the writes of `foundry:intake`, which are confirmed by the human except for the
four writes of the first group.

CLI:
  python3 -m foundry.edit create-issue '<json:{title,body,fields,parent}>'
  python3 -m foundry.edit reconcile-create <ISSUE-ID> <creation-spec.json>  (read-only)
  python3 -m foundry.edit set-field <ISSUE-ID> "<Field>" "<value>"
  python3 -m foundry.edit transition <ISSUE-ID> <state> [<expected-state>]
      (Linear: `transition <ISSUE-ID> dropped <expected-state>` abandons the issue,
       PAT-ADR-0017; `set-field <ISSUE-ID> State dropped` takes the same guarded path;
       `transition <ISSUE-ID> backlog <expected-state>` puts a started or in-review issue
       back to the backlog, expected state `in-progress` or `review`, PAT-165)
  python3 -m foundry.edit link <SRC-ID> <link-type> <DST-ID>
  python3 -m foundry.edit comment <ISSUE-ID> < note.md   (progress note, body on stdin)
  python3 -m foundry.edit body <ISSUE-ID> <expected-body.md> <updated-body.md>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import foundry
from foundry import write
from foundry.models import TransitionContext
from foundry.trackers.base import IssueCreationValidationError


def create_issue(spec_json):
    tr = foundry.tracker()
    spec = json.loads(spec_json)
    try:
        it = write.create_issue(
            tr, spec["title"], spec.get("body", ""),
            fields=spec.get("fields"), parent=spec.get("parent"),
        )
    except IssueCreationValidationError as exc:
        print(json.dumps(exc.details, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from None
    print(f"🆕 {it.id} — {it.title}")


def reconcile_create(issue_id, spec_path):
    spec = json.loads(_body_file(spec_path))
    result = write.reconcile_issue_creation(
        foundry.tracker(), issue_id, spec["title"], spec.get("body", ""),
        fields=spec.get("fields"), parent=spec.get("parent"),
    )
    print(json.dumps(result, ensure_ascii=False))


def set_field(issue_id, field, value):
    tr = foundry.tracker()
    if field == "Estimate":
        value = int(value)
    elif field == "Labels":
        # The portable port carries a list.  Keep one host-independent CLI syntax
        # instead of leaking each adapter's native label representation.
        value = [label.strip() for label in value.split(",") if label.strip()]
    result = write.set_field(tr, issue_id, field, value)
    print(f"✏️  {issue_id} · {field} = {value}")
    if field == "State" and value == "dropped":
        _name_linked_pr(result)


def transition(issue_id, state, expected_state=None):
    context = (
        TransitionContext(expected_state=expected_state)
        if expected_state is not None
        else None
    )
    result = write.transition(foundry.tracker(), issue_id, state, context=context)
    print(f"🔀 {issue_id} → {state}")
    if state == "dropped":
        _name_linked_pr(result)
    elif state == "backlog":
        _name_linked_pr(result, "le retour au backlog")


def _name_linked_pr(issue, cause="l'abandon"):
    """PAT-ADR-0017 / PAT-165: abandoning or returning an issue never touches its PR."""
    pr_url = getattr(issue, "pr_url", None)
    if not pr_url:
        return
    if cause == "l'abandon":
        print(f"⚠️  PR liée non fermée par l'abandon : {pr_url} — "
              "ferme-la à la main si elle est encore ouverte.")
    else:
        print(f"⚠️  PR liée ni fermée ni modifiée par {cause} : {pr_url} — "
              "ferme-la ou mets-la à jour à la main si besoin.")


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


_COMMANDS = {"create-issue": create_issue, "reconcile-create": reconcile_create, "set-field": set_field,
             "transition": transition, "link": link, "comment": comment, "body": body}


def main(argv):
    """PAT-275: `--help`/`-h` prints the module usage and writes nothing."""
    if not argv or argv[0] in ("-h", "--help") or argv[0] not in _COMMANDS:
        wanted_help = bool(argv) and argv[0] in ("-h", "--help")
        if not wanted_help and argv:
            print(f"⛔ Commande inconnue : {argv[0]!r}", file=sys.stderr)
        print((__doc__ or "").split("CLI:\n", 1)[-1].rstrip())
        return 0 if wanted_help else 2
    cmd, args = argv[0], argv[1:]
    if any(a in ("-h", "--help") for a in args):
        usage = [ln for ln in (__doc__ or "").splitlines() if f"foundry.edit {cmd} " in ln]
        print("\n".join(usage) or cmd)
        return 0
    _COMMANDS[cmd](*args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
