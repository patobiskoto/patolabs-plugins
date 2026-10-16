"""PAT-141: deferral of non-blocking remarks in a campaign; doubles only, no network."""
import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from foundry import campaign_cadence as cc
from foundry import campaign_defer as cd
from foundry import campaign_effects as ce
from foundry import review_remarks
import test_campaign_effects
from test_campaign_effects import (
    CLI, NOW, PROJECT, REPOSITORY, effects, result_text, step,
)
from test_campaign_cadence import check
from test_campaign_review import reviewer_calls, roomy, to_review

env = test_campaign_effects.env  # the shared fixture, under the name pytest looks up

LATER = NOW + timedelta(minutes=5)
RATE_LIMITED = "foundry.trackers.linear.LinearQuotaExhaustedError: Linear issue.create -> 429 (RATELIMITED)"


def reviewed(env, nits=2, fix=0):
    to_review(env)
    roomy(env)
    env.world.reviews.append(result_text(nits=nits, fix=fix))
    assert step(env).decision.kind == "review"


def verbs(env):
    return [v for v, _a, _i in env.world.edits]


def companions(env):
    return [i for i in env.world.issues.values() if i.title.startswith("Nits ")]


def follow_ups(env):
    return [i for i in env.world.issues.values() if i.title.startswith("Review remarks")]


def seed_companion(env, *, follow_state=None, title="Nits APP-1"):
    companion = env.world.add_issue("APP-50", title, "Epic", "backlog")
    if follow_state:
        env.world.add_issue("APP-51", "Review remarks deferred from APP-1", "Task",
                            follow_state, parent="APP-50")
    return companion


def test_a_validated_review_with_remarks_leaves_a_receipt_then_defers_then_merges(env):
    reviewed(env)
    assert env.world.remarks_receipts == 1
    receipt = review_remarks.ReviewRemarksStore(REPOSITORY).find(
        "APP-2", test_campaign_effects.DIFF_HASH)
    assert [r["text"] for r in receipt["remarks"]] == ["nit: remark 0", "nit: remark 1"]
    result = step(env)
    assert (result.decision.kind, result.decision.reason, result.outcome) == (
        "defer-remarks", "non_blocking_remarks", "executed")
    assert verbs(env) == ["create-issue", "create-issue", "comment"]
    (_, (epic_spec,), _), (_, (child_spec,), _), (_, (follow,), note) = env.world.edits
    epic_spec, child_spec = json.loads(epic_spec), json.loads(child_spec)
    assert epic_spec["title"] == "Nits APP-1" and epic_spec["fields"]["Type"] == "Epic"
    assert "parent" not in epic_spec  # no parent, and no relation to the origin Epic
    companion, = companions(env)
    origin = env.world.issues["APP-1"]
    assert all(link.target != companion.id for link in origin.links)
    assert all(link.target != "APP-1" for link in companion.links)
    follow_up, = follow_ups(env)
    assert child_spec["parent"] == companion.id and follow == follow_up.id
    # ONE comment carries every deferrable remark, the PR number, the ticket and the round
    assert "- nit: remark 0" in note and "- nit: remark 1" in note
    assert "Pull request #12" in note and "issue APP-2" in note and "review round 1" in note
    assert len(follow_up.comments) == 1
    # the description cites the follow-up before the merge
    line = env.world.bodies[-1].rstrip().splitlines()[-1]
    assert line == (
        f"Deferred remarks: {follow_up.id} (companion Epic {companion.id}, created by this "
        "pull request; 2 remarks; writes: create companion Epic, create follow-up issue, "
        "comment on the follow-up issue)")
    finished = [r for r in ce.read_attempts(env.kwargs["attempts"])
                if r["kind"] == "defer-remarks" and r["event"] == "finished"][-1]
    assert finished["deferral"]["state"] == "done"
    assert finished["deferral"]["follow_up"] == follow_up.id
    assert [c for c in reviewer_calls(env.world)] and not any(
        c[0][0] == "claude" for c in effects(env.world)[-3:])  # no session launched
    env.world.checks = [check()]
    merge = step(env)
    assert (merge.decision.kind, merge.outcome) == ("merge", "executed")
    assert env.world.states["APP-2"] == "done"


def test_the_intent_is_journaled_before_the_first_write(env):
    reviewed(env)
    calls_before = len(env.world.edits)
    step(env)
    records = ce.read_attempts(env.kwargs["attempts"])
    started = [i for i, r in enumerate(records) if r["kind"] == "defer-remarks"]
    assert records[started[0]]["event"] == "started" and calls_before == 0
    assert records[started[0]]["fingerprint"]["diff_hash"] == test_campaign_effects.DIFF_HASH


def test_an_existing_companion_and_open_follow_up_are_reused(env):
    reviewed(env)
    seed_companion(env, follow_state="backlog")
    assert step(env).outcome == "executed"
    assert verbs(env) == ["comment"] and env.world.edits[0][1] == ["APP-51"]
    assert len(companions(env)) == 1 and len(follow_ups(env)) == 1
    assert env.world.bodies[-1].rstrip().splitlines()[-1] == (
        "Deferred remarks: APP-51 (companion Epic APP-50, already existing; 2 remarks; "
        "writes: comment on the follow-up issue)")


def test_a_started_follow_up_is_never_given_a_remark_a_new_one_is_created(env):
    reviewed(env)
    seed_companion(env, follow_state="in-progress")
    assert step(env).outcome == "executed"
    assert verbs(env) == ["create-issue", "comment"]
    spec = json.loads(env.world.edits[0][1][0])
    assert spec["parent"] == "APP-50"
    assert env.world.issues["APP-51"].comments == []
    assert len(companions(env)) == 1 and len(follow_ups(env)) == 2


def test_two_companions_with_the_same_name_are_a_durable_refusal_then_a_correction(env):
    reviewed(env)
    seed_companion(env)
    env.world.add_issue("APP-60", "Nits APP-1", "Epic", "backlog")
    result = step(env)
    assert (result.decision.kind, result.outcome) == ("defer-remarks", "executed")
    assert result.effect["deferral"]["state"] == "refused"
    assert result.effect["deferral"]["reason"] == "companion_epic_ambiguous"
    assert env.world.edits == []  # no substitute write
    decision = step(env)
    assert (decision.decision.kind, decision.decision.reason) == (
        "correct", "remarks_not_deferrable")
    assert decision.decision.evidence["cause"] == "refused"
    argv, _cwd, _env, prompt = effects(env.world)[-1]
    assert argv[0] == "claude" and "every non-blocking remark" in prompt
    assert "nit: remark 1" in prompt or "AC table" in prompt  # the report is handed over
    assert env.world.edits == []


def test_a_capability_refusal_is_durable(env):
    reviewed(env)
    env.world.create_failure = (
        "foundry.trackers.base.TrackerCapabilityUnavailableError: capability unavailable: "
        "linear.issue-creation")
    result = step(env)
    assert result.effect["deferral"]["state"] == "refused" and result.outcome == "executed"
    assert step(env).decision.reason == "remarks_not_deferrable"


def test_a_durable_refusal_is_told_in_the_description(env):
    reviewed(env)
    env.world.create_failure = (
        "foundry.trackers.base.TrackerCapabilityUnavailableError: capability unavailable: "
        "linear.issue-creation")
    result = step(env)
    assert result.effect["deferral"]["state"] == "refused"
    assert env.world.bodies[-1].rstrip().splitlines()[-1] == (
        "Remarks not deferred: durable refusal (foundry.trackers.base."
        "TrackerCapabilityUnavailableError: capability unavailable: linear.issue-creation); "
        "they are corrected before the merge.")
    assert step(env).decision.reason == "remarks_not_deferrable"
    assert "Remarks not deferred" in env.world.bodies[-1]  # kept by a later rewrite


def test_a_missing_receipt_is_a_durable_refusal(env):
    to_review(env)
    roomy(env)
    env.world.refuse_remarks = True
    env.world.reviews.append(result_text(nits=1))
    first = step(env)
    assert first.effect["detail"] == "remarks_receipt_not_recorded"
    result = step(env)
    assert result.effect["deferral"]["reason"] == "remarks_receipt_missing"
    assert step(env).decision.reason == "remarks_not_deferrable"


def test_a_transient_failure_waits_then_retries_without_a_duplicate(env):
    reviewed(env)
    env.world.create_failure = RATE_LIMITED
    env.world.create_effect_first = True  # the effect is unknown: the issue exists
    result = step(env)
    assert result.outcome == "waiting"
    assert result.effect["deferral"]["state"] == "transient"
    assert len(companions(env)) == 1
    waiting = step(env)  # inside the wait: nothing is written, no correction, no merge
    assert (waiting.decision.kind, waiting.decision.reason, waiting.outcome) == (
        "wait", "deferral_transient_failure", "waiting")
    assert verbs(env) == ["create-issue"]
    retry = step(env, LATER)
    assert (retry.decision.kind, retry.outcome) == ("defer-remarks", "executed")
    assert verbs(env) == ["create-issue", "create-issue", "comment"]  # the companion is reused
    assert len(companions(env)) == 1 and len(follow_ups(env)) == 1
    assert len(follow_ups(env)[0].comments) == 1


def test_a_creation_with_a_named_issue_is_reconciled_before_any_retry(env):
    reviewed(env)
    env.world.create_failure = json.dumps({
        "client_id": "x", "issue_id": "<ID>", "creation": "unknown", "validation": "unknown",
        "reconciliation": "required", "automatic_retry": False})
    env.world.create_effect_first = True
    result = step(env)
    assert result.outcome == "executed"  # reconcile-create accepted the existing issue
    assert verbs(env)[:2] == ["create-issue", "reconcile-create"]
    assert len(companions(env)) == 1 and len(follow_ups(env)) == 1


CLIENT = "0b9c1f3e-5a77-4c1e-9d2b-3f6a8e4d7c10"


def test_an_unnamed_unknown_effect_is_reconciled_by_client_id_before_any_creation(env):
    reviewed(env)
    env.world.create_failure = json.dumps({
        "client_id": CLIENT, "issue_id": None, "reconciliation": "required"})
    env.world.create_effect_first = True  # the issue exists, the answer was lost
    env.world.hide_clients = True  # the read of that client id finds nothing yet
    env.world.search_blind = {"Nits APP-1"}  # nor does the search list the created Epic
    first = step(env)
    assert first.outcome == "waiting"
    assert verbs(env) == ["create-issue", "reconcile-create"]  # tried at once, not yet readable
    again = step(env, LATER)
    assert again.outcome == "waiting" and again.effect["deferral"]["state"] == "transient"
    assert verbs(env).count("create-issue") == 1  # never recreated while the effect is unknown
    journaled = ce.read_attempts(env.kwargs["attempts"])[-1]["deferral"]["unknown"]
    assert journaled == [{"title": "Nits APP-1", "client_id": CLIENT}]
    env.world.clients[CLIENT] = companions(env)[0].id
    final = step(env, LATER + timedelta(minutes=5))
    assert final.outcome == "executed"
    assert verbs(env).count("reconcile-create") >= 2
    assert len(companions(env)) == 1 and len(follow_ups(env)) == 1  # adopted, no duplicate
    assert env.world.bodies[-1].rstrip().splitlines()[-1].startswith(
        f"Deferred remarks: {follow_ups(env)[0].id} (companion Epic {companions(env)[0].id}, "
        "created by this pull request;")


def test_a_quota_error_naming_the_client_id_is_reconciled_by_it(env):
    reviewed(env)
    env.world.create_failure = f"{RATE_LIMITED} client_id={CLIENT} creation=unknown"
    env.world.create_effect_first = True
    assert step(env).outcome == "executed"  # reconciled at once by the client id
    assert verbs(env)[:2] == ["create-issue", "reconcile-create"]
    assert len(companions(env)) == 1 and len(follow_ups(env)) == 1


def test_a_comment_that_went_through_is_not_posted_twice(env):
    reviewed(env)
    env.world.write_failures["comment"] = "ConnectionError: transport_error"
    first = step(env)
    assert first.outcome == "waiting"
    follow_up, = follow_ups(env)
    follow_up.comments.append({"text": "ref", "created": 1})  # the lost answer: it was posted
    ref = f"APP-2@{test_campaign_effects.DIFF_HASH[:16]}"
    follow_up.comments[0]["text"] = f"x {ref} x"
    assert step(env, LATER).outcome == "executed"
    assert verbs(env).count("comment") == 1


def test_an_orphan_follow_up_is_attached_to_the_companion(env):
    reviewed(env)
    seed_companion(env)
    env.world.add_issue("APP-51", "Review remarks deferred from APP-1", "Task", "backlog")
    assert step(env).outcome == "executed"
    assert verbs(env) == ["link", "comment"]
    assert env.world.edits[0][1] == ["APP-51", "subtask-of", "APP-50"]
    assert len(follow_ups(env)) == 1


def test_retries_are_bounded_then_the_campaign_waits_for_a_human(env):
    reviewed(env)
    moment = NOW
    for _ in range(cd.MAX_DEFER_TRIES):
        env.world.search_failure = ConnectionError("transport_error")
        assert step(env, moment).outcome == "waiting"
        moment += timedelta(minutes=5)
    result = step(env, moment)
    assert (result.decision.kind, result.decision.reason) == (
        "suspend", "deferral_retries_exhausted")
    assert env.world.edits == []


def test_a_ticket_of_a_companion_epic_gets_no_deferral_but_a_correction(env):
    env.world.issues["APP-1"].title = "Nits APP-0"
    reviewed(env)
    result = step(env)
    assert (result.decision.kind, result.decision.reason) == (
        "correct", "remarks_not_deferrable")
    assert result.decision.evidence["cause"] == "companion_epic"
    assert env.world.edits == []


def test_a_ticket_without_an_origin_epic_gets_no_deferral_but_a_correction(env):
    env.world.issues["APP-1"].type = "Task"  # the direct parent is not an Epic
    reviewed(env)
    result = step(env)
    assert (result.decision.kind, result.decision.reason) == (
        "correct", "remarks_not_deferrable")
    assert result.decision.evidence["cause"] == "no_origin_epic"
    assert env.world.edits == []


def test_nits_beside_a_fix_before_merge_are_deferred_then_the_correction_and_re_review(env):
    reviewed(env, nits=1, fix=1)
    deferral = step(env)  # the nit of round 1 first: a later review need not list it again
    assert (deferral.decision.kind, deferral.decision.reason) == (
        "defer-remarks", "non_blocking_remarks")
    follow_up, = follow_ups(env)
    assert verbs(env) == ["create-issue", "create-issue", "comment"]
    assert "- nit: remark 0" in follow_up.comments[0]["text"]
    correction = step(env)
    assert (correction.decision.kind, correction.decision.reason) == (
        "correct", "review_fix_before_merge")
    assert step(env).decision.reason == "correction_not_pushed"
    env.world.reviews.append(result_text())  # the partial review lists no remark
    assert step(env).decision.kind == "review"
    env.world.checks = [check()]
    assert step(env).decision.kind == "merge"
    assert verbs(env) == ["create-issue", "create-issue", "comment"]  # nothing deferred twice


def test_nits_that_cannot_be_deferred_are_corrected_with_the_fix_before_merge_ones(env):
    env.world.issues["APP-1"].type = "Task"
    reviewed(env, nits=1, fix=1)
    result = step(env)
    assert (result.decision.kind, result.decision.reason) == (
        "correct", "remarks_not_deferrable")
    assert env.world.edits == []


def test_decide_is_pure_over_the_deferral_facts():
    pr = cc.PullFacts(5, "open", False, "a" * 40, "b" * 40)

    def ticket(**deferral):
        return cc.TicketFacts(
            id="APP-2", state="review", category="eligible", reason="", branch=True,
            branch_sha="a" * 40, pr=pr, review="valid", ci="green",
            deferral=cc.DeferralFacts(deferrable=1, origin="APP-1", **deferral))

    assert cc._ticket_step(ticket()).kind == "defer-remarks"
    assert cc._ticket_step(ticket(state="done")).kind == "merge"
    assert cc._ticket_step(ticket(state="transient", cooling=True)).kind == "wait"
    assert cc._ticket_step(ticket(state="transient")).kind == "defer-remarks"
    assert cc._ticket_step(ticket(state="refused")).reason == "remarks_not_deferrable"
    assert cc._ticket_step(ticket(state="exhausted")).reason == "deferral_retries_exhausted"
    clean = cc.TicketFacts(**{**ticket().__dict__, "deferral": cc.DeferralFacts()})
    assert cc._ticket_step(clean).kind == "merge"


def test_classification_of_failures():
    assert cd.classify("TrackerCapabilityUnavailableError: capability unavailable: x").state == "refused"
    assert cd.classify("LinearBindingError: type_unmapped").state == "refused"
    assert cd.classify(RATE_LIMITED).state == "transient"
    assert cd.classify("ConnectionError: transport_error").state == "transient"
    assert cd.classify("").state == "transient"


def test_the_journal_deferral_field_is_a_closed_shape(env):
    reviewed(env)
    step(env)
    path = env.kwargs["attempts"]
    lines = path.read_text().splitlines()
    record = json.loads(lines[-1])
    assert record["kind"] == "defer-remarks" and cd.valid_deferral_field(record["deferral"])
    bad = {**record, "deferral": {**record["deferral"], "extra": 1}}
    path.write_text("\n".join(lines[:-1] + [json.dumps(bad)]) + "\n")
    with pytest.raises(ce.AttemptJournalInvalid):
        ce.read_attempts(path)


def test_effect_commands_are_foundry_edit_subprocesses_in_the_ticket_worktree(env):
    reviewed(env)
    step(env)
    edits = [c for c in effects(env.world) if c[0][:3] == [sys.executable, CLI, "edit"]]
    assert len(edits) == 3
    assert {c[1] for c in edits} == {str(env.tmp / "wt" / "APP-2")}


def test_the_new_follow_up_does_not_change_the_approved_structure(env):
    """AC3: the approved digest is over the Epic's subgraph; the companion is outside it."""
    from foundry.epic_preview import structure_digest, validate_subgraph
    from foundry.epic_subgraph import build_neutral_subgraph

    reviewed(env)
    tracker = _NeutralTracker(env.world)

    def digest():
        return structure_digest(validate_subgraph(
            build_neutral_subgraph(tracker, PROJECT, "APP-1"), expected_epic_id="APP-1"))

    before = digest()
    assert env.kwargs["journal"].exists()
    step(env)
    assert len(companions(env)) == 1 and len(follow_ups(env)) == 1
    assert digest() == before
    # the pass that follows still finds the approval valid: the merge is not blocked
    env.world.checks = [check()]
    assert step(env).decision.kind == "merge"


def test_the_origin_epic_closure_graph_ignores_the_companion(env):
    from foundry import write

    reviewed(env)
    tracker = _NeutralTracker(env.world)
    for key in ("APP-2", "APP-3"):
        env.world.issues[key].state = "done"
    origin = tracker.get_issue("APP-1")
    before = write.bounded_epic_graph_snapshot(tracker, PROJECT, origin)
    step(env)
    env.world.issues["APP-2"].state = "done"
    after = write.bounded_epic_graph_snapshot(tracker, PROJECT, tracker.get_issue("APP-1"))
    assert before == after and len(follow_ups(env)) == 1


class _NeutralTracker:
    """Real ``models.Issue`` objects over the same issues as the world, read only."""

    def __init__(self, world):
        self.world = world

    def validate_issue_binding(self, project, *ids):
        for issue_id in ids:
            assert issue_id in self.world.issues

    def get_issue(self, issue_id):
        from foundry.models import Issue, Link

        raw = self.world.issues[issue_id]
        return Issue(
            id=raw.id, title=raw.title, state=raw.state, type=raw.type, body=raw.body,
            links=[Link(k.type, k.direction, k.target) for k in raw.links],
            ac_done=1 if raw.state == "done" else 0, ac_total=1, version=3,
            acceptance_status="accepted" if raw.state == "done" else None,
            acceptance_source="review-proof", acceptance_coordinates="proof")


def test_the_merge_gate_never_reads_the_receipt():
    root = Path(ce.__file__).resolve().parent
    for name in ("issue.py", "write.py"):
        assert "review_remarks" not in (root / name).read_text(encoding="utf-8"), name
    import inspect

    from foundry.routing import AcceptanceProofStore

    for method in (AcceptanceProofStore.valid_for_merge, AcceptanceProofStore.current_for_sync):
        assert "review_remarks" not in inspect.getsource(method)
    # a receipt that is absent, corrupted or different changes nothing for the gate: the gate
    # code path has no reference to it at all


def test_the_proof_is_unchanged_by_the_receipt(env, tmp_path):
    reviewed(env)
    data = Path(env.tmp / "data")
    assert (data / review_remarks.STORE_DIR).is_dir()
    assert not (data / "acceptance-proofs").exists()  # the double wrote no proof: separate store


def test_a_carried_proof_does_not_defer_the_same_remarks_twice(env):
    reviewed(env)
    assert step(env).decision.kind == "defer-remarks"
    pr = env.world.prs[-1]  # main moved: new base and head, the proof is carried
    pr.base_sha, pr.sha = "9" * 40, "7" * 40
    env.world.heads["APP-2"] = "7" * 40
    env.world.proof, env.world.carry_ok = "none", True
    carried = step(env)
    assert carried.decision.kind == "review" and carried.effect["review"]["carried"] is True
    assert f"Deferred remarks: {follow_ups(env)[0].id} (" in env.world.bodies[-1]
    env.world.checks = [check()]
    assert step(env).decision.kind == "merge"
    assert verbs(env) == ["create-issue", "create-issue", "comment"]
