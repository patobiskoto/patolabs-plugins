"""PAT-108: deterministic tests of the PAT-19 comparison launcher.

Only FAKE arms run: small Python scripts created in temp dirs that edit the bundle and emit a
fake event stream or host session log. No model is loaded, no cloud and no network is used, the
machine facts are injected, and nothing is written outside temp directories."""
from __future__ import annotations

import datetime as dt
import errno
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from foundry import local_first_corpus as lfc
from foundry import local_first_runner as lfr
from test_local_first_corpus import _make_repo

QUALIFICATION = Path(__file__).resolve().parents[1] / "docs" / "qualification"
TODAY = dt.date(2026, 10, 6)

FAKE_ARM = r'''
import argparse, json, os, subprocess, sys, time, hashlib
ap = argparse.ArgumentParser()
ap.add_argument("--role"); ap.add_argument("--workdir"); ap.add_argument("--plan")
ap.add_argument("--session-id", default=""); ap.add_argument("--projects-dir", default="")
ap.add_argument("--review-file", default=""); ap.add_argument("--statement", default="")
a = ap.parse_args()
counter = a.plan + ".count"
counts = json.load(open(counter)) if os.path.exists(counter) else {}
i = counts.get(a.role, 0)
counts[a.role] = i + 1
json.dump(counts, open(counter, "w"))
steps = json.load(open(a.plan))[a.role]
behavior = steps[min(i, len(steps) - 1)]
with open(a.plan + ".statements", "a") as handle:
    handle.write(hashlib.sha256(open(a.statement, "rb").read()).hexdigest() + "\n")
module = os.path.join(a.workdir, "plugins/foundry/tooling/foundry/m.py")
FIXED = "def add(a, b):\n    return a + b\n"
def event(kind, **kw):
    print(json.dumps({"type": kind, **kw}), flush=True)
def claude_log(usage):
    if not a.projects_dir:
        return
    folder = os.path.join(a.projects_dir, "proj")
    os.makedirs(folder, exist_ok=True)
    line = {"type": "assistant", "timestamp": "2026-10-05T10:00:00Z", "sessionId": a.session_id,
            "message": {"model": "claude-sonnet-5-5", "id": "m1", "stop_reason": "end_turn",
                        "usage": usage}, "requestId": "r1"}
    with open(os.path.join(folder, a.session_id + ".jsonl"), "w") as out:
        out.write(json.dumps(line) + "\n")
USAGE = {"implementer": (100, 10, 5, 20), "economy": (40, 0, 0, 8), "reviewer": (30, 0, 0, 10)}
if behavior == "crash":
    sys.exit(3)
if behavior == "hang":
    child = subprocess.Popen(["sleep", "60"])
    open(a.plan + ".child", "w").write(str(child.pid))
    time.sleep(60)
if behavior == "many_steps":
    for _ in range(200):
        event("tool_execution_start", tool="bash")
    time.sleep(60)
if behavior in ("fix", "fix_after_notes"):
    if behavior == "fix_after_notes":
        tests = open(os.path.join(a.workdir, "plugins/foundry/tests/test_m.py")).read()
        if not os.path.exists(os.path.join(a.workdir, "NOTES.txt")) or "test_added" in tests:
            sys.exit(9)
    open(module, "w").write(FIXED)
if behavior == "partial":
    open(os.path.join(a.workdir, "NOTES.txt"), "w").write("tried\n")
if a.role == "reviewer":
    verdict = behavior if behavior in ("PASS", "BLOCK") else "garbage"
    if verdict != "garbage":
        open(a.review_file, "w").write(json.dumps({"verdict": verdict, "findings": ["see code"]}))
if a.role in USAGE:
    i_, c_, w_, o_ = USAGE[a.role]
    claude_log({"input_tokens": i_, "cache_read_input_tokens": c_,
                "cache_creation_input_tokens": w_, "output_tokens": o_})
else:
    for _ in range(3):
        event("tool_execution_start", tool="bash")
    event("message_end", message={"role": "assistant",
          "usage": {"input": 1000, "output": 50, "reasoningTokens": 7}})
'''


def _arm(role, scripts, plan, projects, extra=None):
    cloud = role in ("implementer", "economy", "reviewer")
    argv = [sys.executable, str(scripts), "--role", role, "--workdir", "{workdir}", "--plan", str(plan),
            "--statement", "{statement_file}"]
    if cloud:
        argv += ["--session-id", "{session_id}", "--projects-dir", str(projects),
                 "--review-file", "{review_file}"]
    return {"kind": {"implementer": "cloud_implementer", "economy": "cloud_implementer",
                     "reviewer": "cloud_reviewer", "neutral": "neutral_harness"}.get(role, "local_harness"),
            "fake": True, "verified": False, "argv": argv,
            "home": "real" if cloud else "isolated", "network": "open" if cloud else "loopback",
            "env_allow": [], "stream": {"format": "none" if cloud else "omp-json"},
            **({"session_log": {"host": "claude", "projects_dir": str(projects),
                                         "layout_verified": True}} if cloud else {}),
            **(extra or {})}


def fake_campaign(tmp_path, plan, **overrides):
    scripts = tmp_path / "fake_arm.py"
    scripts.write_text(FAKE_ARM, encoding="utf-8")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    projects = tmp_path / "claude-projects"
    base = json.loads((QUALIFICATION / "pat-19-campaign-v1.json").read_text("utf-8"))
    base["bounds"].update({"local_max_seconds": 20, "cloud_max_seconds": 60})
    base["rules"]["screening"]["tasks"] = 2
    base["rules"]["comparison"].update({"tasks": 2, "min_local_successes": 1})
    base["drivers"] = {
        "local_harness": _arm("local", scripts, plan_path, projects),
        "neutral_harness": _arm("neutral", scripts, plan_path, projects),
        "cloud_implementer_current": _arm("implementer", scripts, plan_path, projects),
        "cloud_implementer_economy": _arm("economy", scripts, plan_path, projects),
        "cloud_reviewer": _arm("reviewer", scripts, plan_path, projects)}
    base["candidates"] = {"cand-a": {"model": "fake/model-a"}, "cand-b": {"model": "fake/model-b"}}
    for key, value in overrides.items():
        base[key].update(value)
    return base, plan_path


def write_envelope(tmp_path, **caps):
    body = {"schema": lfr.ENVELOPE_SCHEMA, "campaign_id": "test-campaign", "expires_on": "2026-12-31",
            "allowed_modes": ["screen", "compare"],
            "caps": {"cloud_executions": 45, "premium_tokens": 10_000_000,
                     "wall_clock_seconds": 100_000, **caps}}
    path = tmp_path / "envelope.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def make_runner(tmp_path, mode, plan, *, repo_bundle=None, caps=None, run=None, campaign_over=None,
                host_env=None):
    repo, snap, _, _ = repo_bundle or _make_repo(tmp_path)
    campaign, plan_path = fake_campaign(tmp_path, plan, **(campaign_over or {}))
    envelope = lfr.load_envelope(write_envelope(tmp_path, **(caps or {})), mode, TODAY)
    runner = lfr.Runner(
        repo=repo, campaign=campaign, envelope=envelope, state_dir=tmp_path / "state",
        work_root=tmp_path / "work", mode=mode, dry_run=True, sandbox=False,
        run=run or (lambda argv: None),
        preflight_run=lambda cid: lfr.dry_run_facts(campaign, campaign["candidates"][cid]["model"]),
        disk_free_gib=lambda: 500.0, host_env=host_env or dict(os.environ), today=lambda: TODAY)
    runner.quiescent_wait = 0.05
    task = snap["prs"][0]
    return runner, campaign, plan_path, [task, {**task, "pr": 2, "issue": "PAT-2"}]


def _sub(tmp_path, name):
    (tmp_path / name).mkdir()
    return tmp_path / name


def results(runner):
    return [json.loads(line) for line in runner.results_path.read_text("utf-8").splitlines()]


def ledger_of(runner):
    return [json.loads(line) for line in runner.ledger.path.read_text("utf-8").splitlines()]


def rule_report(campaign, recs, **kw):
    """Report on hand-written records, with the ledger a real run would have left for them: one
    settled cloud execution per ``cloud_executions``, carrying the record's premium total."""
    entries = [{"kind": "session_started"}]
    recs = [dict(r) for r in recs]
    for n, rec in enumerate(recs):
        if rec.get("record_type") != "attempt":
            continue
        rec["cloud_sessions"] = [f"s{n}-{i}" for i in range(rec["cloud_executions"])]
        total = rec["premium"]["billing_total"]
        for i, sid in enumerate(rec["cloud_sessions"]):
            entries += [{"kind": "cloud_started", "session_id": sid},
                        {"kind": "settled", "cloud": True, "session_id": sid,
                         "premium_tokens": None if total is None else (total if i == 0 else 0)}]
    return lfr.report(campaign, recs, entries, **kw)


def ledger_kinds(runner):
    return [json.loads(line)["kind"] for line in runner.ledger.path.read_text("utf-8").splitlines()]


def counts(plan_path):
    path = Path(str(plan_path) + ".count")
    return json.loads(path.read_text("utf-8")) if path.exists() else {}


FIX_ALL = {"local": ["fix"], "neutral": ["fix"], "implementer": ["fix"], "economy": ["fix"],
           "reviewer": ["PASS"]}


# ---------------------------------------------------------------- envelope (AC4)

def test_launcher_refuses_to_start_without_a_valid_envelope(tmp_path):
    with pytest.raises(lfr.EnvelopeError, match="no authorization envelope"):
        lfr.load_envelope(None, "compare", TODAY)
    with pytest.raises(lfr.EnvelopeError, match="no authorization envelope"):
        lfr.load_envelope(tmp_path / "missing.json", "compare", TODAY)
    good = json.loads(write_envelope(tmp_path).read_text("utf-8"))
    cases = {"schema": {"schema": "x"}, "expired": {"expires_on": "2026-10-05"},
             "mode": {"allowed_modes": ["screen"]}, "campaign": {"campaign_id": "../x"},
             "caps": {"caps": {"cloud_executions": -1, "premium_tokens": 1, "wall_clock_seconds": 1}},
             "missing cap": {"caps": {"cloud_executions": 1}}}
    for name, patch in cases.items():
        path = tmp_path / f"env-{name.replace(' ', '-')}.json"
        path.write_text(json.dumps({**good, **patch}), encoding="utf-8")
        with pytest.raises(lfr.EnvelopeError):
            lfr.load_envelope(path, "compare", TODAY)
    assert lfr.load_envelope(write_envelope(tmp_path), "screen", TODAY)["campaign_id"] == "test-campaign"


def test_cli_without_an_envelope_starts_nothing(tmp_path, capsys):
    repo, snap, _, _ = _make_repo(tmp_path)
    campaign, _ = fake_campaign(tmp_path, FIX_ALL)
    (tmp_path / "c.json").write_text(json.dumps(campaign), encoding="utf-8")
    (tmp_path / "snap.json").write_text(json.dumps(snap), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"screening": [{"pr": 1}], "comparison": [{"pr": 1}]}),
                                            encoding="utf-8")
    code = lfr.main(["screen", "--campaign", str(tmp_path / "c.json"), "--dry-run", "--candidate", "cand-a",
                     "--state-dir", str(tmp_path / "state"), "--work-root", str(tmp_path / "work"),
                     "--repo", str(repo), "--snapshot", str(tmp_path / "snap.json"),
                     "--manifest", str(tmp_path / "manifest.json")], today=TODAY)
    assert code == 2 and "no authorization envelope" in capsys.readouterr().err
    assert not (tmp_path / "state").exists() and not (tmp_path / "plan.json.count").exists()


def test_cloud_execution_cap_stops_the_campaign_and_is_recorded_before_each_execution(tmp_path):
    runner, _, plan, tasks = make_runner(tmp_path, "compare", FIX_ALL, caps={"cloud_executions": 3})
    out = runner.compare(tasks, "cand-a", ("A", "B"))
    started = [json.loads(line) for line in runner.ledger.path.read_text("utf-8").splitlines()
               if json.loads(line)["kind"] == "cloud_started"]
    assert len(started) == 3  # implementer A, reviewer A, implementer B: the 4th is never started
    assert counts(plan) == {"implementer": 1, "reviewer": 1, "economy": 1}
    stop = results(runner)[-1]
    assert {k: stop[k] for k in ("schema", "campaign_id", "mode", "record_type", "reason", "dry_run")} == {
        "schema": lfr.RESULT_SCHEMA, "campaign_id": "test-campaign", "mode": "compare",
        "record_type": "stop", "reason": "cap_reached:cloud_executions", "dry_run": True}
    assert set(lfr.PROVENANCE_KEYS) <= set(stop)
    assert [r["path"] for r in out] == ["A"]


def test_the_ledger_entry_exists_before_the_cloud_process_starts(tmp_path, monkeypatch):
    runner, _, _, tasks = make_runner(tmp_path, "compare", FIX_ALL)
    real = lfr.execute_driver
    seen = []

    def spy(driver, values, **kw):
        if driver["kind"] != "local_harness":
            lines = [json.loads(x) for x in runner.ledger.path.read_text("utf-8").splitlines()]
            seen.append(any(x["kind"] == "cloud_started" and x["session_id"] == values["session_id"]
                            for x in lines))
        return real(driver, values, **kw)

    monkeypatch.setattr(lfr, "execute_driver", spy)
    runner.preflight("cand-a")
    runner.cloud_path(tasks[0], "A", "comparison")
    assert seen == [True, True]


def test_premium_token_and_wall_clock_caps(tmp_path):
    runner, _, plan, tasks = make_runner(tmp_path, "compare", FIX_ALL, caps={"premium_tokens": 100})
    runner.compare(tasks, "cand-a", ("A",))
    assert counts(plan) == {"implementer": 1}  # 135 tokens spent >= 100: the reviewer never starts
    assert results(runner)[-1]["reason"] == "cap_reached:premium_tokens"
    ledger = lfr.Ledger(tmp_path / "s2", lfr.load_envelope(write_envelope(tmp_path, wall_clock_seconds=3),
                                                           "compare", TODAY), "compare")
    ledger.settle(cloud=False, seconds=5, premium_tokens=None)
    with pytest.raises(lfr.CapReached, match="wall_clock_seconds"):
        ledger.check(cloud=False)


def test_unmeasurable_premium_tokens_stop_a_capped_campaign(tmp_path):
    runner, _, plan, tasks = make_runner(tmp_path, "compare", FIX_ALL)
    runner.campaign["drivers"]["cloud_implementer_current"]["session_log"]["projects_dir"] = str(
        tmp_path / "nowhere")
    runner.compare(tasks, "cand-a", ("A",))
    assert counts(plan) == {"implementer": 1}
    assert results(runner)[-1]["reason"] == "cap_reached:premium_tokens_unmeasurable"
    first = results(runner)[0]
    assert first["premium"]["by_role"]["implementer"] is None and first["premium"]["billing_total"] is None
    assert first["unknown"]["premium.implementer"] == "session_log_not_found"


# ------------------------------------------------------- screen never spends cloud (AC4/AC6)

def test_screen_mode_can_never_start_a_cloud_execution(tmp_path, monkeypatch):
    runner, _, plan, tasks = make_runner(tmp_path, "screen", FIX_ALL)
    with pytest.raises(lfr.EnvelopeError, match="can never start a cloud execution"):
        runner.cloud_execution("implementer", "cloud_implementer_current", tmp_path, tmp_path, "implement")
    with pytest.raises(lfr.EnvelopeError):
        runner.ledger.reserve_cloud("implementer", "s")
    with pytest.raises(lfr.EnvelopeError):
        runner.cloud_path(tasks[0], "A")
    assert "cloud_started" not in ledger_kinds(runner) and counts(plan) == {}


def test_dry_run_screen_two_candidates_two_tasks(tmp_path):
    plan = {**FIX_ALL, "local": ["fix", "partial", "fix", "fix"]}
    runner, campaign, plan_path, tasks = make_runner(
        tmp_path, "screen", plan, caps={"cloud_executions": 0})
    spent = []
    runner.cloud_execution = lambda *a, **k: spent.append(a)  # would be a cloud spend
    out = runner.screen(tasks, ["cand-a", "cand-b"])
    assert not spent and counts(plan_path) == {"local": 4}
    assert [(r["local"]["candidate"], r["task"]["pr"], r["accepted"]) for r in out] == [
        ("cand-a", 1, True), ("cand-a", 2, False), ("cand-b", 1, True), ("cand-b", 2, True)]
    first = out[0]
    assert first["path"] == "S" and first["task"]["set"] == "screening" and first["cloud_executions"] == 0
    assert first["local"]["steps"] == 3 and first["local"]["stream_tokens"] == {
        "input": 1000, "output": 50, "reasoning": 7}
    assert first["local"]["harness"] == "local_harness"  # the harness is a recorded coordinate
    assert first["local"]["prefill_tokens_per_second"] is None
    assert first["unknown"]["local.prefill_tokens_per_second"] == "not exposed by the event stream"
    assert first["unknown"]["machine.swap_used_mib"].startswith("vm.swapusage")  # unknown, never 0
    assert first["machine"]["before"]["swap_used_mib"] is None
    assert lfr.Ledger.totals(runner.ledger)["cloud_started"] == 0
    rep = lfr.report(campaign, results(runner), ledger_of(runner))
    assert rep["screening"]["selected"] == "cand-b" and rep["screening"]["complete"]
    assert rep["screening"]["candidates"]["cand-a"]["accepted"] == 1
    assert not list((tmp_path / "work").iterdir())  # every disposable attempt is gone


def test_screen_under_the_neutral_harness_records_the_harness_coordinate(tmp_path):
    runner, _, _, tasks = make_runner(tmp_path, "screen", FIX_ALL)
    rec = runner.screen(tasks[:1], ["cand-a"], harness_id="neutral_harness")[0]
    assert rec["local"]["harness"] == "neutral_harness" and rec["local"]["harness_kind"] == "neutral_harness"


# --------------------------------------------------------------- the three paths (AC1-3)

def test_dry_run_compare_three_paths_two_tasks(tmp_path):
    plan = {**FIX_ALL, "local": ["partial", "fix"],
            "reviewer": ["BLOCK", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS"]}
    runner, campaign, plan_path, tasks = make_runner(tmp_path, "compare", plan)
    out = runner.compare(tasks, "cand-a", ("A", "B", "C"))
    by = {}
    for rec in out:
        by.setdefault((rec["task"]["pr"], rec["path"]), []).append(rec)
    # task 1, C: the local attempt is refused, path A takes over, its cost is added
    local, takeover = by[(1, "C")]
    assert (local["segment"], local["local_outcome"], local["accepted"]) == ("local", "refused", None)
    assert (takeover["segment"], takeover["accepted"]) == ("takeover", True)
    assert local["judge"]["verdict"] == "REFUSED" and local["cloud_executions"] == 0
    # task 1, A: a reviewer BLOCK, then a correction accepted at the second review
    first, corrected = by[(1, "A")]
    assert (first["outcome"], corrected["outcome"]) == ("review_block", "accepted")
    assert first["judge"]["verdict"] == "ACCEPTED" and first["accepted"] is False
    assert sum(r["review"]["rounds"] for r in by[(1, "A")]) == 2 and len(by[(2, "A")]) == 1
    assert "corrector" in corrected["premium"]["by_role"]
    # task 2, C: one local attempt accepted, then the cloud review only
    (c2,) = by[(2, "C")]
    assert c2["local_outcome"] == "accepted" and c2["accepted"] is True and c2["review"]["rounds"] == 1
    # premium tokens: per class and per role from the (fake) host session logs; reasoning is unknown
    impl = by[(2, "A")][0]["premium"]
    assert impl["by_role"]["implementer"] == {
        "input_tokens": 100, "cached_input_tokens": 10, "cache_write_input_tokens": 5,
        "output_tokens": 20, "reasoning_output_tokens": None}
    assert impl["by_role"]["reviewer"]["output_tokens"] == 10 and impl["billing_total"] == 175
    assert by[(1, "B")][0]["premium"]["by_role"]["implementer"]["input_tokens"] == 40
    assert c2["premium"]["billing_total"] == 40  # reviewer only
    assert by[(1, "C")][1]["premium"]["billing_total"] == 175
    assert all(r["wall_seconds"] >= 0 for r in out)
    # 13 cloud executions, every one recorded in the ledger before it started
    assert lfr.Ledger.totals(runner.ledger)["cloud_started"] == 13
    assert counts(plan_path) == {"implementer": 4, "economy": 2, "reviewer": 7, "local": 2}
    # every arm got a fresh bundle; none is left behind
    assert len(runner.handed) == runner.counter and not list((tmp_path / "work").iterdir())
    rep = lfr.report(campaign, results(runner), ledger_of(runner))
    assert rep["promotion"] is False and set(rep["comparison"]["arms"]) == {"B", "C"}
    c = rep["comparison"]["arms"]["C"]
    assert {c["compatibility"], c["quality"]} <= {"pass", "fail", "unavailable"}
    # A: task 1 = (135 + 40) twice = 350, task 2 = 175 -> 525 over two tasks
    assert c["economy_detail"]["reference_premium_billing_tokens"] == 525


def test_a_correction_runs_on_a_fresh_bundle_that_never_saw_the_protected_tests(tmp_path):
    plan = {**FIX_ALL, "implementer": ["partial", "fix_after_notes"]}
    runner, _, plan_path, tasks = make_runner(tmp_path, "compare", plan)
    recs = runner.cloud_path(tasks[0], "A", "comparison")
    assert [r["outcome"] for r in recs] == ["judge_refused", "accepted"]
    assert recs[1]["accepted"] is True  # exit 9 of the script would have broken the correction
    assert counts(plan_path)["implementer"] == 2 and len(runner.handed) == 3  # impl, corr, review


def test_same_statement_footer_for_every_arm(tmp_path):
    runner, campaign, plan_path, tasks = make_runner(tmp_path, "compare", FIX_ALL)
    runner.compare(tasks[:1], "cand-a", ("A", "B", "C", "N"))
    hashes = Path(str(plan_path) + ".statements").read_text("utf-8").split()
    arms = [h for h in hashes]  # implementer, economy, local, neutral, and three reviews
    assert len(set(arms)) == 1 and len(arms) == 7
    expected = hashlib.sha256((tasks[0]["ac_text"] + campaign["statement_footer"]).encode()).hexdigest()
    assert arms[0] == expected
    assert "virtual environments" in campaign["statement_footer"]


def test_neutral_path_is_local_only_and_costs_no_cloud(tmp_path):
    runner, _, plan, tasks = make_runner(tmp_path, "compare", FIX_ALL)
    out = runner.compare(tasks[:1], "cand-a", ("N",))
    assert out[0]["path"] == "N" and out[0]["accepted"] is True and out[0]["cloud_executions"] == 0
    assert out[0]["local"]["harness"] == "neutral_harness" and counts(plan) == {"neutral": 1}


def test_early_stop_on_local_successes_and_on_premium(tmp_path):
    runner, _, plan, tasks = make_runner(tmp_path, "compare", {**FIX_ALL, "local": ["partial"]},
                                         campaign_over={"rules": {}})
    runner.campaign["rules"]["comparison"]["min_local_successes"] = 3
    out = runner.compare(tasks + [{**tasks[0], "pr": 3}], "cand-a", ("A", "C"))
    assert results(runner)[-1]["reason"] == "fewer_than_min_local_successes"
    assert {r["task"]["pr"] for r in out} == {1}  # stopped after the first task
    runner2, _, _, tasks2 = make_runner(_sub(tmp_path, "second"), "compare", {**FIX_ALL, "local": ["partial"]})
    runner2.compare(tasks2 + [{**tasks2[0], "pr": 3}], "cand-a", ("A", "C"))
    assert results(runner2)[-1]["reason"] == "premium_c_not_below_a"  # C paid A's cost plus a local try


# -------------------------------------------------------------------------- bounds (AC1)

def test_local_attempt_is_bounded_in_time_and_the_group_is_killed(tmp_path):
    runner, _, plan, tasks = make_runner(tmp_path, "screen", {**FIX_ALL, "local": ["hang"]},
                                         campaign_over={"bounds": {"local_max_seconds": 2}})
    rec = runner.screen(tasks[:1], ["cand-a"])[0]
    assert rec["local"]["timed_out"] and rec["accepted"] is False and rec["local"]["exit_code"] is None
    assert rec["local"]["ended_by_external_signal"] is False  # our own kill is not an interruption
    child = int(Path(str(plan) + ".child").read_text("utf-8"))
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("the child of the killed attempt survived")


def test_local_attempt_is_bounded_in_steps_when_the_stream_exposes_them(tmp_path):
    runner, _, _, tasks = make_runner(tmp_path, "screen", {**FIX_ALL, "local": ["many_steps"]})
    rec = runner.screen(tasks[:1], ["cand-a"])[0]
    assert rec["local"]["step_limit_hit"] and not rec["local"]["timed_out"]
    assert 40 < rec["local"]["steps"] < 200 and rec["wall_seconds"] < 15


def test_steps_are_unknown_when_the_stream_exposes_none(tmp_path):
    runner, _, _, tasks = make_runner(tmp_path, "screen", FIX_ALL)
    runner.campaign["drivers"]["local_harness"]["stream"] = {"format": "none"}
    rec = runner.screen(tasks[:1], ["cand-a"])[0]
    assert rec["local"]["steps"] is None and rec["unknown"]["local.steps"].startswith("stream format")


def test_a_crashing_arm_is_judged_not_trusted(tmp_path):
    runner, _, _, tasks = make_runner(tmp_path, "screen", {**FIX_ALL, "local": ["crash"]})
    rec = runner.screen(tasks[:1], ["cand-a"])[0]
    assert rec["local"]["exit_code"] == 3 and rec["accepted"] is False


# ---------------------------------------------------------------------------- preflight

def test_preflight_accepts_the_frozen_machine_and_loads_nothing(tmp_path):
    campaign, _ = fake_campaign(tmp_path, FIX_ALL)
    issued = []
    facts = lfr.dry_run_facts(campaign, "fake/model-a")

    def run(argv):
        issued.append(tuple(argv))
        return facts(argv)

    result = lfr.preflight(campaign, "fake/model-a", run, lambda: 99.0)
    assert result["ok"] and result["facts"]["loaded_models"] == ["fake/model-a"]
    assert result["facts"]["baseline"]["swap_used_mib"] == 100.0
    assert result["facts"]["baseline"]["pressure_free_percent"] == 80
    assert issued and set(issued) <= lfr.READ_ONLY_COMMANDS
    assert not any(a[0] == "lms" and a[1] in ("load", "unload", "get", "import") for a in issued)
    with pytest.raises(lfr.RunnerError, match="not allowed"):
        lfr.default_run(["lms", "load", "x"])
    with pytest.raises(lfr.RunnerError, match="not allowed"):
        lfr.default_run(["curl", "http://example.invalid"])


@pytest.mark.parametrize("name,patch,expected", [
    ("other model", {("lms", "ps", "--json"): json.dumps([{"identifier": "fake/model-a"},
                                                         {"identifier": "other/x"}])},
     "other_model_loaded:other/x"),
    ("no model", {("lms", "ps", "--json"): "[]"}, "expected_model_not_loaded"),
    ("wrong model", {("lms", "ps", "--json"): json.dumps([{"identifier": "other/x"}])},
     "expected_model_not_loaded"),
    ("unparseable", {("lms", "ps", "--json"): "not json"}, "lms_ps_unparseable"),
    ("chip", {("sysctl", "-n", "machdep.cpu.brand_string"): "Apple M3"}, "chip_differs"),
    ("memory", {("sysctl", "-n", "hw.memsize"): str(32 * 2**30)}, "memory_differs"),
    ("os", {("sw_vers", "-productVersion"): "26.0"}, "os_version_differs"),
    ("lms", {("lms", "version"): "0.4.0"}, "lm_studio_version_differs"),
    ("runtime", {("lms", "runtime", "ls"): "mlx 1.0.0"}, "mlx_runtime_differs"),
    ("unavailable", {("lms", "version"): None}, "fact_unavailable:lm_studio_version")])
def test_preflight_refuses(tmp_path, name, patch, expected):
    campaign, _ = fake_campaign(tmp_path, FIX_ALL)
    facts = lfr.dry_run_facts(campaign, "fake/model-a")
    result = lfr.preflight(campaign, "fake/model-a",
                           lambda argv: patch[tuple(argv)] if tuple(argv) in patch else facts(argv),
                           lambda: 99.0)
    assert not result["ok"] and expected in result["refusals"], name


def test_preflight_refuses_low_disk_and_a_failed_preflight_launches_nothing(tmp_path):
    campaign, _ = fake_campaign(tmp_path, FIX_ALL)
    low = lfr.preflight(campaign, "fake/model-a", lfr.dry_run_facts(campaign, "fake/model-a"), lambda: 1.0)
    assert "disk_below_minimum" in low["refusals"]
    runner, _, plan, tasks = make_runner(_sub(tmp_path, "r"), "compare", FIX_ALL)
    runner.preflight_run = lambda cid: lfr.dry_run_facts(campaign, "someone/else")
    with pytest.raises(lfr.PreflightRefused, match="expected_model_not_loaded"):
        runner.compare(tasks, "cand-a")
    with pytest.raises(lfr.PreflightRefused):
        runner.screen(tasks, ["cand-a"])
    assert counts(plan) == {} and "cloud_started" not in ledger_kinds(runner)


def test_machine_snapshot_values_and_unknowns():
    def run(argv):
        return {("sysctl", "-n", "vm.swapusage"): "total = 2048.00M  used = 1.50G  free = 1.00M",
                ("memory_pressure",): "The system has ... free percentage: 42%",
                ("ps", "-axo", "rss=,command="): "  2048 /Applications/LM Studio.app/x\n  10 /bin/zsh\n"
                                                  "  1024 /Applications/LM Studio.app/helper"}.get(
            tuple(argv))

    snap = lfr.machine_snapshot(run, "LM Studio")
    assert (snap["swap_used_mib"], snap["pressure_free_percent"], snap["server_rss_kib"]) == (1536.0, 42, 3072)
    empty = lfr.machine_snapshot(lambda argv: None, "LM Studio")
    assert empty["swap_used_mib"] is None and set(empty["unknown"]) == {
        "swap_used_mib", "pressure_free_percent", "server_rss_kib"}


# -------------------------------------------------------------- fresh bundle per attempt

def test_the_runner_refuses_to_hand_a_judged_bundle_to_a_candidate(tmp_path, monkeypatch):
    runner, _, _, tasks = make_runner(tmp_path, "screen", FIX_ALL)
    first, d1 = runner._bundle(tasks[0], "x")
    second, d2 = runner._bundle(tasks[0], "x")
    assert first != second and not lfr.lfc.is_judged(first)
    runner._discard(first, d1)
    runner._discard(second, d2)
    monkeypatch.setattr(lfr.lfc, "is_judged", lambda bundle: True)
    with pytest.raises(lfr.RunnerError, match="judged or reused"):
        runner._bundle(tasks[0], "y")
    monkeypatch.undo()


# --------------------------------------------------------------------- measurements (AC3)

def _log(projects, session, rows, folder="p"):
    path = projects / folder
    path.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"type": "assistant", "timestamp": "2026-10-05T10:00:00Z", "sessionId": sid,
                         "message": {"model": "m", "id": f"i{n}", "stop_reason": "end_turn", "usage": u},
                         "requestId": f"r{n}"}) for n, (sid, u) in enumerate(rows)]
    (path / f"{session}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_premium_tokens_are_attached_by_session_id_and_unknown_is_never_zero(tmp_path):
    projects = tmp_path / "projects"
    usage = {"input_tokens": 5, "cache_read_input_tokens": 1, "cache_creation_input_tokens": 2,
             "output_tokens": 3}
    _log(projects, "sess-1", [("sess-1", usage), ("sess-1", {**usage, "thinking_tokens": 1})])
    _log(projects, "sess-2", [("sess-2", {**usage, "input_tokens": 999})], folder="other")
    cfg = {"host": "claude", "projects_dir": str(projects), "layout_verified": True}
    assert lfr.premium_tokens("sess-1", {**cfg, "layout_verified": False}) == (None, "log_layout_unverified")
    assert lfr.premium_tokens("sess-1", {"host": "claude", "projects_dir": str(projects)})[1] \
        == "log_layout_unverified"  # the layout of a driver is unknown until it is declared verified
    tokens, reason = lfr.premium_tokens("sess-1", cfg)
    assert reason is None and tokens["input_tokens"] == 10 and tokens["output_tokens"] == 6
    assert tokens["reasoning_output_tokens"] is None  # one request does not report it: unknown, not 0
    assert lfr.billing_total(tokens) == 10 + 2 + 4 + 6
    assert lfr.premium_tokens("sess-3", cfg) == (None, "session_log_not_found")
    _log(projects, "sess-4", [("sess-OTHER", usage)])
    assert lfr.premium_tokens("sess-4", cfg) == (None, "log_session_id_mismatch")
    _log(projects, "sess-1", [("sess-1", usage)], folder="dup")
    assert lfr.premium_tokens("sess-1", cfg) == (None, "session_log_ambiguous")
    (projects / "bad").mkdir()
    (projects / "bad" / "sess-5.jsonl").write_text("{not json\n", encoding="utf-8")
    tokens, reason = lfr.premium_tokens("sess-5", cfg)
    assert tokens is None and reason.startswith("host_log_unreadable")
    assert lfr.billing_total(None) is None


def test_stream_parser_reads_steps_tokens_and_declared_speeds():
    stats = lfr.StreamStats({"format": "omp-json", "speed_usage_keys": {"prefill": "pp", "generation": "tg"}})
    for line in ('{"type":"tool_execution_start"}', "garbage", '{"type":"tool_execution_start"}',
                 json.dumps({"type": "message_end", "message": {"role": "assistant", "usage": {
                     "input": 10, "output": 5, "reasoningTokens": 2, "pp": 400.0, "tg": 30.0}}}),
                 json.dumps({"type": "message_end", "message": {"role": "user", "usage": {"input": 99}}})):
        stats.feed(line)
    summary = stats.summary()
    assert summary["steps"] == 2 and summary["stream_tokens"] == {"input": 10, "output": 5, "reasoning": 2}
    assert summary["prefill_tokens_per_second"] == 400.0 and summary["generation_tokens_per_second"] == 30.0


# --------------------------------------------------------------------------- report rules

def _rec(path, pr, *, accepted, rounds=None, premium=100, wall=10.0, segment="cloud", local=None,
         task_set="comparison", outcome=None, swap=(0.0, 0.0), cloud=1):
    rounds = (0 if segment == "local" else 1) if rounds is None else rounds
    local_block = None
    if segment == "local":
        local_block = {"candidate": "c", "harness": "local_harness", "ended_by_external_signal": False,
                       **(local or {})}
    return {"record_type": "attempt", "task": {"pr": pr, "set": task_set}, "path": path,
            "segment": segment, "accepted": accepted, "review": {"rounds": rounds},
            "wall_seconds": wall, "cloud_executions": cloud, "local_outcome": outcome,
            "premium": {"billing_total": premium},
            "machine": {"before": {"swap_used_mib": swap[0]}, "after": {"swap_used_mib": swap[1]}}
            if segment == "local" else None, "local": local_block}


def _campaign(tasks=2):
    campaign = json.loads((QUALIFICATION / "pat-19-campaign-v1.json").read_text("utf-8"))
    campaign["rules"]["comparison"]["tasks"] = tasks
    campaign["rules"]["screening"]["tasks"] = 2
    return campaign


def test_report_applies_the_pre_registered_comparison_rules_with_three_separate_verdicts():
    recs = []
    for pr in (1, 2):
        recs += [_rec("A", pr, accepted=True, premium=100, wall=10),
                 _rec("C", pr, accepted=None, segment="local", outcome="accepted", premium=0, cloud=0,
                      wall=5, swap=(10.0, 20.0)),
                 _rec("C", pr, accepted=True, premium=70, wall=10, segment="cloud"),
                 _rec("B", pr, accepted=True, premium=90, wall=10)]
    rep = rule_report(_campaign(), recs)["comparison"]
    c = rep["arms"]["C"]
    assert (c["compatibility"], c["quality"], c["economy"]) == ("pass", "pass", "pass")
    assert rep["arms"]["B"]["economy"] == "fail"  # 90 > 0.75 * 100
    assert rep["decision"] == "retained" and rep["recommendation"] == "C" and rep["complete"]
    # a time overrun alone fails the economy verdict; quality stays separate
    slow = [dict(r, wall_seconds=100) if r["path"] == "C" else r for r in recs]
    c = rule_report(_campaign(), slow)["comparison"]["arms"]["C"]
    assert (c["quality"], c["economy"]) == ("pass", "fail") and c["economy_detail"]["time_pass"] is False
    # extra swap of 10 GiB fails compatibility only
    swap = [dict(r, machine={"before": {"swap_used_mib": 0.0}, "after": {"swap_used_mib": 10240.0}})
            if r["segment"] == "local" else r for r in recs]
    c = rule_report(_campaign(), swap)["comparison"]["arms"]["C"]
    assert (c["compatibility"], c["quality"], c["economy"]) == ("fail", "pass", "pass")
    # more review rounds than A, or a refused task, fails quality
    c = rule_report(_campaign(), [dict(r, review={"rounds": 3}) if r["path"] == "C" else r
                                 for r in recs])["comparison"]["arms"]["C"]
    assert c["quality"] == "fail"


def test_economy_is_unavailable_when_premium_work_is_not_measurable():
    recs = [_rec("A", 1, accepted=True), _rec("C", 1, accepted=True, premium=None)]
    rep = rule_report(_campaign(1), recs)["comparison"]
    assert rep["arms"]["C"]["economy"] == "unavailable"
    assert rep["decision"] == "inconclusive"
    unknown_swap = [_rec("A", 1, accepted=True), _rec("C", 1, accepted=None, segment="local",
                                                    outcome="accepted", swap=(None, 5.0)),
                    _rec("C", 1, accepted=True)]
    assert rule_report(_campaign(1), unknown_swap)["comparison"]["arms"]["C"]["compatibility"] == "unavailable"


def test_report_stop_and_incomplete_runs_never_retain_the_hybrid():
    recs = [_rec("A", 1, accepted=True), _rec("C", 1, accepted=True, premium=10)]
    assert rule_report(_campaign(6), recs)["comparison"]["decision"] == "inconclusive"
    stopped = recs + [{"record_type": "stop", "reason": "premium_c_not_below_a"}]
    rep = rule_report(_campaign(6), stopped)
    assert rep["comparison"]["decision"] == "keep_cloud" and rep["promotion"] is False


def test_screening_selection_rule_and_threshold():
    def s(cand, pr, ok, wall):
        return dict(_rec("S", pr, accepted=ok, wall=wall, segment="local", task_set="screening",
                         local={"candidate": cand}, outcome="accepted" if ok else "refused"))

    recs = [s("a", 1, True, 50), s("a", 2, True, 50), s("b", 1, True, 10), s("b", 2, True, 10),
            s("c", 1, True, 1), s("c", 2, False, 1)]
    rep = rule_report(_campaign(), recs)["screening"]
    assert rep["selected"] == "b" and rep["reason"] == "tie_broken_by_total_duration"
    low = rule_report(_campaign(), [s("a", 1, True, 5), s("a", 2, False, 5)])["screening"]
    assert low["selected"] is None and "keep_cloud" in low["reason"]
    tie = rule_report(_campaign(), [s("a", 1, True, 5), s("a", 2, True, 5), s("b", 1, True, 5),
                                   s("b", 2, True, 5)])["screening"]
    assert tie["selected"] is None and tie["reason"] == "tie_not_resolved"


# ---------------------------------------------------------------- committed campaign config

def test_committed_campaign_config_matches_the_frozen_protocol():
    campaign = lfr.load_campaign(QUALIFICATION / "pat-19-campaign-v1.json")
    assert campaign["bounds"]["local_max_seconds"] == 1200 and campaign["bounds"]["local_max_steps"] == 40
    rules = campaign["rules"]
    assert rules["screening"] == {"tasks": 6, "min_accepted": 2}
    assert rules["comparison"] == {"tasks": 6, "min_local_successes": 2, "premium_reduction_min": 0.25,
                                   "time_ratio_max": 2.0, "extra_swap_gib_max": 10}
    assert campaign["frozen_machine"]["memory_gib"] == 64 and "M5 Pro" in campaign["frozen_machine"]["chip_contains"]
    local = campaign["drivers"]["local_harness"]
    assert local["verified"] is True and local["argv"][:4] == ["omp", "-p", "--mode", "json"]
    assert "--no-session" in local["argv"] and "--auto-approve" in local["argv"]
    for name in ("neutral_harness", "cloud_implementer_current", "cloud_implementer_economy", "cloud_reviewer"):
        assert campaign["drivers"][name]["verified"] is False and "note" in campaign["drivers"][name]
        with pytest.raises(lfr.RunnerError, match="not verified"):
            lfr._check_driver_usable(name, campaign["drivers"][name], dry_run=False)
        with pytest.raises(lfr.RunnerError, match="non-fake"):
            lfr._check_driver_usable(name, campaign["drivers"][name], dry_run=True)
    with pytest.raises(lfr.RunnerError, match="non-fake"):
        lfr._check_driver_usable("local_harness", local, dry_run=True)  # a dry run never starts a real arm
    assert len(campaign["candidates"]) == 6 and not any(c.get("verified") for c in campaign["candidates"].values())
    assert campaign["statement_footer"].startswith("\n\n---")


def test_a_fake_driver_cannot_be_used_for_a_real_run():
    with pytest.raises(lfr.RunnerError, match="not verified"):
        lfr._check_driver_usable("x", {"fake": True, "verified": True}, dry_run=False)


def test_campaign_config_rejects_unsafe_drivers(tmp_path):
    base = json.loads((QUALIFICATION / "pat-19-campaign-v1.json").read_text("utf-8"))

    def load(mutate):
        data = json.loads(json.dumps(base))
        mutate(data)
        path = tmp_path / "c.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return lfr.load_campaign(path)

    for mutate in (lambda d: d["drivers"]["local_harness"].update(network="open"),
                   lambda d: d["drivers"]["local_harness"].update(home="real"),
                   lambda d: d["drivers"]["local_harness"].update(env_allow=["LINEAR_API_KEY"]),
                   lambda d: d["drivers"]["cloud_reviewer"].update(env_allow=["FOUNDRY_STATE_DIR"]),
                   lambda d: d["drivers"]["cloud_reviewer"].update(kind="other"),
                   lambda d: d["bounds"].update(local_max_steps=-1),
                   lambda d: d.update(schema="x")):
        with pytest.raises(lfr.RunnerError):
            load(mutate)


# --------------------------------------------------------------------- isolation (AC5)

def test_work_root_must_be_outside_the_developer_checkout(tmp_path):
    repo, snap, _, _ = _make_repo(tmp_path)
    campaign, _ = fake_campaign(tmp_path, FIX_ALL)
    envelope = lfr.load_envelope(write_envelope(tmp_path), "screen", TODAY)
    with pytest.raises(lfr.RunnerError, match="inside the developer checkout"):
        lfr.Runner(repo=repo, campaign=campaign, envelope=envelope, state_dir=tmp_path / "s",
                   work_root=repo / "plugins" / "work", mode="screen", dry_run=True, sandbox=False)
    with pytest.raises(lfr.RunnerError, match="needs the sandbox"):
        lfr.Runner(repo=repo, campaign=campaign, envelope=envelope, state_dir=tmp_path / "s",
                   work_root=tmp_path / "w", mode="screen", dry_run=False, sandbox=False)


def test_isolated_environment_is_a_whitelist(tmp_path):
    host = {"PATH": "/usr/bin", "LANG": "C", "HOME": "/Users/real", "LINEAR_API_KEY": "leak",
            "GITHUB_TOKEN": "leak", "FOUNDRY_STATE_DIR": "/x", "SSH_AUTH_SOCK": "/s", "ANTHROPIC_API_KEY": "k",
            "CANARY_SECRET": "canary", "EXTRA": "ok"}
    env = lfr.isolated_environment({"kind": "local_harness"}, tmp_path, host)
    assert set(env) == {"PATH", "LANG", "HOME", "TMPDIR", "TERM", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
                        "XDG_DATA_HOME", "XDG_STATE_HOME"}
    assert env["HOME"] == str(tmp_path / "home") and "Users/real" not in json.dumps(env)
    cloud = lfr.isolated_environment({"kind": "cloud_reviewer", "home": "real", "env_allow": ["EXTRA"]}, tmp_path, host)
    assert cloud["HOME"] == "/Users/real" and cloud["EXTRA"] == "ok"
    assert not {"LINEAR_API_KEY", "GITHUB_TOKEN", "FOUNDRY_STATE_DIR", "SSH_AUTH_SOCK", "ANTHROPIC_API_KEY",
                "CANARY_SECRET"} & set(cloud)


def test_sandbox_profile_denies_writes_outside_and_non_loopback_network(tmp_path):
    bundle, scratch, secret = tmp_path / "bundle", tmp_path / "scratch", tmp_path / "dev" / "thresholds"
    profile = lfr.sandbox_profile(writable=[bundle, scratch], deny_read=[secret], network="loopback")
    lines = profile.splitlines()
    assert lines.index("(deny file-write*)") < next(i for i, x in enumerate(lines) if x.startswith("(allow file-write*"))
    allow = next(x for x in lines if x.startswith("(allow file-write*"))
    assert os.path.realpath(bundle) in allow and os.path.realpath(scratch) in allow
    assert str(tmp_path / "dev") not in allow
    assert f'(deny file-read* (subpath "{os.path.realpath(secret)}"))' in lines
    assert "(deny network*)" in lines and '(allow network* (remote ip "localhost:*"))' in lines
    assert "(deny network*)" not in lfr.sandbox_profile(writable=[bundle], deny_read=[], network="open")
    quoted = lfr.sandbox_profile(writable=[tmp_path / 'we"ird'], deny_read=[], network="open")
    assert '\\"' in quoted


PROBE = r'''
import errno, json, os, socket, sys
out = {}
def attempt(name, fn):
    try:
        fn(); out[name] = "ok"
    except OSError as exc:
        out[name] = errno.errorcode.get(exc.errno, str(exc.errno))
attempt("write_inside", lambda: open(os.path.join(sys.argv[1], "inside.txt"), "w").write("x"))
attempt("write_outside", lambda: open(sys.argv[2], "w").write("x"))
attempt("read_thresholds", lambda: open(sys.argv[3]).read())
def non_loopback():
    s = socket.socket(); s.settimeout(3); s.connect(("192.0.2.1", 80))
attempt("non_loopback", non_loopback)
def loopback():
    s = socket.socket(); s.settimeout(3); s.connect(("127.0.0.1", int(sys.argv[4])))
attempt("loopback", loopback)
out["canary_env"] = "present" if any(k.startswith(("CANARY", "FOUNDRY_", "LINEAR")) for k in os.environ) else "absent"
out["home"] = os.environ.get("HOME")
print(json.dumps(out))
'''


def _run_probe(tmp_path, sandbox, port):
    bundle, scratch = tmp_path / "bundle", tmp_path / "scratch"
    bundle.mkdir(exist_ok=True)
    thresholds = tmp_path / "dev" / "thresholds.json"
    thresholds.parent.mkdir(exist_ok=True)
    thresholds.write_text('{"premium_reduction_min": 0.25}', encoding="utf-8")
    script = tmp_path / "probe.py"
    script.write_text(PROBE, encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.unlink(missing_ok=True)
    driver = {"kind": "local_harness", "argv": [sys.executable, str(script), str(bundle), str(outside),
                                                  str(thresholds), str(port)]}
    execution = lfr.execute_driver(
        driver, {}, workdir=bundle, scratch=scratch, stream_log=tmp_path / "stream.log", max_seconds=60,
        max_steps=None, sandbox=sandbox, deny_read=[thresholds.parent],
        host_env={**os.environ, "CANARY_SECRET": "canary", "FOUNDRY_STATE_DIR": "/x", "LINEAR_API_KEY": "k"})
    probes = json.loads((tmp_path / "stream.log").read_text("utf-8").splitlines()[-1])
    return execution, probes, outside


@pytest.fixture()
def loopback_server():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    yield server.getsockname()[1]
    server.close()


def test_environment_probe_without_a_sandbox_shows_what_the_sandbox_must_stop(tmp_path, loopback_server):
    """Control: unsandboxed, a candidate can write anywhere and read the thresholds; only the
    environment whitelist already holds (it is platform independent)."""
    if sys.platform != "darwin":
        pytest.skip("control needs the macOS network stack for the non-loopback probe")
    (tmp_path / "plain").mkdir()
    _, probes, outside = _run_probe(tmp_path / "plain", False, loopback_server)
    assert probes["write_outside"] == "ok" and probes["read_thresholds"] == "ok"
    assert probes["canary_env"] == "absent" and probes["home"].endswith("scratch/home")


def _sandbox_works(tmp_path):
    scratch = tmp_path / "sbprobe"
    scratch.mkdir()
    profile = scratch / "p.sb"
    profile.write_text(lfr.sandbox_profile(writable=[scratch], deny_read=[], network="loopback"))
    return subprocess.run(["sandbox-exec", "-f", str(profile), "/usr/bin/true"],
                          capture_output=True, check=False).returncode == 0


def test_containment_probes_under_sandbox_exec(tmp_path, loopback_server):
    if not lfr.sandbox_available():
        pytest.skip("sandbox-exec is macOS only: containment probes cannot be enforced on this platform "
                    "(the generated profile and the environment whitelist are unit-tested above)")
    if not _sandbox_works(tmp_path):
        pytest.skip("sandbox-exec cannot be applied here (already inside a sandbox)")
    work = tmp_path / "boxed"
    work.mkdir()
    execution, probes, outside = _run_probe(work, True, loopback_server)
    assert execution["exit_code"] == 0, execution
    assert probes["write_inside"] == "ok"
    assert probes["write_outside"] == "EPERM" and not outside.exists()
    assert probes["read_thresholds"] == "EPERM"
    assert probes["non_loopback"] == "EPERM"
    assert probes["loopback"] == "ok"
    assert probes["canary_env"] == "absent" and probes["home"].endswith("scratch/home")


def test_a_candidate_cannot_find_the_protected_tests_or_the_merged_solution_in_its_bundle(tmp_path):
    repo, snap, _, head = _make_repo(tmp_path)
    task = snap["prs"][0]
    bundle = lfc.build_bundle(repo, task, tmp_path / "b")
    try:
        text = "\n".join(p.read_text("utf-8", "ignore") for p in bundle.rglob("*")
                         if p.is_file() and ".git/objects" not in p.as_posix())
        assert "test_added" not in text and head not in text and str(repo) not in text
        assert "premium_reduction_min" not in text  # thresholds live in the campaign config, not here
        log = subprocess.run(["git", "-C", str(bundle), "log", "--all", "--format=%H"], capture_output=True,
                             text=True, check=True).stdout.split()
        assert head not in log and len(log) == 1
    finally:
        lfc.remove_bundle(bundle)


def test_no_second_framework_and_no_routing_surface_is_touched():
    source = (Path(lfr.__file__)).read_text("utf-8")
    for forbidden in ("benchmark_evidence", "measurement_harness_v2", "routing", "claude_models"):
        assert f"import {forbidden}" not in source and f"from foundry import {forbidden}" not in source
    assert errno  # silence the unused-import linter on platforms without sandbox probes


# ------------------------------------------------------------------ CLI end to end (dry run)

def test_cli_dry_run_screen_then_report(tmp_path, capsys):
    repo, snap, _, _ = _make_repo(tmp_path)
    campaign, _ = fake_campaign(tmp_path, FIX_ALL)
    (tmp_path / "c.json").write_text(json.dumps(campaign), encoding="utf-8")
    (tmp_path / "snap.json").write_text(json.dumps(snap), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"screening": [{"pr": 1}], "comparison": [{"pr": 1}]}),
                                            encoding="utf-8")
    envelope = write_envelope(tmp_path, cloud_executions=0)
    base = ["--campaign", str(tmp_path / "c.json"), "--dry-run", "--envelope", str(envelope),
            "--state-dir", str(tmp_path / "state"), "--work-root", str(tmp_path / "work"),
            "--repo", str(repo), "--snapshot", str(tmp_path / "snap.json"),
            "--manifest", str(tmp_path / "manifest.json")]
    assert lfr.main(["screen", *base, "--candidate", "cand-a", "cand-b"], today=TODAY) == 0
    path = tmp_path / "state" / "results-test-campaign.jsonl"
    assert len(path.read_text("utf-8").splitlines()) == 2
    assert lfr.main(["report", "--campaign", str(tmp_path / "c.json"), "--results", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["promotion"] is False
    # an envelope that does not allow the mode refuses; so does the real campaign in a dry run
    assert lfr.main(["compare", *base, "--candidate", "cand-a", "--paths", "A"], today=TODAY) == 3  # cap 0
    assert "cap_reached:cloud_executions" in path.read_text("utf-8")
    only_compare = tmp_path / "only-compare.json"
    only_compare.write_text(json.dumps({**json.loads(envelope.read_text("utf-8")),
                                        "allowed_modes": ["compare"]}), encoding="utf-8")
    other = [*base[:4], str(only_compare), *base[5:]]
    assert lfr.main(["screen", *other, "--candidate", "cand-a"], today=TODAY) == 2
    real = ["--campaign", str(QUALIFICATION / "pat-19-campaign-v1.json")] + base[2:]
    assert lfr.main(["screen", *real, "--candidate", "qwen3.8-27b-mlx-6bit"], today=TODAY) == 2


def test_cli_preflight_dry_run_prints_facts(tmp_path, capsys):
    campaign, _ = fake_campaign(tmp_path, FIX_ALL)
    (tmp_path / "c.json").write_text(json.dumps(campaign), encoding="utf-8")
    assert lfr.main(["preflight", "--campaign", str(tmp_path / "c.json"), "--dry-run",
                     "--candidate", "cand-a"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


# ============================================================== PAT-108 review round 1 (B1, N1-N11)

SLEEPER = r"""
import os, subprocess, sys, time
child = subprocess.Popen(["sleep", "60"])
open(sys.argv[1], "w").write(f"{os.getpid()} {child.pid}")
time.sleep(60)
"""


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _interrupt_when_ready(pids, sig):
    def go():
        for _ in range(200):
            if pids.exists() and len(pids.read_text("utf-8").split()) == 2:
                break
            time.sleep(0.05)
        os.kill(os.getpid(), sig)

    thread = threading.Thread(target=go, daemon=True)
    thread.start()
    return thread


@pytest.mark.parametrize("sig,expected", [(signal.SIGTERM, SystemExit), (signal.SIGINT, KeyboardInterrupt)])
def test_B1_an_interrupted_execution_kills_the_whole_process_group(tmp_path, sig, expected):
    script, pids = tmp_path / "sleeper.py", tmp_path / "pids"
    script.write_text(SLEEPER, encoding="utf-8")
    driver = {"kind": "cloud_implementer", "home": "real", "network": "open",
              "argv": [sys.executable, str(script), str(pids)]}
    before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)}
    _interrupt_when_ready(pids, sig)
    with pytest.raises(expected):
        lfr.execute_driver(driver, {}, workdir=tmp_path, scratch=tmp_path / "scratch",
                           stream_log=tmp_path / "stream.log", max_seconds=60, max_steps=None,
                           sandbox=False, deny_read=[], host_env=dict(os.environ))
    survivors = []
    for pid in map(int, pids.read_text("utf-8").split()):
        for _ in range(60):
            if not _alive(pid):
                break
            time.sleep(0.1)
        else:
            survivors.append(pid)
    for pid in survivors:  # never leave a stray process behind if the assertion below fails
        os.kill(pid, signal.SIGKILL)
    assert not survivors, "the arm or its child outlived the interrupted call"
    assert {s: signal.getsignal(s) for s in before} == before  # the exact previous handlers are back


def test_B1_an_interrupted_cloud_execution_is_settled_with_unknown_tokens(tmp_path, monkeypatch):
    runner, _, _, tasks = make_runner(tmp_path, "compare", FIX_ALL)

    def interrupted(*a, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(lfr, "execute_driver", interrupted)
    with pytest.raises(KeyboardInterrupt):
        runner.cloud_path(tasks[0], "A", "comparison")
    totals = runner.ledger.totals()
    assert totals["cloud_started"] == 1 and totals["unsettled_sessions"] == []
    assert totals["tokens_unmeasurable"] is True  # unknown, never counted as 0
    with pytest.raises(lfr.CapReached, match="premium_tokens_unmeasurable"):
        runner.ledger.check(cloud=True)


def test_B1_a_resume_on_an_orphan_cloud_started_refuses_a_new_cloud_execution(tmp_path):
    """A launcher killed between ``cloud_started`` and ``settled`` leaves an execution of unknown
    cost: the relaunched campaign must not count it as 0 tokens."""
    runner, _, plan, tasks = make_runner(tmp_path, "compare", FIX_ALL)
    runner.ledger.append("cloud_started", role="implementer", session_id="orphan-1")
    again, _, _, _ = make_runner(_sub(tmp_path, "second"), "compare", FIX_ALL)  # fresh state: control
    assert again.ledger.totals()["tokens_unmeasurable"] is False
    resumed = lfr.Ledger(runner.state_dir, lfr.load_envelope(write_envelope(tmp_path), "compare", TODAY),
                         "compare", dry_run=True)
    assert resumed.totals()["unsettled_sessions"] == ["orphan-1"] and resumed.totals()["tokens_unmeasurable"]
    with pytest.raises(lfr.CapReached, match="premium_tokens_unmeasurable"):
        resumed.reserve_cloud("implementer", "new")
    runner.compare(tasks, "cand-a", ("A",))
    assert counts(plan) == {} and results(runner)[-1]["reason"] == "cap_reached:premium_tokens_unmeasurable"
    resumed.append("settled", cloud=True, seconds=1, premium_tokens=5, session_id="orphan-1")  # paired
    assert resumed.totals()["unsettled_sessions"] == [] and not resumed.totals()["tokens_unmeasurable"]


# ------------------------------------------------------------------------------------- N1

def _fake_home(tmp_path):
    home = tmp_path / "home"
    for rel in (".claude/plugins/cache/foundry/1.0.0/tests", ".ssh", ".config/foundry", ".aws"):
        (home / rel).mkdir(parents=True)
    (home / ".claude/plugins/cache/foundry/1.0.0/tests/test_linear_tracker.py").write_text("canary")
    (home / ".ssh/id_ed25519").write_text("canary")
    (home / ".config/foundry/config.json").write_text("canary")
    (home / ".aws/credentials").write_text("canary")
    return home


def test_N1_the_read_deny_list_covers_home_configuration_other_worktrees_and_launcher_inputs(tmp_path):
    repo, _, _, _ = _make_repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(tmp_path / "other-wt")],
                   check=True, capture_output=True)
    home, inputs = _fake_home(tmp_path), tmp_path / "inputs"
    inputs.mkdir()
    envelope = inputs / "envelope.json"
    envelope.write_text("{}")
    kwargs = dict(repo=repo, home=home, state_dir=tmp_path / "state", input_paths=[envelope])
    local = {os.path.realpath(p) for p in lfr.read_deny_list(kind="local_harness", **kwargs)}
    secrets = [".ssh", ".gnupg", ".aws", ".netrc", "Library/Keychains", ".git-credentials", ".npmrc",
               ".pypirc", ".docker", ".kube", ".zsh_history", ".bash_history", ".python_history"]
    for expected in (home / ".claude", home / ".claude.json", home / ".codex", home / ".config",
                     *(home / name for name in secrets), repo, tmp_path / "other-wt",
                     tmp_path / "state", inputs):
        assert os.path.realpath(expected) in local, expected
    cloud = {os.path.realpath(p) for p in lfr.read_deny_list(kind="cloud_implementer", **kwargs)}
    assert os.path.realpath(home / ".claude") not in cloud  # OAuth identity (AGENTS.md R6)
    assert os.path.realpath(home / ".claude.json") not in cloud  # Claude Code reads and writes it
    assert all(os.path.realpath(home / name) in cloud for name in secrets)
    assert os.path.realpath(home / ".ssh") in cloud and os.path.realpath(home / ".config/foundry") in cloud
    custom = lfr.read_deny_list(kind="local_harness", isolation={"local": [".only"], "cloud": []}, **kwargs)
    assert os.path.realpath(home / ".only") in {os.path.realpath(p) for p in custom}
    assert os.path.realpath(home / ".ssh") not in {os.path.realpath(p) for p in custom}


def test_N1_a_writable_root_inside_a_denied_read_path_is_refused(tmp_path):
    with pytest.raises(lfr.RunnerError, match="read-denied"):
        lfr.sandbox_profile(writable=[tmp_path / "inputs" / "work"], deny_read=[tmp_path / "inputs"],
                            network="loopback")


def test_N1_the_campaign_config_declares_the_home_deny_list_and_rejects_a_bad_one(tmp_path):
    campaign = lfr.load_campaign(QUALIFICATION / "pat-19-campaign-v1.json")
    assert campaign["isolation"]["deny_read_home"]["local"] == lfr.DEFAULT_HOME_DENY["local"]
    assert campaign["isolation"]["deny_read_home"]["cloud"] == lfr.DEFAULT_HOME_DENY["cloud"]
    for bad in ({"local": ["/abs"], "cloud": []}, {"local": ["../x"], "cloud": []}, {"local": []}):
        data = json.loads(json.dumps(campaign))
        data["isolation"]["deny_read_home"] = bad
        path = tmp_path / "c.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(lfr.RunnerError, match="deny_read_home"):
            lfr.load_campaign(path)


READ_PROBE = r"""
import errno, json, os, sys
out = {}
for name, path in zip(sys.argv[1::2], sys.argv[2::2]):
    try:
        open(path).read(); out[name] = "ok"
    except OSError as exc:
        out[name] = errno.errorcode.get(exc.errno, str(exc.errno))
print(json.dumps(out))
"""


def test_N1_a_local_candidate_cannot_read_plugin_cache_ssh_config_or_launcher_inputs(tmp_path, monkeypatch):
    if not lfr.sandbox_available():
        pytest.skip("sandbox-exec is macOS only: read-denial probes cannot run on this platform "
                    "(the deny list and the profile are unit-tested above)")
    if not _sandbox_works(tmp_path):
        pytest.skip("sandbox-exec cannot be applied here (already inside a sandbox)")
    repo, _, _, _ = _make_repo(tmp_path)
    home, inputs, boxed = _fake_home(tmp_path), tmp_path / "inputs", tmp_path / "boxed"
    inputs.mkdir()
    boxed.mkdir()
    envelope = inputs / "envelope.json"
    envelope.write_text("canary")
    bundle = boxed / "bundle"
    bundle.mkdir()
    control = bundle / "control.txt"
    control.write_text("fine")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    monkeypatch.setattr(lfr.tempfile, "mkdtemp", lambda prefix: str(profiles))
    monkeypatch.setattr(lfr.shutil, "rmtree", lambda *a, **kw: None)  # keep the profile for the assert
    targets = {"plugin_cache": home / ".claude/plugins/cache/foundry/1.0.0/tests/test_linear_tracker.py",
               "ssh": home / ".ssh/id_ed25519", "config": home / ".config/foundry/config.json",
               "aws": home / ".aws/credentials", "envelope": envelope, "control": control,
               "profile": profiles / "profile.sb"}
    script = tmp_path / "read_probe.py"
    script.write_text(READ_PROBE, encoding="utf-8")
    argv = [sys.executable, str(script)] + [x for k, v in targets.items() for x in (k, str(v))]
    driver = {"kind": "local_harness", "argv": argv}
    deny = lfr.read_deny_list(repo=repo, home=home, state_dir=tmp_path / "state", input_paths=[envelope],
                              kind="local_harness")
    execution = lfr.execute_driver(driver, {}, workdir=bundle, scratch=boxed / "scratch",
                                   stream_log=tmp_path / "stream.log", max_seconds=60, max_steps=None,
                                   sandbox=True, deny_read=deny, host_env=dict(os.environ))
    assert execution["exit_code"] == 0, execution
    probes = json.loads((tmp_path / "stream.log").read_text("utf-8").splitlines()[-1])
    assert probes == {"plugin_cache": "EPERM", "ssh": "EPERM", "config": "EPERM", "aws": "EPERM",
                      "envelope": "EPERM", "control": "ok", "profile": "EPERM"}
    assert (profiles / "profile.sb").is_file()  # the launcher itself could read it


# ------------------------------------------------------------------------------------- N2

def test_N2_a_candidate_that_commits_or_adds_a_file_still_yields_a_reviewable_patch(tmp_path):
    runner, _, _, tasks = make_runner(tmp_path, "screen", FIX_ALL)
    bundle, attempt = runner._bundle(tasks[0], "commit")
    (bundle / lfc_mod()).write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (bundle / "plugins/foundry/tooling/foundry/new_mod.py").write_text("X = 1\n", encoding="utf-8")
    lfr._git_in(bundle, "add", "-A")
    lfr._git_in(bundle, "commit", "-q", "-m", "the candidate committed its work")
    patch = runner._patch_of(bundle)
    runner._discard(bundle, attempt)
    assert b"new_mod.py" in patch and b"return a + b" in patch  # not an empty patch against HEAD
    review, review_attempt = runner._bundle(tasks[0], "review", patch)
    changed = lfr._git_in(review, "diff", "HEAD", "--name-only").decode().split()
    runner._discard(review, review_attempt)
    assert sorted(changed) == ["plugins/foundry/tooling/foundry/m.py",
                               "plugins/foundry/tooling/foundry/new_mod.py"]  # new file visible too


def lfc_mod():
    return "plugins/foundry/tooling/foundry/m.py"


def test_N2_a_failed_patch_leaves_no_bundle_on_disk(tmp_path):
    runner, _, _, tasks = make_runner(tmp_path, "screen", FIX_ALL)
    with pytest.raises(lfr.RunnerError, match="git apply failed"):
        runner._bundle(tasks[0], "bad", b"this is not a patch\n")
    assert not list((tmp_path / "work").iterdir()) and not runner.guards and not runner.handed


# ------------------------------------------------------------------------------------- N3

def _rerun_env(tmp_path):
    return _make_repo(tmp_path)


def test_N3_a_replayed_attempt_is_refused_and_a_duplicate_record_never_counts(tmp_path):
    first = _make_repo(tmp_path)
    runner, _, plan, tasks = make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)
    runner.screen(tasks[:1], ["cand-a"])
    assert counts(plan) == {"local": 1}
    again, _, _, tasks = make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)
    with pytest.raises(lfr.RunnerError, match="already recorded"):
        again.screen(tasks[:1], ["cand-a"])
    assert counts(plan) == {"local": 1}  # the replayed candidate never ran
    assert results(again)[-1]["reason"].startswith("tool_error:")
    dup = [json.loads(line) for line in again.results_path.read_text("utf-8").splitlines()][0]
    with pytest.raises(lfr.RunnerError, match="duplicated attempts"):
        lfr.report(_campaign(), [dup, dup], ledger_of(again))


def test_N3_the_selection_waits_for_every_candidate_to_have_every_screening_task():
    def s(cand, pr, ok):
        return dict(_rec("S", pr, accepted=ok, segment="local", task_set="screening",
                         local={"candidate": cand}, outcome="accepted" if ok else "refused"))

    partial = lfc_report([s("a", 1, True), s("a", 2, True), s("b", 1, True)])
    assert partial["selected"] is None and partial["complete"] is False
    assert partial["reason"] == "incomplete_screening"
    done = lfc_report([s("a", 1, True), s("a", 2, True), s("b", 1, True), s("b", 2, False)])
    assert done["complete"] and done["selected"] == "a"


def lfc_report(recs):
    return rule_report(_campaign(), recs)["screening"]


# ------------------------------------------------------------------------------------- N4

def test_N4_records_carry_the_config_digests_and_the_rules_cannot_change_after_the_fact(tmp_path):
    first = _make_repo(tmp_path)
    runner, campaign, _, tasks = make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)
    rec = runner.screen(tasks[:1], ["cand-a"])[0]
    envelope_sha = hashlib.sha256((tmp_path / "envelope.json").read_bytes()).hexdigest()
    assert rec["envelope_sha256"] == envelope_sha and rec["campaign_sha256"] == lfr.config_digest(campaign)
    started = json.loads(runner.ledger.path.read_text("utf-8").splitlines()[0])
    assert started["kind"] == "session_started" and started["campaign_sha256"] == rec["campaign_sha256"]
    rep = lfr.report(campaign, results(runner), ledger_of(runner), campaign_sha256=rec["campaign_sha256"])
    assert rep["rules_applied"] == campaign["rules"]  # the applied rules are printed
    assert rep["provenance"]["envelope_sha256"] == envelope_sha
    with pytest.raises(lfr.RunnerError, match="campaign_sha256 mismatch"):
        lfr.report(campaign, results(runner), ledger_of(runner), campaign_sha256="0" * 64)
    # results of two campaign configs never share a file
    loosened, _, _, tasks = make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)
    mixed = results(loosened) + [dict(results(loosened)[0], campaign_sha256="f" * 64)]
    with pytest.raises(lfr.RunnerError, match="several campaign_sha256"):
        lfr.report(campaign, mixed, ledger_of(loosened))
    changed = json.loads(json.dumps(campaign))
    changed["rules"]["screening"]["min_accepted"] = 1  # a rule edited after the first record
    envelope = lfr.load_envelope(write_envelope(tmp_path), "screen", TODAY)
    with pytest.raises(lfr.RunnerError, match="another campaign_sha256"):
        lfr.Runner(repo=first[0], campaign=changed, envelope=envelope, state_dir=tmp_path / "state",
                   work_root=tmp_path / "work", mode="screen", dry_run=True, sandbox=False)


def test_N4_the_cli_report_refuses_another_campaign_file_and_prints_the_rules(tmp_path, capsys):
    repo, snap, _, _ = _make_repo(tmp_path)
    campaign, _ = fake_campaign(tmp_path, FIX_ALL)
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps(campaign), encoding="utf-8")
    (tmp_path / "snap.json").write_text(json.dumps(snap), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"screening": [{"pr": 1}], "comparison": [{"pr": 1}]}),
                                            encoding="utf-8")
    envelope = write_envelope(tmp_path, cloud_executions=0)
    assert lfr.main(["screen", "--campaign", str(cfg), "--dry-run", "--envelope", str(envelope),
                     "--state-dir", str(tmp_path / "state"), "--work-root", str(tmp_path / "work"),
                     "--repo", str(repo), "--snapshot", str(tmp_path / "snap.json"),
                     "--manifest", str(tmp_path / "manifest.json"), "--candidate", "cand-a"],
                    today=TODAY) == 0
    path = tmp_path / "state" / "results-test-campaign.jsonl"
    assert lfr.main(["report", "--campaign", str(cfg), "--results", str(path)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["rules_applied"]["screening"]["min_accepted"] == 2 and printed["dry_run"] is True
    campaign["rules"]["screening"]["min_accepted"] = 0
    other = tmp_path / "other.json"
    other.write_text(json.dumps(campaign), encoding="utf-8")
    assert lfr.main(["report", "--campaign", str(other), "--results", str(path)]) == 2
    assert "campaign_sha256 mismatch" in capsys.readouterr().err


# ------------------------------------------------------------------------------------- N5

def test_N5_dry_run_and_real_records_never_share_a_state_directory(tmp_path):
    first = _make_repo(tmp_path)
    runner, campaign, _, tasks = make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)
    rec = runner.screen(tasks[:1], ["cand-a"])[0]
    assert rec["dry_run"] is True
    assert all(json.loads(x)["dry_run"] is True for x in runner.ledger.path.read_text("utf-8").splitlines())
    envelope = lfr.load_envelope(write_envelope(tmp_path), "screen", TODAY)
    with pytest.raises(lfr.RunnerError, match="mix dry-run and real"):
        lfr.Runner(repo=first[0], campaign=campaign, envelope=envelope, state_dir=tmp_path / "state",
                   work_root=tmp_path / "work", mode="screen", dry_run=False, sandbox=True)
    with pytest.raises(lfr.RunnerError, match="mix dry-run and real"):
        lfr.Ledger(tmp_path / "state", envelope, "screen", dry_run=False)
    with pytest.raises(lfr.RunnerError, match="mix dry-run and real"):
        lfr.report(campaign, [rec, dict(rec, dry_run=False, attempt=7)], ledger_of(runner))
    # a dry-run cloud_started never counts against a real cap
    ledger = lfr.Ledger(tmp_path / "other", envelope, "compare", dry_run=True)
    ledger.append("cloud_started", role="x", session_id="s")
    real = lfr.Ledger.__new__(lfr.Ledger)
    real.path, real.dry_run = ledger.path, False
    assert real.totals()["cloud_started"] == 0


# ------------------------------------------------------------------------------------- N6

def test_N6_an_unreadable_review_is_unknown_not_a_failed_task(tmp_path):
    plan = {**FIX_ALL, "reviewer": ["PASS", "garbage"]}
    runner, campaign, _, tasks = make_runner(tmp_path, "compare", plan)
    out = runner.compare(tasks[:1], "cand-a", ("A", "C"))
    unreadable = next(r for r in out if r["path"] == "C" and r["segment"] == "local")
    assert unreadable["outcome"] == "review_unreadable" and unreadable["accepted"] is None
    assert unreadable["unknown"]["review"] == "review_unreadable"
    campaign["rules"]["comparison"]["tasks"] = 1
    arm = lfr.report(campaign, results(runner), ledger_of(runner))["comparison"]["arms"]["C"]
    assert arm["quality"] == "unavailable"  # not "fail"


def test_N6_a_tool_error_after_a_paid_execution_is_recorded_before_the_campaign_stops(tmp_path, monkeypatch):
    runner, _, _, tasks = make_runner(tmp_path, "compare", FIX_ALL)

    def broken(*a, **kw):
        raise lfc.CorpusError("judge tool failure")

    monkeypatch.setattr(lfc, "judge", broken)
    with pytest.raises(lfc.CorpusError):
        runner.compare(tasks[:1], "cand-a", ("A",))
    *_, error, stop = results(runner)
    assert error["status"] == "tool_error" and error["outcome"] == "tool_error"
    assert error["reason"] == "judge tool failure" and error["accepted"] is None
    assert error["cloud_executions"] == 1 and error["premium"]["billing_total"] == 135  # the paid arm
    assert stop["record_type"] == "stop" and stop["reason"].startswith("tool_error:")
    rep = lfr.report(_campaign(1), [error, stop], ledger_of(runner))  # no reference path: nothing is concluded
    assert rep["comparison"]["decision"] == "inconclusive"


def test_N6_a_local_tool_error_is_recorded_and_makes_the_screening_incomplete(tmp_path, monkeypatch):
    runner, _, _, tasks = make_runner(tmp_path, "screen", FIX_ALL)

    def broken(*a, **kw):
        raise lfc.CorpusError("judge tool failure")

    monkeypatch.setattr(lfc, "judge", broken)
    with pytest.raises(lfc.CorpusError):
        runner.screen(tasks[:1], ["cand-a"])
    error = results(runner)[-2]
    assert error["status"] == "tool_error" and error["path"] == "S" and error["local"]["candidate"] == "cand-a"
    rep = lfr.report(_campaign(), results(runner), ledger_of(runner))["screening"]
    assert rep["selected"] is None and rep["complete"] is False  # a tool failure is not a refusal


def test_N6_a_stop_on_a_cap_is_inconclusive_a_rule_stop_keeps_the_cloud():
    recs = [_rec("A", 1, accepted=True), _rec("C", 1, accepted=True, premium=10)]
    capped = rule_report(_campaign(6), recs + [{"record_type": "stop", "reason": "cap_reached:premium_tokens"}])
    assert capped["comparison"]["decision"] == "inconclusive" and capped["comparison"]["recommendation"] is None
    errored = rule_report(_campaign(6), recs + [{"record_type": "stop", "reason": "tool_error:x"}])
    assert errored["comparison"]["decision"] == "inconclusive"
    ruled = rule_report(_campaign(6), recs + [{"record_type": "stop", "reason": "fewer_than_min_local_successes"}])
    assert ruled["comparison"]["decision"] == "keep_cloud"


# ------------------------------------------------------------------------------------- N7 / N10

def test_N7_cloud_drivers_declare_an_unverified_log_layout_so_premium_tokens_stay_unknown():
    campaign = lfr.load_campaign(QUALIFICATION / "pat-19-campaign-v1.json")
    for name in ("cloud_implementer_current", "cloud_implementer_economy", "cloud_reviewer"):
        log = campaign["drivers"][name]["session_log"]
        assert log["layout_verified"] is False and "layout_note" in log
        assert lfr.premium_tokens("any", log) == (None, "log_layout_unverified")


def test_N10_choices_that_are_not_protocol_coordinates_are_labelled_and_the_breakdown_is_reported(tmp_path):
    campaign = lfr.load_campaign(QUALIFICATION / "pat-19-campaign-v1.json")
    choices = campaign["non_protocol_choices"]
    assert set(choices) == {"min_free_disk_gib", "economy_time_counted", "swap_metric", "premium_work_definition"}
    for name, choice in choices.items():
        assert choice["not_a_protocol_coordinate"] is True and choice["rationale"], name
    runner, camp, _, tasks = make_runner(tmp_path, "compare", FIX_ALL)
    out = runner.compare(tasks[:1], "cand-a", ("A", "B"))
    assert out[0]["premium"]["by_model"] == {"claude-sonnet-5-5": {
        "input_tokens": 130, "cached_input_tokens": 10, "cache_write_input_tokens": 5,
        "output_tokens": 30, "reasoning_output_tokens": None}}
    camp["rules"]["comparison"]["tasks"] = 1
    detail = lfr.report(camp, results(runner), ledger_of(runner))["comparison"]["arms"]["B"]["economy_detail"]
    assert detail["reference_premium_by_model"]["claude-sonnet-5-5"]["output_tokens"] == 30
    assert "unweighted" in detail["premium_definition"]


# ------------------------------------------------------------------------------------- N8

def test_N8_git_never_runs_on_a_bundle_whose_config_or_attributes_were_changed(tmp_path):
    runner, _, _, tasks = make_runner(tmp_path, "screen", FIX_ALL)
    for name, tamper in {
            "config": lambda b: (b / ".git" / "config").open("a").write('[core]\n\tfsmonitor = "touch x"\n'),
            "attributes": lambda b: (b / ".gitattributes").write_text("* filter=evil\n"),
            "info attributes": lambda b: ((b / ".git" / "info").mkdir(),
                                          (b / ".git" / "info" / "attributes").write_text("* diff=evil\n")),
            "git replaced": lambda b: (shutil.rmtree(b / ".git"), (b / ".git").symlink_to(tmp_path))}.items():
        bundle, attempt = runner._bundle(tasks[0], "tamper")
        tamper(bundle)
        with pytest.raises(lfr.RunnerError, match="git configuration|not a plain directory"):
            runner._patch_of(bundle)
        shutil.rmtree(attempt, ignore_errors=True)
        runner.guards.pop(bundle, None)
        lfc._CREATED_BUNDLES.discard(bundle)


def test_N8_git_commands_on_a_bundle_are_neutralised(tmp_path, monkeypatch):
    runner, _, _, tasks = make_runner(tmp_path, "screen", FIX_ALL)
    seen = []
    real = subprocess.run

    def spy(cmd, *a, **kw):
        if cmd[:1] == ["git"] and "-C" in cmd:
            seen.append((cmd, kw.get("env")))
        return real(cmd, *a, **kw)

    bundle, attempt = runner._bundle(tasks[0], "x")
    monkeypatch.setattr(lfr.subprocess, "run", spy)
    runner._patch_of(bundle)
    monkeypatch.undo()
    runner._discard(bundle, attempt)
    assert seen
    for cmd, env in seen:
        for option in ("core.fsmonitor=false", f"core.hooksPath={os.devnull}", "protocol.file.allow=never"):
            assert option in cmd
        assert env["GIT_CONFIG_GLOBAL"] == os.devnull and env["GIT_CONFIG_SYSTEM"] == os.devnull
    diff = next(cmd for cmd, _ in seen if "diff" in cmd)
    assert "--no-ext-diff" in diff and "--no-textconv" in diff


def test_N8_a_bundle_that_is_still_changing_is_not_judged(tmp_path):
    runner, _, _, tasks = make_runner(tmp_path, "screen", FIX_ALL)
    bundle, attempt = runner._bundle(tasks[0], "busy")
    stop = threading.Event()

    def writer():
        n = 0
        while not stop.is_set():
            n += 1
            (bundle / "late.txt").write_text("x" * n, encoding="utf-8")
            time.sleep(0.01)

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    runner.quiescent_wait = 0.3
    try:
        with pytest.raises(lfr.RunnerError, match="not quiescent"):
            runner._patch_of(bundle)
    finally:
        stop.set()
        thread.join()
        runner._discard(bundle, attempt)


# ------------------------------------------------------------------------------------- N11

def test_N11_the_envelope_expiry_is_checked_before_each_cloud_execution(tmp_path):
    runner, _, plan, tasks = make_runner(tmp_path, "compare", FIX_ALL)
    runner.cloud_path(tasks[0], "A", "comparison")
    runner.today = lambda: dt.date(2027, 1, 1)  # the envelope expired while the campaign was running
    with pytest.raises(lfr.EnvelopeError, match="expired"):
        runner.cloud_path(tasks[1], "A", "comparison")
    assert counts(plan) == {"implementer": 1, "reviewer": 1}
    assert ledger_kinds(runner).count("cloud_started") == 2


def test_N11_a_truncated_local_attempt_records_the_effective_bound(tmp_path):
    runner, _, _, tasks = make_runner(tmp_path, "screen", FIX_ALL, caps={"wall_clock_seconds": 5})
    rec = runner.screen(tasks[:1], ["cand-a"])[0]
    assert rec["local"]["max_seconds"] == 5.0 and rec["local"]["nominal_max_seconds"] == 20


def test_N11_the_preflight_is_repeated_between_tasks_and_a_b_paths_do_not_need_the_local_model(tmp_path):
    runner, _, _, tasks = make_runner(tmp_path, "screen", FIX_ALL)
    runner.screen(tasks, ["cand-a"])
    assert ledger_kinds(runner).count("preflight") == 2
    second, _, _, tasks2 = make_runner(_sub(tmp_path, "ab"), "compare", FIX_ALL)
    second.preflight_run = lambda cid: pytest.fail("the local model must not be needed for A and B")
    second.compare(tasks2[:1], "cand-a", ("A", "B"))
    assert "preflight" not in ledger_kinds(second)
    third, _, _, tasks3 = make_runner(_sub(tmp_path, "c"), "compare", FIX_ALL)
    third.compare(tasks3, "cand-a", ("C",))
    assert ledger_kinds(third).count("preflight") == 2


def test_N11_an_unknown_preflight_candidate_is_a_clean_error(tmp_path, capsys):
    campaign, _ = fake_campaign(tmp_path, FIX_ALL)
    (tmp_path / "c.json").write_text(json.dumps(campaign), encoding="utf-8")
    assert lfr.main(["preflight", "--campaign", str(tmp_path / "c.json"), "--dry-run",
                     "--candidate", "nope"]) == 2
    assert "unknown candidate nope" in capsys.readouterr().err


def test_N11_the_containment_probes_report_passed_or_skipped_explicitly(tmp_path, loopback_server):
    """Not a probe itself: the probes above skip with their reason off macOS or inside a sandbox, so
    ``pytest -rs`` states for each machine whether containment was passed or skipped."""
    status = "passed" if lfr.sandbox_available() and _sandbox_works(tmp_path) else "skipped"
    assert status in ("passed", "skipped")


# ====================================================== PAT-108 review round 2 (B1', items 1-8)
# The ledger and the results never disagree about spent work, and ``report`` gives no economy
# verdict (and no decision) while the ledger knows that some spent work is unknown.

def _interrupt_nth(monkeypatch, kind, nth, exc=KeyboardInterrupt):
    """The ``nth`` execution of a driver of ``kind`` is cut, like a Ctrl-C while the arm runs."""
    real, seen = lfr.execute_driver, []

    def cut(driver, values, **kw):
        if driver["kind"] == kind:
            seen.append(1)
            if len(seen) == nth:
                raise exc
        return real(driver, values, **kw)

    monkeypatch.setattr(lfr, "execute_driver", cut)


def _hard_crash(runner):
    """Rewrite the state as a SIGKILL would have left it: the interrupted record, the stop and the
    settlement of the interrupted execution were never written."""
    kept = [r for r in results(runner) if r.get("outcome") != "interrupted" and r["record_type"] != "stop"]
    runner.results_path.write_text("".join(json.dumps(r) + "\n" for r in kept), encoding="utf-8")
    entries = [e for e in ledger_of(runner) if not e.get("interrupted") and e["kind"] != "stopped"]
    runner.ledger.path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")


B1P_CASES = {
    # path B: round 0 recorded (review BLOCK), the correction is interrupted after it started
    "B": dict(plan={**FIX_ALL, "reviewer": ["PASS", "BLOCK"]}, paths=("A", "B"), nth=3,
              before=["review_block"], segment="cloud"),
    # path C: the local attempt is recorded (refused), the takeover is interrupted after it started
    "C": dict(plan={**FIX_ALL, "local": ["partial"]}, paths=("A", "C"), nth=2,
              before=["local_refused"], segment="takeover")}


@pytest.mark.parametrize("path", ["B", "C"])
def test_B1p_an_interrupted_correction_or_takeover_is_recorded_and_never_counts_as_zero(
        tmp_path, monkeypatch, path):
    case = B1P_CASES[path]
    runner, campaign, _, tasks = make_runner(tmp_path, "compare", case["plan"])
    campaign["rules"]["comparison"]["tasks"] = 1
    _interrupt_nth(monkeypatch, "cloud_implementer", case["nth"])
    with pytest.raises(KeyboardInterrupt):
        runner.compare(tasks[:1], "cand-a", case["paths"])
    monkeypatch.undo()
    *recs, stop = results(runner)
    arm = [r for r in recs if r["path"] == path]
    assert [r["outcome"] for r in arm] == [*case["before"], "interrupted"]
    cut = arm[-1]
    assert cut["status"] == "interrupted" and cut["reason"].startswith("KeyboardInterrupt")
    assert cut["segment"] == case["segment"] and cut["accepted"] is None
    assert cut["premium"]["billing_total"] is None and len(cut["cloud_sessions"]) == 1  # unknown, never 0
    assert stop["record_type"] == "stop" and stop["reason"] == "interrupted:KeyboardInterrupt"
    assert ledger_of(runner)[-1]["kind"] == "stopped"
    rep = lfr.report(campaign, results(runner), ledger_of(runner))
    assert rep["comparison"]["arms"][path]["economy"] == "unavailable"
    assert rep["comparison"]["arms"][path]["quality"] == "unavailable"
    assert rep["comparison"]["decision"] == "inconclusive" and rep["comparison"]["recommendation"] is None
    assert rep["ledger"]["unknown_spent_work"] == [f"interrupted:{cut['cloud_sessions'][0]}"]
    # the same interruption as a hard crash: nothing could be written after the ``cloud_started``
    _hard_crash(runner)
    assert [r["outcome"] for r in results(runner) if r["path"] == path] == case["before"]
    crashed = lfr.report(campaign, results(runner), ledger_of(runner))
    assert crashed["comparison"]["arms"][path]["economy"] == "unavailable"  # the results alone say "pass"
    assert crashed["comparison"]["decision"] == "inconclusive"
    lost = cut["cloud_sessions"][0]
    assert crashed["ledger"]["unknown_spent_work"] == [f"cloud_started_without_settled:{lost}",
                                                       f"no_result_record:{lost}"]


def test_B1p_a_sigterm_during_a_cloud_arm_leaves_an_interrupted_record_and_no_verdict(tmp_path):
    runner, campaign, plan, tasks = make_runner(tmp_path, "compare", {**FIX_ALL, "economy": ["hang"]})
    campaign["rules"]["comparison"]["tasks"] = 1
    child = Path(str(plan) + ".child")

    def terminate():
        for _ in range(400):
            if child.exists() and child.read_text("utf-8").strip():
                break
            time.sleep(0.05)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=terminate, daemon=True).start()
    with pytest.raises(SystemExit):
        runner.compare(tasks[:1], "cand-a", ("A", "B"))
    *_, cut, stop = results(runner)
    assert (cut["path"], cut["status"], cut["premium"]["billing_total"]) == ("B", "interrupted", None)
    assert stop["reason"] == "interrupted:SystemExit"
    for _ in range(60):
        if not _alive(int(child.read_text("utf-8"))):
            break
        time.sleep(0.1)
    else:
        pytest.fail("the child of the interrupted arm survived")
    rep = lfr.report(campaign, results(runner), ledger_of(runner))
    assert rep["comparison"]["arms"]["B"]["economy"] == "unavailable"
    assert rep["comparison"]["decision"] == "inconclusive"
    with pytest.raises(lfr.CapReached, match="premium_tokens_unmeasurable"):
        runner.ledger.check(cloud=True)


def test_B1p_an_interruption_after_a_settled_execution_still_gives_no_verdict(tmp_path, monkeypatch):
    """The ledger is clean here (every execution settled with known tokens): only the
    ``interrupted`` record says that the correction was cut while it was being judged."""
    runner, campaign, _, tasks = make_runner(tmp_path, "compare", {**FIX_ALL, "reviewer": ["PASS", "BLOCK"]})
    campaign["rules"]["comparison"]["tasks"] = 1
    real, calls = lfc.judge, []

    def judge(*a, **kw):
        calls.append(1)
        if len(calls) == 3:  # A, B round 0, then the correction of B
            raise KeyboardInterrupt
        return real(*a, **kw)

    monkeypatch.setattr(lfc, "judge", judge)
    with pytest.raises(KeyboardInterrupt):
        runner.compare(tasks[:1], "cand-a", ("A", "B"))
    cut = results(runner)[-2]
    assert cut["outcome"] == "interrupted" and cut["premium"]["billing_total"] is None
    rep = lfr.report(campaign, results(runner), ledger_of(runner))
    assert rep["ledger"]["unknown_spent_work"] == []
    assert rep["comparison"]["arms"]["B"]["economy"] == "unavailable"
    assert rep["comparison"]["decision"] == "inconclusive"


def test_B1p_report_refuses_results_the_ledger_cannot_account_for(tmp_path, capsys):
    runner, campaign, _, tasks = make_runner(tmp_path, "compare", FIX_ALL)
    runner.compare(tasks[:1], "cand-a", ("A", "B"))
    recs, entries = results(runner), ledger_of(runner)
    assert lfr.report(campaign, recs, entries)["ledger"]["unknown_spent_work"] == []
    assert sorted(s for r in recs for s in r["cloud_sessions"]) == sorted(
        e["session_id"] for e in entries if e["kind"] == "cloud_started")  # one to one
    sid = recs[0]["cloud_sessions"][0]
    with pytest.raises(lfr.RunnerError, match="no ledger entry"):
        lfr.report(campaign, recs, [])
    with pytest.raises(lfr.RunnerError, match="unknown to the ledger"):
        lfr.report(campaign, recs, [e for e in entries if e.get("session_id") != sid])
    with pytest.raises(lfr.RunnerError, match="named by two result records"):
        lfr.report(campaign, [recs[0], dict(recs[1], cloud_sessions=recs[0]["cloud_sessions"])], entries)
    with pytest.raises(lfr.RunnerError, match="does not name its cloud executions"):
        lfr.report(campaign, [{k: v for k, v in recs[0].items() if k != "cloud_sessions"}], entries)
    cheaper = [dict(e, premium_tokens=1) if e.get("session_id") == sid and e["kind"] == "settled" else e
               for e in entries]
    with pytest.raises(lfr.RunnerError, match="premium tokens, the ledger"):
        lfr.report(campaign, recs, cheaper)
    with pytest.raises(lfr.RunnerError, match="another envelope_sha256"):
        lfr.report(campaign, recs, [dict(e, envelope_sha256="0" * 64) for e in entries])
    with pytest.raises(lfr.RunnerError, match="mix dry-run and real"):
        lfr.report(campaign, recs, [dict(e, dry_run=False) for e in entries])
    # a settled execution whose tokens are unknown, and one that no result record names
    unread = [dict(e, premium_tokens=None) if e.get("session_id") == sid and e["kind"] == "settled" else e
              for e in entries]
    unknown = lfr.report(campaign, [dict(recs[0], premium={**recs[0]["premium"], "billing_total": None}),
                                    *recs[1:]], unread)
    assert unknown["ledger"]["unknown_spent_work"] == [f"tokens_unknown:{sid}"]
    assert unknown["comparison"]["arms"]["B"]["economy"] == "unavailable"
    assert unknown["comparison"]["decision"] == "inconclusive"
    extra = entries + [{"kind": "cloud_started", "session_id": "x", "dry_run": True},
                       {"kind": "settled", "cloud": True, "session_id": "x", "premium_tokens": 5,
                        "dry_run": True}]
    paid = lfr.report(campaign, recs, extra)
    assert paid["ledger"]["unknown_spent_work"] == ["no_result_record:x"]
    assert paid["comparison"]["arms"]["B"]["economy"] == "unavailable"
    # the CLI has no way to report without the ledger of the same state directory
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps(campaign), encoding="utf-8")
    alone = tmp_path / "alone" / runner.results_path.name
    alone.parent.mkdir()
    shutil.copy(runner.results_path, alone)
    assert lfr.main(["report", "--campaign", str(cfg), "--results", str(alone)]) == 2
    assert "no ledger beside" in capsys.readouterr().err
    renamed = tmp_path / "state" / "results-other.jsonl"
    shutil.copy(runner.results_path, renamed)
    shutil.copy(runner.ledger.path, tmp_path / "state" / "ledger-other.jsonl")
    assert lfr.main(["report", "--campaign", str(cfg), "--results", str(renamed)]) == 2
    assert "another campaign id" in capsys.readouterr().err


def test_item1_a_cap_at_the_start_of_a_takeover_or_a_correction_is_recorded_and_inconclusive(tmp_path):
    # takeover: A spends 2 executions, the local attempt of C is refused, the takeover cannot start
    runner, campaign, plan, tasks = make_runner(tmp_path, "compare", {**FIX_ALL, "local": ["partial"]},
                                                caps={"cloud_executions": 2})
    campaign["rules"]["comparison"]["tasks"] = 1
    runner.compare(tasks[:1], "cand-a", ("A", "C"))
    *recs, stop = results(runner)
    assert [(r["path"], r["segment"], r["outcome"]) for r in recs] == [
        ("A", "cloud", "accepted"), ("C", "local", "local_refused"), ("C", "takeover", "stopped_by_cap")]
    assert recs[-1]["cloud_executions"] == 0 and recs[-1]["premium"]["billing_total"] == 0
    assert stop["reason"] == "cap_reached:cloud_executions" and counts(plan)["implementer"] == 1
    rep = lfr.report(campaign, results(runner), ledger_of(runner))["comparison"]
    assert (rep["arms"]["C"]["quality"], rep["arms"]["C"]["economy"]) == ("unavailable", "unavailable")
    assert rep["decision"] == "inconclusive" and rep["recommendation"] is None
    # correction: A spends 2, B round 0 spends 2 (review BLOCK), the correction cannot start
    second, campaign2, _, tasks2 = make_runner(_sub(tmp_path, "b"), "compare",
                                               {**FIX_ALL, "reviewer": ["PASS", "BLOCK"]},
                                               caps={"cloud_executions": 4})
    campaign2["rules"]["comparison"]["tasks"] = 1
    second.compare(tasks2[:1], "cand-a", ("A", "B"))
    assert [r["outcome"] for r in results(second) if r.get("path") == "B"] == ["review_block", "stopped_by_cap"]
    rep = lfr.report(campaign2, results(second), ledger_of(second))["comparison"]
    assert (rep["arms"]["B"]["quality"], rep["arms"]["B"]["economy"]) == ("unavailable", "unavailable")
    assert rep["decision"] == "inconclusive"


def _bare_runner(tmp_path, **kw):
    repo, _, _, _ = _make_repo(tmp_path)
    campaign, _ = fake_campaign(tmp_path, FIX_ALL)
    envelope = lfr.load_envelope(write_envelope(tmp_path), "compare", TODAY)
    return lfr.Runner(repo=repo, campaign=campaign, envelope=envelope, state_dir=tmp_path / "state",
                      mode="compare", dry_run=True, host_env={"HOME": str(tmp_path / "home")}, **kw)


def test_item3_a_configuration_error_is_refused_before_any_claim_or_reservation(tmp_path, monkeypatch):
    monkeypatch.setattr(lfr, "sandbox_available", lambda: False)
    with pytest.raises(lfr.RunnerError, match="sandbox-exec is unavailable"):
        _bare_runner(tmp_path, work_root=tmp_path / "work", sandbox=True)
    assert not (tmp_path / "state").exists()  # no ledger, no result: nothing is burnt
    monkeypatch.setattr(lfr, "sandbox_available", lambda: True)
    inputs = _sub(tmp_path, "inputs")
    with pytest.raises(lfr.RunnerError, match="read-denied"):
        _bare_runner(_sub(tmp_path, "second"), work_root=inputs / "work", sandbox=True,
                     input_paths=[inputs / "envelope.json"])
    assert not (tmp_path / "second" / "state").exists()
    # sandbox-exec disappearing later: refused before the reservation, the campaign stays alive
    runner, _, plan, tasks = make_runner(_sub(tmp_path, "third"), "compare", FIX_ALL)
    runner.sandbox = True
    monkeypatch.setattr(lfr, "sandbox_available", lambda: False)
    with pytest.raises(lfr.RunnerError, match="sandbox-exec is unavailable"):
        runner.cloud_path(tasks[0], "A", "comparison")
    assert "cloud_started" not in ledger_kinds(runner) and counts(plan) == {}
    assert runner.ledger.totals()["tokens_unmeasurable"] is False
    runner.ledger.check(cloud=True)  # a later cloud execution is still allowed
    error = results(runner)[-1]
    assert (error["status"], error["cloud_executions"], error["premium"]["billing_total"]) == ("tool_error", 0, 0)


def test_item3_a_launcher_error_after_the_reservation_never_reads_as_zero_tokens(tmp_path, monkeypatch):
    runner, campaign, _, tasks = make_runner(tmp_path, "compare", FIX_ALL)

    def broken(*a, **kw):
        raise lfr.RunnerError("the arm could not be started")

    monkeypatch.setattr(lfr, "execute_driver", broken)
    with pytest.raises(lfr.RunnerError):
        runner.cloud_path(tasks[0], "A", "comparison")
    error = results(runner)[-1]
    assert error["status"] == "tool_error" and error["cloud_executions"] == 1
    assert error["premium"]["billing_total"] is None  # reserved in the ledger, not read back: unknown
    assert lfr.report(campaign, results(runner), ledger_of(runner))["ledger"]["unknown_spent_work"] == [
        f"interrupted:{error['cloud_sessions'][0]}"]


def test_item4_an_interrupted_local_attempt_is_counted_recorded_and_cannot_be_replayed(tmp_path, monkeypatch):
    first = _make_repo(tmp_path)
    runner, campaign, plan, tasks = make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)
    _interrupt_nth(monkeypatch, "local_harness", 1)
    with pytest.raises(KeyboardInterrupt):
        runner.screen(tasks[:1], ["cand-a"])
    monkeypatch.undo()
    cut, stop = results(runner)
    assert (cut["status"], cut["path"], cut["local"]["candidate"]) == ("interrupted", "S", "cand-a")
    assert stop["reason"] == "interrupted:KeyboardInterrupt"
    settled = [e for e in ledger_of(runner) if e["kind"] == "settled"]
    assert len(settled) == 1 and settled[0]["interrupted"] is True and settled[0]["cloud"] is False
    assert not list((tmp_path / "work").iterdir())
    rep = lfr.report(campaign, results(runner), ledger_of(runner))["screening"]
    assert rep["selected"] is None and rep["complete"] is False  # an interruption is not a refusal
    again, _, _, tasks = make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)
    with pytest.raises(lfr.RunnerError, match="already recorded"):
        again.screen(tasks[:1], ["cand-a"])
    assert counts(plan) == {}  # no second chance by Ctrl-C


def test_item5_the_exact_previous_signal_handlers_are_restored(tmp_path):
    def custom(signum, frame):
        raise AssertionError("not expected")

    signals = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
    saved = {s: signal.getsignal(s) for s in signals}
    try:
        for sig in signals:
            signal.signal(sig, custom)
        execution = lfr.execute_driver(
            {"kind": "local_harness", "argv": [sys.executable, "-c", "print(1)"]}, {}, workdir=tmp_path,
            scratch=tmp_path / "scratch", stream_log=tmp_path / "s.log", max_seconds=30, max_steps=None,
            sandbox=False, deny_read=[], host_env=dict(os.environ))
        assert execution["exit_code"] == 0
        assert all(signal.getsignal(sig) is custom for sig in signals)
        # a handler that was not set from Python reads as None: the default one is put back
        signal.signal(signal.SIGHUP, custom)
        lfr._restore_handlers({signal.SIGHUP: None})
        assert signal.getsignal(signal.SIGHUP) is signal.SIG_DFL
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # an ignored Ctrl-C stays ignored
        assert signal.SIGINT not in lfr._install_term_handlers({"defer": False, "pending": None})
    finally:
        for sig, old in saved.items():
            signal.signal(sig, old)


def test_item5_a_signal_received_while_the_arm_is_being_started_still_kills_it(tmp_path, monkeypatch):
    script, pids = tmp_path / "sleeper.py", tmp_path / "pids"
    script.write_text(SLEEPER, encoding="utf-8")
    real, started = subprocess.Popen, []

    def popen(*a, **kw):
        proc = real(*a, **kw)
        if not started:  # the signal lands inside ``Popen``, before the caller knows the process
            started.append(proc.pid)
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(0.05)
        return proc

    monkeypatch.setattr(lfr.subprocess, "Popen", popen)
    try:
        with pytest.raises(SystemExit):
            lfr.execute_driver({"kind": "cloud_implementer", "home": "real", "network": "open",
                                "argv": [sys.executable, str(script), str(pids)]}, {}, workdir=tmp_path,
                               scratch=tmp_path / "scratch", stream_log=tmp_path / "s.log",
                               max_seconds=60, max_steps=None, sandbox=False, deny_read=[],
                               host_env=dict(os.environ))
        for _ in range(60):
            if not _alive(started[0]):
                break
            time.sleep(0.1)
        survived = _alive(started[0])
    finally:
        monkeypatch.undo()
        for pid in started:  # never leave a stray process behind
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    assert not survived, "the arm started during the signal outlived the call"


def test_item6_the_generated_profile_is_denied_to_the_arm(tmp_path, monkeypatch):
    seen = {}

    def popen(argv, **kw):
        seen["argv"], seen["profile"] = argv, Path(argv[2]).read_text("utf-8")
        raise OSError("not started: the profile is all this test needs")

    monkeypatch.setattr(lfr, "sandbox_available", lambda: True)
    monkeypatch.setattr(lfr.subprocess, "Popen", popen)
    execution = lfr.execute_driver(
        {"kind": "local_harness", "argv": ["arm"]}, {}, workdir=_sub(tmp_path, "bundle"),
        scratch=tmp_path / "scratch", stream_log=tmp_path / "s.log", max_seconds=5, max_steps=None,
        sandbox=True, deny_read=[tmp_path / "secret"], host_env={"PATH": "/usr/bin"})
    monkeypatch.undo()
    assert execution["start_error"] and seen["argv"][:2] == ["sandbox-exec", "-f"]
    profile_dir = Path(seen["argv"][2]).parent
    deny = next(x for x in seen["profile"].splitlines() if x.startswith("(deny file-read*"))
    assert f'(subpath "{profile_dir}")' in deny and f'(subpath "{os.path.realpath(tmp_path / "secret")}")' in deny
    assert not profile_dir.exists()  # removed after the call


def test_item8_results_are_synced_and_a_truncated_line_is_a_clean_refusal(tmp_path, monkeypatch, capsys):
    first = _make_repo(tmp_path)
    runner, campaign, _, tasks = make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)
    synced = []
    real = os.fsync
    monkeypatch.setattr(lfr.os, "fsync", lambda fd: (synced.append(fd), real(fd))[1])
    runner._emit({"record_type": "stop", "reason": "probe"})
    monkeypatch.undo()
    assert len(synced) == 1  # the record that forbids a replay survives a crash
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps(campaign), encoding="utf-8")
    with runner.ledger.path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "settled", "clou')  # the launcher died while writing
    with pytest.raises(lfr.RunnerError, match=r"ledger-test-campaign\.jsonl: line 2 is not a JSON record"):
        runner.ledger.totals()
    with pytest.raises(lfr.RunnerError, match="line 2"):
        make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)
    assert lfr.main(["report", "--campaign", str(cfg), "--results", str(runner.results_path)]) == 2
    assert "line 2 is not a JSON record" in capsys.readouterr().err
    runner.ledger.path.write_text(runner.ledger.path.read_text("utf-8").rsplit("\n", 1)[0] + "\n")
    with runner.results_path.open("a", encoding="utf-8") as handle:
        handle.write('{"record_type": "att')
    with pytest.raises(lfr.RunnerError, match=r"results-test-campaign\.jsonl: line 2"):
        make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)
    assert lfr.main(["report", "--campaign", str(cfg), "--results", str(runner.results_path)]) == 2
    assert "results-test-campaign.jsonl: line 2" in capsys.readouterr().err
