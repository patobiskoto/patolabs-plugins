"""PAT-218: review and correction in a campaign; doubles only, no network, no real claude."""
import json
import os
import sys
from pathlib import Path

import pytest

from foundry import campaign_cadence as cc
from foundry import campaign_effects as ce
from foundry import campaign_review as cr
from test_campaign_cadence import approve, check
import test_campaign_effects
from test_campaign_effects import (
    CLAIM_ID, CLI, CRITERION, DIFF_HASH, NOW, PROJECT, effects, result_text, step,
)
from test_epic_preview import graph, node

env = test_campaign_effects.env  # the shared fixture, under the name pytest looks up

TWO = graph([node("APP-2"), node("APP-3")], dependencies=[("APP-3", "APP-2")])


def roomy(env):
    """An approval whose ceilings fit a whole review loop (the fixture's allows 2 per issue)."""
    journal = env.tmp / "roomy.jsonl"
    approve(TWO, journal, sessions=20, per_issue=10)
    env.kwargs["journal"] = journal


def to_review(env):
    for _ in range(3):  # start, implement, open-pr
        assert step(env).outcome == "executed"


def routing_verbs(world):
    return [c[0][3] for c in world.calls if c[0][:1] == [sys.executable] and c[0][2] == "routing"]


def reviewer_calls(world):
    return [c for c in world.calls if c[0][0] == "claude" and "--tools" in c[0]]


def journal_text(env):
    return env.kwargs["attempts"].read_text()


def test_review_claims_packets_runs_a_read_only_reviewer_and_records_the_proof(env):
    to_review(env)
    env.world.reviews.append(result_text(fix=0, nits=2))
    result = step(env)
    assert (result.decision.kind, result.outcome) == ("review", "executed")
    assert routing_verbs(env.world) == ["claim-review", "prepare-review", "record-review-proof",
                                        "record-review-remarks"]
    claim = next(c[0] for c in env.world.calls if c[0][3:4] == ["claim-review"])
    assert claim[claim.index("--base") + 1] == "b" * 40 and "--git-diff" in claim
    assert len(claim[claim.index("--claim-attempt-token") + 1]) == 64
    (argv, cwd, child_env, prompt), = reviewer_calls(env.world)
    assert cwd == str((env.tmp / "wt" / "APP-2").resolve())
    assert env.world.proofs == [{"outcomes": [{**CRITERION, "verdict": "pass"}],
                                 "quality": "mergeable"}]
    assert env.world.proof == "valid"
    # the effect output exposes the verdict and the counts; the remarks are in the receipt
    assert result.effect["review"]["verdict"] == "validated"
    assert (result.effect["review"]["fix_before_merge"], result.effect["review"]["non_blocking"]) == (0, 2)
    assert Path(result.effect["report_file"]).read_text().startswith("## AC table")
    assert oct(Path(result.effect["report_file"]).stat().st_mode & 0o777) == "0o600"
    # the packet and the claim file are removed once the claim is terminal
    assert not list((env.kwargs["attempts"].parent / cr.REVIEWS_DIR).glob("*/*"))
    assert "FULL review" in prompt and cr.RESULT_SENTINEL in prompt
    assert "routing read-review --diff-hash " + DIFF_HASH in prompt
    assert "Stage 1" in prompt and "fix before merge" in prompt


def test_reviewer_argv_is_routed_at_the_gate_floor_and_read_only(env):
    to_review(env)
    env.world.reviews.append(result_text())
    step(env)
    (argv, _cwd, child_env, _prompt), = reviewer_calls(env.world)
    from foundry.routing import RoutingPolicy
    from foundry.routing_facades import claude_effort_parameters, claude_invocation_model

    policy = RoutingPolicy.load(env.runtime.root)
    route = policy.resolve("reviewer", "claude")
    assert route.gate_floor == "frontier" and route.gate_effort_floor == "high"
    model = claude_invocation_model(route.model, project_models=policy.claude_models)
    assert argv[argv.index("--model") + 1] == model
    effort = claude_effort_parameters(route, invocation_model=model)["transmitted"]
    assert effort in ("high", "xhigh", "max")
    assert argv[argv.index("--effort") + 1] == effort
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob,Bash"
    allowed = argv[argv.index("--allowedTools") + 1:]
    assert allowed[:3] == ["Read", "Grep", "Glob"]
    assert sorted(allowed[3:]) == sorted(
        f"Bash(python3 {CLI} routing {verb} *)"
        for verb in ("read-review", "search-review", "list-review"))
    flat = " ".join(argv)
    for forbidden in ("Edit", "Write", "NotebookEdit", "WebFetch", "bypassPermissions",
                      "--dangerously", "acceptEdits", "auto"):
        assert forbidden not in flat.replace("--output-format", "")
    assert set(child_env) <= {"HOME", "LANG", "LC_ALL", "LOGNAME", "PATH", "TMPDIR", "USER",
                              "FOUNDRY_RUNTIME_CONFIG_ISOLATED", "FOUNDRY_DATA"}
    # the claim lives under registry.data_dir(): the reviewer's read verbs must find it
    assert child_env["FOUNDRY_DATA"] == str(env.tmp / "data")
    assert argv[argv.index("--max-budget-usd") + 1] == "5.00"
    # the worktree's own .claude settings, hooks and MCP servers are not loaded
    assert argv[argv.index("--setting-sources") + 1] == "user"
    assert "--strict-mcp-config" in argv
    assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert argv.index("--setting-sources") < argv.index("--allowedTools")


def test_foundry_data_is_forwarded_to_the_reviewer_only_when_set(env, monkeypatch):
    to_review(env)
    monkeypatch.delenv("FOUNDRY_DATA")
    env.world.reviews.append(result_text())
    step(env)
    (_argv, _cwd, child_env, _prompt), = reviewer_calls(env.world)
    assert "FOUNDRY_DATA" not in child_env


def test_the_reviewer_prompt_says_its_output_contract_overrides_the_packet_text(env):
    to_review(env)
    env.world.reviews.append(result_text())
    step(env)
    prompt = reviewer_calls(env.world)[-1][3]
    assert prompt.rindex("overrides the packet instructions") > prompt.index(
        "Run the independent Foundry two-stage review")
    assert "do not load the `review-pr` skill" in prompt


def test_the_pr_description_records_the_review_rounds_and_the_partial_reliance(env):
    to_review(env)
    roomy(env)
    env.world.reviews += [result_text(fix=1, nits=0), result_text()]
    step(env)  # round 1: validated with a remark to fix
    first, = env.world.bodies
    assert first.startswith("## Summary\nAPP-2")  # the saved implementation summary is kept
    assert "- Round 1: full review, verdict validated, head `" in first and "partial" not in first
    assert effects(env.world)[-1][0] == [sys.executable, CLI, "issue", "openpr", "APP-2",
                                         "--summary-stdin"]
    for _ in range(3):  # correct, open-pr, review (partial)
        step(env)
    last = env.world.bodies[-1]
    assert "- Round 1: full review" in last and "- Round 2: partial review" in last
    assert "the proof relies on the previous round for the unchanged parts" in last
    assert last.count("## Review rounds") == 1


def test_without_a_saved_summary_the_description_is_only_the_rounds_section():
    body = cr.pr_body(None, [], "E-1", "APP-2", {
        "verdict": "blocked", "carried": False, "partial": False, "head": "a" * 40})
    assert body == "## Review rounds\n- Round 1: full review, verdict blocked, head `aaaaaaa`."


def test_a_failed_description_update_keeps_the_recorded_verdict(env):
    to_review(env)
    runner = env.world.runner

    def failing(argv, **kw):
        done = runner(argv, **kw)
        if argv[3:5] == ["openpr", "APP-2"] and "--summary-stdin" in argv:
            done.returncode = 1
        return done

    env.runtime.runner = failing
    env.world.reviews.append(result_text())
    result = step(env)
    assert (result.outcome, result.effect["detail"]) == ("executed", "pr_body_not_updated")
    assert result.effect["review"]["verdict"] == "validated"


def test_malformed_reviewer_output_records_no_proof_and_is_not_replayed(env):
    to_review(env)
    env.world.reviews.append("looks fine to me, no structured verdict")
    result = step(env)
    assert (result.outcome, result.effect["status"]) == ("failed", "failed")
    assert "record-review-proof" not in routing_verbs(env.world)
    assert env.world.proofs == []
    finished = [r for r in ce.read_attempts(env.kwargs["attempts"]) if r["kind"] == "review"][-1]
    assert finished["error"] == "reviewer_output_invalid" and "review" not in finished
    again = step(env)  # same fingerprint: an attempt is never replayed
    assert (again.decision.kind, again.decision.reason) == ("suspend", "attempt_already_made")


@pytest.mark.parametrize("text", [
    "report\nNOT_THE_MARKER\n{}",
    "report\n" + cr.RESULT_SENTINEL + "\n{not json}",
    "report\n" + cr.RESULT_SENTINEL + "\n" + json.dumps({"outcomes": [], "quality": "mergeable"}),
    "report\n" + cr.RESULT_SENTINEL + "\n[]",
    cr.RESULT_SENTINEL + "\n{}",  # empty report
])
def test_parse_result_refuses_what_is_not_the_closed_schema(text):
    with pytest.raises(cr.ReviewOutputInvalid):
        cr.parse_result(text)


def test_parse_result_is_strict_about_every_field():
    good = json.loads(result_text().split(cr.RESULT_SENTINEL + "\n")[1])
    assert cr.parse_result(result_text())[1] == good

    def bad(**change):
        data = {**good, **change}
        with pytest.raises(cr.ReviewOutputInvalid):
            cr.parse_result("r\n" + cr.RESULT_SENTINEL + "\n" + json.dumps(data))

    bad(extra=1)
    bad(quality="ok")
    bad(fix_before_merge=True)
    bad(non_blocking=-1)
    bad(outcomes=[{"id": "ac-1", "digest": "short", "verdict": "pass"}])
    bad(outcomes=[{"id": "ac-1", "digest": "a" * 64, "verdict": "maybe"}])
    bad(outcomes=[{"id": "ac-1", "digest": "a" * 64, "verdict": "pass", "x": 1}])
    bad(outcomes=[{"id": "ac-1", "digest": "a" * 64, "verdict": "fail"}])  # mergeable + fail
    with pytest.raises(cr.ReviewOutputInvalid):  # the marker twice
        cr.parse_result("r\n" + cr.RESULT_SENTINEL + "\n" + cr.RESULT_SENTINEL + "\n{}")
    fenced = "r\n" + cr.RESULT_SENTINEL + "\n```json\n" + json.dumps(good) + "\n```"
    assert cr.parse_result(fenced)[1] == good


def test_blocking_review_escalates_and_the_correction_session_gets_the_findings(env):
    to_review(env)
    roomy(env)
    findings = "BLOCKING: the sort is not stable (campaign_review.py:42)"
    env.world.reviews.append(result_text(
        quality="blocked", verdict="fail", report=f"## AC\n{findings}"))
    assert step(env).outcome == "executed"
    assert env.world.proof == "blocked"
    result = step(env)
    assert (result.decision.kind, result.decision.reason, result.outcome) == (
        "correct", "review_blocked", "executed")
    escalation, = env.world.escalations
    assert escalation[3:7] == ["escalation", "failure", "APP-2", "implementer"]
    assert escalation[escalation.index("--kind") + 1] == "review_blocking"
    from foundry.routing import RoutingPolicy

    # The tier sent excludes the escalation floor, so a replay after `escalated` is identical.
    assert escalation[escalation.index("--current-tier") + 1] == RoutingPolicy.load(
        env.runtime.root).resolve("implementer", "claude").selected_tier
    assert "--idempotency-key" in escalation and "--base" in escalation
    argv, cwd, _env, prompt = effects(env.world)[-1]
    assert argv[0] == "claude" and "--tools" not in argv  # the implementer session
    assert findings in prompt and "<review>" in prompt
    assert "AGENTS.md#R9(c)" in prompt and "as few sentences as possible" in prompt
    assert '<issue id="APP-2">' in prompt
    assert "never push" in prompt
    # the commit is pushed through `issue openpr` (no summary: the PR body is kept)
    push = step(env)
    assert (push.decision.kind, push.decision.reason) == ("open-pr", "correction_not_pushed")
    assert effects(env.world)[-1][0] == [sys.executable, CLI, "issue", "openpr", "APP-2"]
    # after a blocking round the review is full
    env.world.reviews.append(result_text())
    again = step(env)
    assert again.decision.kind == "review" and "FULL review" in reviewer_calls(env.world)[-1][3]
    assert again.effect["review"]["partial"] is False


def test_second_blocking_review_uses_the_after_fix_kind(env):
    to_review(env)
    roomy(env)
    env.world.reviews += [result_text(quality="blocked", verdict="fail")] * 2
    for _ in range(4):  # review, correct, open-pr, review
        step(env)
    step(env)  # correct again
    kinds = [e[e.index("--kind") + 1] for e in env.world.escalations]
    assert kinds == ["review_blocking", "review_blocking_after_fix"]
    keys = [e[e.index("--idempotency-key") + 1] for e in env.world.escalations]
    assert len(set(keys)) == 2 and all(k.startswith("campaign-APP-1-APP-2-") for k in keys)


def _escalations(env):
    return getattr(env.world, "escalations", [])


def test_a_failed_correction_session_does_not_make_the_next_signal_after_fix(env):
    to_review(env)
    roomy(env)
    env.world.reviews.append(result_text(quality="blocked", verdict="fail"))
    step(env)
    env.world.session_commits = False  # the correction session ends without a commit
    assert step(env).decision.kind == "correct"
    # the same head: the attempt exists, nothing new is signalled (and nothing is relaunched)
    again = step(env)
    assert (again.decision.kind, again.decision.reason) == ("suspend", "attempt_already_made")
    kinds = [e[e.index("--kind") + 1] for e in _escalations(env)]
    assert kinds == ["review_blocking"]


def test_escalation_is_not_signalled_when_the_claim_would_refuse(env):
    to_review(env)
    env.world.reviews.append(result_text(quality="blocked", verdict="fail"))
    step(env)
    ticket = cc.observe(env.tracker, env.codehost, PROJECT, "APP-1", env.runtime.root, NOW,
                        journal=env.kwargs["journal"], refs=env.kwargs["refs"],
                        branch_facts=env.kwargs["branch_facts"],
                        diff_reader=env.kwargs["diff_reader"],
                        proof_store=env.kwargs["proof_store"]).tickets
    ticket = next(t for t in ticket if t.id == "APP-2")
    attempts = env.kwargs["attempts"]
    decision = cc.Decision("correct", "APP-2", "review_blocked")
    identity = ce.identity("APP-1", "APP-2", "correct", ce.fingerprint(ticket))
    # a session ceiling already reached: refused before any escalation write
    plan = cr.plan_correct(env.runtime, decision, "APP-1", ticket, attempts, env.tracker, (0, None))
    assert plan["refuse"][0] == "session_ceiling_reached"
    assert _escalations(env) == []
    ce.claim(attempts, {
        "schema_version": ce.EFFECTS_CONTRACT, "event": "started", "attempt": identity,
        "epic": "APP-1", "ticket": "APP-2", "kind": "correct",
        "fingerprint": ce.fingerprint(ticket), "session_id": None, "route": None,
        "at": "2026-01-01T00:00:00Z"}, None)
    plan = cr.plan_correct(env.runtime, decision, "APP-1", ticket, attempts, env.tracker, None)
    assert plan["suspend"][0] == "attempt_already_made" and _escalations(env) == []


def test_an_escalation_replay_with_an_active_floor_sends_the_same_signal(env, monkeypatch):
    """A floor raised by `escalated` must not change the tier sent on a replay (same identity)."""
    from foundry.escalation import EscalationStore
    from foundry.routing import LEVELS, RoutingPolicy

    to_review(env)
    env.world.reviews.append(result_text(quality="blocked", verdict="fail"))
    step(env)
    ticket = next(t for t in cc.observe(
        env.tracker, env.codehost, PROJECT, "APP-1", env.runtime.root, NOW,
        journal=env.kwargs["journal"], refs=env.kwargs["refs"],
        branch_facts=env.kwargs["branch_facts"], diff_reader=env.kwargs["diff_reader"],
        proof_store=env.kwargs["proof_store"]).tickets if t.id == "APP-2")
    decision = cc.Decision("correct", "APP-2", "review_blocked")
    base = RoutingPolicy.load(env.runtime.root).resolve("implementer", "claude").selected_tier
    higher = LEVELS[min(LEVELS.index(base) + 1, len(LEVELS) - 1)]
    floors = iter([None, higher])  # first call: no floor; replay: the floor `escalated` raised
    monkeypatch.setattr(EscalationStore, "active_floor",
                        lambda self, issue, role: next(floors) if role == "implementer" else None)
    attempts = env.kwargs["attempts"]
    cr.plan_correct(env.runtime, decision, "APP-1", ticket, attempts, env.tracker, None)
    cr.plan_correct(env.runtime, decision, "APP-1", ticket, attempts, env.tracker, None)
    first, replay = _escalations(env)
    assert first == replay
    assert replay[replay.index("--current-tier") + 1] == base


def test_a_halted_state_that_is_not_a_continue_action_launches_no_session(env, monkeypatch):
    to_review(env)
    roomy(env)
    env.world.reviews.append(result_text(quality="blocked", verdict="fail"))
    step(env)
    original = type(env.world).routing

    def ambiguous(self, argv, cwd, ok):
        if argv[3] == "escalation":
            self.escalations = getattr(self, "escalations", []) + [argv]
            return ok(json.dumps({"human_required": False, "action": "authority_ambiguous",
                                  "final_tier": "frontier"}))
        return original(self, argv, cwd, ok)

    monkeypatch.setattr(type(env.world), "routing", ambiguous)
    sessions = len([c for c in env.world.calls if c[0][0] == "claude"])
    result = step(env)
    assert (result.decision.kind, result.decision.reason, result.outcome) == (
        "suspend", "authority_ambiguous", "stopped")
    assert len([c for c in env.world.calls if c[0][0] == "claude"]) == sessions
    assert set(cr.CONTINUE_ACTIONS) == {"failure_recorded", "escalated", "remediation_continued"}


def test_human_required_suspends_without_a_session(env):
    to_review(env)
    roomy(env)
    env.world.reviews.append(result_text(quality="blocked", verdict="fail"))
    step(env)
    env.world.human_required = True
    sessions = len([c for c in env.world.calls if c[0][0] == "claude"])
    result = step(env)
    assert (result.decision.kind, result.decision.reason, result.outcome) == (
        "suspend", "human_required", "stopped")
    assert len([c for c in env.world.calls if c[0][0] == "claude"]) == sessions
    started = [r for r in ce.read_attempts(env.kwargs["attempts"])
               if r["event"] == "started" and r["kind"] == "correct"]
    assert started == []  # nothing was claimed: the escalation write is idempotent


def test_the_loop_is_bounded_then_the_campaign_waits_for_a_human(env):
    to_review(env)
    roomy(env)
    env.world.reviews += [result_text(quality="blocked", verdict="fail")] * 5
    kinds = []
    for _ in range(12):
        result = step(env)
        kinds.append(result.decision.kind)
        if result.outcome == "stopped":
            break
    assert kinds == ["review", "correct", "open-pr", "review", "correct", "open-pr", "review",
                     "suspend"]
    assert (result.decision.reason, result.decision.evidence["max_corrections"]) == (
        "corrections_exhausted", cc.MAX_CORRECTIONS)
    assert cc.MAX_REVIEW_ROUNDS == 3 and cc.MAX_CORRECTIONS == 2
    sessions = [r for r in ce.read_attempts(env.kwargs["attempts"])
                if r["event"] == "started" and r["kind"] in ce.SESSION_KINDS]
    assert len(sessions) == 1 + 3 + 2  # implement, three reviews, two corrections


def test_review_rounds_exhausted_is_a_pure_decision():
    ticket = cc.TicketFacts(
        id="APP-2", state="review", category="eligible", reason="", branch=True,
        branch_sha="a" * 40, pr=cc.PullFacts(5, "open", False, "a" * 40, "b" * 40),
        review="none", review_rounds=cc.MAX_REVIEW_ROUNDS)
    decision = cc._ticket_step(ticket)
    assert (decision.kind, decision.reason) == ("suspend", "review_rounds_exhausted")
    below = cc._ticket_step(cc.TicketFacts(**{**ticket.__dict__, "review_rounds": 2}))
    assert below.kind == "review"
    valid = cc.TicketFacts(**{**ticket.__dict__, "review": "valid", "ci": "green",
                              "fix_before_merge": 1})
    assert cc._ticket_step(valid).reason == "review_fix_before_merge"
    spent = cc.TicketFacts(**{**valid.__dict__, "corrections": cc.MAX_CORRECTIONS})
    assert cc._ticket_step(spent).reason == "corrections_exhausted"
    clean = cc.TicketFacts(**{**valid.__dict__, "fix_before_merge": 0})
    assert cc._ticket_step(clean).kind == "merge"


def test_session_ceilings_count_reviewer_and_correction_sessions(env):
    to_review(env)  # the approval allows 2 sessions per issue: implement is the first
    env.world.reviews.append(result_text(quality="blocked", verdict="fail"))
    assert step(env).outcome == "executed"  # review: the second session
    result = step(env)
    assert (result.decision.kind, result.decision.reason) == (
        "refuse", "session_ceiling_per_issue_reached")
    assert len([c for c in env.world.calls if c[0][0] == "claude"]) == 2


def test_a_pushed_correction_is_an_open_pr_decision_not_a_review():
    pr = cc.PullFacts(5, "open", False, "a" * 40, "b" * 40)
    ticket = cc.TicketFacts(id="APP-2", state="review", category="eligible", reason="",
                            branch=True, branch_sha="c" * 40, implemented=True, pr=pr,
                            review="blocked")
    decision = cc._ticket_step(ticket)
    assert (decision.kind, decision.reason) == ("open-pr", "correction_not_pushed")
    pushed = cc.TicketFacts(**{**ticket.__dict__, "branch_sha": "a" * 40})
    assert cc._ticket_step(pushed).kind == "correct"


def test_remarks_to_fix_then_a_partial_review_with_the_previous_round(env):
    to_review(env)
    roomy(env)
    env.world.reviews.append(result_text(fix=1, nits=1, report="## AC\nNIT fix before merge: typo"))
    first = step(env)
    assert first.effect["review"]["fix_before_merge"] == 1
    assert step(env).decision.kind == "defer-remarks"  # the nit comes first (PAT-141)
    result = step(env)
    assert (result.decision.kind, result.decision.reason) == ("correct", "review_fix_before_merge")
    assert getattr(env.world, "escalations", []) == []  # a non-blocking verdict touches no state
    assert "NIT fix before merge: typo" in effects(env.world)[-1][3]
    assert step(env).decision.reason == "correction_not_pushed"
    previous_head = ce.read_attempts(env.kwargs["attempts"])
    previous_head = [r for r in previous_head if r["kind"] == "review"][-1]["review"]["head"]
    env.world.reviews.append(result_text())
    partial = step(env)
    assert partial.decision.kind == "review" and partial.effect["review"]["partial"] is True
    prompt = reviewer_calls(env.world)[-1][3]
    new_head = env.world.prs[-1].sha
    assert "PARTIAL REVIEW (PAT-ADR-0022)" in prompt and f"{previous_head}..{new_head}" in prompt
    assert "ALL criteria for the exact current diff" in prompt
    declared = env.world.declarations
    assert declared["partial_review"] is True and declared["commit_range"] == f"{previous_head}..{new_head}"
    assert "typo" in declared["previous_report"] and declared["previous_fix_before_merge"] == 1


def test_a_clean_integration_of_main_carries_the_proof_without_a_session(env):
    to_review(env)
    roomy(env)
    env.world.reviews.append(result_text())
    step(env)
    sessions = len([c for c in env.world.calls if c[0][0] == "claude"])
    pr = env.world.prs[-1]  # main moved: new base, new head, no proof for it yet
    pr.base_sha, pr.sha = "9" * 40, "7" * 40
    env.world.heads["APP-2"] = "7" * 40
    env.world.proof, env.world.carry_ok = "none", True
    result = step(env)
    assert (result.decision.kind, result.outcome) == ("review", "executed")
    carry, = env.world.carried
    assert carry[3] == "carry-review-proof"
    assert carry[carry.index("--from-base") + 1] == "b" * 40
    assert carry[carry.index("--base") + 1] == "9" * 40
    assert carry[carry.index("--from-diff-hash") + 1] == DIFF_HASH
    assert len([c for c in env.world.calls if c[0][0] == "claude"]) == sessions
    assert result.effect["review"]["carried"] is True
    # a carried review is not a review round
    assert cr.history(env.kwargs["attempts"], "APP-1")("APP-2", "7" * 40, "9" * 40) == (0, 1, 0)
    assert step(env).decision.kind in ("wait-ci", "merge")


def test_a_refused_carry_means_a_full_review(env):
    to_review(env)
    roomy(env)
    env.world.reviews += [result_text(), result_text()]
    step(env)
    pr = env.world.prs[-1]
    pr.base_sha, pr.sha = "9" * 40, "7" * 40
    env.world.heads["APP-2"] = "7" * 40
    env.world.proof = "none"  # carry_ok stays False
    result = step(env)
    assert len(env.world.carried) == 1
    assert result.effect["review"]["carried"] is False
    assert len(reviewer_calls(env.world)) == 2 and "FULL review" in reviewer_calls(env.world)[-1][3]


def test_no_carry_is_tried_after_a_correction_or_with_remarks_to_fix():
    def done(kind, head, **review):
        review = {"verdict": "validated", **review} if kind == "review" else review
        record = {"event": "finished", "epic": "E", "ticket": "T-1", "kind": kind,
                  "status": "completed", "branch_head": head, "summary_file": None}
        if review:
            record["review"] = {"verdict": "validated", "fix_before_merge": 0, "non_blocking": 0,
                                "diff_hash": "d" * 64, "base": "b" * 40, "head": head,
                                "carried": False, "partial": False, **review}
        return record

    clean = [done("review", "1" * 40)]
    assert cr.review_mode(clean, "E", "T-1", "2" * 40, "c" * 40)["mode"] == "carry"
    assert cr.review_mode(clean, "E", "T-1", "2" * 40, "b" * 40)["mode"] == "full"  # same base
    marked = [done("review", "1" * 40, fix_before_merge=1)]
    assert cr.review_mode(marked, "E", "T-1", "2" * 40, "c" * 40)["mode"] == "full"
    corrected = clean + [done("correct", "2" * 40)]
    assert cr.review_mode(corrected, "E", "T-1", "2" * 40, "b" * 40)["mode"] == "partial"
    assert cr.review_mode(corrected, "E", "T-1", "3" * 40, "b" * 40)["mode"] == "full"
    blocked = [done("review", "1" * 40, verdict="blocked")]
    assert cr.review_mode(blocked, "E", "T-1", "2" * 40, "c" * 40)["mode"] == "full"


def test_the_claim_capability_never_reaches_the_journal_or_the_output(env, capsys):
    to_review(env)
    env.world.reviews.append(
        f"## AC\nthe claim {CLAIM_ID} must stay private\n" + result_text().split("\n", 1)[1])
    result = step(env)
    token = next(c[0] for c in env.world.calls if c[0][3:4] == ["claim-review"])
    token = token[token.index("--claim-attempt-token") + 1]
    assert CLAIM_ID not in journal_text(env) and token not in journal_text(env)
    assert CLAIM_ID not in json.dumps(result.to_dict()) and token not in json.dumps(result.to_dict())
    assert CLAIM_ID not in Path(result.effect["report_file"]).read_text()
    for path in (env.kwargs["attempts"].parent).rglob("*"):
        if path.is_file():
            assert CLAIM_ID not in path.read_text(errors="ignore")


def test_a_failed_record_keeps_the_private_claim_for_a_human_and_logs_nothing_secret(env):
    to_review(env)
    env.world.reviews.append(result_text())
    base = env.world.runner

    def refusing(argv, **kw):
        if argv[0] == sys.executable and argv[2:4] == ["routing", "record-review-proof"]:
            env.world.calls.append((argv, kw.get("cwd"), None, None))
            from types import SimpleNamespace

            return SimpleNamespace(returncode=2, stdout="", stderr=f"refused {CLAIM_ID}")
        return base(argv, **kw)

    env.runtime.runner = refusing
    result = step(env)
    assert result.outcome == "failed"
    assert CLAIM_ID not in journal_text(env) and CLAIM_ID not in json.dumps(result.to_dict())
    claims = list((env.kwargs["attempts"].parent / cr.REVIEWS_DIR).glob("*/claim.json"))
    assert len(claims) == 1 and oct(claims[0].stat().st_mode & 0o777) == "0o600"
    assert oct(claims[0].parent.stat().st_mode & 0o777) == "0o700"


def test_claim_not_granted_launches_no_session(env):
    to_review(env)
    base = env.world.runner

    def taken(argv, **kw):
        if argv[0] == sys.executable and argv[2:4] == ["routing", "claim-review"]:
            from types import SimpleNamespace

            return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(
                {"diff_hash": DIFF_HASH, "should_run": False, "state": "in_progress",
                 "generation": 1}))
        return base(argv, **kw)

    env.runtime.runner = taken
    result = step(env)
    assert result.outcome == "failed" and not reviewer_calls(env.world)
    finished = [r for r in ce.read_attempts(env.kwargs["attempts"]) if r["kind"] == "review"][-1]
    assert finished["error"] == "claim_not_granted"


def test_a_red_ci_is_not_corrected_by_the_campaign(env):
    to_review(env)
    env.world.reviews.append(result_text())
    step(env)
    env.world.checks = [check(conclusion="failure")]
    result = step(env)
    assert (result.decision.kind, result.decision.reason, result.outcome) == (
        "suspend", "ci_failing_not_executed", "stopped")


def test_journal_review_field_is_a_closed_shape(env):
    to_review(env)
    env.world.reviews.append(result_text())
    step(env)
    path = env.kwargs["attempts"]
    lines = path.read_text().splitlines()
    record = json.loads(lines[-1])
    assert record["event"] == "finished" and set(record["review"]) == cr.REVIEW_KEYS | {cr.DESCRIPTION_KEY}
    record["review"]["extra"] = 1
    path.write_text("\n".join(lines[:-1] + [json.dumps(record)]) + "\n")
    with pytest.raises(ce.AttemptJournalInvalid):
        ce.read_attempts(path)
    record["review"] = {k: record["review"][k] for k in cr.REVIEW_KEYS}  # no description key: PAT-227 optional
    path.write_text("\n".join(lines[:-1] + [json.dumps(record)]) + "\n")
    assert ce.read_attempts(path)  # a line journaled before PAT-227 stays valid
    record["kind"] = "implement"  # a verdict belongs to a review
    path.write_text("\n".join(lines[:-1] + [json.dumps(record)]) + "\n")
    with pytest.raises(ce.AttemptJournalInvalid):
        ce.read_attempts(path)


def test_epic_of_two_tickets_with_a_blocking_first_review_reaches_the_verdict_awaited(env):
    roomy(env)
    sleeps = []
    kw = dict(max_steps=40, poll_seconds=3, sleep=sleeps.append, clock=lambda: NOW, **env.kwargs)

    def go():
        return ce.run(env.tracker, env.codehost, PROJECT, "APP-1", env.runtime, **kw)

    env.world.reviews += [result_text(quality="blocked", verdict="fail"), result_text(),
                          result_text()]
    results, stopped = go()  # APP-2: blocked, corrected, pushed, re-reviewed; CI is not proven
    assert stopped == "max_steps" and sleeps
    assert [r.decision.kind for r in results[:8]] == [
        "start", "implement", "open-pr", "review", "correct", "open-pr", "review", "wait-ci"]
    assert env.world.proof == "valid"
    env.world.checks = [check()]
    results, stopped = go()
    assert [r.decision.kind for r in results[:2]] == ["merge", "start"]
    assert env.world.states["APP-2"] == "done"
    env.world.checks = []
    results, stopped = go()  # APP-3: reviewed clean, CI not proven yet
    assert stopped == "max_steps" and env.world.proof == "valid"
    env.world.checks = [check()]
    results, stopped = go()
    assert stopped == "terminal" and results[-1].decision.kind == "await-verdict"
    assert env.world.states == {"APP-2": "done", "APP-3": "done"}
    assert env.world.reviews == []  # no injected proof: every proof came from a reviewer session
    verbs = [c[0][3] for c in effects(env.world) if c[0][0] == sys.executable
             and c[0][2] == "issue"]
    # each review also updates the PR description (the rounds); 3 reviews, 3 updates among the openpr lines
    assert verbs == ["start", "openpr", "openpr", "openpr", "openpr", "merge", "start",
                     "openpr", "openpr", "merge"]
    assert routing_verbs(env.world).count("record-review-proof") == 3
    assert routing_verbs(env.world).count("claim-review") == 3
    assert not any(CLAIM_ID in line for line in journal_text(env).splitlines())


def test_the_reviewer_contract_marks_the_two_never_deferrable_cases_fix_before_merge():
    contract = " ".join(cr.review_contract().split())
    assert "Always set fix_before_merge true on a remark about a page frozen after " in contract
    assert "(release note, migration guide, CHANGELOG section of a published version)" in contract
    assert "missing or generic documentation status under AGENTS.md#R5" in contract


# ---------------------------------------------------------------- PAT-227


def _description_only(env):
    """A validated review whose only remark to fix targets the description."""
    to_review(env)
    env.world.session_commits = False  # a description correction commits nothing
    roomy(env)
    env.world.reviews.append(result_text(fix=1, description=1))
    assert step(env).effect["review"]["fix_description"] == 1


def test_a_description_only_remark_is_settled_by_a_new_description_without_a_commit(env):
    _description_only(env)
    env.world.session_summary = f"{ce.SUMMARY_MARK}\n## Corrected\nthe new description"
    result = step(env)
    assert (result.decision.kind, result.decision.reason, result.outcome) == (
        "correct", "review_fix_description", "executed")
    prompt = [c for c in effects(env.world) if c[0][0] == "claude"][-1][3]
    assert "pull-request description" in prompt and "Change no file and make no commit" in prompt
    assert ce.SUMMARY_MARK in prompt
    body = env.world.bodies[-1]
    assert body.startswith("## Corrected\nthe new description\n\n## Review rounds")
    assert "Description corrected per a remark" in body and "not re-reviewed" in body
    assert ce.SUMMARY_MARK not in body
    assert effects(env.world)[-1][0] == [sys.executable, CLI, "issue", "openpr", "APP-2",
                                         "--summary-stdin"]
    done = [r for r in ce.read_attempts(env.kwargs["attempts"]) if r["kind"] == "correct"
            and r["event"] == "finished"][-1]
    assert done["status"] == "completed" and set(done["description"]) == {"head", "base"}
    # the next pass neither corrects again nor reviews again: no attempt_already_made
    env.world.checks = [check()]
    nxt = step(env)
    assert (nxt.decision.kind, nxt.decision.reason) == ("merge", "review_valid_ci_green")
    assert len(reviewer_calls(env.world)) == 1


def test_decide_is_pure_over_the_description_facts():
    pr = cc.PullFacts(5, "open", False, "a" * 40, "b" * 40)
    ticket = cc.TicketFacts(
        id="APP-2", state="review", category="eligible", reason="", branch=True,
        branch_sha="a" * 40, pr=pr, review="valid", ci="green", fix_before_merge=2,
        fix_description=2)
    assert cc._ticket_step(ticket).reason == "review_fix_description"
    done = cc.TicketFacts(**{**ticket.__dict__, "description_corrected": True})
    assert cc._ticket_step(done).kind == "merge"
    mixed = cc.TicketFacts(**{**ticket.__dict__, "fix_description": 1})
    assert cc._ticket_step(mixed).reason == "review_fix_before_merge"
    mixed_done = cc.TicketFacts(**{**mixed.__dict__, "description_corrected": True})
    assert cc._ticket_step(mixed_done).reason == "review_fix_before_merge"
    spent = cc.TicketFacts(**{**ticket.__dict__, "corrections": cc.MAX_CORRECTIONS})
    assert cc._ticket_step(spent).reason == "corrections_exhausted"


def test_a_remark_on_the_diff_keeps_the_correction_session_with_commits(env):
    to_review(env)
    roomy(env)
    env.world.reviews.append(result_text(fix=2, description=1))
    step(env)
    result = step(env)
    assert (result.decision.reason, result.outcome) == ("review_fix_before_merge", "executed")
    assert "Change no file" not in effects(env.world)[-1][3]
    done = [r for r in ce.read_attempts(env.kwargs["attempts"]) if r["kind"] == "correct"
            and r["event"] == "finished"]
    assert done and all("description" not in r for r in done)
    assert len(getattr(env.world, "bodies", [])) == 1  # only the body of the review


def test_a_description_correction_without_the_summary_line_is_not_published(env):
    _description_only(env)
    env.world.session_summary = "I ran the tests as a background command."
    result = step(env)
    assert (result.decision.reason, result.outcome) == ("review_fix_description", "failed")
    done = [r for r in ce.read_attempts(env.kwargs["attempts"]) if r["kind"] == "correct"
            and r["event"] == "finished"][-1]
    assert (done["status"], done["error"]) == ("failed", "description_summary_unusable")
    assert len(getattr(env.world, "bodies", [])) == 1  # only the body of the review
    again = step(env)
    assert (again.decision.kind, again.decision.reason) == (
        "suspend", "description_correction_not_settled")


def test_a_description_that_could_not_be_published_suspends_with_its_own_reason(env):
    _description_only(env)
    runner = env.world.runner

    def failing(argv, **kw):
        done = runner(argv, **kw)
        if argv[3:5] == ["openpr", "APP-2"] and "--summary-stdin" in argv:
            done.returncode = 1
        return done

    env.runtime.runner = failing
    result = step(env)
    assert result.outcome == "failed"
    assert ce.read_attempts(env.kwargs["attempts"])[-1]["error"] == "description_not_published"
    assert step(env).decision.reason == "description_correction_not_settled"


def test_an_unusable_implementation_summary_is_never_published_as_the_pr_body(env):
    env.world.session_summary = "Done. Waiting for the background command."
    step(env), step(env)
    result = step(env)
    assert result.decision.kind == "open-pr"
    assert effects(env.world)[2][3] == ce.SUMMARY_UNUSABLE_BODY
    env.world.reviews.append(result_text())
    step(env)
    assert env.world.bodies[-1].startswith(ce.SUMMARY_UNUSABLE_BODY + "\n\n## Review rounds")
    assert "placeholder" in reviewer_calls(env.world)[-1][3]


def test_a_usable_implementation_summary_loses_its_marker_and_the_prompt_is_not_a_placeholder(env):
    to_review(env)
    assert effects(env.world)[2][3] == "## Summary\nAPP-2"
    env.world.reviews.append(result_text())
    step(env)
    assert "placeholder" not in reviewer_calls(env.world)[-1][3]
    assert ce.SUMMARY_MARK in effects(env.world)[1][3]  # asked of the implementation session


def test_the_summary_criterion_is_mechanical():
    mark = ce.SUMMARY_MARK
    assert ce.usable_summary(f"{mark}\nbody\n") == "body"
    assert ce.usable_summary(f"\n{mark}\nbody") == "body"
    for bad in (None, "", "  \n", "body", f"{mark}", f"{mark}\n \n", f"x\n{mark}\nbody",
                f"{mark}x\nbody"):
        assert ce.usable_summary(bad) is None
        assert ce.published_summary(bad) == ce.SUMMARY_UNUSABLE_BODY


def test_the_reviewer_contract_asks_each_remark_for_a_target():
    contract = cr.review_contract()
    assert 'target "diff"' in contract and '"description"' in contract


def test_a_later_body_update_keeps_the_corrected_description(env, tmp_path):
    _description_only(env)
    env.world.session_summary = f"{ce.SUMMARY_MARK}\n## Corrected\nthe new description"
    step(env)
    records = ce.read_attempts(env.kwargs["attempts"])
    assert cr.body_summary(records, "APP-1", "APP-2") == "## Corrected\nthe new description"
    ticket = type("T", (), {"id": "APP-2"})()
    cr.update_pr_body(env.runtime, "APP-1", ticket, env.kwargs["attempts"], tmp_path, None)
    body = env.world.bodies[-1]
    assert body.startswith("## Corrected\nthe new description\n\n## Review rounds")
    assert body.count("Description corrected per a remark") == 1


def test_the_description_is_not_published_when_the_session_moved_the_worktree_head(env):
    _description_only(env)
    env.world.session_summary = f"{ce.SUMMARY_MARK}\n## Corrected\nthe new description"
    env.world.worktree_head["APP-2"] = "main"  # the session switched the worktree HEAD
    before = len(env.world.bodies)
    result = step(env)
    assert (result.decision.reason, result.outcome) == ("review_fix_description", "failed")
    done = ce.read_attempts(env.kwargs["attempts"])[-1]
    assert (done["status"], done["error"]) == ("failed", "description_worktree_branch_mismatch")
    assert len(env.world.bodies) == before and "description" not in done
    # the two `issue openpr` of the opening and of the review, none for the description
    assert len([c for c in effects(env.world) if c[0][3:4] == ["openpr"]]) == 2


def test_the_description_is_not_published_when_the_session_committed(env):
    _description_only(env)
    env.world.session_summary = f"{ce.SUMMARY_MARK}\n## Corrected\nthe new description"
    env.world.head_sha["APP-2"] = "9" * 40  # a new commit on the ticket branch
    before = len(env.world.bodies)
    result = step(env)
    assert result.outcome == "failed"
    done = ce.read_attempts(env.kwargs["attempts"])[-1]
    assert (done["status"], done["error"]) == ("failed", "description_session_moved_head")
    assert len(env.world.bodies) == before and "description" not in done


def test_a_corrupt_review_field_is_a_journal_error_not_a_type_error(env):
    to_review(env)
    env.world.reviews.append(result_text())
    step(env)
    path = env.kwargs["attempts"]
    lines = path.read_text().splitlines()
    record = json.loads(lines[-1])
    for bad in ({"fix_description": "1"}, {"fix_before_merge": "1"}, {"fix_before_merge": None}):
        record["review"] = {**json.loads(lines[-1])["review"], **bad}
        path.write_text("\n".join(lines[:-1] + [json.dumps(record)]) + "\n")
        with pytest.raises(ce.AttemptJournalInvalid):
            ce.read_attempts(path)


def test_the_description_prompt_carries_the_published_description(env):
    _description_only(env)
    step(env)
    prompt = [c for c in effects(env.world) if c[0][0] == "claude"][-1][3]
    assert "<description>\n## Summary\nAPP-2\n</description>" in prompt
    assert "Do not write a `## Review rounds` section" in prompt


def test_a_correction_through_step_runs_in_the_native_sandbox(env):
    """PAT-236 (220-6): the correction session launched by `step` -> `_launch` carries the
    sandbox settings of its worktree, the unit test of the argv alone cannot show that."""
    to_review(env)
    roomy(env)
    env.world.reviews.append(result_text(quality="blocked", verdict="fail"))
    step(env)
    result = step(env)
    assert (result.decision.kind, result.outcome) == ("correct", "executed")
    argv, cwd, _env, _prompt = effects(env.world)[-1]
    assert argv[0] == "claude" and "--tools" not in argv
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    settings = json.loads(argv[argv.index("--settings") + 1])
    assert settings["sandbox"]["enabled"] is True
    assert os.path.realpath(cwd) in settings["sandbox"]["filesystem"]["allowWrite"]
    common = str(Path(os.environ["FOUNDRY_DATA"]).parent / "repo" / ".git")
    assert os.path.join(os.path.realpath(common), "objects") in (
        settings["sandbox"]["filesystem"]["allowWrite"])


def test_the_version_probe_runs_where_and_as_the_session_launches(env, monkeypatch):
    """PAT-236: `claude --version` runs in the directory, with the environment, of the session
    it precedes (implementation, review, correction), as in the other two headless runners."""
    from foundry import routing_facades

    # every route declares a minimum, so that every session is preceded by a probe
    monkeypatch.setattr(routing_facades, "claude_host_version_requirement",
                        lambda model, observed: {"status": "conforming", "required": "2.1.293"})
    monkeypatch.setenv("FOUNDRY_DATA", os.environ["FOUNDRY_DATA"])
    to_review(env)
    roomy(env)
    env.world.reviews.append(result_text(quality="blocked", verdict="fail"))
    step(env), step(env)
    calls = [c for c in env.world.calls if c[0][0] == "claude"]
    assert any(c[0][1] == "--version" for c in calls)
    pairs = 0
    for index, (argv, cwd, child_env, _input) in enumerate(calls):
        if argv[1] != "--version":
            continue
        launch = next(c for c in calls[index + 1:] if c[0][1] != "--version")
        assert os.path.realpath(cwd) == os.path.realpath(launch[1]) != os.path.realpath(
            env.runtime.root)
        assert child_env == launch[2]
        pairs += 1
    assert pairs >= 3  # implementation, review, correction
