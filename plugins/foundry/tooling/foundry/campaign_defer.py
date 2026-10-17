"""Deferral of the non-blocking remarks of a validated review (PAT-141, PAT-ADR-0028).

After a validated review the cadencer carries the deferrable remarks to the companion Epic
``Nits <ORIGIN-EPIC-ID>`` with the four writes that AGENTS.md#R9 (b) authorizes in advance.
Every write is a Foundry command run as a subprocess (``edit create-issue``, ``edit comment``,
``edit link``, ``edit reconcile-create``); the tracker is only read, through ``search`` and
``get_issue``, before each write, so that a retry finds what an earlier attempt of this journal
or the listing shows (``journal_memory``, a limit: ``docs/campaign-effects.md``).  The effect launches no
model session and the merge gate never reads the remarks receipt.  The intent is the
``started`` line of the attempt journal, written before the first write.  The ``finished``
line keeps the client identifier of a creation whose effect is unknown, and the next pass
reconciles by it before any new creation (PAT-ADR-0028).  See ``docs/campaign-effects.md``.
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
DEFERRAL_KEYS = {"state", "follow_up", "companion", "reason", "diff_hash", "remarks", "writes",
                 "unknown"}
WRITE_COMPANION = "create companion Epic"
WRITE_FOLLOW_UP = "create follow-up issue"
WRITE_ATTACH = "attach follow-up issue to the companion Epic"
WRITE_COMMENT = "comment on the follow-up issue"
_CLIENT = re.compile(r"client_id=([0-9A-Fa-f-]{36})")
# What a Foundry command or a tracker read reports when the project cannot carry the write
# (capability absent, Epic type refused, binding refused); anything else is transient.
_DURABLE = re.compile(
    r"CapabilityUnavailable|capability unavailable|BindingError|ValueError|SystemExit|"
    r"type_unmapped|invalid_field_options|unknown type", re.I)
# What `edit reconcile-create` prints when the adapter refuses to validate the creation
# (`TrackerConflictError`, "validation refused; no retry") or finds no such issue
# (`IssueUnavailableError`, "issue unavailable"); anything else leaves the effect unknown.
_REFUSED = re.compile(r"TrackerConflictError|validation refused", re.I)
_ABSENT = re.compile(r"IssueUnavailableError|issue unavailable", re.I)
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
        and (value["remarks"] is None or isinstance(value["remarks"], int))
        and isinstance(value["writes"], list) and all(isinstance(w, str) for w in value["writes"])
        and isinstance(value["unknown"], list) and all(
            isinstance(u, dict) and set(u) == {"title", "client_id"}
            and all(isinstance(v, str) for v in u.values()) for u in value["unknown"])
    )


def classify(text: str) -> Outcome:
    """Durable refusal or transient failure from the last 400 characters of an error."""
    tail = fx.without_request_count(text)[-400:]
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


def _reconcile(runtime, cwd: Path, ref: str, spec: dict) -> tuple[str, str | None]:
    """``edit reconcile-create <issue id or client id> <spec>`` (read-only): ``("accepted",
    issue id)``; ``("absent", None)`` when the read finds no such issue (the creation did not
    happen); ``("refused", None)`` when the adapter refuses to validate it (no replay);
    otherwise ``("unknown", None)``."""
    with tempfile.TemporaryDirectory() as tmp:
        spec_file = Path(tmp) / "spec.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")
        check = _write(runtime, cwd, ["reconcile-create", ref, str(spec_file)])
    if check.returncode != 0:
        tail = fx.without_request_count(check.stderr)[-400:]
        if _REFUSED.search(tail):
            return "refused", None
        return ("absent", None) if _ABSENT.search(tail) else ("unknown", None)
    verdict = review._json_object(check.stdout)
    if verdict and verdict.get("validation") == "refused":
        return "refused", None
    found = verdict.get("issue_id") if verdict and verdict.get("validation") == "accepted" else None
    return ("accepted", found) if isinstance(found, str) and found else ("unknown", None)


def _create(runtime, cwd: Path, spec: dict, ledger: dict, label: str) -> str:
    """``edit create-issue``; the id it prints.  A failed creation whose effect is unknown is
    reconciled by its client identifier (kept in ``ledger["unknown"]``, so in the journal)
    before any new creation (PAT-ADR-0028).  The read then decides (AGENTS.md#R9 (b)): the
    issue exists, adopt it; it does not exist, the next pass creates again with a new client
    identifier; the adapter refuses the replay, durable refusal; unreadable, transient."""
    for entry in [u for u in ledger["unknown"] if u["title"] == spec["title"]]:
        status, found = _reconcile(runtime, cwd, entry["client_id"], spec)
        if status == "refused":
            raise Durable("creation_replay_refused")
        if status == "unknown":
            raise Transient("creation_effect_unknown")
        ledger["unknown"].remove(entry)
        if status == "accepted":
            ledger["writes"].append(label)
            return found
    done = _write(runtime, cwd, ["create-issue", json.dumps(spec)])
    if done.returncode == 0:
        created = _CREATED.search(done.stdout or "")
        if created is None:
            raise Transient("create_output_unreadable")
        ledger["writes"].append(label)
        return created.group(1)
    stderr = fx.without_request_count(done.stderr)
    details = review._json_object(stderr.strip().splitlines()[-1] if stderr.strip() else "")
    if details and details.get("reconciliation") == "required":
        if details.get("validation") == "refused" or details.get("automatic_retry") is True:
            if details.get("creation") == "created":
                ledger["writes"].append(label)  # the issue exists: report the write (R9 b)
            raise Durable("creation_replay_refused")  # the adapter refuses to replay
        named, client = details.get("issue_id"), details.get("client_id")
    else:  # a quota error names the client identifier in its message (docs/linear-tracker.md)
        named, found = None, _CLIENT.search(stderr[-400:])
        client = found.group(1) if found else None
        if not isinstance(classify(stderr), Transient) or client is None:
            raise classify(stderr)
    ref = next((v for v in (named, client) if isinstance(v, str) and v), None)
    if ref is None:
        raise Durable("creation_effect_unreconcilable")
    status, found = _reconcile(runtime, cwd, ref, spec)
    if status == "accepted":
        ledger["writes"].append(label)
        return found
    if status == "refused":
        raise Durable("creation_replay_refused")
    if status == "absent":  # the creation did not happen: the next pass creates again
        raise Transient("creation_not_observed")
    ledger["unknown"].append({"title": spec["title"], "client_id": str(client or ref)})
    raise Transient("creation_effect_unknown")


class Listing:
    """The project listing (``tracker.search``) of ONE attempt, read at most once and only when
    a lookup needs it (PAT-228): on Linear it costs one request per page plus the replay of every
    closed Epic of the project, which made it most of the requests of a campaign."""

    def __init__(self, tracker, project):
        self.tracker, self.project, self._issues = tracker, project, None

    @property
    def loaded(self) -> bool:
        return self._issues is not None

    def issues(self) -> list:
        if self._issues is None:
            self._issues = _read(self.tracker.search, self.project)
        return self._issues


def journal_memory(attempts: Path, epic: str) -> tuple[list[str], bool]:
    """What the earlier deferral attempts of this Epic left in the attempt journal (PAT-228):
    (the companion Epics they named, most recent first; whether one of them may have created a
    follow-up issue).  The current attempt is the last ``started`` line, claimed before the
    launch, and is left out.  An earlier attempt with no ``deferral`` field on its ``finished``
    line, or none, counts as one that may have created it."""
    records = [r for r in fx.read_attempts(attempts)
               if r["epic"] == epic and r["kind"] == KIND]
    started = [r for r in records if r["event"] == "started"][:-1]
    finished = {r["attempt"]: r.get("deferral") for r in records if r["event"] == "finished"}
    companions: list[str] = []
    maybe_created = False
    for record in reversed(started):
        end = finished.get(record["attempt"])
        if end is None:
            maybe_created = True
            continue
        if end["companion"] and end["companion"] not in companions:
            companions.append(end["companion"])
        maybe_created |= WRITE_FOLLOW_UP in end["writes"] or bool(end["unknown"])
    return companions, maybe_created


def _find_companion(listing: Listing, name: str, known: list[str] = ()) -> str | None:
    """The companion Epic named ``name``.  A companion that an earlier attempt of this Epic
    journaled is read by identifier (``get_issue``) and kept when it is still an Epic of that
    title; only otherwise is the project listed."""
    tracker = listing.tracker
    for candidate in known:
        try:
            issue = tracker.get_issue(candidate)
        except Exception:  # noqa: BLE001 - unreadable: the listing decides
            continue
        if issue.type == "Epic" and issue.title == name:
            return issue.id
    named = [i for i in listing.issues() if i.title == name]
    epics = [i for i in named if (i.type or _read(tracker.get_issue, i.id).type) == "Epic"]
    if len(epics) > 1:
        raise Durable("companion_epic_ambiguous")
    return epics[0].id if epics else None


def _find_follow_up(listing: Listing, companion: str, title: str,
                    scan_orphans: bool = True) -> tuple[str | None, bool]:
    """(open follow-up of the companion Epic or an orphan to attach, whether to attach it).

    The orphan scan (a creation whose parent link failed) reads the listing; it is skipped when
    that would cost a new listing and no earlier attempt of this Epic can have created a
    follow-up (PAT-228)."""
    tracker = listing.tracker
    children = [link.target for link in _read(tracker.get_issue, companion).links
                if link.type == "parent-of"]
    unstarted = [c for c in (_read(tracker.get_issue, child) for child in children)
                 if c.state in cadence.READY_STATES]
    if unstarted:
        return min(unstarted, key=lambda c: cadence._id_key(c.id)).id, False
    if not scan_orphans:
        return None, False
    for issue in listing.issues():  # a creation whose parent link failed
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
        *(f"- {fx.mask_credentials(remark['text'])}" for remark in remarks),
    ]
    return "\n".join(lines)


def already_deferred(repository: str, records: list[dict], epic: str, ticket: str,
                     diff_hash: str) -> set[str]:
    """PAT-237: the texts of the remarks that an earlier validated round of this ticket (another
    diff hash) already deferred, from the receipts of its ``done`` deferrals in the journal."""
    store = review_remarks.ReviewRemarksStore(repository)
    texts: set[str] = set()
    for record in review._mine(records, epic, ticket):
        field = record.get("deferral")
        if (record["event"] == "finished" and record["kind"] == KIND and field
                and field["state"] == "done" and field["diff_hash"] != diff_hash):
            receipt = store.find(ticket, field["diff_hash"])
            texts.update(r["text"] for r in (receipt or {}).get("remarks", [])
                         if not r["fix_before_merge"])
    return texts


def run_deferral(runtime, tracker, project, epic: str, ticket, attempts: Path,
                 worktree: Path, repository: str, ledger: dict) -> dict:
    """The six steps; raises ``Durable`` or ``Transient``; returns the ``deferral`` field.

    ``ledger`` is ``{"writes": [...], "unknown": [...], "companion": ...}``: what this attempt
    wrote, the creations whose effect is still unknown and the companion Epic once found or
    created; the caller journals it whatever the outcome."""
    facts = ticket.deferral
    receipt = review_remarks.ReviewRemarksStore(repository).find(ticket.id, facts.diff_hash)
    if receipt is None:
        raise Durable("remarks_receipt_missing")
    deferrable = [r for r in receipt["remarks"] if not r["fix_before_merge"]]
    if not deferrable or len(deferrable) != facts.deferrable:
        raise Durable("remarks_receipt_inconsistent")
    earlier = already_deferred(repository, fx.read_attempts(attempts), epic, ticket.id,
                               facts.diff_hash)
    deferrable = [r for r in deferrable if r["text"] not in earlier]  # no double deferral
    origin = facts.origin
    if origin is None or facts.origin_is_companion:
        raise Durable("no_deferral_case")
    name = f"{COMPANION_PREFIX}{origin}"
    listing = Listing(tracker, project)
    known, maybe_created = journal_memory(attempts, epic)
    companion = _find_companion(listing, name, known)
    if companion is None:
        companion = _create(runtime, worktree, {
            "title": name, "fields": {"Type": "Epic", "State": "backlog"},
            "body": f"Companion Epic of {origin}: holds the non-blocking review remarks "
                    "deferred from it (AGENTS.md#R9 (b)). No tracker link to the origin Epic.",
        }, ledger, WRITE_COMPANION)
    ledger["companion"] = companion
    title = FOLLOW_UP_TITLE.format(origin=origin)
    follow_up, attach = _find_follow_up(listing, companion, title,
                                        maybe_created or listing.loaded)
    if follow_up is None:
        follow_up = _create(runtime, worktree, {
            "title": title, "body": FOLLOW_UP_BODY, "parent": companion},
            ledger, WRITE_FOLLOW_UP)
    elif attach:  # write 4: the creation succeeded, the parent link was not set
        done = _write(runtime, worktree, ["link", follow_up, "subtask-of", companion])
        if done.returncode != 0:
            raise classify(done.stderr or "")
        ledger["writes"].append(WRITE_ATTACH)
    ref = f"{ticket.id}@{facts.diff_hash[:16]}"
    existing = _read(tracker.get_issue, follow_up).comments
    if deferrable and not any(ref in str(c.get("text", "")) for c in existing):
        rounds = len(review.review_records(fx.read_attempts(attempts), epic, ticket.id))
        done = _write(runtime, worktree, ["comment", follow_up], input=comment_text(
            ticket.id, ref, ticket.pr.number, rounds, deferrable))
        if done.returncode != 0:
            raise classify(done.stderr or "")
        ledger["writes"].append(WRITE_COMMENT)
    field = deferral_field("done", facts.diff_hash, ledger, follow_up=follow_up,
                           companion=companion, remarks=len(deferrable))
    if review.update_pr_body(runtime, epic, ticket, attempts, worktree, None, deferral=field):
        raise Transient("pr_body_not_updated")
    return field


def deferral_field(state: str, diff_hash: str, ledger: dict, *, follow_up=None,
                   companion=None, reason=None, remarks=None) -> dict:
    return {"state": state, "follow_up": follow_up, "companion": companion, "reason": reason,
            "diff_hash": diff_hash, "remarks": remarks, "writes": list(ledger["writes"]),
            "unknown": list(ledger["unknown"])}


def launch(runtime, plan: dict, epic: str, ticket, attempts: Path, worktree: Path) -> dict:
    """The fields of the ``finished`` record; no session, no cost."""
    from foundry.routing import repository_identity

    fields = {"exit_code": None, "cost_usd": None, "duration_ms": None, "summary_file": None,
              "branch_head": None, "error": None, "detail": "", "status": "completed"}
    diff_hash = ticket.deferral.diff_hash
    earlier = [r["deferral"] for r in review._mine(fx.read_attempts(attempts), epic, ticket.id)
               if r["event"] == "finished" and r["kind"] == KIND and "deferral" in r
               and r["deferral"]["diff_hash"] == diff_hash]
    ledger = {"writes": [], "unknown": list(earlier[-1]["unknown"]) if earlier else []}
    try:
        deferral = run_deferral(runtime, plan["tracker"], plan["project"], epic, ticket,
                                attempts, worktree, repository_identity(runtime.root), ledger)
        return {**fields, "deferral": deferral}
    except Outcome as outcome:
        deferral = deferral_field(outcome.state, diff_hash, ledger, reason=outcome.reason,
                                  companion=ledger.get("companion"))
        if outcome.state == "refused" and review.update_pr_body(
                runtime, epic, ticket, attempts, worktree, None, deferral=deferral):
            deferral = deferral_field("transient", diff_hash, ledger, reason="pr_body_not_updated",
                                      companion=ledger.get("companion"))
            outcome = Transient("pr_body_not_updated")
        return {**fields, "status": "completed" if outcome.state == "refused" else "failed",
                "error": None if outcome.state == "refused" else "transient",
                "detail": outcome.reason, "deferral": deferral}
