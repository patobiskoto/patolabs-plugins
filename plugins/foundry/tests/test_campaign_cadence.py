"""PAT-216: dry computation of the next step of an Epic campaign; no effect anywhere."""
import copy
import json
import subprocess
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from foundry import campaign_approval as ca
from foundry import campaign_cadence as cc
from foundry.epic_preview import structure_digest, validate_subgraph
from foundry.models import Check, PullRequest
from foundry.routing import RoutingConfigError
from foundry.trackers.base import IssueUnavailableError, Tracker
from test_epic_preview import graph, node

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
PROJECT = SimpleNamespace(key="APP")
SHA = "a" * 40
BASE = "b" * 40


def _forbidden(name):
    def refuse(self, *args, **kwargs):
        raise AssertionError(f"effect attempted: {name}")
    return refuse


class FakeTracker:
    epic_subgraph_supported = True
    append_only_lifecycle_supported = True
    name = "fake"
    calls = None

    def __init__(self, raw, fail=None):
        self.raw, self.fail, self.calls = raw, fail, []

    def reconcile_issue_creation(self, *args, **kwargs):
        raise AssertionError("effect attempted: reconcile_issue_creation")

    def get_epic_subgraph(self, project, epic, *, depth, nodes):
        self.calls.append("get_epic_subgraph")
        if self.fail:
            raise self.fail
        return copy.deepcopy(self.raw)

    def get_issue(self, issue_id):
        self.calls.append("get_issue")
        return SimpleNamespace(id=issue_id, body="- [ ] done")


for _name in ("create_issue", "update_fields", "add_comment", "transition", "link",
              "record_delivery_receipt", "update_body", "recover_done_projection"):
    setattr(FakeTracker, _name, _forbidden(_name))


class FakeCodeHost:
    name = "fake"

    def __init__(self, prs=(), checks=(), boom=None):
        self.prs, self.checks, self.boom = list(prs), list(checks), boom
        self.calls = []

    def resolve_repo(self, cwd=None):
        return "o/r"

    def list_prs(self, repo, head):
        self.calls.append("list_prs")
        if self.boom:
            raise self.boom
        return list(self.prs)

    def get_pr(self, repo, number):
        self.calls.append("get_pr")
        return next(pr for pr in self.prs if pr.number == number)

    def check_runs(self, repo, sha):
        self.calls.append("check_runs")
        return list(self.checks)

    def commit_statuses(self, repo, sha):
        return []

    def ci_expected(self, repo, sha):
        return False

    open_pr = _forbidden("open_pr")
    update_pr = _forbidden("update_pr")
    merge_pr = _forbidden("merge_pr")
    delete_branch = _forbidden("delete_branch")


class FakeProofs:
    def __init__(self, status="none"):
        self.status = status

    def current_for_sync(self, **kwargs):
        if self.status in ("valid", "blocked"):
            return {"quality": self.status}
        raise RoutingConfigError("absent")

    def valid_for_merge(self, **kwargs):
        if self.status == "valid":
            return {}
        raise RoutingConfigError("not mergeable")


def pr(state="open", merged=False, number=7):
    return PullRequest(
        number=number, url=f"https://x/o/r/pull/{number}", head="feat/app-2-x", base="main",
        base_sha=BASE, sha=SHA, merged=merged, state=state,
    )


def check(conclusion="success", status="completed"):
    return Check(name="ci", status=status, conclusion=conclusion)


def approve(raw, journal, *, expires=NOW + timedelta(hours=2), structure=None, sessions=5,
            per_issue=2):
    digest = structure or structure_digest(validate_subgraph(raw))
    record = {
        "schema_version": ca.APPROVAL_CONTRACT, "epic": "APP-1",
        "preview_contract": "foundry-epic-preview.v1", "preview_digest": "c" * 64,
        "structure_digest": digest, "issued_at": "2026-10-16T10:00:00Z",
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"), "max_sessions": sessions,
        "max_sessions_per_issue": per_issue, "approved_at": "2026-10-16T10:00:00Z",
        "approver": {"kind": ca.APPROVER_KIND},
    }
    record["record_digest"] = ca.record_digest(record)
    ca._append(record, journal)


def run(children, tmp_path, *, deps=None, prs=(), checks=(), proofs="none", refs=(),
        approved=True, tracker=None, codehost=None, raw=None, **extra):
    raw = raw or graph(children, dependencies=deps)
    journal = tmp_path / "journal.jsonl"
    if approved:
        approve(raw, journal)
    tracker = tracker or FakeTracker(raw)
    codehost = codehost or FakeCodeHost(prs, checks)
    obs = cc.observe(
        tracker, codehost, PROJECT, "APP-1", str(tmp_path), NOW, journal=journal,
        refs=lambda root: list(refs), diff_reader=lambda root, base, head: b"diff",
        proof_store=FakeProofs(proofs), **extra,
    )
    return obs, cc.decide(obs), tracker, codehost


def started(**kw):
    return node("APP-2", state="in-progress", **kw)


# One test per ticket state (AC1) -------------------------------------------------------

def test_state_to_start(tmp_path):
    _, decision, _, _ = run([node("APP-2", state="ready")], tmp_path)
    assert (decision.kind, decision.ticket) == ("start", "APP-2")


def test_start_picks_first_eligible_by_numeric_order_with_dependencies_done(tmp_path):
    children = [node("APP-10"), node("APP-9"), node("APP-3", state="done"), node("APP-11")]
    _, decision, _, _ = run(children, tmp_path, deps=[("APP-11", "APP-10"), ("APP-9", "APP-3")])
    assert (decision.kind, decision.ticket) == ("start", "APP-9")


def test_state_started_without_pr_is_implement(tmp_path):
    _, decision, _, _ = run([started()], tmp_path, refs=["feat/app-2-x"])
    assert (decision.kind, decision.ticket, decision.reason) == (
        "implement", "APP-2", "started_without_pr")


def test_implemented_branch_without_pr_is_open_pr(tmp_path):
    kwargs = dict(refs=["feat/app-2-x"], branch_facts=lambda root, branch: (SHA, 2),
                  implemented=lambda ticket, sha: sha == SHA)
    _, decision, _, _ = run([started()], tmp_path, **kwargs)
    assert (decision.kind, decision.ticket, decision.reason) == (
        "open-pr", "APP-2", "implemented_without_pr")
    # no commit beyond the base, or not marked implemented: still `implement`
    kwargs["branch_facts"] = lambda root, branch: (SHA, 0)
    assert run([started()], tmp_path, **kwargs)[1].kind == "implement"
    kwargs.update(branch_facts=lambda root, branch: (SHA, 2), implemented=lambda t, sha: False)
    assert run([started()], tmp_path, **kwargs)[1].kind == "implement"


def test_closed_pr_from_a_list_without_merged_is_re_read(tmp_path):
    """The list endpoint carries no ``merged`` (only ``merged_at``): get_pr decides."""
    class ListWithoutMerged(FakeCodeHost):
        def list_prs(self, repo, head):
            self.calls.append("list_prs")
            return [pr("closed", False)]  # the list says merged=False

        def get_pr(self, repo, number):
            self.calls.append("get_pr")
            return pr("closed", True, number)

    _, decision, _, codehost = run(
        [started()], tmp_path, refs=["feat/app-2-x"], codehost=ListWithoutMerged())
    assert (decision.kind, decision.reason) == ("suspend", "merged_pr_state_behind")
    assert "get_pr" in codehost.calls


def test_started_without_branch_resumes_with_start(tmp_path):
    _, decision, _, _ = run([started()], tmp_path)
    assert (decision.kind, decision.reason) == ("start", "in_progress_branch_absent")


def test_state_review_awaited(tmp_path):
    _, decision, _, _ = run(
        [started()], tmp_path, refs=["feat/app-2-x"], prs=[pr()], proofs="none")
    assert (decision.kind, decision.reason) == ("review", "review_missing")


def test_state_review_blocking_is_correct(tmp_path):
    _, decision, _, _ = run(
        [started()], tmp_path, refs=["feat/app-2-x"], prs=[pr()], proofs="blocked")
    assert (decision.kind, decision.reason) == ("correct", "review_blocked")


def test_state_ci_awaited(tmp_path):
    _, decision, _, _ = run(
        [started()], tmp_path, refs=["feat/app-2-x"], prs=[pr()], proofs="valid",
        checks=[check(None, "in_progress")])
    assert (decision.kind, decision.reason) == ("wait-ci", "ci_pending")


def test_ci_without_any_check_is_not_green(tmp_path):
    _, decision, _, _ = run(
        [started()], tmp_path, refs=["feat/app-2-x"], prs=[pr()], proofs="valid")
    assert (decision.kind, decision.reason) == ("wait-ci", "ci_not_proven")


def test_ci_failing_is_correct(tmp_path):
    _, decision, _, _ = run(
        [started()], tmp_path, refs=["feat/app-2-x"], prs=[pr()], proofs="valid",
        checks=[check("failure")])
    assert (decision.kind, decision.reason) == ("correct", "ci_failing")


def test_ready_to_merge_with_valid_review_and_green_ci(tmp_path):
    _, decision, _, _ = run(
        [started()], tmp_path, refs=["feat/app-2-x"], prs=[pr()], proofs="valid",
        checks=[check()])
    assert (decision.kind, decision.ticket) == ("merge", "APP-2")


def test_state_merged_moves_to_the_next_ticket(tmp_path):
    children = [node("APP-2", state="done"), node("APP-3", state="ready")]
    _, decision, _, codehost = run(children, tmp_path, deps=[("APP-3", "APP-2")])
    assert (decision.kind, decision.ticket) == ("start", "APP-3")
    assert codehost.calls == []  # a finished ticket costs no PR read


def test_epic_verdict_awaited_when_every_ticket_is_terminal(tmp_path):
    children = [node("APP-2", state="done"), node("APP-3", state="dropped")]
    _, decision, _, _ = run(children, tmp_path)
    assert decision.kind == "await-verdict"


def test_merged_pr_with_tracker_not_done_suspends(tmp_path):
    _, decision, _, _ = run(
        [started()], tmp_path, refs=["feat/app-2-x"], prs=[pr("closed", True)])
    assert (decision.kind, decision.reason) == ("suspend", "merged_pr_state_behind")


def test_closed_unmerged_and_ambiguous_prs_suspend(tmp_path):
    _, closed, _, _ = run([started()], tmp_path, refs=["feat/app-2-x"], prs=[pr("closed")])
    assert closed.reason == "pr_closed_unmerged"
    _, many, _, _ = run(
        [started()], tmp_path, refs=["feat/app-2-x"], prs=[pr(number=7), pr(number=8)])
    assert many.reason == "pr_ambiguous"


def test_several_tickets_in_progress_and_nothing_eligible_suspend(tmp_path):
    _, two, _, _ = run([started(), node("APP-3", state="review")], tmp_path)
    assert (two.kind, two.reason) == ("suspend", "several_in_progress")
    _, none, _, _ = run([node("APP-2", state="blocked")], tmp_path)
    assert (none.kind, none.reason) == ("suspend", "no_eligible_ticket")


# Drift (PAT-ADR-0027) ------------------------------------------------------------------

def test_structure_change_suspends(tmp_path):
    old = graph([node("APP-2"), node("APP-3")])
    journal = tmp_path / "journal.jsonl"
    approve(old, journal)
    new = graph([node("APP-2"), node("APP-3")], dependencies=[("APP-3", "APP-2")])
    obs = cc.observe(
        FakeTracker(new), FakeCodeHost(), PROJECT, "APP-1", str(tmp_path), NOW,
        journal=journal, refs=lambda root: [])
    decision = cc.decide(obs)
    assert (decision.kind, decision.reason) == ("suspend", "structure_changed")
    assert decision.evidence["observed"] != decision.evidence["approved"]


def test_advancing_state_and_version_are_not_drift(tmp_path):
    old = graph([node("APP-2"), node("APP-3")])
    journal = tmp_path / "journal.jsonl"
    approve(old, journal)
    new = graph([node("APP-2", state="done", version=9), node("APP-3", version=4)])
    obs = cc.observe(
        FakeTracker(new), FakeCodeHost(), PROJECT, "APP-1", str(tmp_path), NOW,
        journal=journal, refs=lambda root: [])
    assert cc.decide(obs).kind == "start"


def test_unreadable_node_suspends(tmp_path):
    raw = graph([node("APP-2")])
    tracker = FakeTracker(raw, fail=IssueUnavailableError("APP-2 illisible"))
    _, decision, _, _ = run([node("APP-2")], tmp_path, tracker=tracker, raw=raw)
    assert (decision.kind, decision.reason) == ("suspend", "node_unreadable")
    broken = graph([node("APP-2")])
    broken["nodes"][1]["state"] = "weird"
    _, decision, _, _ = run([], tmp_path, raw=broken, tracker=FakeTracker(broken), approved=False)
    assert decision.reason == "node_unreadable"


def test_dependency_state_behind_is_a_regression(tmp_path):
    children = [node("APP-2", state="ready"), node("APP-3", state="in-progress")]
    _, decision, _, _ = run(children, tmp_path, deps=[("APP-3", "APP-2")])
    assert (decision.kind, decision.ticket, decision.reason) == (
        "suspend", "APP-3", "dependency_state_behind")


# Approval is one input of the decision; the observation stays complete -----------------

def test_absent_approval_refuses_but_observes_everything(tmp_path):
    obs, decision, tracker, codehost = run(
        [started()], tmp_path, refs=["feat/app-2-x"], prs=[pr()], proofs="valid",
        checks=[check()], approved=False)
    assert (decision.kind, decision.reason) == ("refuse", "approval_absent")
    assert decision.blocked_step["kind"] == "merge"
    assert obs.approval_status == "absent"
    assert "check_runs" in codehost.calls and "get_epic_subgraph" in tracker.calls


def test_expired_approval_refuses(tmp_path):
    raw = graph([node("APP-2", state="ready")])
    journal = tmp_path / "journal.jsonl"
    approve(raw, journal, expires=NOW - timedelta(minutes=1))
    obs = cc.observe(FakeTracker(raw), FakeCodeHost(), PROJECT, "APP-1", str(tmp_path), NOW,
                     journal=journal, refs=lambda root: [])
    decision = cc.decide(obs)
    assert (decision.kind, decision.reason) == ("refuse", "approval_expired")
    assert decision.blocked_step["kind"] == "start"


def test_invalid_journal_refuses(tmp_path):
    raw = graph([node("APP-2", state="ready")])
    journal = tmp_path / "journal.jsonl"
    journal.write_text("not json\n")
    obs = cc.observe(FakeTracker(raw), FakeCodeHost(), PROJECT, "APP-1", str(tmp_path), NOW,
                     journal=journal, refs=lambda root: [])
    assert cc.decide(obs).reason == "approval_journal_invalid"


# Capabilities ---------------------------------------------------------------------------

class Bare:
    """Declares nothing; any other access fails the test."""
    epic_subgraph_supported = False
    append_only_lifecycle_supported = True

    def __getattribute__(self, name):
        if name in ("epic_subgraph_supported", "append_only_lifecycle_supported"):
            return object.__getattribute__(self, name)
        raise AssertionError(f"read beyond the capability flags: {name}")


def test_missing_capability_refuses_before_any_read(tmp_path):
    codehost = FakeCodeHost()
    obs = cc.observe(Bare(), codehost, PROJECT, "APP-1", str(tmp_path), NOW)
    decision = cc.decide(obs)
    assert (decision.kind, decision.reason) == ("refuse", "capability_missing")
    assert decision.evidence["missing"] == [
        "epic_subgraph_supported", "issue_creation_reconciliation"]
    assert codehost.calls == []


def test_creation_reconciliation_is_the_adapter_override(tmp_path):
    class NoReconcile(FakeTracker):
        reconcile_issue_creation = Tracker.reconcile_issue_creation

    assert cc.missing_capabilities(FakeTracker(graph([]))) == ()
    assert cc.missing_capabilities(NoReconcile(graph([]))) == ("issue_creation_reconciliation",)


def test_linear_declares_the_required_capabilities():
    from foundry.trackers.linear import LinearTracker

    assert LinearTracker.epic_subgraph_supported is True
    assert LinearTracker.append_only_lifecycle_supported is True
    assert LinearTracker.reconcile_issue_creation is not Tracker.reconcile_issue_creation


# Idempotence and no effect ----------------------------------------------------------------

def test_same_observation_gives_byte_identical_output(tmp_path):
    kwargs = dict(refs=["feat/app-2-x"], prs=[pr()], proofs="valid", checks=[check()])
    first = run([started()], tmp_path, **kwargs)
    second = run([started()], tmp_path, **kwargs)
    assert cc.report(first[0], first[1]) == cc.report(second[0], second[1])
    assert cc.decide(first[0]) == cc.decide(first[0])
    json.loads(cc.report(first[0], first[1]))


def test_interrupted_pass_then_rerun_gives_the_same_decision(tmp_path):
    kwargs = dict(refs=["feat/app-2-x"], prs=[pr()], proofs="valid", checks=[check()])
    baseline = run([started()], tmp_path, **kwargs)
    with pytest.raises(RuntimeError):
        run([started()], tmp_path, codehost=FakeCodeHost([pr()], boom=RuntimeError("net")),
            refs=["feat/app-2-x"], proofs="valid")
    again = run([started()], tmp_path, **kwargs)
    assert cc.report(again[0], again[1]) == cc.report(baseline[0], baseline[1])


def test_no_module_effect_primitive_is_referenced():
    source = open(cc.__file__, encoding="utf-8").read()
    for name in ("open_pr", "merge_pr", "update_pr", "delete_branch", "create_issue",
                 "update_fields", "add_comment", "transition", "record_delivery_receipt",
                 "subprocess.Popen", "\"git\", \"checkout\"", "\"push\"", "\"commit\""):
        assert name not in source
    assert source.count("subprocess.run") == 1  # `git for-each-ref`, read-only


# CLI ---------------------------------------------------------------------------------------

def test_cli_refuses_a_tracker_without_capabilities(monkeypatch, capsys, tmp_path):
    import foundry

    monkeypatch.setattr(foundry, "tracker", lambda *a, **k: Bare())
    assert cc.main(["next", "APP-1", "--root", str(tmp_path)]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["decision"]["kind"] == "refuse" and out["contract"] == cc.CADENCE_CONTRACT


def test_cli_next_prints_one_decision(monkeypatch, capsys, tmp_path):
    import foundry

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    raw = graph([node("APP-2", state="ready")])
    tracker = FakeTracker(raw)
    tracker.resolve_checkout_project = lambda root: PROJECT
    monkeypatch.setattr(foundry, "tracker", lambda *a, **k: tracker)
    monkeypatch.setattr(foundry, "codehost", lambda *a, **k: FakeCodeHost())
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "data"))
    assert cc.main(["next", "APP-1", "--root", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"]["kind"] == "refuse"  # no approval in the fresh data dir
    assert out["decision"]["blocked_step"]["kind"] == "start"
    assert out["observation"]["approval"]["status"] == "absent"
