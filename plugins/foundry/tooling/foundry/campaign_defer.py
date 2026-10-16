"""Deferral of the non-blocking remarks of a validated review (PAT-141, PAT-ADR-0028).

After a validated review the cadencer carries the deferrable remarks to the companion Epic
``Nits <ORIGIN-EPIC-ID>`` with the four writes that AGENTS.md#R9 (b) authorizes in advance.
Every write is a Foundry command run as a subprocess (``edit create-issue``, ``edit comment``,
``edit link``, ``edit reconcile-create``); the tracker is only read, through ``search`` and
``get_issue``, before each write, so a retry never duplicates one.  The effect launches no
model session and the merge gate never reads the remarks receipt.  The intent is the
``started`` line of the attempt journal, written before the first write.  See
``docs/campaign-effects.md``.
"""
from __future__ import annotations

import json
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from foundry import campaign_cadence as cadence
from foundry import campaign_effects as fx
from foundry import campaign_review as review
from foundry import review_remarks

KIND = "defer-remarks"
COMPANION_PREFIX = "Nits "
FOLLOW_UP_TITLE = "Review remarks deferred from {origin}"
FOLLOW_UP_BODY = "- [ ] The deferred non-blocking review remarks are corrected or dropped"
DEFER_RETRY_SECONDS = 60  # a transient failure is retried after this wait; not measured
MAX_DEFER_TRIES = 5  # attempts per diff; beyond them the campaign waits for a human
STATES = ("pending", "done", "refused", "transient", "exhausted")
DEFERRAL_KEYS = {"state", "follow_up", "companion", "reason", "diff_hash"}
# What a Foundry command or a tracker read reports when the project cannot carry the write
# (capability absent, Epic type refused, binding refused); anything else is transient.
_DURABLE = re.compile(
    r"CapabilityUnavailable|capability unavailable|BindingError|ValueError|SystemExit|"
    r"type_unmapped|invalid_field_options|unknown type", re.I)
_CREATED = re.compile(r"^\S+\s+(\S+)\s+—", re.M)


class Outcome(Exception):  # noqa: N818 - control flow of one effect
    state = "transient"

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Durable(Outcome):
    """The project cannot carry the write: the remarks are corrected before the merge."""

    state = "refused"


class Transient(Outcome):
    """Quota, network or an unknown effect: wait, read the tracker, retry."""

    state = "transient"


def valid_deferral_field(value: object) -> bool:
    """Closed shape of the ``deferral`` key of a ``finished`` journal line."""
    return (
        isinstance(value, dict) and set(value) == DEFERRAL_KEYS
        and value["state"] in ("done", "refused", "transient")
        and all(value[k] is None or isinstance(value[k], str)
                for k in ("follow_up", "companion", "reason", "diff_hash"))
    )


def classify(text: str) -> Outcome:
    """Durable refusal or transient failure from the last 400 characters of an error."""
    tail = (text or "")[-400:]
    reason = re.sub(r"[0-9a-f]{64}", "[hex]", tail.strip().splitlines()[-1] if tail.strip()
                    else "error")[:200]
    return Durable(reason) if _DURABLE.search(tail) else Transient(reason)


# --------------------------------------------------------------------- observation


def origin_of(tracker, ticket: str) -> tuple[str | None, bool]:
    """(direct parent Epic, whether it is a companion Epic); ``(None, False)`` without one.

    R9 (b): the chain of parents is not walked up; a parent that is not an Epic is no origin.
    """
    parents = [link.target for link in tracker.get_issue(ticket).links
               if link.type == "subtask-of"]
    if len(parents) != 1:
        return None, False
    parent = tracker.get_issue(parents[0])
    if parent.type != "Epic":
        return None, False
    return parent.id, parent.title.startswith(COMPANION_PREFIX)


def _moment(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def deferral_facts(path: Path, epic: str):
    """``cadence.observe(deferral=...)``: the journal facts of the deferral of one review."""
    def read(ticket: str, head: str | None, base: str | None, now: datetime | None):
        records = fx.read_attempts(path)
        last = review.review_on(records, epic, ticket, head, base)
        if last is None or last["review"]["verdict"] != "validated":
            return cadence.DeferralFacts()
        diff_hash = last["review"]["diff_hash"]
        started = [r for r in records if r["event"] == "started" and r["kind"] == KIND
                   and r["epic"] == epic and r["ticket"] == ticket
                   and r["fingerprint"].get("diff_hash") == diff_hash]
        finished = {r["attempt"]: r for r in records if r["event"] == "finished"}
        facts = dict(deferrable=last["review"]["non_blocking"], diff_hash=diff_hash,
                     tries=len(started), state="pending")
        for record in started:
            end = finished.get(record["attempt"], {}).get("deferral")
            if end and end["state"] in ("done", "refused"):
                return cadence.DeferralFacts(
                    **{**facts, "state": end["state"], "follow_up": end["follow_up"]})
        if started:
            stamp = finished.get(started[-1]["attempt"], started[-1])["at"]
            facts["state"] = "exhausted" if len(started) >= MAX_DEFER_TRIES else "transient"
            facts["cooling"] = bool(now) and _moment(stamp) + timedelta(
                seconds=DEFER_RETRY_SECONDS) > now
        return cadence.DeferralFacts(**facts)
    return read


# --------------------------------------------------------------------------- effect


def _read(call, *args):
    try:
        return call(*args)
    except Outcome:
        raise
    except Exception as exc:  # noqa: BLE001 - classified, never swallowed
        raise classify(f"{type(exc).__name__}: {exc}") from None


def _write(runtime, cwd: Path, args: list[str], input: str | None = None):
    done = fx._run(runtime, runtime.command("edit", *args), cwd, input=input)
    return done


def _create(runtime, cwd: Path, spec: dict) -> str:
    """``edit create-issue``; the id it prints.  A failure is never retried here: the next
    pass reads the tracker first (and reconciles by the adapter's client identifier when the
    adapter named the created issue)."""
    done = _write(runtime, cwd, ["create-issue", json.dumps(spec)])
    if done.returncode == 0:
        created = _CREATED.search(done.stdout or "")
        if created is None:
            raise Transient("create_output_unreadable")
        return created.group(1)
    details = review._json_object((done.stderr or "").strip().splitlines()[-1]
                                  if (done.stderr or "").strip() else "")
    if details and details.get("reconciliation") == "required":
        issue_id = details.get("issue_id")
        if isinstance(issue_id, str) and issue_id:
            with tempfile.TemporaryDirectory() as tmp:
                spec_file = Path(tmp) / "spec.json"
                spec_file.write_text(json.dumps(spec), encoding="utf-8")
                check = _write(runtime, cwd, ["reconcile-create", issue_id, str(spec_file)])
            verdict = review._json_object(check.stdout) if check.returncode == 0 else None
            if verdict and verdict.get("validation") == "accepted":
                return issue_id
        raise Transient("creation_effect_unknown")
    raise classify(done.stderr or "")


def _find_companion(tracker, project, name: str) -> str | None:
    named = [i for i in _read(tracker.search, project) if i.title == name]
    epics = [i for i in named if (i.type or _read(tracker.get_issue, i.id).type) == "Epic"]
    if len(epics) > 1:
        raise Durable("companion_epic_ambiguous")
    return epics[0].id if epics else None


def _find_follow_up(tracker, project, companion: str, title: str) -> tuple[str | None, bool]:
    """(open follow-up of the companion Epic or an orphan to attach, whether to attach it)."""
    children = [link.target for link in _read(tracker.get_issue, companion).links
                if link.type == "parent-of"]
    unstarted = [c for c in (_read(tracker.get_issue, child) for child in children)
                 if c.state in cadence.READY_STATES]
    if unstarted:
        return min(unstarted, key=lambda c: cadence._id_key(c.id)).id, False
    for issue in _read(tracker.search, project):  # a creation whose parent link failed
        if issue.title == title and issue.state in cadence.READY_STATES:
            parents = [link.target for link in _read(tracker.get_issue, issue.id).links
                       if link.type == "subtask-of"]
            if not parents:
                return issue.id, True
    return None, False


def comment_text(ticket, receipt_ref: str, pr: int, rounds: int, remarks: list[dict]) -> str:
    lines = [
        f"Non-blocking review remarks deferred from {ticket} (AGENTS.md#R9 (b)).",
        f"Pull request #{pr}, issue {ticket}, review round {rounds}.",
        f"Deferral reference: {receipt_ref}",
        "",
        *(f"- {remark['text']}" for remark in remarks),
    ]
    return "\n".join(lines)


def run_deferral(runtime, tracker, project, epic: str, ticket, attempts: Path,
                 worktree: Path, repository: str) -> dict:
    """The six steps; raises ``Durable`` or ``Transient``; returns the ``deferral`` field."""
    facts = ticket.deferral
    receipt = review_remarks.ReviewRemarksStore(repository).find(ticket.id, facts.diff_hash)
    if receipt is None:
        raise Durable("remarks_receipt_missing")
    deferrable = [r for r in receipt["remarks"] if not r["fix_before_merge"]]
    if not deferrable or len(deferrable) != facts.deferrable:
        raise Durable("remarks_receipt_inconsistent")
    origin = facts.origin
    if origin is None or facts.origin_is_companion:
        raise Durable("no_deferral_case")
    name = f"{COMPANION_PREFIX}{origin}"
    companion = _find_companion(tracker, project, name)
    if companion is None:
        companion = _create(runtime, worktree, {
            "title": name, "fields": {"Type": "Epic", "State": "backlog"},
            "body": f"Companion Epic of {origin}: holds the non-blocking review remarks "
                    "deferred from it (AGENTS.md#R9 (b)). No tracker link to the origin Epic.",
        })
    title = FOLLOW_UP_TITLE.format(origin=origin)
    follow_up, attach = _find_follow_up(tracker, project, companion, title)
    if follow_up is None:
        follow_up = _create(runtime, worktree, {
            "title": title, "body": FOLLOW_UP_BODY, "parent": companion})
    elif attach:  # write 4: the creation succeeded, the parent link was not set
        done = _write(runtime, worktree, ["link", follow_up, "subtask-of", companion])
        if done.returncode != 0:
            raise classify(done.stderr or "")
    ref = f"{ticket.id}@{facts.diff_hash[:16]}"
    existing = _read(tracker.get_issue, follow_up).comments
    if not any(ref in str(c.get("text", "")) for c in existing):
        rounds = len(review.review_records(fx.read_attempts(attempts), epic, ticket.id))
        done = _write(runtime, worktree, ["comment", follow_up], input=comment_text(
            ticket.id, ref, ticket.pr.number, rounds, deferrable))
        if done.returncode != 0:
            raise classify(done.stderr or "")
    if review.update_pr_body(runtime, epic, ticket, attempts, worktree, None,
                             deferred=follow_up):
        raise Transient("pr_body_not_updated")
    return {"state": "done", "follow_up": follow_up, "companion": companion, "reason": None,
            "diff_hash": facts.diff_hash}


def launch(runtime, plan: dict, epic: str, ticket, attempts: Path, worktree: Path) -> dict:
    """The fields of the ``finished`` record; no session, no cost."""
    from foundry.routing import repository_identity

    fields = {"exit_code": None, "cost_usd": None, "duration_ms": None, "summary_file": None,
              "branch_head": None, "error": None, "detail": ""}
    try:
        deferral = run_deferral(runtime, plan["tracker"], plan["project"], epic, ticket,
                                attempts, worktree, repository_identity(runtime.root))
        return {**fields, "status": "completed", "deferral": deferral}
    except Outcome as outcome:
        deferral = {"state": outcome.state, "follow_up": None, "companion": None,
                    "reason": outcome.reason, "diff_hash": ticket.deferral.diff_hash}
        return {**fields, "status": "completed" if outcome.state == "refused" else "failed",
                "error": None if outcome.state == "refused" else "transient",
                "detail": outcome.reason, "deferral": deferral}
