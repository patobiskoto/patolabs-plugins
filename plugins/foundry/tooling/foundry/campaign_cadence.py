"""Dry computation of the next step of an Epic campaign (PAT-216, PAT-ADR-0026/0027).

One pass re-reads the tracker, git and the PR, and returns exactly one ``Decision``.  It
produces no effect: no tracker write, no git mutation, no PR mutation, no session launch.
The state lives in the tracker, git, the PR, the local attempt journal, the approval journal
and the review ledger with its acceptance-proof store, never here.  ``decide`` is pure over an
immutable ``Observation``; ``observe`` gathers it through declared capabilities only.
Executing a decision is ``campaign_effects`` (PAT-217, PAT-218); this module stays effect-free.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from foundry import campaign_approval as approval
from foundry.epic_preview import (
    PREVIEW_DEPTH,
    PREVIEW_NODE_LIMIT,
    EpicPreviewError,
    classify_nodes,
    structure_digest,
    validate_subgraph,
)

CADENCE_CONTRACT = "foundry-campaign-cadence.v1"
# Bounds of the review and correction loop of one ticket (PAT-218, AC3): beyond them the
# campaign suspends and waits for a human.  Counted from the attempt journal.
MAX_REVIEW_ROUNDS = 3
MAX_CORRECTIONS = 2
STARTED_STATES = ("in-progress", "review")
READY_STATES = ("backlog", "ready")
KINDS = (
    "start", "implement", "open-pr", "review", "correct", "defer-remarks", "wait-ci", "wait",
    "merge", "await-verdict", "suspend", "refuse",
)
# Tracker states strictly behind a started or finished dependent (`dependency_state_behind`).
_UNFINISHED = ("backlog", "ready", "blocked", "in-progress", "review")
_PR_NUMBER = re.compile(r"/pull/([0-9]+)(?:[/?#]|$)")


@dataclass(frozen=True)
class PullFacts:
    number: int
    state: str  # open | closed
    merged: bool
    head_sha: str | None
    base_sha: str | None


@dataclass(frozen=True)
class DeferralFacts:
    """The non-blocking remarks of the validated review of the current PR head (PAT-141).

    ``deferrable`` is the number of remarks that are not marked "fix before merge";
    ``origin`` the direct parent Epic of the ticket (``None``: no origin Epic) and
    ``origin_is_companion`` whether that Epic is itself a companion Epic.  ``state`` comes from
    the attempt journal, for this ``diff_hash``: ``pending`` (never attempted), ``done``,
    ``refused`` (durable refusal), ``transient`` (to retry; ``cooling`` while the wait runs)
    or ``exhausted`` (``tries`` reached the ceiling).
    """
    deferrable: int = 0
    origin: str | None = None
    origin_is_companion: bool = False
    state: str = "pending"
    tries: int = 0
    cooling: bool = False
    diff_hash: str | None = None
    follow_up: str | None = None


@dataclass(frozen=True)
class TicketFacts:
    id: str
    state: str
    category: str  # eligible | blocked | omitted (epic_preview.classify_nodes)
    reason: str
    depends_on: tuple[str, ...] = ()
    pr_url: str | None = None
    # Observed only for the one ticket in focus; ``None`` means not observed.
    branch: bool | None = None
    branch_sha: str | None = None  # head of the branch, when it can be read
    ahead: int | None = None  # commits of the branch beyond the remote default branch
    # True when the last implementation or correction attempt ended on this very branch head
    # (PAT-217, PAT-218).
    implemented: bool = False
    pr: PullFacts | None = None
    review: str | None = None  # none | valid | blocked | unobservable | integrity
    ci: str | None = None  # green | pending | red | none
    anomaly: str | None = None
    # From the attempt journal (PAT-218), for the ticket in focus only.  ``fix_before_merge``
    # is the number of remarks marked "fix before merge" in the validated review of exactly
    # this PR head and base; ``review_rounds`` the recorded (not carried) reviews and
    # ``corrections`` the correction sessions started for this ticket.
    fix_before_merge: int = 0
    review_rounds: int = 0
    corrections: int = 0
    deferral: DeferralFacts | None = None
    # PAT-227: how many of those "fix before merge" remarks target the PR description, and
    # whether a correction already replaced the description for this PR head and base.
    fix_description: int = 0
    description_corrected: bool = False


@dataclass(frozen=True)
class Observation:
    epic: str
    missing_capabilities: tuple[str, ...] = ()
    read_error: str | None = None
    structure_observed: str | None = None
    structure_approved: str | None = None
    approval_status: str = "unchecked"  # valid | unchecked | an approval refusal code
    approval_detail: str | None = None
    tickets: tuple[TicketFacts, ...] = ()
    focus: str | None = None


@dataclass(frozen=True)
class Decision:
    kind: str
    ticket: str | None = None
    reason: str = ""
    evidence: dict = field(default_factory=dict)
    # Only on an approval refusal: the step the pass would return once approved.
    blocked_step: dict | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown decision kind: {self.kind}")


def _id_key(issue_id: str) -> tuple[str, int]:
    prefix, _, number = issue_id.rpartition("-")
    return prefix, int(number)


def select_focus(tickets: tuple[TicketFacts, ...]) -> tuple[str | None, Decision | None]:
    """The one ticket in progress, else the first startable one; pure and deterministic."""
    by_id = {ticket.id: ticket for ticket in tickets}
    started = sorted(
        (t.id for t in tickets if t.state in STARTED_STATES and t.category != "omitted"),
        key=_id_key,
    )
    if len(started) > 1:
        return None, Decision(
            "suspend", reason="several_in_progress", evidence={"tickets": started},
        )
    if started:
        return started[0], None
    for ticket in sorted(tickets, key=lambda t: _id_key(t.id)):
        if (ticket.category == "eligible" and ticket.state in READY_STATES
                and all(by_id[dep].state == "done" for dep in ticket.depends_on)):
            return ticket.id, None
    return None, None


def _regression(tickets: tuple[TicketFacts, ...]) -> Decision | None:
    by_id = {ticket.id: ticket for ticket in tickets}
    for ticket in sorted(tickets, key=lambda t: _id_key(t.id)):
        if ticket.state not in (*STARTED_STATES, "done"):
            continue
        behind = [dep for dep in ticket.depends_on if by_id[dep].state in _UNFINISHED]
        if behind:
            return Decision(
                "suspend", ticket.id, "dependency_state_behind",
                {"state": ticket.state, "unfinished_dependencies": behind},
            )
    return None


def _bounded(ticket: TicketFacts, decision: Decision) -> Decision:
    """Suspend instead of a ``review`` or ``correct`` beyond the bounds of the loop."""
    counts = {"reviews": ticket.review_rounds, "corrections": ticket.corrections,
              "max_reviews": MAX_REVIEW_ROUNDS, "max_corrections": MAX_CORRECTIONS}
    if decision.kind == "review" and ticket.review_rounds >= MAX_REVIEW_ROUNDS:
        return Decision("suspend", ticket.id, "review_rounds_exhausted", counts)
    if decision.kind == "correct" and ticket.corrections >= MAX_CORRECTIONS:
        return Decision("suspend", ticket.id, "corrections_exhausted", counts)
    return decision


def _remarks(ticket: TicketFacts, evidence: dict) -> Decision | None:
    """The non-blocking remarks of a validated review: defer them, else correct them (PAT-141).

    ``None`` when there is nothing left to do about them (none, or already deferred).
    """
    facts = ticket.deferral
    if facts is None or facts.deferrable == 0 or facts.state == "done":
        return None
    detail = {**evidence, "remarks": facts.deferrable}
    cause = ("no_origin_epic" if facts.origin is None
             else "companion_epic" if facts.origin_is_companion
             else "refused" if facts.state == "refused" else None)
    if cause is not None:  # no second-level deferral, no origin Epic, or a durable refusal
        return _bounded(ticket, Decision(
            "correct", ticket.id, "remarks_not_deferrable", {**detail, "cause": cause}))
    if facts.state == "exhausted":
        return Decision("suspend", ticket.id, "deferral_retries_exhausted",
                        {**detail, "tries": facts.tries})
    if facts.state == "transient" and facts.cooling:
        return Decision("wait", ticket.id, "deferral_transient_failure",
                        {**detail, "tries": facts.tries})
    return Decision("defer-remarks", ticket.id, "non_blocking_remarks",
                    {**detail, "origin": facts.origin, "tries": facts.tries})


def _ticket_step(ticket: TicketFacts) -> Decision:
    if ticket.anomaly:
        return Decision("suspend", ticket.id, ticket.anomaly)
    if ticket.state in READY_STATES:
        return Decision("start", ticket.id, "eligible_dependencies_done")
    pr = ticket.pr
    if pr is None:
        if ticket.branch:
            if ticket.implemented and (ticket.ahead or 0) > 0:
                return Decision("open-pr", ticket.id, "implemented_without_pr",
                                {"branch": True, "ahead": ticket.ahead})
            return Decision("implement", ticket.id, "started_without_pr", {"branch": True})
        if ticket.state == "review":  # a review state implies a PR: restarting would redo it
            return Decision("suspend", ticket.id, "review_state_without_branch_or_pr",
                            {"branch": False})
        return Decision("start", ticket.id, "in_progress_branch_absent", {"branch": False})
    evidence = {"pr": pr.number}
    if pr.merged:
        return Decision("suspend", ticket.id, "merged_pr_state_behind",
                        {**evidence, "tracker_state": ticket.state})
    if pr.state != "open":
        return Decision("suspend", ticket.id, "pr_closed_unmerged", evidence)
    if not pr.head_sha:
        return Decision("suspend", ticket.id, "pr_head_unreadable", evidence)
    if ticket.implemented and ticket.branch_sha and ticket.branch_sha != pr.head_sha:
        # A correction session committed locally: the PR still holds the reviewed head.
        return Decision("open-pr", ticket.id, "correction_not_pushed", evidence)
    if ticket.review == "integrity":  # a corrupt proof or ledger is not "no review" (PAT-235)
        return Decision("suspend", ticket.id, "review_proof_integrity", evidence)
    if ticket.review == "blocked":
        return _bounded(ticket, Decision("correct", ticket.id, "review_blocked", evidence))
    if ticket.review != "valid":
        reason = "review_unobservable" if ticket.review == "unobservable" else "review_missing"
        return _bounded(ticket, Decision("review", ticket.id, reason, evidence))
    # The remarks come first, also beside "fix before merge" ones: the correction changes the
    # head, and the next review (of another diff hash) need not list them again (PAT-141).
    remarks = _remarks(ticket, evidence)
    if remarks is not None:
        return remarks
    if ticket.fix_before_merge > 0:
        # PAT-227: only the description is to fix and the diff stays: the description is
        # replaced once (no new review: the diff hash is unchanged), then the pass goes on.
        description_only = ticket.fix_description == ticket.fix_before_merge
        if not (description_only and ticket.description_corrected):
            return _bounded(ticket, Decision(
                "correct", ticket.id,
                "review_fix_description" if description_only else "review_fix_before_merge",
                {**evidence, "fix_before_merge": ticket.fix_before_merge}))
    if ticket.ci == "green":
        return Decision("merge", ticket.id, "review_valid_ci_green", evidence)
    if ticket.ci == "red":
        return Decision("correct", ticket.id, "ci_failing", evidence)
    reason = "ci_pending" if ticket.ci == "pending" else "ci_not_proven"
    return Decision("wait-ci", ticket.id, reason, evidence)


def _step(obs: Observation) -> Decision:
    regression = _regression(obs.tickets)
    if regression is not None:
        return regression
    focus, problem = select_focus(obs.tickets)
    if problem is not None:
        return problem
    if focus is None:
        pending = sorted(
            (t for t in obs.tickets if t.category != "omitted"), key=lambda t: _id_key(t.id),
        )
        if not pending:
            return Decision("await-verdict", reason="all_tickets_terminal")
        return Decision(
            "suspend", reason="no_eligible_ticket",
            evidence={"tickets": [
                {"id": t.id, "state": t.state, "reason": t.reason} for t in pending
            ]},
        )
    return _ticket_step(next(t for t in obs.tickets if t.id == focus))


def decide(obs: Observation) -> Decision:
    """Exactly one decision; no effect.  Order: capability, strict read, drift, suspension or
    ``await-verdict``, approval."""
    if obs.missing_capabilities:
        return Decision(
            "refuse", reason="capability_missing",
            evidence={"missing": list(obs.missing_capabilities)},
        )
    if obs.read_error is not None:
        return Decision("suspend", reason="node_unreadable", evidence={"error": obs.read_error})
    if obs.approval_status == approval.StructureDigestMismatch.code:
        return Decision(
            "suspend", reason="structure_changed",
            evidence={"observed": obs.structure_observed, "approved": obs.structure_approved},
        )
    step = _step(obs)
    # An expired or absent approval refuses an effect; `await-verdict` requests none (PAT-235).
    if step.kind in ("suspend", "await-verdict"):
        return step
    if obs.approval_status != "valid":
        return Decision(
            "refuse", reason=f"approval_{obs.approval_status}",
            evidence={"detail": obs.approval_detail},
            blocked_step={"kind": step.kind, "ticket": step.ticket, "reason": step.reason},
        )
    return step


def missing_capabilities(tracker) -> tuple[str, ...]:
    """Declared capabilities PAT-ADR-0026 requires; reads only these flags."""
    from foundry.trackers.base import Tracker

    missing = [
        flag for flag in ("epic_subgraph_supported", "append_only_lifecycle_supported")
        if getattr(tracker, flag, False) is not True
    ]
    # No declarative flag exists for reconcilable issue creation (PAT-216 gap): the
    # capability is the adapter overriding ``Tracker.reconcile_issue_creation``.
    override = getattr(type(tracker), "reconcile_issue_creation", None)
    if override is None or override is Tracker.reconcile_issue_creation:
        missing.append("issue_creation_reconciliation")
    return tuple(missing)


# PAT-220: what a session wrote in a worktree (a hook a relative core.hooksPath selects there)
# must not run in a git command of the cadencer, which runs outside the sandbox.
def git_neutral_env(environ, hooks: str) -> dict:
    """``environ`` plus ``GIT_CONFIG_COUNT`` entries ``core.hooksPath=<hooks>`` and
    ``core.fsmonitor=false``, appended after any entries ``environ`` already declares."""
    env = dict(environ)
    count = env.get("GIT_CONFIG_COUNT", "0")
    count = int(count) if count.isdigit() else 0
    for key, value in (("core.hooksPath", hooks), ("core.fsmonitor", "false")):
        env[f"GIT_CONFIG_KEY_{count}"], env[f"GIT_CONFIG_VALUE_{count}"] = key, value
        count += 1
    env["GIT_CONFIG_COUNT"] = str(count)
    return env


def _git(root: str, *args: str, check: bool = True) -> str:
    """The one read-only git call of this module; empty on a refused, unchecked call.  Its
    verbs (``for-each-ref``, ``rev-parse``, ``rev-list``) run no hook: hooks stay off."""
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=check,
        env=git_neutral_env(os.environ, os.devnull),
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_refs(root: str) -> list[str]:
    return sorted(
        _git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads",
             "refs/remotes/origin").split()
    )


def _git_branch_facts(root: str, branch: str) -> tuple[str | None, int | None]:
    """(head sha, commits beyond the remote default branch); ``None`` when unreadable."""
    from foundry import registry

    head = (_git(root, "rev-parse", "--verify", "-q", f"refs/heads/{branch}", check=False)
            or _git(root, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}",
                    check=False))
    if not head:
        return None, None
    default = registry.default_branch(root) or "main"
    base = (_git(root, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{default}",
                 check=False) and f"refs/remotes/origin/{default}") or default
    count = _git(root, "rev-list", "--count", f"{base}..{head}", check=False)
    return head, int(count) if count.isdigit() else None


def _branch_for(refs: list[str], issue_id: str) -> str | None:
    pattern = re.compile(rf"^(?:origin/)?[a-z]+/{re.escape(issue_id.lower())}(?:-|$)")
    names = sorted({ref.removeprefix("origin/") for ref in refs if pattern.match(ref)})
    return names[0] if names else None


def _pull_facts(pr) -> PullFacts:
    return PullFacts(
        number=int(pr.number), state=pr.state, merged=bool(pr.merged),
        head_sha=pr.sha, base_sha=pr.base_sha,
    )


def _find_pr(codehost, repo: str, branch: str | None, pr_url: str | None):
    """(PullFacts | None, anomaly | None) from the branch, else from the tracker PR link."""
    if branch is not None:
        candidates = codehost.list_prs(repo, branch)
        opened = [pr for pr in candidates if pr.state == "open"]
        if len(opened) > 1:
            return None, "pr_ambiguous"
        if opened:
            return _pull_facts(opened[0]), None
        # The list endpoint carries no ``merged`` field (only ``merged_at``): a closed PR is
        # re-read one by one before it is classified merged or closed unmerged.
        closed = [codehost.get_pr(repo, pr.number)
                  for pr in sorted(candidates, key=lambda pr: pr.number, reverse=True)]
        chosen = [pr for pr in closed if pr.merged][:1] or closed[:1]
        return (_pull_facts(chosen[0]) if chosen else None), None
    match = _PR_NUMBER.search(pr_url or "")
    if match:
        return _pull_facts(codehost.get_pr(repo, int(match.group(1)))), None
    return None, None


def _default_diff(root: str, base: str, head: str) -> bytes:
    from foundry.routing import git_diff

    return git_diff(root, base, head=head)


def _review_status(store, issue, pr: PullFacts, root: str, diff_reader) -> str:
    from foundry.routing import ProofIntegrityError, RoutingConfigError

    if not pr.head_sha or not pr.base_sha:
        return "unobservable"
    try:
        diff = diff_reader(root, pr.base_sha, pr.head_sha)
    except RoutingConfigError:
        return "unobservable"
    coordinates = dict(
        issue_id=issue.id, issue_body=issue.body, head=pr.head_sha, diff=diff, base=pr.base_sha,
    )
    try:
        store.valid_for_merge(**coordinates)
        return "valid"
    except ProofIntegrityError:
        return "integrity"
    except RoutingConfigError:
        pass
    try:
        store.current_for_sync(**coordinates)
        return "blocked"
    except ProofIntegrityError:
        return "integrity"
    except RoutingConfigError:
        return "none"


def _ci_status(codehost, repo: str, sha: str) -> str:
    from foundry.write import ci_gate

    verdict = ci_gate(codehost, repo, sha)
    if verdict["passed"]:
        return "green"
    if verdict["failing"]:  # a known failure is red without waiting for the other checks
        return "red"
    return "pending" if verdict["pending"] else "none"


def observe(
    tracker, codehost, project, epic: str, root: str, now: datetime | None = None, *,
    journal: Path | None = None, refs: Callable[[str], list[str]] = _git_refs,
    diff_reader: Callable[[str, str, str], bytes] = _default_diff, proof_store=None,
    branch_facts: Callable[[str, str], tuple[str | None, int | None]] = _git_branch_facts,
    implemented: Callable[[str, str | None], bool] | None = None,
    history: Callable[[str, str | None, str | None], tuple[int, int, int]] | None = None,
    deferral: Callable[..., DeferralFacts] | None = None,
    origin: Callable[[str], tuple[str | None, bool]] | None = None,
    description: Callable[[str, str | None, str | None], tuple[int, bool]] | None = None,
) -> Observation:
    """Gather one observation with declared capabilities and read-only calls only."""
    missing = missing_capabilities(tracker)
    if missing:
        return Observation(epic=epic, missing_capabilities=missing)
    from foundry.trackers.base import (
        IssueUnavailableError, TrackerBindingError, TrackerConflictError,
    )

    try:
        raw = tracker.get_epic_subgraph(
            project, epic, depth=PREVIEW_DEPTH, nodes=PREVIEW_NODE_LIMIT,
        )
        graph = validate_subgraph(
            raw, expected_epic_id=epic, expected_project_key=getattr(project, "key", None),
        )
    except (EpicPreviewError, IssueUnavailableError, TrackerBindingError,
            TrackerConflictError) as exc:
        return Observation(epic=epic, read_error=f"{type(exc).__name__}: {exc}")
    digest = structure_digest(graph)
    categories = classify_nodes(graph)
    tickets = tuple(
        TicketFacts(
            id=key, state=graph.nodes[key]["state"], category=categories[key][0],
            reason=categories[key][1], depends_on=graph.dependencies.get(key, ()),
            pr_url=graph.nodes[key]["pr_url"],
        )
        for key in sorted(graph.nodes, key=_id_key) if key != graph.value["root"]
    )

    status, detail, approved = "unchecked", None, None
    try:
        own = [r for r in approval.read_journal(journal) if r["epic"] == epic]
        latest = max(own, key=lambda r: (r["approved_at"], r["record_digest"]), default=None)
        approved = latest["structure_digest"] if latest else None
        approval.check(
            epic, latest["preview_digest"] if latest else "0" * 64, digest, now, journal,
        )
        status = "valid"
    except approval.ApprovalError as exc:
        status, detail = exc.code, str(exc)

    focus, _problem = select_focus(tickets)
    if focus is not None:
        tickets = tuple(
            _focus_facts(t, tracker, codehost, root, refs, diff_reader, proof_store,
                         branch_facts, implemented, history, deferral, origin, now, description)
            if t.id == focus else t for t in tickets
        )
    return Observation(
        epic=epic, structure_observed=digest, structure_approved=approved,
        approval_status=status, approval_detail=detail, tickets=tickets, focus=focus,
    )


def _focus_facts(ticket, tracker, codehost, root, refs, diff_reader, proof_store,
                 branch_facts, implemented, history=None, deferral=None, origin=None,
                 now=None, description=None):
    from dataclasses import replace

    branch = _branch_for(refs(root), ticket.id)
    sha, ahead = branch_facts(root, branch) if branch else (None, None)
    done = bool(implemented and branch and implemented(ticket.id, sha))
    known = dict(branch=branch is not None, branch_sha=sha, ahead=ahead, implemented=done)
    if ticket.state in READY_STATES:  # `start` is the step whatever git holds
        return replace(ticket, **known)
    repo = codehost.resolve_repo(root)
    pr, anomaly = _find_pr(codehost, repo, branch, ticket.pr_url)
    facts = replace(ticket, **known, pr=pr, anomaly=anomaly)
    if pr is None or pr.merged or pr.state != "open" or anomaly:
        return facts
    if proof_store is None:
        from foundry.routing import AcceptanceProofStore, repository_identity

        proof_store = AcceptanceProofStore(repository_identity(root))
    review = _review_status(proof_store, tracker.get_issue(ticket.id), pr, root, diff_reader)
    ci = _ci_status(codehost, repo, pr.head_sha) if review == "valid" and pr.head_sha else None
    fix, rounds, corrections = history(ticket.id, pr.head_sha, pr.base_sha) if history else (0, 0, 0)
    remarks = None
    if review == "valid" and deferral is not None:
        remarks = deferral(ticket.id, pr.head_sha, pr.base_sha, now)
        if remarks.deferrable > 0 and remarks.state != "done" and origin is not None:
            found, companion = origin(ticket.id)  # tracker reads, only when remarks remain
            remarks = replace(remarks, origin=found, origin_is_companion=companion)
    fix_description, corrected = description(ticket.id, pr.head_sha, pr.base_sha) \
        if description and review == "valid" else (0, False)
    return replace(facts, review=review, ci=ci, fix_before_merge=fix if review == "valid" else 0,
                   review_rounds=rounds, corrections=corrections, deferral=remarks,
                   fix_description=fix_description, description_corrected=corrected)


def report(obs: Observation, decision: Decision) -> str:
    """Canonical JSON of one pass: same observation, same bytes."""
    payload = {
        "contract": CADENCE_CONTRACT,
        "epic": obs.epic,
        "observation": {
            "missing_capabilities": list(obs.missing_capabilities),
            "read_error": obs.read_error,
            "structure": {"observed": obs.structure_observed, "approved": obs.structure_approved},
            "approval": {"status": obs.approval_status, "detail": obs.approval_detail},
            "focus": obs.focus,
            "tickets": [asdict(ticket) for ticket in obs.tickets],
        },
        "decision": asdict(decision),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)


def _context(root: str):
    """(tracker, project) of the checkout, or (tracker, None) when a capability is missing."""
    import foundry
    from foundry import registry

    tracker = foundry.tracker()
    if missing_capabilities(tracker):
        return tracker, None
    resolver = getattr(tracker, "resolve_checkout_project", None)
    project = (
        resolver(root) if callable(resolver)
        else tracker.resolve_project(registry.repo_basename(root))
    )
    return tracker, project


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Étape suivante d'une campagne d'Epic")
    sub = parser.add_subparsers(dest="verb", required=True)
    nxt = sub.add_parser("next", help="observe, then print the one next step (no effect)")
    nxt.add_argument("epic_id")
    nxt.add_argument("--root", default=".")
    from foundry import campaign_effects

    campaign_effects.add_parsers(sub)
    args = parser.parse_args(argv)
    if args.verb != "next":  # `step` and `run` execute effects (PAT-217)
        return campaign_effects.cli(args)

    import foundry

    tracker, project = _context(args.root)
    if project is None:  # before any read of the Epic
        obs = Observation(epic=args.epic_id, missing_capabilities=missing_capabilities(tracker))
        print(report(obs, decide(obs)))
        return 2
    path = campaign_effects.journal_path()
    try:  # the same journal facts as `step`, so that `next` announces what `step` will do
        campaign_effects.read_attempts(path)
    except campaign_effects.AttemptJournalInvalid as exc:
        obs = Observation(epic=args.epic_id)
        print(report(obs, Decision("suspend", reason="attempts_journal_invalid",
                                   evidence={"error": str(exc)})))
        return 0
    obs = observe(
        tracker, foundry.codehost(), project, args.epic_id, args.root,
        datetime.now(timezone.utc),
        **campaign_effects.journal_facts(tracker, args.epic_id, path),
    )
    print(report(obs, decide(obs)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
