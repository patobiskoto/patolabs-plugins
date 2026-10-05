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
ap.add_argument("--init-tools", default=None); ap.add_argument("--traj", default="")
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
if a.init_tools is not None:
    print(json.dumps({"type": "system", "subtype": "init",
                      "tools": [t for t in a.init_tools.split(",") if t]}), flush=True)
if a.traj:
    json.dump({"info": {"model_stats": {"api_calls": 7}}}, open(a.traj, "w"))
if behavior.startswith("peek:"):  # the arm reads a path it should not: both stream shapes, then works
    target = behavior[5:]
    behavior = "PASS" if a.role == "reviewer" else "fix"
    event("tool_execution_start", toolName="read", args={"path": target})
    event("assistant", message={"content": [{"type": "tool_use", "name": "Read",
                                             "input": {"file_path": target}}]})
if behavior.startswith("cmd:"):  # the arm runs a command through its shell tool: both stream shapes
    command = behavior[4:]
    behavior = "PASS" if a.role == "reviewer" else "fix"
    event("tool_execution_start", toolName="bash", args={"command": command})
    event("assistant", message={"content": [{"type": "tool_use", "name": "Bash",
                                             "input": {"command": command}}]})
if behavior.startswith(("show:", "say:")):  # a tool result: a bundle file the arm reads, or literal text
    kind, _, arg = behavior.partition(":")
    text = open(os.path.join(a.workdir, arg)).read() if kind == "show" else arg
    behavior = "PASS" if a.role == "reviewer" else "fix"
    if kind == "show":
        event("tool_execution_start", toolName="read", args={"path": arg})
        event("assistant", message={"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": arg}}]})
    event("tool_execution_end", toolName="read", result={"content": [{"type": "text", "text": text}]})
    event("user", message={"content": [{"type": "tool_result", "tool_use_id": "t", "content": text}]})
if behavior == "tamper":  # the arm edits the git configuration of its bundle, then does the work
    open(os.path.join(a.workdir, ".git", "config"), "a").write("[core]\n\tfsmonitor = false\n")
    behavior = "fix"
if behavior == "lock":  # a stale index lock, as a bound's SIGKILL in the middle of a git command leaves it
    open(os.path.join(a.workdir, ".git", "index.lock"), "w").close()
    behavior = "fix"
if behavior == "unreadable":  # a file git cannot read
    path = os.path.join(a.workdir, "plugins/foundry/tooling/foundry/secret.txt")
    open(path, "w").write("x\n")
    os.chmod(path, 0)
    behavior = "fix"
if behavior == "cache_unreadable":  # out of the patch (``__pycache__``) but read by the judge
    os.makedirs(os.path.join(a.workdir, "__pycache__"), exist_ok=True)
    path = os.path.join(a.workdir, "__pycache__", "notes.txt")
    open(path, "w").write("x\n")
    os.chmod(path, 0)
    behavior = "fix"
if behavior == "nested":  # an empty nested repository (``git init sub/``)
    subprocess.run(["git", "init", "-q", os.path.join(a.workdir, "sub")], check=True,
                   env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": a.workdir,
                        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
    behavior = "fix"
if behavior == "dotclaude":  # project settings the reviewer's host would load
    os.makedirs(os.path.join(a.workdir, ".claude"), exist_ok=True)
    open(os.path.join(a.workdir, ".claude", "settings.json"), "w").write("{}\n")
    behavior = "fix"
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


def write_envelope(tmp_path, campaign_id="test-campaign", **caps):
    body = {"schema": lfr.ENVELOPE_SCHEMA, "campaign_id": campaign_id, "expires_on": "2026-12-31",
            "allowed_modes": ["screen", "compare"],
            "caps": {"cloud_executions": 45, "premium_tokens": 10_000_000,
                     "wall_clock_seconds": 100_000, **caps}}
    path = tmp_path / "envelope.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def make_runner(tmp_path, mode, plan, *, repo_bundle=None, caps=None, run=None, campaign_over=None,
                host_env=None, campaign_id="test-campaign", require_screening=False,
                screening_campaign=None):
    repo, snap, _, _ = repo_bundle or _make_repo(tmp_path)
    campaign, plan_path = fake_campaign(tmp_path, plan, **(campaign_over or {}))
    envelope = lfr.load_envelope(write_envelope(tmp_path, campaign_id, **(caps or {})), mode, TODAY)
    runner = lfr.Runner(
        repo=repo, campaign=campaign, envelope=envelope, state_dir=tmp_path / "state",
        work_root=tmp_path / "work", mode=mode, dry_run=True, sandbox=False,
        run=run or (lambda argv: None),
        preflight_run=lambda cid: lfr.dry_run_facts(campaign, campaign["candidates"][cid]["model"]),
        disk_free_gib=lambda: 500.0, host_env=host_env or dict(os.environ), today=lambda: TODAY,
        require_screening=require_screening, screening_campaign=screening_campaign)
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
    # exact verdicts: the injected probe reads no swap, so compatibility is unavailable (never pass)
    assert (c["compatibility"], c["quality"]) == ("unavailable", "pass")
    assert c["economy_detail"]["premium_billing_tokens"] == 215 and c["economy_detail"]["premium_pass"] is True
    assert c["local_successes"] == 1 and c["tasks_compared"] == 2
    b = rep["comparison"]["arms"]["B"]
    assert (b["compatibility"], b["quality"]) == ("pass", "pass")
    assert b["economy_detail"]["premium_billing_tokens"] == 176 and b["economy_detail"]["premium_pass"] is True
    assert rep["comparison"]["decision"] == "retained" and rep["comparison"]["recommendation"] == "B"
    assert rep["comparison"]["warnings"] == [] and rep["ledger"]["unsettled_starts"] == []
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
        assert campaign["drivers"][name]["verified"] is True and "note" in campaign["drivers"][name]
        lfr._check_driver_usable(name, campaign["drivers"][name], dry_run=False)  # pinned by PAT-111
        with pytest.raises(lfr.RunnerError, match="non-fake"):
            lfr._check_driver_usable(name, campaign["drivers"][name], dry_run=True)
    unverified = {**campaign["drivers"]["cloud_reviewer"], "verified": False}
    with pytest.raises(lfr.RunnerError, match="not verified"):
        lfr._check_driver_usable("cloud_reviewer", unverified, dry_run=False)  # a failing driver stays refused
    with pytest.raises(lfr.RunnerError, match="non-fake"):
        lfr._check_driver_usable("local_harness", local, dry_run=True)  # a dry run never starts a real arm
    assert len(campaign["candidates"]) == 6 and not any(c.get("verified") for c in campaign["candidates"].values())
    assert campaign["candidates"]["devstral-small-2-24b-gguf"]["unused"] is True
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


def test_N3_a_decided_attempt_is_never_replayed_and_a_duplicate_record_never_counts(tmp_path):
    first = _make_repo(tmp_path)
    runner, _, plan, tasks = make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)
    runner.screen(tasks[:1], ["cand-a"])
    assert counts(plan) == {"local": 1}
    again, _, _, tasks = make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)
    assert again.screen(tasks[:1], ["cand-a"]) == []  # resume: the decided task is skipped
    assert counts(plan) == {"local": 1}  # the decided candidate never ran again
    assert len(results(again)) == 1  # the first launch's record: nothing added
    with pytest.raises(lfr.RunnerError, match="already recorded"):  # the claim itself still refuses
        again._claim("S", tasks[0], "screening", "cand-a", "local", 0)
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

def test_N7_a_log_layout_stays_unverified_until_declared_so_premium_tokens_stay_unknown():
    campaign = lfr.load_campaign(QUALIFICATION / "pat-19-campaign-v1.json")
    for name in ("cloud_implementer_current", "cloud_implementer_economy", "cloud_reviewer"):
        log = campaign["drivers"][name]["session_log"]
        assert log["layout_verified"] is True and "layout_note" in log  # PAT-111: reconciled on a real run
        assert campaign["drivers"][name]["stream"]["format"] == "claude-stream-json"
        assert lfr.premium_tokens("any", {**log, "layout_verified": False}) == (None, "log_layout_unverified")


def test_N10_choices_that_are_not_protocol_coordinates_are_labelled_and_the_breakdown_is_reported(tmp_path):
    campaign = lfr.load_campaign(QUALIFICATION / "pat-19-campaign-v1.json")
    choices = campaign["non_protocol_choices"]
    assert set(choices) == {"min_free_disk_gib", "economy_time_counted", "swap_metric", "premium_work_definition",
                            "economy_unavailable_when_a_compared_task_is_undecided"}
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


def test_item4_an_interrupted_local_attempt_is_counted_recorded_and_replayed_once(tmp_path, monkeypatch):
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
    again.screen(tasks[:1], ["cand-a"])  # cut before any verdict: void, replayed once
    assert counts(plan) == {"local": 1}
    assert results(again)[-1]["replay_of"]["outcome"] == "interrupted"


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


# ------------------------------------------------------------------------- PAT-111 (launcher fixes)

SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)


@pytest.fixture
def sentinel_handlers():
    """Python handlers that fail loudly: a signal that reaches one was NOT converted by the launcher
    (and never kills the test process). The launcher must put exactly these back."""
    saved = {s: signal.getsignal(s) for s in SIGNALS}

    def unconverted(signum, frame):
        raise AssertionError(f"signal {signum} was not handled by the launcher")

    for sig in SIGNALS:
        signal.signal(sig, unconverted)
    try:
        yield unconverted
    finally:
        for sig, old in saved.items():
            signal.signal(sig, old)


def _send(sig):
    os.kill(os.getpid(), sig)
    time.sleep(0.02)  # the handler runs at the next bytecode boundary of this (main) thread


def _fsync_on(monkeypatch, path, nth, sig):
    """The ``nth`` fsync of the file ``path`` (a ledger or results write) receives ``sig``."""
    real, seen = os.fsync, []

    def fsync(fd):
        if os.fstat(fd).st_ino == Path(path).stat().st_ino:
            seen.append(1)
            real(fd)
            if len(seen) == nth:
                _send(sig)
            return
        real(fd)

    monkeypatch.setattr(lfr.os, "fsync", fsync)


def _once(monkeypatch, owner, name, sig, *, after=False):
    """``owner.name`` sends ``sig`` the first time it is called (before it runs, or after with
    ``after=True``)."""
    real, done = getattr(owner, name), []

    def wrapper(*a, **kw):
        if not done:
            done.append(1)
            if not after:
                _send(sig)
        out = real(*a, **kw)
        if after and len(done) == 1:
            done.append(2)
            _send(sig)
        return out

    monkeypatch.setattr(owner, name, wrapper)


def _expect_exit(sig):
    return KeyboardInterrupt if sig == signal.SIGINT else SystemExit


def _check_interrupted_screen(runner, campaign, exc_name):
    *recs, stop = results(runner)
    cut = [r for r in recs if r["path"] == "S"]
    assert len(cut) == 1, "exactly one record for the attempt (no loss, no duplicate)"
    assert stop["record_type"] == "stop" and stop["reason"] == f"interrupted:{exc_name}"
    assert ledger_of(runner)[-1]["kind"] == "stopped"
    assert lfr._unsettled_starts(ledger_of(runner)) == []  # the cut attempt is settled in the ledger
    assert not list((runner.work_root).iterdir())  # no bundle left behind
    return cut[0]


@pytest.mark.parametrize("where,sig", [("bundle", signal.SIGTERM), ("judge", signal.SIGHUP),
                                        ("after_return", signal.SIGINT), ("results_write", signal.SIGTERM),
                                        ("ledger_settle", signal.SIGTERM)])
def test_pat111_ac1_a_stop_outside_the_driver_leaves_an_interrupted_record_and_a_stop(
        tmp_path, monkeypatch, sentinel_handlers, where, sig):
    first = _make_repo(tmp_path)
    runner, campaign, plan, tasks = make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)
    if where == "bundle":
        _once(monkeypatch, lfc, "build_bundle", sig)
    elif where == "judge":
        _once(monkeypatch, lfc, "judge", sig)
    elif where == "after_return":  # between the return of ``local_attempt`` and ``_emit``
        _once(monkeypatch, runner, "local_attempt", sig, after=True)
    elif where == "results_write":
        runner.results_path.touch()
        _fsync_on(monkeypatch, runner.results_path, 1, sig)
    else:  # the settlement line of the ledger (after preflight and attempt_started)
        _fsync_on(monkeypatch, runner.ledger.path, 3, sig)
    with pytest.raises(_expect_exit(sig)):
        runner.screen(tasks[:1], ["cand-a"])
    monkeypatch.undo()
    cut = _check_interrupted_screen(runner, campaign, _expect_exit(sig).__name__)
    if where == "results_write":  # the write is atomic for signals: the finished record is the one kept
        assert cut.get("status") != "interrupted" and cut["local_outcome"] == "accepted"
    else:
        assert (cut["status"], cut["outcome"], cut["accepted"]) == ("interrupted", "interrupted", None)
    assert {s: signal.getsignal(s) for s in SIGNALS} == dict.fromkeys(SIGNALS, sentinel_handlers)
    rep = lfr.report(campaign, results(runner), ledger_of(runner))
    assert rep["screening"]["selected"] is None  # an interrupted screening never selects
    played = counts(plan).get("local", 0)
    make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)[0].screen(tasks[:1], ["cand-a"])
    # cut before any verdict: replayed once; a verdict was received (record or flushed): never replayed
    assert counts(plan).get("local", 0) - played == (
        0 if where in ("after_return", "results_write", "ledger_settle") else 1)


def test_pat111_ac1_compare_covers_the_neutral_path_and_the_cloud_record_window(
        tmp_path, monkeypatch, sentinel_handlers):
    runner, campaign, _, tasks = make_runner(tmp_path, "compare", FIX_ALL)
    _once(monkeypatch, runner, "local_attempt", signal.SIGTERM, after=True)
    with pytest.raises(SystemExit):
        runner.compare(tasks[:1], "cand-a", ("N",))
    monkeypatch.undo()
    cut, stop = results(runner)
    assert (cut["path"], cut["status"], cut["local"]["candidate"]) == ("N", "interrupted", "cand-a")
    assert stop["reason"] == "interrupted:SystemExit"
    # a cut between the end of a cloud round and the write of its record still leaves a record
    second, _, _, tasks2 = make_runner(_sub(tmp_path, "b"), "compare", FIX_ALL)
    _once(monkeypatch, lfr, "_record_total", signal.SIGHUP)
    with pytest.raises(SystemExit):
        second.compare(tasks2[:1], "cand-a", ("A",))
    monkeypatch.undo()
    cut, stop = results(second)
    assert (cut["path"], cut["status"]) == ("A", "interrupted") and len(cut["cloud_sessions"]) == 2
    assert cut["premium"]["billing_total"] is None and stop["reason"] == "interrupted:SystemExit"
    assert {s: signal.getsignal(s) for s in SIGNALS} == dict.fromkeys(SIGNALS, sentinel_handlers)


def test_pat111_ac1_a_second_signal_is_never_masked_and_the_records_are_still_written(
        tmp_path, monkeypatch, sentinel_handlers):
    runner, _, _, tasks = make_runner(tmp_path, "screen", FIX_ALL)

    def interrupted(*a, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(lfc, "judge", interrupted)
    real = runner.ledger.append

    def append(kind, **fields):
        if kind == "stopped":
            _send(signal.SIGTERM)  # a second signal while the stop is being written
        real(kind, **fields)

    monkeypatch.setattr(runner.ledger, "append", append)
    with pytest.raises(SystemExit):  # raised once both writes are done, replacing the Ctrl-C
        runner.screen(tasks[:1], ["cand-a"])
    monkeypatch.undo()
    cut, stop = results(runner)
    assert cut["status"] == "interrupted" and stop["reason"] == "interrupted:KeyboardInterrupt"
    assert ledger_of(runner)[-1]["kind"] == "stopped"
    assert {s: signal.getsignal(s) for s in SIGNALS} == dict.fromkeys(SIGNALS, sentinel_handlers)


def test_pat111_ac1_a_signal_during_the_cleanup_of_a_driver_does_not_skip_it(
        tmp_path, monkeypatch, sentinel_handlers):
    """The cleanup of ``execute_driver`` (profile directory, previous handlers) completes, then the
    signal is raised."""
    seen = {}

    def popen(argv, **kw):
        seen["profile_dir"] = Path(argv[2]).parent
        raise OSError("not started: only the cleanup matters here")

    real_rmtree = shutil.rmtree

    def rmtree(path, *a, **kw):
        _send(signal.SIGTERM)
        real_rmtree(path, *a, **kw)

    monkeypatch.setattr(lfr, "sandbox_available", lambda: True)
    monkeypatch.setattr(lfr.subprocess, "Popen", popen)
    monkeypatch.setattr(lfr.shutil, "rmtree", rmtree)
    with pytest.raises(SystemExit):
        lfr.execute_driver({"kind": "local_harness", "argv": ["arm"]}, {}, workdir=_sub(tmp_path, "bundle"),
                           scratch=tmp_path / "scratch", stream_log=tmp_path / "s.log", max_seconds=5,
                           max_steps=None, sandbox=True, deny_read=[], host_env={"PATH": "/usr/bin"})
    monkeypatch.undo()
    assert not seen["profile_dir"].exists()  # the profile was removed
    assert {s: signal.getsignal(s) for s in SIGNALS} == dict.fromkeys(SIGNALS, sentinel_handlers)


# ---- AC2: the existing ledger must have been written under the same digests

def test_pat111_ac2_a_ledger_left_by_a_refused_launch_pins_the_digests_before_any_spend(tmp_path):
    first = _make_repo(tmp_path)
    runner, campaign, plan, tasks = make_runner(tmp_path, "compare", FIX_ALL, repo_bundle=first)
    runner.preflight_run = lambda cid: (lambda argv: None)  # no machine fact: the preflight refuses
    with pytest.raises(lfr.PreflightRefused):
        runner.compare(tasks, "cand-a", ("A", "C"))
    assert not runner.results_path.exists() or "attempt" not in runner.results_path.read_text("utf-8")
    before = runner.ledger.path.read_text("utf-8")
    # the operator edits the configuration, keeping the campaign id: refused, nothing written
    with pytest.raises(lfr.RunnerError, match="ledger-test-campaign.jsonl was written under another campaign_sha256"):
        make_runner(tmp_path, "compare", FIX_ALL, repo_bundle=first,
                    campaign_over={"bounds": {"local_max_seconds": 21}})
    # ... or the envelope (a larger cap)
    with pytest.raises(lfr.RunnerError, match="written under another envelope_sha256"):
        make_runner(tmp_path, "compare", FIX_ALL, repo_bundle=first, caps={"cloud_executions": 99})
    # ... or the manifest
    campaign2, _ = fake_campaign(tmp_path, FIX_ALL)
    envelope = lfr.load_envelope(write_envelope(tmp_path), "compare", TODAY)
    with pytest.raises(lfr.RunnerError, match="written under another manifest_sha256"):
        lfr.Runner(repo=first[0], campaign=campaign, envelope=envelope, state_dir=tmp_path / "state",
                   work_root=tmp_path / "work", mode="compare", dry_run=True, sandbox=False,
                   provenance={"manifest_sha256": "f" * 64})
    assert runner.ledger.path.read_text("utf-8") == before  # no session_started, no reservation
    assert counts(plan) == {}
    # the same configuration and envelope resume normally
    again, _, _, tasks = make_runner(tmp_path, "compare", FIX_ALL, repo_bundle=first)
    assert [e["kind"] for e in ledger_of(again)].count("session_started") == 2
    assert again.ledger.launch == 2


# ---- AC3: undecided-task rule labelled, unsettled starts, selected candidate, unique attempt names

def test_pat111_ac3_the_economy_undecided_rule_is_labelled_as_a_launcher_choice():
    campaign = lfr.load_campaign(QUALIFICATION / "pat-19-campaign-v1.json")
    choice = campaign["non_protocol_choices"]["economy_unavailable_when_a_compared_task_is_undecided"]
    assert choice["not_a_protocol_coordinate"] is True and "undecided" in choice["rationale"]


def test_pat111_ac3_report_lists_a_killed_attempt_that_a_relaunch_replayed_and_warns(
        tmp_path, monkeypatch):
    first = _make_repo(tmp_path)
    runner, campaign, plan, tasks = make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)
    runner.screen(tasks[:1], ["cand-a"])
    # SIGKILL during the first attempt: neither its record nor its settlement were written
    runner.results_path.write_text("", encoding="utf-8")
    kept = [e for e in ledger_of(runner) if e["kind"] != "settled"]
    runner.ledger.path.write_text("".join(json.dumps(e) + "\n" for e in kept), encoding="utf-8")
    killed = "attempt-l01-0001-pr1-S-local-cand-a"
    assert any(e.get("attempt_dir") == killed for e in kept)
    again, _, _, tasks = make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)
    again.screen(tasks, ["cand-a"])  # the relaunch replays it once (hard kill: void, no verdict)
    rep = lfr.report(campaign, results(again), ledger_of(again))
    assert [h["attempt_dir"] for h in rep["ledger"]["unsettled_starts"]] == [killed]
    assert rep["screening"]["complete"] and rep["screening"]["selected"] == "cand-a"
    assert rep["screening"]["warnings"] == []  # replayed: no longer an open hole...
    assert [(v["outcome"], v["attempt_dir"], v["replayed"]) for v in rep["void_attempts"]] == [
        ("hard_kill", killed, True)]  # ...but still listed, with its replay
    assert [r["replay_of"]["attempt_dir"] for r in rep["replays"]] == [killed]
    # the stream log of the killed attempt is not overwritten by the replay
    names = sorted(p.name for p in (tmp_path / "state" / "streams").iterdir())
    assert any("-attempt-l01-0001-pr1-" in n for n in names) and any("-attempt-l02-0001-pr1-" in n for n in names)


def test_pat111_ac3_a_preflight_not_followed_by_an_attempt_is_listed_a_clean_stop_is_not():
    def entry(kind, **kw):
        return {"kind": kind, **kw}

    ledger = [entry("session_started", mode="compare"), entry("preflight", ok=True, candidate="c1"),
              entry("session_started", mode="compare"), entry("preflight", ok=True, candidate="c2"),
              entry("attempt_started", attempt_dir="d1", path="C", candidate="c2"),
              entry("settled", cloud=False, attempt_dir="d1"),
              entry("preflight", ok=True, candidate="c3"), entry("stopped", reason="cap_reached:x"),
              entry("preflight", ok=False, candidate="c4")]
    found = lfr._unsettled_starts(ledger)
    assert [(h["kind"], h["candidate"], h["mode"]) for h in found] == [("preflight", "c1", "compare")]
    assert found[0]["warning"] == "preflight_without_attempt:c1@ledger_line_2"
    # an attempt started in another launch and never settled
    found = lfr._unsettled_starts(ledger[:5])
    assert [(h["kind"], h.get("candidate"), h.get("attempt_dir"), h["mode"]) for h in found] == [
        ("preflight", "c1", None, "compare"), ("attempt_started", "c2", "d1", "compare")]


def test_pat111_ac3_a_comparison_with_an_unsettled_local_start_is_inconclusive(tmp_path):
    runner, campaign, _, tasks = make_runner(tmp_path, "compare", FIX_ALL)
    campaign["rules"]["comparison"]["tasks"] = 1
    runner.compare(tasks[:1], "cand-a", ("A", "B", "C"))
    clean = lfr.report(campaign, results(runner), ledger_of(runner))["comparison"]
    assert clean["decision"] == "retained" and clean["warnings"] == []
    hole = {"kind": "attempt_started", "attempt_dir": "attempt-l01-0099-pr1-C-local-cand-a", "path": "C",
            "candidate": "cand-a", "dry_run": True}
    rep = lfr.report(campaign, results(runner), [*ledger_of(runner), hole])
    assert rep["comparison"]["decision"] == "inconclusive" and rep["comparison"]["recommendation"] is None
    assert rep["comparison"]["warnings"] == [
        "attempt_started_without_settled:attempt-l01-0099-pr1-C-local-cand-a"]
    assert rep["ledger"]["unsettled_starts"][0]["mode"] == "compare"


def _screened(tmp_path, local_plan):
    first = _make_repo(tmp_path)
    runner, campaign, plan, tasks = make_runner(tmp_path, "screen", {**FIX_ALL, "local": local_plan},
                                                repo_bundle=first)
    runner.screen(tasks, ["cand-a", "cand-b"])
    return first, campaign, runner


def test_pat111_ac3_compare_and_report_refuse_a_candidate_the_screening_did_not_select(tmp_path):
    first, campaign, screened = _screened(tmp_path, ["fix", "fix", "partial", "partial"])
    rep = lfr.report(campaign, results(screened), ledger_of(screened))
    assert rep["screening"]["selected"] == "cand-a" and rep["comparison"]["screening_selected"] == "cand-a"
    runner, _, plan, tasks = make_runner(tmp_path, "compare", FIX_ALL, repo_bundle=first)
    with pytest.raises(lfr.RunnerError, match="not the one the screening selected"):
        runner.compare(tasks, "cand-b", ("A", "C"))  # refused at the start: nothing reserved
    assert "cloud_started" not in ledger_kinds(runner) and "implementer" not in counts(plan)
    assert runner.results_path.read_text("utf-8") == screened.results_path.read_text("utf-8")
    runner.compare(tasks[:1], "cand-a", ("A",))  # a cloud-only comparison has no local candidate
    # report: a comparison made on another candidate than the selected one is refused
    recs = results(screened)
    foreign = dict(recs[0], path="C", segment="local", task={"pr": 1, "issue": "PAT-1", "set": "comparison"},
                   local=dict(recs[0]["local"], candidate="cand-b"), attempt=0)
    with pytest.raises(lfr.RunnerError, match="not the one the screening selected"):
        lfr.report(campaign, [*recs, foreign], ledger_of(screened))
    # an incomplete screening selects nobody: a comparison of any local candidate is refused
    partial = [r for r in recs if r["task"]["pr"] == 1]
    with pytest.raises(lfr.RunnerError, match="not the one the screening selected"):
        lfr.report(campaign, [*partial, dict(foreign, local=dict(foreign["local"], candidate="cand-a"))],
                   ledger_of(screened))


def test_pat111_ac3_without_screening_results_the_report_says_the_check_was_not_possible(tmp_path):
    runner, campaign, _, tasks = make_runner(tmp_path, "compare", FIX_ALL)
    runner.compare(tasks[:1], "cand-a", ("A", "C"))  # no screening results in this state directory
    comparison = lfr.report(campaign, results(runner), ledger_of(runner))["comparison"]
    assert comparison["screening_selected"] == "screening_results_not_available"


# ============================================================ PAT-111: bounded resume (2026-10-05)
# A relaunch under the same campaign id, envelope, config and manifest resumes: decided work is
# skipped, an attempt cut before any verdict is void and replayed ONCE, a verdict is never replayed.

def _three(tasks):
    return [*tasks, {**tasks[0], "pr": 3, "issue": "PAT-3"}]


def _resume_report(campaign, runner):
    rules = json.loads(json.dumps(campaign))
    rules["rules"]["screening"]["tasks"] = 3
    return lfr.report(rules, results(runner), ledger_of(runner))


def _sigterm_judge_on(monkeypatch, *nths):
    """SIGTERM just before the judge of the ``nths``-th judged attempt (the arm already ran)."""
    real, seen = lfc.judge, []

    def judge(*a, **kw):
        seen.append(1)
        if len(seen) in nths:
            _send(signal.SIGTERM)
        return real(*a, **kw)

    monkeypatch.setattr(lfc, "judge", judge)


def _launch(tmp_path, first, plan, tasks, candidates, monkeypatch=None, cut=()):
    runner, campaign, plan_path, _ = make_runner(tmp_path, "screen", plan, repo_bundle=first)
    if cut:
        _sigterm_judge_on(monkeypatch, *cut)
        with pytest.raises(SystemExit):
            runner.screen(tasks, candidates)
        monkeypatch.undo()
    else:
        runner.screen(tasks, candidates)
    return runner, campaign, plan_path


RESUME_PLAN = {**FIX_ALL, "local": ["fix"] * 4 + ["partial"] * 3}


def test_pat111_resume_screens_candidate_by_candidate_and_replays_the_cut_task_once(
        tmp_path, monkeypatch, sentinel_handlers):
    first = _make_repo(tmp_path)
    tasks = _three(make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)[3])
    # launch 1: candidate a, SIGTERM during task 2 before the judge
    _launch(tmp_path, first, RESUME_PLAN, tasks, ["cand-a"], monkeypatch, cut=(2,))
    # launch 2: task 1 is skipped (not replayed), task 2 replayed once, task 3 played
    runner, campaign, plan = _launch(tmp_path, first, RESUME_PLAN, tasks, ["cand-a"])
    assert counts(plan) == {"local": 4}  # 2 in launch 1, then task 2 again and task 3
    recs = [r for r in results(runner) if r["record_type"] == "attempt"]
    assert [(r["task"]["pr"], r.get("outcome"), "replay_of" in r) for r in recs] == [
        (1, None, False), (2, "interrupted", False), (2, None, True), (3, None, False)]
    replay = recs[2]
    assert replay["replay_of"]["outcome"] == "interrupted" and replay["replay_of"]["reason"].startswith("SystemExit")
    assert replay["replay_of"]["attempt_dir"].endswith("-0002-pr2-S-local-cand-a")
    # launch 3: candidate b runs on its own
    runner, campaign, plan = _launch(tmp_path, first, RESUME_PLAN, tasks, ["cand-b"])
    assert counts(plan) == {"local": 7}
    rep = _resume_report(campaign, runner)
    screening = rep["screening"]
    assert screening["complete"] and screening["selected"] == "cand-a" and screening["undecided_tasks"] == []
    assert screening["candidates"]["cand-a"]["tasks"] == 3  # the task is counted once (decided record)
    assert [(v["task"]["pr"], v["outcome"], v["replayed"]) for v in rep["void_attempts"]] == [
        (2, "interrupted", True)]
    assert [(r["task"]["pr"], r["replay_of"]["outcome"]) for r in rep["replays"]] == [(2, "interrupted")]
    # a decided record is still never claimed again, and a duplicate of it never counts
    with pytest.raises(lfr.RunnerError, match="already recorded"):
        runner._claim("S", tasks[0], "screening", "cand-a", "local", 0)
    decided = next(r for r in results(runner) if r.get("record_type") == "attempt" and r.get("judge"))
    with pytest.raises(lfr.RunnerError, match="decided twice"):
        lfr.report(campaign, [*results(runner), {**decided, "replay_of": {"attempt": 0}}], ledger_of(runner))


def test_pat111_a_second_cut_of_the_same_attempt_is_not_replayable(tmp_path, monkeypatch, sentinel_handlers):
    first = _make_repo(tmp_path)
    tasks = _three(make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)[3])
    _launch(tmp_path, first, RESUME_PLAN, tasks, ["cand-a"], monkeypatch, cut=(2,))
    _launch(tmp_path, first, RESUME_PLAN, tasks, ["cand-a"], monkeypatch, cut=(1,))  # the replay is cut too
    runner, campaign, plan = _launch(tmp_path, first, RESUME_PLAN, tasks, ["cand-a"])
    assert counts(plan) == {"local": 4}  # task 1, task 2, its replay, then only task 3: no third try
    screening = _resume_report(campaign, runner)["screening"]
    assert screening["selected"] is None and screening["reason"] == "incomplete_screening"
    assert screening["undecided_tasks"] == [{"candidate": "cand-a", "pr": 2, "outcome": "interrupted",
                                             "replayed": True}]


def _drop(runner, *, record=True, settlement=True, task_pr=2):
    """Rewrite the state as a SIGKILL would have left it for the LAST attempt of ``task_pr``: no stop,
    no settlement (``settlement=False`` keeps it) and no record (``record=False`` keeps it)."""
    last = [e for e in ledger_of(runner) if e["kind"] == "attempt_started" and e["pr"] == task_pr][-1]
    entries = [e for e in ledger_of(runner) if e["kind"] != "stopped" and not (
        settlement and e["kind"] == "settled" and e.get("attempt_dir") == last["attempt_dir"])]
    runner.ledger.path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    recs = [r for r in results(runner) if r["record_type"] != "stop"]
    mine = [r for r in recs if r["record_type"] == "attempt" and r["task"]["pr"] == task_pr]
    if record and mine:
        recs.remove(mine[-1])
    runner.results_path.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    return last["attempt_dir"]


def test_pat111_a_hard_kill_without_settlement_is_replayable_once(tmp_path):
    first = _make_repo(tmp_path)
    tasks = _three(make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)[3])
    runner, _, plan = _launch(tmp_path, first, RESUME_PLAN, tasks[:2], ["cand-a"])
    killed = _drop(runner)  # the attempt of task 2: neither record nor settlement
    runner, campaign, plan = _launch(tmp_path, first, RESUME_PLAN, tasks, ["cand-a"])
    assert counts(plan) == {"local": 4}  # task 2 replayed, task 3 played, task 1 skipped
    replay = next(r for r in results(runner) if r.get("replay_of"))
    assert replay["replay_of"] == {"attempt": 0, "attempt_dir": killed, "outcome": "hard_kill",
                                   "reason": "attempt_started without settlement"}
    rep = _resume_report(campaign, runner)
    assert rep["screening"]["selected"] == "cand-a" and rep["screening"]["warnings"] == []
    assert [(v["outcome"], v["replayed"]) for v in rep["void_attempts"]] == [("hard_kill", True)]
    # the replay is killed in its turn: the same attempt is not replayable a second time
    _drop(runner)
    runner, campaign, plan = _launch(tmp_path, first, RESUME_PLAN, tasks, ["cand-a"])
    assert counts(plan) == {"local": 4}
    assert [r["task"]["pr"] for r in results(runner) if r["record_type"] == "attempt"] == [1, 3]
    screening = _resume_report(campaign, runner)["screening"]
    assert screening["selected"] is None and screening["killed_not_replayed"] == 1


def test_pat111_a_local_attempt_with_a_verdict_is_never_replayed(tmp_path):
    first = _make_repo(tmp_path)
    tasks = make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)[3]
    runner, _, plan = _launch(tmp_path, first, FIX_ALL, tasks[:1], ["cand-a"])
    _drop(runner, record=False, task_pr=1)  # killed right after the record write: no settlement left
    runner, _, plan = _launch(tmp_path, first, FIX_ALL, tasks[:1], ["cand-a"])
    assert counts(plan) == {"local": 1} and not any(r.get("replay_of") for r in results(runner))
    # settled but no record (killed between the two writes): a verdict may have been received
    runner, _, plan = _launch(tmp_path, first, FIX_ALL, tasks[:2], ["cand-a"])
    _drop(runner, settlement=False)
    before = counts(plan)["local"]
    runner, _, plan = _launch(tmp_path, first, FIX_ALL, tasks[:2], ["cand-a"])
    assert counts(plan)["local"] == before


def test_pat111_compare_keeps_the_cost_of_a_void_cloud_round_and_the_invariant_holds(tmp_path, monkeypatch):
    first = _make_repo(tmp_path)
    runner, campaign, plan, tasks = make_runner(tmp_path, "compare", FIX_ALL, repo_bundle=first)
    real, seen = lfc.judge, []

    def judge(*a, **kw):  # a tool failure after the cloud execution was read (cost known)
        seen.append(1)
        if len(seen) == 1:
            raise lfc.CorpusError("judge crashed")
        return real(*a, **kw)

    monkeypatch.setattr(lfc, "judge", judge)
    with pytest.raises(lfc.CorpusError):
        runner.compare(tasks[:1], "cand-a", ("A",))
    monkeypatch.undo()
    again, _, plan, tasks = make_runner(tmp_path, "compare", FIX_ALL, repo_bundle=first)
    again.compare(tasks[:1], "cand-a", ("A",))
    assert counts(plan)["implementer"] == 2  # the void round and its replay (one each)
    recs = [r for r in results(again) if r["record_type"] == "attempt"]
    assert [(r["outcome"], "replay_of" in r) for r in recs] == [("tool_error", False), ("accepted", True)]
    assert recs[1]["replay_of"]["outcome"] == "tool_error"
    started = [e["session_id"] for e in ledger_of(again) if e["kind"] == "cloud_started"]
    assert sorted(s for r in recs for s in r["cloud_sessions"]) == sorted(started) and len(started) == 3
    rep = lfr.report(campaign, results(again), ledger_of(again))  # the ledger/results invariant
    assert rep["ledger"]["unknown_spent_work"] == []
    paid = sum(e["premium_tokens"] for e in ledger_of(again) if e["kind"] == "settled" and e["cloud"])
    assert sum(r["premium"]["billing_total"] for r in recs) == paid  # the void round stays paid for
    assert [v["outcome"] for v in rep["void_attempts"]] == ["tool_error"] and len(rep["replays"]) == 1
    # a decided path is skipped on the next launch
    third, _, plan, tasks = make_runner(tmp_path, "compare", FIX_ALL, repo_bundle=first)
    third.compare(tasks[:1], "cand-a", ("A",))
    assert counts(plan)["implementer"] == 2


def test_pat111_an_interrupted_cloud_round_keeps_its_unknown_cost_and_the_path_stays_undecided(
        tmp_path, monkeypatch):
    first = _make_repo(tmp_path)
    runner, campaign, plan, tasks = make_runner(tmp_path, "compare", FIX_ALL, repo_bundle=first)
    _interrupt_nth(monkeypatch, "cloud_implementer", 1)
    with pytest.raises(KeyboardInterrupt):
        runner.compare(tasks[:1], "cand-a", ("A",))
    monkeypatch.undo()
    again, _, plan, tasks = make_runner(tmp_path, "compare", FIX_ALL, repo_bundle=first)
    again.compare(tasks[:1], "cand-a", ("A",))  # an unmeasured spend still stops every cloud execution
    assert counts(plan) == {} and again.stopped == "cap_reached:premium_tokens_unmeasurable"
    recs = [r for r in results(again) if r["record_type"] == "attempt"]
    assert [r["outcome"] for r in recs] == ["interrupted"] and recs[0]["premium"]["billing_total"] is None
    rep = lfr.report(campaign, results(again), ledger_of(again))
    assert rep["comparison"]["decision"] == "inconclusive"
    assert rep["ledger"]["unknown_spent_work"] == [f"interrupted:{recs[0]['cloud_sessions'][0]}"]
    assert [v["replayed"] for v in rep["void_attempts"]] == [False] and rep["replays"] == []


def test_pat111_a_cut_correction_is_not_replayed_and_the_path_stays_undecided(tmp_path, monkeypatch):
    first = _make_repo(tmp_path)
    plan = {**FIX_ALL, "reviewer": ["PASS", "BLOCK"]}
    runner, campaign, _, tasks = make_runner(tmp_path, "compare", plan, repo_bundle=first)
    _interrupt_nth(monkeypatch, "cloud_implementer", 3)  # the correction of path B
    with pytest.raises(KeyboardInterrupt):
        runner.compare(tasks[:1], "cand-a", ("A", "B"))
    monkeypatch.undo()
    again, _, pplan, tasks = make_runner(tmp_path, "compare", plan, repo_bundle=first)
    again.compare(tasks[:1], "cand-a", ("A", "B"))
    assert counts(pplan) == {"implementer": 1, "economy": 1, "reviewer": 2}  # nothing replayed
    rep = lfr.report(campaign, results(again), ledger_of(again))
    assert rep["comparison"]["arms"]["B"]["economy"] == "unavailable" and rep["replays"] == []


# ------------------------------------------------------------------ PAT-111 pinning (AC4 to AC8)

EVIDENCE = QUALIFICATION / "pat-19-preflight-2026-10-05.json"
PINNED = ("local_harness", "neutral_harness", "cloud_implementer_current", "cloud_implementer_economy",
          "cloud_reviewer")


DISALLOWED = ["Task", "TaskStop", "Workflow", "SendMessage", "ListAgents", "Monitor", "CronCreate",
              "CronDelete", "CronList", "ScheduleWakeup", "RemoteTrigger", "PushNotification", "WebFetch",
              "WebSearch", "Skill", "DesignSync", "EnterWorktree", "ExitWorktree", "ReportFindings",
              "ToolSearch", "NotebookEdit", "Artifact", "ArtifactComments", "ArtifactData", "SendFeedback",
              "TaskCreate", "TaskGet", "TaskList", "TaskUpdate"]
BASH_DENY = ["Bash(gh:*)", "Bash(git push:*)", "Bash(git remote:*)", "Bash(git clone:*)",
             "Bash(git fetch:*)", "Bash(git pull:*)", "Bash(curl:*)", "Bash(wget:*)", "Bash(claude:*)",
             "Bash(codex:*)", "Bash(omp:*)", "Bash(ssh:*)", "Bash(scp:*)", "Bash(nc:*)",
             "Bash(python3 -m http:*)", "Bash(pip install:*)", "Bash(npm:*)", "Bash(security:*)",
             "Bash(open:*)"]


def _committed():
    return json.loads((QUALIFICATION / "pat-19-campaign-v1.json").read_text("utf-8"))


def test_pat111_every_pinned_driver_names_the_evidence_of_its_trial_and_the_file_has_it():
    campaign, evidence = _committed(), json.loads(EVIDENCE.read_text("utf-8"))
    for name in PINNED:
        driver = campaign["drivers"][name]
        ref_file, _, key = driver["evidence"].partition("#")
        assert ref_file == EVIDENCE.name and key.split(".")[0] == "drivers" and key.split(".")[1] == name
        assert name in evidence["drivers"] and driver["verified"] is True
    for cid, cand in campaign["candidates"].items():
        if cand.get("unused"):
            continue
        entry = evidence["candidates"][cid]
        assert cand["smoke"]["evidence"] == f"{EVIDENCE.name}#candidates.{cid}"
        assert cand["smoke"]["passed_at_65536"] is True and entry["smoke_at_65536"]["passed"] is True
        assert cand["file_sha256"] == entry["file_sha256"] and cand["chat_template_sha256"] == entry["chat_template_sha256"]
        assert all(len(h) == 64 for h in cand["file_sha256"].values())
        assert cand["min_context"] == 65536 and cand["load_command"][:5] == ["lms", "load", cand["lm_studio_key"], "-c", "65536"]
        assert cand["model"] == cand["lm_studio_key"] and "--identifier" in cand["load_command"]
        assert "defaults" in cand["generation_parameters"]
    assert campaign["candidates"]["muse-glimmer-30b-gguf"]["engine"] == "llama.cpp"
    assert evidence["candidates"]["muse-glimmer-30b-gguf"]["smoke_at_default_8192"]["passed"] is False
    assert evidence["candidates"]["qwen3-coder-30b-a3b-mlx-4bit"]["smoke_at_default_8192"]["passed"] is False


def test_pat111_the_committed_files_hold_no_absolute_user_path_and_no_secret():
    for path in (EVIDENCE, QUALIFICATION / "pat-19-campaign-v1.json"):
        text = path.read_text("utf-8")
        for needle in ("/Users/", "/private/", "/home/", "BEGIN PRIVATE", "sk-ant", "Bearer "):
            assert needle not in text, (path.name, needle)


def test_pat111_cloud_drivers_are_unsandboxed_with_a_reason_and_pinned_to_one_command_shape():
    campaign = _committed()
    tails = set()
    for name, model, effort in (("cloud_implementer_current", "claude-sonnet-5-5", "medium"),
                                ("cloud_implementer_economy", "claude-haiku-4-5-20251001", None),
                                ("cloud_reviewer", "claude-opus-5-5", "high")):
        driver = campaign["drivers"][name]
        assert driver["sandbox"] is False and driver["sandbox_reason"] and driver["model"] == model
        argv = driver["argv"]
        assert (("--effort" in argv) and argv[argv.index("--effort") + 1] == effort) if effort else "--effort" not in argv
        assert argv[:2] == ["claude", "-p"] and argv[2] == "{prompt}"
        start = argv.index("--disallowedTools") + 1
        denied = argv[start:argv.index("--output-format")]
        assert denied == DISALLOWED + BASH_DENY and "Agent" not in denied and len(denied) == 29 + 19
        assert campaign["cloud_bash_deny"] == BASH_DENY
        assert driver["allowed_tools"] == ["Bash", "Edit", "Read", "Write"]
        tails.add(tuple(argv[argv.index("--permission-mode"):]))  # incl. the disallow list
        assert driver["home"] == "real" and driver["env_allow"] == [] and driver["stream"]["format"] == "claude-stream-json"
    assert len(tails) == 1  # identical host flags for every arm
    for name in ("local_harness", "neutral_harness"):
        assert campaign["drivers"][name].get("sandbox", True) is True


def _load(tmp_path, mutate):
    data = _committed()
    mutate(data)
    path = tmp_path / "c.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return lfr.load_campaign(path)


@pytest.mark.parametrize("mutate,match", [
    (lambda d: d["drivers"]["local_harness"].update(sandbox=False), "cannot run unsandboxed"),
    (lambda d: d["drivers"]["neutral_harness"].update(sandbox=False, sandbox_reason="x"), "cannot run unsandboxed"),
    (lambda d: d["drivers"]["cloud_reviewer"].pop("sandbox_reason"), "needs a sandbox_reason"),
    (lambda d: d["drivers"]["cloud_reviewer"].update(sandbox_reason=" "), "needs a sandbox_reason"),
    (lambda d: d["drivers"]["cloud_reviewer"].update(sandbox="no"), "must be a boolean"),
    (lambda d: d["drivers"]["local_harness"].pop("evidence"), "without evidence"),
    (lambda d: d["drivers"]["cloud_reviewer"]["stream"].update(format="none"), "claude-stream-json"),
    (lambda d: d["drivers"]["cloud_reviewer"]["stream"].update(format="weird"), "unknown stream format"),
    (lambda d: d["drivers"]["cloud_reviewer"].pop("allowed_tools"), "needs allowed_tools"),
    (lambda d: d["drivers"]["cloud_reviewer"].update(allowed_tools="Read"), "allowed_tools must be"),
    (lambda d: d["drivers"]["neutral_harness"]["env_set"].update(OPENAI_API_KEY="sk-real"), "placeholder"),
    (lambda d: d["drivers"]["neutral_harness"]["env_set"].update(FOUNDRY_X="1"), "bad env_set"),
    (lambda d: d["drivers"]["neutral_harness"]["executable"].pop("version"), "executable needs"),
    (lambda d: d["candidates"]["muse-glimmer-30b-gguf"].update(min_context="big"), "min_context")])
def test_pat111_the_campaign_loader_refuses_unsafe_or_unproven_pins(tmp_path, mutate, match):
    with pytest.raises(lfr.RunnerError, match=match):
        _load(tmp_path, mutate)


def test_pat111_the_loaded_instance_is_checked_for_context_quantization_and_key(tmp_path):
    campaign = _committed()
    cid = "qwen3.8-27b-mlx-4bit"
    cand = campaign["candidates"][cid]
    good = {"identifier": cand["model"], "modelKey": cand["lm_studio_key"], "contextLength": 262144,
            "quantization": {"name": "4bit", "bits": 4}}
    facts = lfr.dry_run_facts(campaign, cand["model"])

    def check(**patch):
        item = {**good, **patch}
        item = {k: v for k, v in item.items() if v is not None}
        return lfr.preflight(campaign, cand["model"], lambda argv: json.dumps([item])
                             if tuple(argv) == ("lms", "ps", "--json") else facts(argv), lambda: 99.0, cand)

    assert check()["ok"] and check(contextLength=65536)["ok"]  # LM Studio may load MORE than asked
    assert check(contextLength=8192)["refusals"] == ["loaded_context_below_minimum:8192<65536"]
    assert check(contextLength=None)["refusals"] == ["loaded_context_unknown"]
    assert check(quantization="6bit")["refusals"] == ["loaded_quantization_differs:6bit!=4bit"]
    assert check(quantization=None)["refusals"] == ["loaded_quantization_unknown"]
    assert check(modelKey="qwen3.8-27b-mlx-alias")["refusals"] == [
        "loaded_model_key_differs:qwen3.8-27b-mlx-alias!=qwen/qwen3.8-27b"]
    assert check(quantization="4BIT")["ok"]  # case-insensitive
    ok = check()
    assert ok["facts"]["loaded_context_length"] == 262144 and ok["facts"]["loaded_quantization"] == "4bit"
    for candidate_id, candidate in campaign["candidates"].items():  # the dry-run canned facts pass the real pins
        if not candidate.get("unused"):
            assert lfr.preflight(campaign, candidate["model"], lfr.dry_run_facts(campaign, candidate["model"]),
                                 lambda: 99.0, candidate)["ok"], candidate_id


def test_pat111_the_runner_preflight_refuses_a_candidate_loaded_with_too_small_a_context(tmp_path):
    runner, campaign, plan, tasks = make_runner(tmp_path, "screen", FIX_ALL, caps={"cloud_executions": 0})
    campaign["candidates"]["cand-a"].update(min_context=65536, quantization="4bit", lm_studio_key="k")
    small = {"identifier": "fake/model-a", "modelKey": "k", "contextLength": 8192, "quantization": "4bit"}
    base = lfr.dry_run_facts(campaign, "fake/model-a")
    runner.preflight_run = lambda cid: (lambda argv: json.dumps([small])
                                        if tuple(argv) == ("lms", "ps", "--json") else base(argv))
    with pytest.raises(lfr.PreflightRefused, match="loaded_context_below_minimum"):
        runner.screen(tasks, ["cand-a"])
    assert counts(plan) == {}  # nothing was started


def test_pat111_an_unsandboxed_cloud_driver_runs_under_a_real_sandboxed_run_and_a_local_one_never_does(
        tmp_path, monkeypatch):
    runner, campaign, plan, tasks = make_runner(tmp_path, "compare", FIX_ALL)
    for name in ("cloud_implementer_current", "cloud_reviewer"):
        campaign["drivers"][name]["sandbox"] = False
    runner.sandbox = True
    monkeypatch.setattr(lfr, "sandbox_available", lambda: False)  # as on the CI Linux runner
    seen = []
    real = lfr.execute_driver

    def spy(*args, **kwargs):
        seen.append(kwargs["sandbox"])
        return real(*args, **{**kwargs, "sandbox": False})  # the fake arm has no profile to apply

    monkeypatch.setattr(lfr, "execute_driver", spy)
    assert runner.cloud_path(tasks[0], "A", "comparison")[0]["outcome"] == "accepted"
    assert seen == [False, False]  # implementer and reviewer: no profile asked, none needed
    with pytest.raises(lfr.RunnerError, match="sandbox-exec is unavailable"):  # the economy driver keeps it
        runner.cloud_path(tasks[0], "B", "comparison")
    assert counts(plan).get("economy", 0) == 0
    runner.local_attempt(tasks[0], "cand-a", "local_harness", "S", 0, "screening")
    assert seen[-1] is True  # and a local driver always asks for its profile


def test_pat111_the_r6_environment_reaches_an_unsandboxed_cloud_arm_with_the_real_tmpdir(tmp_path):
    host = {"HOME": "/h", "LANG": "C", "LC_ALL": "C", "LOGNAME": "u", "PATH": "/bin", "TMPDIR": "/real/tmp/",
            "USER": "u", "ANTHROPIC_API_KEY": "secret", "FOUNDRY_STATE_DIR": "/s", "SSH_AUTH_SOCK": "/a"}
    cloud = lfr.isolated_environment({"home": "real", "env_allow": []}, tmp_path / "scratch", host)
    assert {k: cloud[k] for k in ("HOME", "LANG", "LC_ALL", "LOGNAME", "PATH", "TMPDIR", "USER")} == {
        k: host[k] for k in ("HOME", "LANG", "LC_ALL", "LOGNAME", "PATH", "TMPDIR", "USER")}
    assert set(cloud) == {"HOME", "LANG", "LC_ALL", "LOGNAME", "PATH", "TMPDIR", "USER"}
    local = lfr.isolated_environment({"home": "isolated"}, tmp_path / "scratch", host)
    assert local["TMPDIR"] == str(tmp_path / "scratch" / "tmp") and local["HOME"] != "/h"


# ---- the sub-agent assertion from the init event

def _claude_stream_driver(driver, tools):
    driver["stream"] = {"format": "claude-stream-json"}
    if tools is not None:
        driver["argv"] += ["--init-tools", tools]
    return driver


@pytest.mark.parametrize("tools,reason", [("Read,Bash,Task", "sub-agent tool available to the arm: Task"),
                                          ("Agent,Read", "sub-agent tool available to the arm: Agent"),
                                          (None, "no system/init event")])
def test_pat111_a_cloud_record_is_refused_when_the_init_event_allows_a_subagent_or_is_missing(
        tmp_path, tools, reason):
    runner, campaign, plan, tasks = make_runner(tmp_path, "compare", FIX_ALL)
    _claude_stream_driver(campaign["drivers"]["cloud_implementer_current"], tools)
    with pytest.raises(lfr.RunnerError, match=reason):
        runner.cloud_path(tasks[0], "A", "comparison")
    last = results(runner)[-1]
    assert last["status"] == "tool_error" and reason in last["reason"] and last["accepted"] is None
    assert last["premium"]["billing_total"] is None  # refused: its cost is not attributed
    assert runner.ledger.totals()["cloud_started"] == 1 and not runner.ledger.totals()["unsettled_sessions"]
    assert counts(plan).get("reviewer", 0) == 0  # no review is paid on a refused record


def test_pat111_a_cloud_record_with_a_clean_init_event_is_kept(tmp_path):
    runner, campaign, _, tasks = make_runner(tmp_path, "compare", FIX_ALL)
    for name in ("cloud_implementer_current", "cloud_reviewer"):
        _claude_stream_driver(campaign["drivers"][name], "Read,Edit,Bash,Write")
    rec = runner.cloud_path(tasks[0], "A", "comparison")[0]
    assert rec["outcome"] == "accepted" and not rec.get("contaminated")


# ---- the contamination audit

def _stream(path, *calls):
    lines = []
    for kind, name, args in calls:
        if kind == "claude":
            lines.append({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "x"}, {"type": "tool_use", "name": name, "input": args}]}})
        else:
            lines.append({"type": "tool_execution_start", "toolName": name, "args": args})
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n", encoding="utf-8")


def test_pat111_the_audit_flags_reads_of_the_plugin_cache_other_checkouts_and_the_home_configuration(tmp_path):
    repo, _, _, _ = _make_repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(tmp_path / "other-wt")],
                   check=True, capture_output=True)
    home = _fake_home(tmp_path)
    bundle, scratch = tmp_path / "work" / "b", tmp_path / "work" / "s"
    bundle.mkdir(parents=True)
    scratch.mkdir()
    sensitive = lfr.read_deny_list(repo=repo, home=home, state_dir=tmp_path / "state", input_paths=[],
                                   kind="local_harness")
    cache = home / ".claude/plugins/cache/foundry/1.0.0/tests/test_linear_tracker.py"
    stream = tmp_path / "stream.jsonl"

    def audit(*calls):
        _stream(stream, *calls)
        return lfr.audit_transcript(stream, bundle=bundle, scratch=scratch, sensitive=sensitive, home=str(home))

    assert audit(("claude", "Read", {"file_path": str(cache)})) == [
        "~/.claude/plugins/cache/foundry/1.0.0/tests/test_linear_tracker.py"]
    assert audit(("omp", "read", {"path": str(cache)})) == audit(("claude", "Read", {"file_path": str(cache)}))
    assert audit(("claude", "Grep", {"pattern": "def test_", "path": str(home / ".claude/plugins")})) == ["~/.claude/plugins"]
    assert audit(("claude", "Glob", {"pattern": f"{home}/.claude/plugins/**/test_*.py"}))
    assert audit(("claude", "Bash", {"command": "cat ~/.ssh/id_ed25519 | head"})) == ["~/.ssh/id_ed25519"]
    assert audit(("claude", "Bash", {"command": "ls $HOME/.aws && cat ${HOME}/.config/foundry/config.json"})) == [
        "~/.aws", "~/.config/foundry/config.json"]
    assert audit(("claude", "Bash", {"command": f"cd {tmp_path / 'other-wt'} && git log"})) == [
        str(Path(os.path.realpath(tmp_path / "other-wt")))]
    assert audit(("claude", "Read", {"file_path": str(repo / "plugins/foundry/tests/test_m.py")}))
    assert audit(("claude", "Read", {"file_path": str(tmp_path / "state" / "ledger.jsonl")}))
    escaped = os.path.relpath(cache, bundle)  # a relative path that climbs out of the bundle
    assert audit(("claude", "Read", {"file_path": escaped})) == [
        "~/.claude/plugins/cache/foundry/1.0.0/tests/test_linear_tracker.py"]
    assert audit(("claude", "Bash", {"command": f"cat {escaped}"}))
    # what an honest arm does is clean
    assert audit(("claude", "Read", {"file_path": str(bundle / "src/calc.py")}),
                 ("claude", "Edit", {"file_path": "src/calc.py", "old_string": "a", "new_string": "b"}),
                 ("claude", "Bash", {"command": "python3 -m pytest -q 2>&1 | tail -5; ls ./tests /usr/bin /dev/null"}),
                 ("claude", "Write", {"file_path": str(scratch / "review.json"), "content": "{}"}),
                 ("omp", "read", {"path": "tests/test_calc.py"}), ("omp", "bash", {"command": "git ls-files"})) == []
    assert lfr.audit_transcript(tmp_path / "missing.jsonl", bundle=bundle, scratch=scratch,
                                sensitive=sensitive, home=str(home)) == []


def _peek_plan(home, **roles):
    cache = home / ".claude/plugins/cache/foundry/1.0.0/tests/test_linear_tracker.py"
    return {**FIX_ALL, **{role: [f"peek:{cache}"] for role in roles}}


def test_pat111_a_local_attempt_that_read_the_plugin_cache_is_contaminated_undecided_and_never_replayed(tmp_path):
    home = _fake_home(tmp_path)
    first = _make_repo(tmp_path)
    runner, campaign, plan, tasks = make_runner(
        tmp_path, "screen", _peek_plan(home, local=1), caps={"cloud_executions": 0}, repo_bundle=first,
        host_env={**os.environ, "HOME": str(home)})
    out = runner.screen(tasks[:1], ["cand-a"])
    rec = out[0]
    assert rec["contaminated"] is True and rec["accepted"] is None and rec["outcome"] == "contaminated"
    assert rec["local_outcome"] == "contaminated" and rec["judge"]["verdict"] == "ACCEPTED"  # verdict kept, not trusted
    assert rec["contamination"]["paths"] == ["~/.claude/plugins/cache/foundry/1.0.0/tests/test_linear_tracker.py"]
    assert "contaminated" in rec["unknown"]
    rep = lfr.report(campaign, results(runner), ledger_of(runner))
    assert rep["screening"]["selected"] is None and rep["screening"]["reason"] == "incomplete_screening"
    assert rep["screening"]["undecided_tasks"][0]["outcome"] == "contaminated"
    assert rep["screening"]["candidates"] == {}  # not counted as accepted nor as refused
    assert rep["contaminated"][0]["paths"] == rec["contamination"]["paths"]
    again, _, _, tasks = make_runner(tmp_path, "screen", _peek_plan(home, local=1), caps={"cloud_executions": 0},
                                     repo_bundle=first, host_env={**os.environ, "HOME": str(home)})
    assert again.screen(tasks[:1], ["cand-a"]) == [] and counts(plan) == {"local": 1}  # never replayed


def test_pat111_a_clean_local_attempt_is_not_marked(tmp_path):
    runner, _, _, tasks = make_runner(tmp_path, "screen", FIX_ALL, caps={"cloud_executions": 0})
    rec = runner.screen(tasks[:1], ["cand-a"])[0]
    assert "contaminated" not in rec and rec["accepted"] is True


def test_pat111_a_cloud_arm_that_read_the_plugin_cache_is_undecided_unreviewed_and_not_continued(tmp_path):
    home = _fake_home(tmp_path)
    first = _make_repo(tmp_path)
    runner, campaign, plan, tasks = make_runner(
        tmp_path, "compare", _peek_plan(home, implementer=1), repo_bundle=first,
        host_env={**os.environ, "HOME": str(home)})
    records = runner.cloud_path(tasks[0], "A", "comparison")
    assert len(records) == 1
    rec = records[0]
    assert rec["outcome"] == "contaminated" and rec["accepted"] is None and rec["contaminated"] is True
    assert rec["judge"] is None and rec["review"]["rounds"] == 0 and rec["cloud_executions"] == 1
    assert rec["premium"]["billing_total"] == 135  # the money spent stays counted
    assert counts(plan) == {"implementer": 1}  # no review, no correction
    again, _, _, tasks = make_runner(tmp_path, "compare", _peek_plan(home, implementer=1), repo_bundle=first,
                                     host_env={**os.environ, "HOME": str(home)})
    assert again.cloud_path(tasks[0], "A", "comparison") == [] and counts(plan) == {"implementer": 1}  # no replay
    runner.cloud_path(tasks[0], "B", "comparison")  # a clean economy path on the same task
    rep = lfr.report(campaign, results(runner), ledger_of(runner))
    assert rep["comparison"]["arms"]["B"]["economy"] == "unavailable"  # A's cost is not a clean reference
    assert rep["comparison"]["decision"] == "inconclusive" and rep["contaminated"][0]["path"] == "A"
    assert lfr._arm_totals([r for r in results(runner) if r["path"] == "A"])["tasks"][1]["unknown"] is True


def test_pat111_a_reviewer_that_read_the_plugin_cache_leaves_the_task_undecided(tmp_path):
    home = _fake_home(tmp_path)
    runner, campaign, plan, tasks = make_runner(
        tmp_path, "compare", _peek_plan(home, reviewer=1), host_env={**os.environ, "HOME": str(home)})
    rec = runner.cloud_path(tasks[0], "A", "comparison")[-1]
    assert rec["outcome"] == "contaminated" and rec["accepted"] is None and rec["cloud_executions"] == 2
    assert counts(plan) == {"implementer": 1, "reviewer": 1}


def test_pat111_a_contaminated_local_attempt_of_path_c_is_taken_over_by_the_cloud(tmp_path):
    home = _fake_home(tmp_path)
    runner, _, plan, tasks = make_runner(
        tmp_path, "compare", _peek_plan(home, local=1), host_env={**os.environ, "HOME": str(home)})
    recs = runner.hybrid_path(tasks[0], "cand-a", "local_harness", "comparison")
    assert recs[0]["outcome"] == "contaminated" and recs[0]["local_outcome"] == "contaminated"
    assert recs[0]["accepted"] is None and recs[1]["outcome"] == "accepted" and counts(plan)["implementer"] == 1


# ---- the neutral harness executable comes from an operator variable

def _fake_venv(tmp_path, version="2.4.6"):
    venv = tmp_path / "venv-mini"
    (venv / "bin").mkdir(parents=True)
    (venv / f"lib/python3.12/site-packages/mini_swe_agent-{version}.dist-info").mkdir(parents=True)
    exe = venv / "bin" / "mini"
    exe.write_text(FAKE_ARM, encoding="utf-8")
    exe.chmod(0o755)
    return exe


def _neutral_driver(campaign):
    driver = campaign["drivers"]["neutral_harness"]
    driver["argv"] = [sys.executable, "{mini_bin}", *driver["argv"][2:], "--traj", "{scratch}/traj.json"]
    driver["executable"] = {"env": "PAT19_MINI_BIN", "placeholder": "mini_bin", "package": "mini-swe-agent",
                            "version": "2.4.6"}
    driver["env_set"] = {"MSWEA_GLOBAL_CONFIG_DIR": "{scratch}/mswea", "OPENAI_API_KEY": "local-endpoint-no-key"}
    driver["make_dirs"] = ["{scratch}/mswea"]
    driver["trajectory"] = {"file": "{scratch}/traj.json", "steps_path": ["info", "model_stats", "api_calls"]}
    return driver


def test_pat111_the_harness_executable_must_come_from_the_operator_variable_at_the_pinned_version(tmp_path):
    exe = _fake_venv(tmp_path)
    spec = {"executable": {"env": "PAT19_MINI_BIN", "placeholder": "mini_bin", "package": "mini-swe-agent",
                           "version": "2.4.6"}}
    got = lfr.resolve_executable(spec, {"PAT19_MINI_BIN": str(exe)})
    assert got["placeholder"] == "mini_bin" and got["path"] == str(exe) and got["version"] == "2.4.6"
    assert lfr.resolve_executable({}, {}) is None
    for env, match in (({}, "absolute path"), ({"PAT19_MINI_BIN": "mini"}, "absolute path"),
                       ({"PAT19_MINI_BIN": str(tmp_path / "nope")}, "executable file")):
        with pytest.raises(lfr.RunnerError, match=match):
            lfr.resolve_executable(spec, env)
    exe.chmod(0o644)
    with pytest.raises(lfr.RunnerError, match="executable file"):
        lfr.resolve_executable(spec, {"PAT19_MINI_BIN": str(exe)})
    other = _fake_venv(_sub(tmp_path, "other"), version="2.5.0")
    with pytest.raises(lfr.RunnerError, match="is 2.5.0, the pinned version is 2.4.6"):
        lfr.resolve_executable(spec, {"PAT19_MINI_BIN": str(other)})
    bare = tmp_path / "bare" / "bin"
    bare.mkdir(parents=True)
    (bare / "mini").write_text("x")
    (bare / "mini").chmod(0o755)
    with pytest.raises(lfr.RunnerError, match="is unknown"):
        lfr.resolve_executable(spec, {"PAT19_MINI_BIN": str(bare / "mini")})
    committed = _committed()["drivers"]["neutral_harness"]
    assert committed["argv"][0] == "{mini_bin}" and committed["executable"]["env"] == "PAT19_MINI_BIN"
    assert "/" not in committed["executable"]["env"] and committed["executable"]["version"] == "2.4.6"


def test_pat111_the_neutral_harness_runs_from_the_variable_and_reads_its_steps_from_the_trajectory(tmp_path):
    exe = _fake_venv(tmp_path)
    env = {**os.environ, "PAT19_MINI_BIN": str(exe)}
    runner, campaign, plan, tasks = make_runner(tmp_path, "screen", FIX_ALL, caps={"cloud_executions": 0},
                                                host_env=env)
    _neutral_driver(campaign)
    rec = runner.screen(tasks[:1], ["cand-a"], harness_id="neutral_harness")[0]
    assert rec["accepted"] is True and rec["local"]["steps"] == 7 and "local.steps" not in rec["unknown"]
    assert rec["local"]["harness_executable"] == {"env": "PAT19_MINI_BIN", "package": "mini-swe-agent",
                                                  "version": "2.4.6"}
    assert str(exe) not in json.dumps(rec)  # no path in the record


def test_pat111_an_unresolvable_harness_executable_is_refused_before_any_claim_or_spend(tmp_path):
    runner, campaign, plan, tasks = make_runner(tmp_path, "screen", FIX_ALL, caps={"cloud_executions": 0},
                                                host_env={**os.environ})
    os.environ.pop("PAT19_MINI_BIN", None)
    _neutral_driver(campaign)
    with pytest.raises(lfr.RunnerError, match="PAT19_MINI_BIN"):
        runner.screen(tasks, ["cand-a"], harness_id="neutral_harness")
    assert counts(plan) == {} and "attempt_started" not in ledger_kinds(runner) \
        and not runner.results_path.exists()
    runner2, campaign2, plan2, tasks2 = make_runner(_sub(tmp_path, "c"), "compare", FIX_ALL, host_env={**os.environ})
    _neutral_driver(campaign2)
    with pytest.raises(lfr.RunnerError, match="PAT19_MINI_BIN"):
        runner2.compare(tasks2, "cand-a", ["A", "N"])
    assert counts(plan2) == {} and "cloud_started" not in ledger_kinds(runner2)  # not even path A started


def test_pat111_the_stream_stats_read_the_init_event_only_for_a_claude_stream():
    claude = lfr.StreamStats({"format": "claude-stream-json"})
    for line in ('{"type":"assistant"}', "garbage", '{"type":"system","subtype":"init","tools":["Read",3,"Task"]}',
                 '{"type":"system","subtype":"init","tools":["Agent"]}'):
        claude.feed(line)
    assert claude.summary()["init_tools"] == ["Read", "Task"]  # the first init event counts
    none = lfr.StreamStats({"format": "none"})
    none.feed('{"type":"system","subtype":"init","tools":["Task"]}')
    assert "init_tools" not in none.summary() and none.summary()["steps"] is None
    assert lfr.tool_allowlist_refusal({"stream": {"format": "none"}}, {}) is None


@pytest.mark.parametrize("tools,reason", [
    ("Bash,Edit,Read,Write,WebFetch", "outside the allowlist available to the arm: WebFetch"),
    ("Read,Write,Bash,Edit,Task,Skill", "outside the allowlist available to the arm: Skill, Task"),
    (None, "no system/init event")])
def test_pat111_an_extra_tool_in_the_init_event_refuses_a_cloud_record_under_the_allowlist(
        tmp_path, tools, reason):
    runner, campaign, plan, tasks = make_runner(tmp_path, "compare", FIX_ALL)
    driver = _claude_stream_driver(campaign["drivers"]["cloud_implementer_current"], tools)
    driver["allowed_tools"] = ["Bash", "Edit", "Read", "Write"]
    with pytest.raises(lfr.RunnerError, match=reason):
        runner.cloud_path(tasks[0], "A", "comparison")
    last = results(runner)[-1]
    assert last["status"] == "tool_error" and last["accepted"] is None
    assert not runner.ledger.totals()["unsettled_sessions"] and counts(plan).get("reviewer", 0) == 0


def test_pat111_the_exact_allowlist_in_any_order_or_a_subset_is_kept(tmp_path):
    summary = {"init_tools": ["Write", "Read", "Edit", "Bash"]}
    driver = {"stream": {"format": "claude-stream-json"}, "allowed_tools": ["Bash", "Edit", "Read", "Write"]}
    assert lfr.tool_allowlist_refusal(driver, summary) is None
    assert lfr.tool_allowlist_refusal(driver, {"init_tools": ["Read"]}) is None


# =========================================== PAT-111 review round 1 fixes (blocking 1, blocking 2, ac-7)
# A cut AFTER a verdict never gives a second chance; the unsandboxed cloud arms' exposure is limited
# (best-effort Bash deny rules in the pinned argv, an audit of commands and paths) and stated; local arms
# deny the home by default.

def _nth(monkeypatch, owner, name, nth, sig, *, after):
    """The ``nth`` call of ``owner.name`` receives ``sig`` just before (or just after) it runs."""
    real, seen = getattr(owner, name), []

    def wrapper(*a, **kw):
        seen.append(1)
        if len(seen) == nth and not after:
            _send(sig)
        out = real(*a, **kw)
        if len(seen) == nth and after:
            _send(sig)
        return out

    monkeypatch.setattr(owner, name, wrapper)


@pytest.mark.parametrize("after", [False, True])
def test_pat111_fix1_a_local_attempt_cut_right_after_its_verdict_is_decided_and_never_replayed(
        tmp_path, monkeypatch, sentinel_handlers, after):
    first = _make_repo(tmp_path)
    runner, campaign, plan, tasks = make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)
    _nth(monkeypatch, runner, "_discard", 1, signal.SIGTERM, after=after)  # the first thing after the judge
    with pytest.raises(SystemExit):
        runner.screen(tasks[:1], ["cand-a"])
    monkeypatch.undo()
    cut = [r for r in results(runner) if r["record_type"] == "attempt"]
    assert len(cut) == 1 and cut[0]["outcome"] == "interrupted" and cut[0]["accepted"] is None
    assert cut[0]["judge"]["verdict"] == "ACCEPTED"  # the verdict received is kept on the cut record
    assert lfr.report(campaign, results(runner), ledger_of(runner))["void_attempts"] == []
    again, _, _, tasks = make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)
    assert again.screen(tasks[:1], ["cand-a"]) == [] and counts(plan) == {"local": 1}  # no second chance


def test_pat111_fix5_a_signal_between_the_discard_and_the_settle_never_replays_a_judged_attempt(
        tmp_path, monkeypatch, sentinel_handlers):
    """The gap after the bundle is discarded and before the attempt is settled: the signal lands as
    the next critical section starts. It waits for the record; the judged attempt is never replayed."""
    first = _make_repo(tmp_path)
    runner, campaign, plan, tasks = make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)
    real_discard, real_critical, state = runner._discard, runner._critical, {"discarded": False}

    def discard(*a, **kw):
        out = real_discard(*a, **kw)
        state["discarded"] = True
        return out

    def critical():
        if state.pop("discarded", False):  # the first critical section after the discard
            _send(signal.SIGTERM)
        return real_critical()

    monkeypatch.setattr(runner, "_discard", discard)
    monkeypatch.setattr(runner, "_critical", critical)
    with pytest.raises(SystemExit):
        runner.screen(tasks[:1], ["cand-a"])
    monkeypatch.undo()
    rec = [r for r in results(runner) if r["record_type"] == "attempt"]
    assert len(rec) == 1 and rec[0]["judge"]["verdict"] == "ACCEPTED"
    assert lfr.report(campaign, results(runner), ledger_of(runner))["void_attempts"] == []
    again, _, _, tasks = make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)
    assert again.screen(tasks[:1], ["cand-a"]) == [] and counts(plan) == {"local": 1}  # no second chance


CLOUD_CUTS = {  # where the signal falls, which attempt of the path has to stay decided
    "judge_discard": lambda m, r: _nth(m, r, "_discard", 1, signal.SIGTERM, after=False),
    "review_discard": lambda m, r: _nth(m, r, "_discard", 2, signal.SIGTERM, after=True),
    "review_return": lambda m, r: _nth(m, r, "_review", 1, signal.SIGHUP, after=True),
    "before_record": lambda m, r: _once(m, lfr, "_record_total", signal.SIGHUP)}  # review state is set


@pytest.mark.parametrize("where,reviews", [("judge_discard", 0), ("review_discard", 1), ("review_return", 1),
                                           ("before_record", 1)])
def test_pat111_fix1_a_cloud_round_cut_after_its_verdict_or_review_is_never_replayed(
        tmp_path, monkeypatch, sentinel_handlers, where, reviews):
    first = _make_repo(tmp_path)
    runner, campaign, plan, tasks = make_runner(tmp_path, "compare", FIX_ALL, repo_bundle=first)
    CLOUD_CUTS[where](monkeypatch, runner)
    with pytest.raises(SystemExit):
        runner.compare(tasks[:1], "cand-a", ("A",))
    monkeypatch.undo()
    cut = [r for r in results(runner) if r["record_type"] == "attempt"]
    assert len(cut) == 1 and cut[0]["outcome"] == "interrupted" and cut[0]["accepted"] is None
    assert cut[0]["judge"]["verdict"] == "ACCEPTED"
    if where == "before_record":
        assert cut[0]["review"] == {"rounds": 1, "verdicts": ["PASS"]}  # the review is kept too
    again, _, _, tasks = make_runner(tmp_path, "compare", FIX_ALL, repo_bundle=first)
    assert again.compare(tasks[:1], "cand-a", ("A",)) == []
    assert counts(plan) == {"implementer": 1, **({"reviewer": reviews} if reviews else {})}  # no replay
    rep = lfr.report(campaign, results(again), ledger_of(again))
    assert rep["void_attempts"] == [] and rep["replays"] == []
    assert rep["comparison"]["decision"] == "inconclusive"


def test_pat111_fix1_a_cut_before_the_verdict_is_still_void_and_replayed_once(tmp_path, monkeypatch,
                                                                             sentinel_handlers):
    """Control: the rule for a genuinely void attempt (no verdict received) is unchanged."""
    first = _make_repo(tmp_path)
    runner, campaign, plan, tasks = make_runner(tmp_path, "compare", FIX_ALL, repo_bundle=first)
    _once(monkeypatch, lfc, "judge", signal.SIGTERM)  # before the judge runs
    with pytest.raises(SystemExit):
        runner.compare(tasks[:1], "cand-a", ("A",))
    monkeypatch.undo()
    assert [r["judge"] for r in results(runner) if r["record_type"] == "attempt"] == [None]
    again, _, _, tasks = make_runner(tmp_path, "compare", FIX_ALL, repo_bundle=first)
    again.compare(tasks[:1], "cand-a", ("A",))
    assert counts(plan)["implementer"] == 2 and any(r.get("replay_of") for r in results(again))


def test_pat111_fix1_a_bundle_tampered_by_the_arm_is_a_refusal_never_a_void_attempt(tmp_path):
    first = _make_repo(tmp_path)
    runner, campaign, plan, tasks = make_runner(tmp_path, "screen", {**FIX_ALL, "local": ["tamper"]},
                                                repo_bundle=first)
    rec = runner.screen(tasks[:1], ["cand-a"])[0]
    assert rec["local_outcome"] == "refused" and rec["accepted"] is False
    assert rec["judge"]["verdict"] == "REFUSED" and rec["judge"]["note"].startswith("candidate_fault")
    assert rec.get("outcome") not in lfr.LOST_OUTCOMES
    again, _, _, tasks = make_runner(tmp_path, "screen", {**FIX_ALL, "local": ["tamper"]}, repo_bundle=first)
    assert again.screen(tasks[:1], ["cand-a"]) == [] and counts(plan) == {"local": 1}  # not replayed
    rep = lfr.report(campaign, results(runner), ledger_of(runner))
    assert rep["void_attempts"] == [] and rep["screening"]["candidates"]["cand-a"]["accepted"] == 0
    # the cloud round is judged refused (a correction may follow), not a tool error
    second, _, _, tasks2 = make_runner(_sub(tmp_path, "c"), "compare",
                                       {**FIX_ALL, "implementer": ["tamper", "fix"]})
    recs = second.cloud_path(tasks2[0], "A", "comparison")
    assert [r["outcome"] for r in recs] == ["judge_refused", "accepted"]
    assert recs[0]["judge"]["note"].startswith("candidate_fault")


def test_pat111_fix1_a_cloud_session_without_a_result_record_is_listed_as_a_void_attempt(tmp_path):
    runner, campaign, _, tasks = make_runner(tmp_path, "compare", FIX_ALL)
    runner.cloud_path(tasks[0], "A", "comparison")
    # SIGKILL after the settlements, before the record: the ledger knows two sessions no record names
    runner.results_path.write_text("", encoding="utf-8")
    rep = lfr.report(campaign, [], ledger_of(runner))
    assert [(v["outcome"], v["role"], v["replayed"]) for v in rep["void_attempts"]] == [
        ("hard_kill", "implementer", None), ("hard_kill", "reviewer", None)]
    assert all(v["reason"] == "cloud_started without a result record" and len(v["cloud_sessions"]) == 1
               for v in rep["void_attempts"])
    assert len(rep["ledger"]["unknown_spent_work"]) == 2


def test_pat111_fix1_a_contaminated_review_in_path_c_has_no_takeover_at_first_launch_nor_at_resume(tmp_path):
    home = _fake_home(tmp_path)
    first = _make_repo(tmp_path)
    env = {**os.environ, "HOME": str(home)}
    runner, _, plan, tasks = make_runner(tmp_path, "compare", _peek_plan(home, reviewer=1),
                                         repo_bundle=first, host_env=env)
    recs = runner.hybrid_path(tasks[0], "cand-a", "local_harness", "comparison")
    assert [(r["outcome"], r["local_outcome"]) for r in recs] == [("contaminated", "accepted")]
    assert counts(plan).get("implementer", 0) == 0  # no takeover, the task stays undecided, cost kept
    assert recs[0]["premium"]["billing_total"] is not None and recs[0]["cloud_executions"] == 1
    again, _, _, tasks = make_runner(tmp_path, "compare", _peek_plan(home, reviewer=1), repo_bundle=first,
                                     host_env=env)
    assert again.hybrid_path(tasks[0], "cand-a", "local_harness", "comparison") == []
    assert counts(plan).get("implementer", 0) == 0  # identical at resume
    # a contaminated LOCAL attempt is still taken over, at first launch and at resume
    other, _, plan2, tasks2 = make_runner(_sub(tmp_path, "b"), "compare", _peek_plan(home, local=1),
                                          host_env=env)
    other.hybrid_path(tasks2[0], "cand-a", "local_harness", "comparison")
    assert counts(plan2)["implementer"] == 1
    cut = [r for r in results(other) if r["segment"] == "takeover"]
    runner_fix = [r for r in results(other) if r["segment"] == "local"][0]
    assert runner_fix["local_outcome"] == "contaminated" and len(cut) == 1


# ---- blocking 2: the pinned argv, the loader and the audit

def test_pat111_fix2_the_bash_deny_rules_are_data_in_the_config_and_in_the_argv_of_every_cloud_driver():
    campaign = lfr.load_campaign(QUALIFICATION / "pat-19-campaign-v1.json")
    assert campaign["cloud_bash_deny"] == BASH_DENY
    assert "not a sandbox" in campaign["cloud_bash_deny_note"].lower() or "NOT a sandbox" in campaign["cloud_bash_deny_note"]
    for name in ("cloud_implementer_current", "cloud_implementer_economy", "cloud_reviewer"):
        argv = campaign["drivers"][name]["argv"]
        denied = argv[argv.index("--disallowedTools") + 1:]
        assert all(rule in denied for rule in BASH_DENY), name
        assert argv[-2:] == ["stream-json", "--verbose"]  # the flags after the list are unchanged


@pytest.mark.parametrize("mutate,match", [
    (lambda d: d["drivers"]["cloud_reviewer"]["argv"].remove("Bash(gh:*)"), "missing from --disallowedTools: Bash.gh"),
    (lambda d: d["drivers"]["cloud_implementer_economy"]["argv"].remove("Bash(open:*)"), "cloud_implementer_economy"),
    (lambda d: d.pop("cloud_bash_deny"), "need cloud_bash_deny"),
    (lambda d: d.update(cloud_bash_deny=[]), "non-empty list"),
    (lambda d: d.update(cloud_bash_deny=["gh"]), "Bash\\(\\.\\.\\.\\) rules")])
def test_pat111_fix2_the_loader_refuses_a_cloud_driver_whose_argv_lacks_a_bash_deny_rule(tmp_path, mutate, match):
    with pytest.raises(lfr.RunnerError, match=match):
        _load(tmp_path, mutate)


FORBIDDEN_COMMANDS = [
    ("gh pr view 12 --json files", "gh"), ("cd src && git push origin main", "git push"),
    ("git -C . remote add x u", "git remote"), ("git clone https://example.invalid/r.git", "git clone"),
    ("git -c user.name=x fetch --all", "git fetch"), ("git pull", "git pull"),
    ("env FOO=1 curl -s http://example.invalid", "curl"), ("sudo wget x", "wget"),
    ("/usr/bin/curl x", "curl"), ("FOO=1 \\gh api /x", "gh"), ("bash -c 'git clone u'", "git clone"),
    ("sh -lc \"cd src && gh pr merge 3\"", "gh"), ("timeout 5 gh api x", "gh"), ("ls | xargs curl", "curl"),
    ("python3 -m http.server 8000", "python -m http"), ("pip install requests", "pip install"),
    ("python3 -m pip install x", "pip install"), ("npm test", "npm"), ("claude -p hi", "claude"),
    ("codex exec x", "codex"), ("omp -p x", "omp"), ("ssh host ls", "ssh"), ("scp a b:c", "scp"),
    ("nc -l 9", "nc"), ("security find-generic-password -s x", "security"), ("open .", "open"),
    ("echo ok; nohup curl x &", "curl")]
CLEAN_COMMANDS = ["git status && git diff", "git log --oneline -5", "git -C . commit -m 'gh fix'",
                  "python3 -m pytest -q 2>&1 | tail -5", "grep -rn gh src/", "echo open the door",
                  "cat src/open.py", "ls ./tests", "python3 -c 'print(open(1))'", "git remote-ish-name",
                  "cd src && python3 -m pytest ../tests", "sed -i 's/a/b/' src/x.py", "cat ../s/review.json",
                  "pytest -q tests/test_gh.py"]


def _audit_in(tmp_path):
    home = _fake_home(tmp_path)
    repo, _, _, _ = _make_repo(tmp_path)
    bundle, scratch = tmp_path / "work" / "b", tmp_path / "work" / "s"
    bundle.mkdir(parents=True)
    scratch.mkdir()
    sensitive = lfr.read_deny_list(repo=repo, home=home, state_dir=tmp_path / "state", input_paths=[],
                                   kind="local_harness")
    stream = tmp_path / "stream.jsonl"

    def audit(*calls, raw=()):
        _stream(stream, *calls)
        stream.write_text(stream.read_text("utf-8") + "".join(json.dumps(r) + "\n" for r in raw), "utf-8")
        return lfr.audit_transcript(stream, bundle=bundle, scratch=scratch, sensitive=sensitive,
                                    home=str(home))

    return audit, home, bundle


@pytest.mark.parametrize("shape,name", [("claude", "Bash"), ("omp", "bash")])
def test_pat111_fix2_the_audit_flags_forbidden_commands_in_both_stream_shapes(tmp_path, shape, name):
    audit, _, _ = _audit_in(tmp_path)
    for command, label in FORBIDDEN_COMMANDS:
        assert audit((shape, name, {"command": command})) == [f"command:{label}"], command
    for command in CLEAN_COMMANDS:
        assert audit((shape, name, {"command": command})) == [], command
    assert audit((shape, name, {"cmd": "gh api x"})) == ["command:gh"]  # the other usual key
    assert audit((shape, name, {"command": "gh x; curl y; git push"})) == [
        "command:curl", "command:gh", "command:git push"]  # every forbidden command, once each
    assert audit((shape, "Edit", {"file_path": "src/a.py", "new_string": "gh pr view; curl x"})) == []  # not a command


@pytest.mark.parametrize("shape,name", [("claude", "Bash"), ("omp", "bash")])
def test_pat111_fix2_the_audit_resolves_command_paths_against_the_bundle_and_flags_what_leaves_it(
        tmp_path, shape, name):
    audit, home, bundle = _audit_in(tmp_path)
    escaped = str(Path(os.path.realpath(tmp_path)))
    work = str(Path(os.path.realpath(tmp_path / "work")))

    def bash(command):
        return audit((shape, name, {"command": command}))

    assert bash("grep -rn token ~") == ["~"]
    assert bash("find ~ -name '*.py'") == ["~"]
    assert bash("find $HOME/projects -name x") == ["~/projects"]
    assert bash("cd ~ && cat .claude/settings.json") == ["~", "~/.claude/settings.json"]
    assert bash("cd ${HOME}/.config; ls foo/bar") == ["~/.config", "~/.config/foo/bar"]
    assert bash("ls src/../../..") == [escaped]
    assert bash("cat ../../x") == [escaped + "/x"]
    assert bash("ls ..") == [work]
    assert bash("cd .. && ls") == [work]
    assert bash("grep -r x --include=*.py --exclude-dir=/usr/lib .") == []
    assert bash("cat ../s/review.json") == []  # the attempt scratch is allowed
    assert bash("ls src/../tests ./ && cd /usr/bin && ls x/y") == []
    # a path key resolves the same way
    assert audit(("claude", "Read", {"file_path": "src/../../.."})) == [escaped]
    assert audit(("omp", "read", {"path": "~"})) == ["~"]
    assert audit(("claude", "Grep", {"pattern": "a", "path": str(home)})) == ["~"]
    assert audit(("claude", "Edit", {"file_path": "src/a.py", "old_string": "../..", "new_string": "~"})) == []


@pytest.mark.parametrize("shape,name", [("claude", "Bash"), ("omp", "bash")])
def test_pat111_fix3_a_division_operator_in_a_script_is_not_a_path(tmp_path, shape, name):
    audit, home, bundle = _audit_in(tmp_path)
    heredoc = ("python3 - <<'PY'\nfrom pathlib import Path\np = Path('src/calc.py')\n"
               "s = p.read_text().replace('return 0', 'return sum(values) / len(values)')\n"
               "n = len(s) // 2\nprint(n / 2, n // 2)\np.write_text(s)\nPY")
    assert audit((shape, name, {"command": heredoc})) == []
    assert audit((shape, name, {"command": "echo $((4 / 2)) $((4 // 2))"})) == []
    # what names something under the root, the home or outside the bundle is still caught
    assert audit((shape, name, {"command": f"ls {tmp_path}"})) == [str(Path(os.path.realpath(tmp_path)))]
    assert audit((shape, name, {"command": f"ls {home}/.ssh"})) == ["~/.ssh"]
    assert audit((shape, name, {"command": "ls ~"})) == ["~"]
    assert audit((shape, name, {"command": "find ~ -name x"})) == ["~"]
    assert audit((shape, name, {"command": "cat ../../x"}))
    # a tool result that only shows a division is clean too
    event = {"type": "user", "message": {"content": [{"type": "tool_result", "content": "a / b // c"}]}}
    assert audit(raw=[event]) == []


def test_pat111_fix2_the_audit_reads_tool_results_for_sensitive_paths(tmp_path):
    audit, home, bundle = _audit_in(tmp_path)
    cache = home / ".claude/plugins/cache/foundry/1.0.0/tests/test_linear_tracker.py"
    expected = ["tool_result:~/.claude/plugins/cache/foundry/1.0.0/tests/test_linear_tracker.py"]
    claude = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t",
                                                        "content": f"1\t{cache}:12: assert x"}]}}
    claude_blocks = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t",
                      "content": [{"type": "text", "text": f"see {cache}"}]}]}}
    omp = {"type": "tool_execution_end", "toolName": "bash", "result": {"content": [
        {"type": "text", "text": f"File \"{cache}\", line 3"}]}}
    for event in (claude, claude_blocks, omp):
        assert audit(raw=[event]) == expected
    benign = {"type": "user", "message": {"content": [{"type": "tool_result", "content":
              f"{bundle}/src/a.py /usr/bin/python3 ./x ../y ~/notes {tmp_path}/other"}]}}
    assert audit(raw=[benign, {"type": "user", "message": {"content": "plain text"}}]) == []
    both = audit(("claude", "Bash", {"command": "gh pr view 1"}), raw=[claude])
    assert both == [*expected, "command:gh"]  # paths first, then commands


def test_pat111_fix2_a_forbidden_command_marks_the_record_contaminated_in_every_arm(tmp_path):
    first = _make_repo(tmp_path)
    plan = {**FIX_ALL, "local": ["cmd:gh pr view 3"], "implementer": ["cmd:curl -s example.invalid"]}
    runner, campaign, p, tasks = make_runner(tmp_path, "compare", plan, repo_bundle=first)
    local = runner.hybrid_path(tasks[0], "cand-a", "local_harness", "comparison")
    assert local[0]["outcome"] == "contaminated" and local[0]["accepted"] is None
    assert local[0]["contamination"] == {"paths": [], "commands": ["gh"]}
    assert local[0]["judge"]["verdict"] == "ACCEPTED" and local[0]["local_outcome"] == "contaminated"
    assert "forbidden command" in local[0]["unknown"]["contaminated"]
    cloud = runner.cloud_path(tasks[0], "A", "comparison")  # path A: implementer runs curl
    assert [r["outcome"] for r in cloud] == ["contaminated"] and cloud[0]["accepted"] is None
    assert cloud[0]["contamination"] == {"paths": [], "commands": ["curl"]}
    assert cloud[0]["judge"] is None and cloud[0]["premium"]["billing_total"] == 135  # cost kept, no review
    assert counts(p)["implementer"] == 2 and counts(p).get("reviewer", 0) == 0
    rep = lfr.report(campaign, results(runner), ledger_of(runner))
    assert {tuple(c["commands"]) for c in rep["contaminated"]} == {("gh",), ("curl",)}
    again, _, _, tasks = make_runner(tmp_path, "compare", plan, repo_bundle=first)
    assert again.cloud_path(tasks[0], "A", "comparison") == []  # not replayed
    assert counts(p)["implementer"] == 2


# ---- ac-7: default denial of the home for local drivers

def _real(path):
    return os.path.realpath(path)


def test_pat111_ac7_the_profile_denies_the_home_then_allows_only_the_list_and_keeps_the_explicit_deny_on_top(
        tmp_path):
    home = tmp_path / "home"
    bundle, scratch = home / "work" / "attempt" / "bundle", home / "work" / "attempt" / "scratch"
    tool = home / ".local" / "tool"
    profile = lfr.sandbox_profile(writable=[bundle, scratch], deny_read=[home / ".ssh"], network="loopback",
                                  deny_home=home, allow_read=[tool])
    lines = profile.splitlines()
    deny_home = f'(deny file-read* (subpath "{_real(home)}"))'
    allow = next(x for x in lines if x.startswith("(allow file-read* "))
    explicit = f'(deny file-read* (subpath "{_real(home / ".ssh")}"))'
    assert lines.index(deny_home) < lines.index(allow) < lines.index(explicit)  # last match wins
    for kept in (bundle, scratch, tool):
        assert f'(subpath "{_real(kept)}")' in allow
    assert _real(home / ".ssh") not in allow and _real(home / "Documents") not in profile
    assert allow.count("(subpath") == 3  # nothing else is readable under the home
    metadata = next(x for x in lines if x.startswith("(allow file-read-metadata "))
    for ancestor in (home, home / "work", home / "work" / "attempt", home / ".local"):
        assert f'(literal "{_real(ancestor)}")' in metadata
    assert _real(bundle) not in metadata and _real(tool) not in metadata  # metadata of ancestors only
    assert "(deny file-write*)" in lines and "(deny network*)" in lines
    plain = lfr.sandbox_profile(writable=[bundle], deny_read=[], network="open")  # unchanged without it
    assert "file-read" not in plain
    outside = lfr.sandbox_profile(writable=[tmp_path / "w"], deny_read=[], network="open", deny_home=home)
    assert "file-read-metadata" not in outside  # work root outside the home: no ancestor to stat
    with pytest.raises(lfr.RunnerError, match="read-denied"):  # the existing guard still holds
        lfr.sandbox_profile(writable=[home / ".ssh" / "w"], deny_read=[home / ".ssh"], network="open",
                            deny_home=home)


def test_pat111_ac7_only_local_drivers_deny_the_home_and_the_option_and_allow_list_come_from_the_config(
        tmp_path, monkeypatch):
    home = tmp_path / "home"
    runner, campaign, plan, tasks = make_runner(tmp_path, "compare", FIX_ALL, host_env={**os.environ, "HOME": str(home)})
    drivers = campaign["drivers"]
    for name in ("local_harness", "neutral_harness"):
        assert runner._home_policy(drivers[name]) == (home, [])
    for name in ("cloud_implementer_current", "cloud_implementer_economy", "cloud_reviewer"):
        assert runner._home_policy(drivers[name]) == (None, [])  # never a cloud arm (AGENTS.md R6)
    campaign["isolation"]["allow_read_home"] = [".bun", ".local/tool"]
    exe = {"path": str(home / "venv" / "bin" / "mini")}
    assert runner._home_policy(drivers["local_harness"], exe)[1][:2] == [home / ".bun", home / ".local/tool"]
    assert home / "venv" in runner._home_policy(drivers["local_harness"], exe)[1]
    seen = {}
    real = lfr.execute_driver

    def spy(driver, *args, **kwargs):
        seen[driver["kind"]] = (kwargs.get("deny_home"), list(kwargs.get("allow_read", [])))
        return real(driver, *args, **{**kwargs, "sandbox": False})  # the fake arm has no profile

    monkeypatch.setattr(lfr, "execute_driver", spy)
    runner.sandbox = True
    runner.local_attempt(tasks[0], "cand-a", "local_harness", "S", 0, "screening")
    assert seen["local_harness"] == (home, [home / ".bun", home / ".local/tool"])
    runner.cloud_execution("implementer", "cloud_implementer_current", *runner._bundle(tasks[0], "x"), "implement")
    assert seen["cloud_implementer"] == (None, [])
    campaign["isolation"]["deny_home_by_default"] = False  # the documented fallback
    assert runner._home_policy(drivers["local_harness"]) == (None, [])
    committed = lfr.load_campaign(QUALIFICATION / "pat-19-campaign-v1.json")["isolation"]
    assert committed["deny_home_by_default"] is True and committed["allow_read_home"] == []
    assert "deny_home_by_default" not in lfr.load_campaign(QUALIFICATION / "pat-19-campaign-v1.json").get("drivers", {})


@pytest.mark.parametrize("patch,match", [
    ({"deny_home_by_default": "yes"}, "must be a boolean"),
    ({"allow_read_home": ["/abs"]}, "allow_read_home"), ({"allow_read_home": ["../x"]}, "allow_read_home"),
    ({"allow_read_home": "x"}, "allow_read_home"), ({"allow_read_home": [""]}, "allow_read_home")])
def test_pat111_ac7_the_loader_refuses_a_bad_home_policy(tmp_path, patch, match):
    with pytest.raises(lfr.RunnerError, match=match):
        _load(tmp_path, lambda d: d["isolation"].update(patch))


def test_pat111_ac7_a_local_arm_under_the_real_sandbox_reads_nothing_under_the_home_but_its_allow_list(
        tmp_path, monkeypatch):
    if not lfr.sandbox_available():
        pytest.skip("sandbox-exec is macOS only: the home read-denial probe cannot run on this platform "
                    "(the generated profile is unit-tested above)")
    if not _sandbox_works(tmp_path):
        pytest.skip("sandbox-exec cannot be applied here (already inside a sandbox)")
    home, boxed = _fake_home(tmp_path), tmp_path / "boxed"
    (home / "Documents").mkdir()
    (home / "Documents" / "note.txt").write_text("canary")
    (home / ".local" / "tool").mkdir(parents=True)
    (home / ".local" / "tool" / "lib.txt").write_text("fine")
    (boxed / "bundle").mkdir(parents=True)
    (boxed / "bundle" / "control.txt").write_text("fine")
    script = tmp_path / "read_probe.py"
    script.write_text(READ_PROBE, encoding="utf-8")
    targets = {"note": home / "Documents" / "note.txt", "ssh": home / ".ssh" / "id_ed25519",
               "allowed": home / ".local" / "tool" / "lib.txt", "control": boxed / "bundle" / "control.txt"}
    driver = {"kind": "local_harness",
              "argv": [sys.executable, str(script)] + [x for k, v in targets.items() for x in (k, str(v))]}
    execution = lfr.execute_driver(
        driver, {}, workdir=boxed / "bundle", scratch=boxed / "scratch", stream_log=tmp_path / "stream.log",
        max_seconds=60, max_steps=None, sandbox=True, deny_read=[home / ".ssh"], host_env=dict(os.environ),
        deny_home=home, allow_read=[home / ".local" / "tool"])
    assert execution["exit_code"] == 0, execution
    probes = json.loads((tmp_path / "stream.log").read_text("utf-8").splitlines()[-1])
    assert probes == {"note": "EPERM", "ssh": "EPERM", "allowed": "ok", "control": "ok"}


# ---- the other nits

def test_pat111_fix_nit3_a_loaded_instance_without_a_model_key_is_refused(tmp_path):
    campaign = _committed()
    cid = "qwen3.8-27b-mlx-4bit"
    cand = campaign["candidates"][cid]
    facts = lfr.dry_run_facts(campaign, cand["model"])
    item = {"identifier": cand["model"], "contextLength": 262144, "quantization": {"name": "4bit", "bits": 4}}
    result = lfr.preflight(campaign, cand["model"], lambda argv: json.dumps([item])
                           if tuple(argv) == ("lms", "ps", "--json") else facts(argv), lambda: 99.0, cand)
    assert result["refusals"] == ["loaded_model_key_unknown"] and result["ok"] is False
    evidence = json.loads(EVIDENCE.read_text("utf-8"))["lms_ps_json_observed"]
    assert set(evidence["fields"]) == {"modelKey", "identifier", "quantization", "contextLength"}
    assert set(evidence["fields"]["quantization"]) == {"name", "bits"}


def test_pat111_fix_nit4_the_neutral_step_bound_is_told_to_the_harness_and_marked_after_the_run(tmp_path):
    argv = _committed()["drivers"]["neutral_harness"]["argv"]
    assert ["-c", "agent.step_limit={max_steps}"] == argv[argv.index("agent.step_limit={max_steps}") - 1:][:2]
    exe = _fake_venv(tmp_path)
    runner, campaign, plan, tasks = make_runner(
        tmp_path, "screen", FIX_ALL, caps={"cloud_executions": 0}, host_env={**os.environ, "PAT19_MINI_BIN": str(exe)},
        campaign_over={"bounds": {"local_max_steps": 5}})
    _neutral_driver(campaign)  # the fake harness reports 7 API calls
    rec = runner.screen(tasks[:1], ["cand-a"], harness_id="neutral_harness")[0]
    assert rec["local"]["steps"] == 7 and rec["local"]["step_limit_hit"] is True
    assert rec["local"]["ended_by_external_signal"] is False and rec["judge"]["verdict"] == "ACCEPTED"
    assert rec["local_outcome"] == "refused" and rec["accepted"] is False  # over the bound: never accepted
    assert "local.step_limit" in rec["unknown"]


def _screening_runner(tmp_path, first, campaign_id, plan=FIX_ALL, **over):
    runner, campaign, _, tasks = make_runner(tmp_path, "screen", plan, repo_bundle=first,
                                             campaign_id=campaign_id, **over)
    runner.screen(tasks, ["cand-a"])
    return runner


def test_pat111_fix_nit9_a_local_comparison_needs_the_screening_of_its_campaign_or_a_named_completed_one(
        tmp_path):
    first = _make_repo(tmp_path)
    _screening_runner(tmp_path, first, "screen-1")
    strict = dict(repo_bundle=first, campaign_id="compare-1", require_screening=True)
    runner, _, plan, tasks = make_runner(tmp_path, "compare", FIX_ALL, **strict)
    for paths in (("C",), ("N",), ("A", "C")):
        with pytest.raises(lfr.RunnerError, match="no screening results under campaign id compare-1"):
            runner.compare(tasks, "cand-a", paths)
    assert "implementer" not in counts(plan) and "cloud_started" not in ledger_kinds(runner)  # no spend
    runner.compare(tasks[:1], "cand-a", ("A",))  # cloud-only paths have no local candidate to check
    ok, _, _, tasks = make_runner(tmp_path, "compare", FIX_ALL, screening_campaign="screen-1", **{
        **strict, "campaign_id": "compare-2"})
    assert len(ok.compare(tasks[:1], "cand-a", ("C",))) >= 1  # a completed screening that selected it
    before = (tmp_path / "state" / "results-screen-1.jsonl").read_text("utf-8")
    assert (tmp_path / "state" / "results-screen-1.jsonl").read_text("utf-8") == before  # read-only
    for campaign_id, screening, match in (
            ("compare-3", "screen-9", "no screening results at results-screen-9"),
            ("compare-3", "compare-3", "another plain campaign id"),
            ("compare-3", "../x", "another plain campaign id")):
        bad, _, _, tasks = make_runner(tmp_path, "compare", FIX_ALL, screening_campaign=screening,
                                       **{**strict, "campaign_id": campaign_id})
        with pytest.raises(lfr.RunnerError, match=match):
            bad.compare(tasks, "cand-a", ("C",))
    wrong, _, _, tasks = make_runner(tmp_path, "compare", FIX_ALL, screening_campaign="screen-1",
                                     **{**strict, "campaign_id": "compare-4"})
    with pytest.raises(lfr.RunnerError, match="not the one the screening selected"):
        wrong.compare(tasks, "cand-b", ("C",))
    # an incomplete screening, or one made under another config, cannot select a candidate here
    _screening_runner(tmp_path, first, "screen-2", campaign_over={"bounds": {"local_max_seconds": 21}})
    other, _, _, tasks = make_runner(tmp_path, "compare", FIX_ALL, screening_campaign="screen-2",
                                     **{**strict, "campaign_id": "compare-5"})
    with pytest.raises(lfr.RunnerError, match="not produced under this campaign config"):
        other.compare(tasks, "cand-a", ("C",))


def test_pat111_fix_nit9_the_cli_refuses_a_local_comparison_without_screening_results(tmp_path, capsys):
    repo, snap, _, _ = _make_repo(tmp_path)
    campaign, _ = fake_campaign(tmp_path, FIX_ALL)
    (tmp_path / "c.json").write_text(json.dumps(campaign), encoding="utf-8")
    (tmp_path / "snap.json").write_text(json.dumps(snap), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"screening": [{"pr": 1}], "comparison": [{"pr": 1}]}),
                                            encoding="utf-8")
    base = ["--campaign", str(tmp_path / "c.json"), "--dry-run", "--envelope", str(write_envelope(tmp_path)),
            "--state-dir", str(tmp_path / "state"), "--work-root", str(tmp_path / "work"),
            "--repo", str(repo), "--snapshot", str(tmp_path / "snap.json"),
            "--manifest", str(tmp_path / "manifest.json"), "--candidate", "cand-a"]
    assert lfr.main(["compare", *base, "--paths", "C"], today=TODAY) == 2
    assert "no screening results under campaign id test-campaign" in capsys.readouterr().err
    assert lfr.main(["compare", *base, "--paths", "C", "--screening-campaign", "nope"], today=TODAY) == 2
    assert "results-nope.jsonl" in capsys.readouterr().err


# ---- review round 2

def _root_reads_anything():
    return hasattr(os, "geteuid") and os.geteuid() == 0


@pytest.mark.parametrize("behavior", ["lock", "unreadable", "nested", "cache_unreadable"])
def test_pat111_fix3_a_bundle_the_arm_left_unreadable_is_a_refusal_never_a_void_attempt(tmp_path, behavior):
    """B1: a git failure or an ``OSError`` on the bundle AFTER the arm ran (stale index lock, unreadable
    file, empty nested repository, a file the judge cannot read) is the arm's doing: REFUSED, decided,
    cost kept, never replayed."""
    if "unreadable" in behavior and _root_reads_anything():
        pytest.skip("root reads a mode-0 file: the unreadable-file case cannot be built")
    first = _make_repo(tmp_path)
    plan = {**FIX_ALL, "local": [behavior]}
    runner, campaign, p, tasks = make_runner(tmp_path, "screen", plan, repo_bundle=first)
    rec = runner.screen(tasks[:1], ["cand-a"])[0]
    assert rec["judge"]["verdict"] == "REFUSED" and rec["judge"]["note"].startswith("candidate_fault: ")
    assert str(tmp_path.resolve()) not in rec["judge"]["note"]  # the error is recorded, no work path
    assert {"lock": "index.lock", "unreadable": "Permission denied", "nested": "sub/",
            "cache_unreadable": "__pycache__/notes.txt"}[behavior] in rec["judge"]["note"]
    assert rec["local_outcome"] == "refused" and rec["accepted"] is False
    assert rec.get("outcome") not in lfr.LOST_OUTCOMES and rec["wall_seconds"] > 0  # cost kept
    again, _, _, tasks = make_runner(tmp_path, "screen", plan, repo_bundle=first)
    assert again.screen(tasks[:1], ["cand-a"]) == [] and counts(p) == {"local": 1}  # never replayed
    assert lfr.report(campaign, results(runner), ledger_of(runner))["void_attempts"] == []
    # a cloud round is judged refused, its cost kept, and a correction may follow
    second, _, _, tasks2 = make_runner(_sub(tmp_path, "c"), "compare", {**FIX_ALL, "implementer": [behavior, "fix"]})
    recs = second.cloud_path(tasks2[0], "A", "comparison")
    assert [r["outcome"] for r in recs] == ["judge_refused", "accepted"]
    assert recs[0]["judge"]["note"].startswith("candidate_fault: ") and recs[0]["premium"]["billing_total"] == 135


def test_pat111_fix3_only_an_oserror_on_the_bundle_content_is_the_arm_s_doing(tmp_path, monkeypatch):
    first = _make_repo(tmp_path)
    runner, campaign, p, tasks = make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first)

    def locked(candidate, base=None):  # ``purge_bytecode`` cannot unlink a bytecode file of the bundle
        raise PermissionError(errno.EACCES, "Permission denied", str(candidate / "src" / "x.pyc"))

    monkeypatch.setattr(lfc, "purge_bytecode", locked)
    rec = runner.screen(tasks[:1], ["cand-a"])[0]
    assert rec["judge"]["verdict"] == "REFUSED"
    assert rec["judge"]["note"] == ("candidate_fault: the judge cannot handle the bundle content: "
                                    "PermissionError on src/x.pyc: Permission denied")  # no absolute path
    # an OSError of the launcher's environment (outside the bundle) stays a void, replayable attempt
    other, _, p2, tasks2 = make_runner(_sub(tmp_path, "b"), "screen", FIX_ALL)

    def environment(*_args, **_kw):
        raise OSError(errno.ENOSPC, "No space left on device", str(tmp_path / "elsewhere"))

    monkeypatch.setattr(lfc, "judge", environment)
    with pytest.raises(OSError):
        other.screen(tasks2[:1], ["cand-a"])
    assert [r["outcome"] in lfr.LOST_OUTCOMES and r["judge"] is None
            for r in results(other) if r["record_type"] == "attempt"] == [True]


@pytest.mark.parametrize("shape", ["claude", "omp"])
def test_pat111_fix3_text_that_mentions_a_home_path_is_not_an_access(tmp_path, shape):
    """B2: a bundle file, a written text or a searched pattern that MENTIONS ``~/.config`` or ``~/.claude``
    is not a read of the home; a command or a result that really names it still is."""
    audit, home, bundle = _audit_in(tmp_path)
    read, edit, write, bash = (("Read", "Edit", "Write", "Bash") if shape == "claude"
                               else ("read", "edit", "write", "bash"))
    path_key = "file_path" if shape == "claude" else "path"
    mention = "registry: `~/.config/foundry/registry.json`; settings under ~/.claude and $HOME/.claude\n"

    def result(text):
        if shape == "claude":
            return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t",
                                                             "content": text}]}}
        return {"type": "tool_execution_end", "toolName": read, "result": {"content": [
            {"type": "text", "text": text}]}}

    assert audit((shape, read, {path_key: str(bundle / "registry.py")}), raw=[result(mention)]) == []
    assert audit((shape, edit, {path_key: "src/a.py", "old_string": "~/.config/foundry/registry.json",
                                "new_string": "~/.claude/x", "oldText": "~/.claude", "newText": "$HOME/.ssh"})) == []
    assert audit((shape, write, {path_key: "docs/a.md", "content": mention})) == []
    assert audit(("claude", "Grep", {"pattern": "~/.claude", "path": "."})) == []
    for command in ("grep -rn '~/.claude' .", 'grep -rn "~/.config/foundry" src', "rg -n '$HOME/.claude' .",
                    "echo '~/.claude' > notes.txt"):
        assert audit((shape, bash, {"command": command})) == [], command
    # the guards stay red
    assert audit((shape, bash, {"command": "cat ~/.config/foundry/registry.json"})) == [
        "~/.config/foundry/registry.json"]
    assert audit((shape, bash, {"command": "ls ~/.claude/plugins"})) == ["~/.claude/plugins"]
    assert audit((shape, bash, {"command": 'cat "$HOME/.claude/settings.json"'})) == ["~/.claude/settings.json"]
    assert audit((shape, bash, {"command": "grep -rn x '~/.claude' ~/.claude"})) == ["~/.claude"]
    assert audit((shape, read, {path_key: "~/.claude/settings.json"})) == ["~/.claude/settings.json"]
    listing = f"{home}/.claude/plugins/cache/foundry/1.0.0/tests/test_m.py\n"
    assert audit(raw=[result(listing)]) == [
        "tool_result:~/.claude/plugins/cache/foundry/1.0.0/tests/test_m.py"]


@pytest.mark.parametrize("shape,name", [("claude", "Bash"), ("omp", "bash")])
def test_pat111_fix3_a_bare_root_given_to_a_filesystem_reader_is_a_path(tmp_path, shape, name):
    """N2: ``/`` is the division operator, except as an argument of find, grep -r/-R, rg, ls, du, tree, cat."""
    audit, _, _ = _audit_in(tmp_path)
    for command in ("find / -name x -exec cat {} +", "ls /", "grep -rn token /", "grep -R x / --include=*.py",
                    "rg token /", "du -sh /", "tree /", "cat /", "cd src && find / -name '*.py'",
                    "sudo find / -name x", "grep --recursive x /"):
        assert audit((shape, name, {"command": command})) == ["/"], command
    heredoc = ("python3 - <<'E'\nvalues = [1, 2]\nprint(sum(values) / len(values))\nE")
    for command in (heredoc, "echo $((4 / 2))", "grep -n x / || true", "expr 4 / 2"):
        assert audit((shape, name, {"command": command})) == [], command


def test_pat111_fix3_claude_settings_of_the_implementer_never_reach_the_reviewer_bundle(tmp_path, monkeypatch):
    """N3: the implementer's ``.claude/`` files are not applied to the reviewer's bundle, and the record
    says so."""
    applied = []
    real = lfr._apply_patch

    def spy(bundle, patch):
        applied.append(patch)
        return real(bundle, patch)

    monkeypatch.setattr(lfr, "_apply_patch", spy)
    runner, _, _, tasks = make_runner(tmp_path, "compare", {**FIX_ALL, "implementer": ["dotclaude"],
                                                            "local": ["dotclaude"]})
    rec = runner.cloud_path(tasks[0], "A", "comparison")[0]
    assert rec["outcome"] == "accepted" and rec["review_excluded"] == [".claude/settings.json"]
    assert applied and b".claude/" not in applied[-1] and b"m.py" in applied[-1]
    local = runner.hybrid_path(tasks[1], "cand-a", "local_harness", "comparison")[0]
    assert local["review_excluded"] == [".claude/settings.json"] and b".claude/" not in applied[-1]
    clean, _, _, tasks = make_runner(_sub(tmp_path, "b"), "compare", FIX_ALL)
    assert "review_excluded" not in clean.cloud_path(tasks[0], "A", "comparison")[0]
    patch = (b"diff --git a/x.py b/x.py\n+1\ndiff --git a/.claude/s.json b/.claude/s.json\n+{}\n"
             b"diff --git a/p/.claude/agents/a.md b/p/.claude/agents/a.md\n+x\n")
    assert lfr._without_claude_dirs(patch) == (b"diff --git a/x.py b/x.py\n+1\n",
                                               [".claude/s.json", "p/.claude/agents/a.md"])


def test_pat111_fix3_a_refusal_on_init_still_records_the_contamination_and_is_never_replayed(tmp_path):
    """N5: the audit runs even when the ``init`` tool set refuses the record."""
    home = _fake_home(tmp_path)
    first = _make_repo(tmp_path)
    env = {**os.environ, "HOME": str(home)}
    plan = _peek_plan(home, implementer=1)
    runner, campaign, p, tasks = make_runner(tmp_path, "compare", plan, repo_bundle=first, host_env=env)
    _claude_stream_driver(campaign["drivers"]["cloud_implementer_current"], "Read,Edit,Bash,Write,Task")
    with pytest.raises(lfr.RunnerError, match="sub-agent tool"):
        runner.cloud_path(tasks[0], "A", "comparison")
    rec = [r for r in results(runner) if r["record_type"] == "attempt"][0]
    assert rec["status"] == "tool_error" and "sub-agent tool" in rec["reason"]
    assert rec["contaminated"] is True and rec["outcome"] == "contaminated" and rec["accepted"] is None
    assert rec["contamination"]["paths"] == ["~/.claude/plugins/cache/foundry/1.0.0/tests/test_linear_tracker.py"]
    again, campaign2, _, tasks = make_runner(tmp_path, "compare", plan, repo_bundle=first, host_env=env)
    _claude_stream_driver(campaign2["drivers"]["cloud_implementer_current"], "Read,Edit,Bash,Write")
    assert again.cloud_path(tasks[0], "A", "comparison") == [] and counts(p)["implementer"] == 1  # not replayed


# ---- round 3 of the review: what is text is never read as an access, a contamination is never lost

def _audit_kw(tmp_path):
    """Like ``_audit_in``, with the ``arm_home`` / ``own_session`` options of ``audit_transcript``."""
    home = _fake_home(tmp_path)
    repo, _, _, _ = _make_repo(tmp_path)
    bundle, scratch = tmp_path / "work" / "b", tmp_path / "work" / "s"
    bundle.mkdir(parents=True)
    scratch.mkdir()
    sensitive = lfr.read_deny_list(repo=repo, home=home, state_dir=tmp_path / "state", input_paths=[],
                                   kind="local_harness")
    stream = tmp_path / "stream.jsonl"

    def audit(*calls, raw=(), **kw):
        _stream(stream, *calls)
        stream.write_text(stream.read_text("utf-8") + "".join(json.dumps(r) + "\n" for r in raw), "utf-8")
        return lfr.audit_transcript(stream, bundle=bundle, scratch=scratch, sensitive=sensitive,
                                    home=str(home), **kw)

    return audit, home, bundle, scratch


SESSION = "0b6c1e9e-2f6a-4d1c-9a51-3b7f0c2d4e81"
OTHER_SESSION = "7d2e4a10-8c3b-4f5e-b6a7-1c9d0e2f3a4b"


def test_pat111_fix4_b1_a_cloud_arm_reads_back_the_tool_output_claude_code_saved_in_its_own_session(tmp_path):
    """B1 (reproduced): Claude Code saves a tool output too large for the stream under
    ``<projects_dir>/<slug>/<session_id>/tool-results/`` and the result names it; the arm then reads it.
    Its OWN session directory is allowed; another session's directory stays a hit."""
    audit, home, bundle, _ = _audit_kw(tmp_path)

    def saved(session):
        target = home / ".claude/projects/-tmp-work-b" / session / "tool-results/toolu_01Xy.txt"
        result = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "content":
                  f"Output too large (61.2KB). Full output saved to: {target}\n\nPreview (first 2KB):\nok"}]}}
        return ("claude", "Read", {"file_path": str(target)}), result, target

    own = ("~/.claude/projects", SESSION)
    call, result, _ = saved(SESSION)
    assert audit(call, raw=[result], own_session=own) == []
    assert audit(("claude", "Bash", {"command": f"tail -50 {saved(SESSION)[2]}"}), own_session=own) == []
    call, result, target = saved(OTHER_SESSION)  # another session: flagged, in the result and in the read
    shown = "~/.claude/projects/-tmp-work-b/" + OTHER_SESSION + "/tool-results/toolu_01Xy.txt"
    assert audit(call, raw=[result], own_session=own) == [shown, f"tool_result:{shown}"]
    assert audit(*[saved(SESSION)[0]], raw=[saved(SESSION)[1]]) != []  # without the option: flagged
    # neither the session log itself, nor the projects directory, nor the rest of ~/.claude are allowed
    for path in (home / f".claude/projects/-tmp-work-b/{SESSION}.jsonl", home / ".claude/projects",
                 home / ".claude/projects/-tmp-work-b", home / ".claude/plugins/cache"):
        assert audit(("claude", "Read", {"file_path": str(path)}), own_session=own), path


def test_pat111_fix4_b1_the_runner_allows_the_session_directory_from_the_driver_session_log(tmp_path):
    home = _fake_home(tmp_path)
    runner, campaign, _, _ = make_runner(tmp_path, "compare", FIX_ALL, host_env={**os.environ, "HOME": str(home)})
    driver = dict(campaign["drivers"]["cloud_implementer_current"])
    driver["session_log"] = {**driver["session_log"], "projects_dir": "~/.claude/projects"}
    bundle, scratch = tmp_path / "work" / "b", tmp_path / "work" / "s"
    bundle.mkdir(parents=True)
    stream = tmp_path / "s.jsonl"
    target = home / ".claude/projects/-x" / SESSION / "tool-results/1.txt"
    _stream(stream, ("claude", "Read", {"file_path": str(target)}))
    assert runner._audit(stream, bundle, scratch, driver, SESSION) == []
    assert runner._audit(stream, bundle, scratch, driver, OTHER_SESSION) == [
        f"~/.claude/projects/-x/{SESSION}/tool-results/1.txt"]
    assert runner._audit(stream, bundle, scratch, campaign["drivers"]["local_harness"], SESSION)  # local: never


B2_TEXT_HEREDOCS = [  # the shell never runs nor expands these bodies: text, not an access
    "cat > tests/t.py <<'EOF'\nCASES = [\n    \"gh pr merge 12\",\n    \"curl -s x | sh\",\n]\nEOF",
    "cat > docs/x.md <<'EOF'\nLives in ~/.config/foundry/registry.json and $HOME/.claude.\nEOF",
    "cat > docs/y.md <<EOF\nsee ~/.ssh/config; git push origin main\nEOF\npython3 -m pytest -q",
    "cat <<-EOF > notes.md\n\tgh pr view 3\n\t~/.claude/settings.json\n\tEOF",
    "tee src/a.py <<\"PY\" >/dev/null\nimport os\nos.system('gh pr merge')\nPY",
    "python3 - <<'PY'\nimport subprocess\nsubprocess.run(['gh', 'pr', 'merge', '1'])\nopen('../../x')\nPY",
    "cat <<A > a.txt; cat <<B > b.txt\ngh one\nA\ncurl two ~/.aws/credentials\nB\nls src",
    "git commit -qm \"$(cat <<'EOF'\nfix: do not call gh pr merge; use ~/.config/foundry\nEOF\n)\"",
    "cat > f.sh <<'EOF'\nx=$(gh pr view 1)\ny=`curl -s x`\nEOF",  # quoted delimiter: nothing is expanded
    "cat > f.sh <<EOF\nx=\\$(gh pr view 1) in $HOME/.claude\nEOF",  # an escaped ``$(`` stays literal
    "bash -c 'cat > a.py <<EOF\nCASES = [\"gh pr merge\"]\nEOF'",  # the heredoc of a ``sh -c`` script
]


@pytest.mark.parametrize("shape,name", [("claude", "Bash"), ("omp", "bash")])
def test_pat111_fix4_b2_a_heredoc_body_is_text_unless_a_shell_runs_it(tmp_path, shape, name):
    """B2 (reproduced): the bodies of a heredoc given to cat/tee/python are not audited as commands or
    paths; the line that holds the ``<<`` still is, and a body a SHELL runs is audited as a command line."""
    audit, home, bundle, _ = _audit_kw(tmp_path)

    def bash(command):
        return audit((shape, name, {"command": command}))

    for command in B2_TEXT_HEREDOCS:
        assert bash(command) == [], command
    # the line that holds the ``<<`` stays audited
    assert bash("cat > ~/.config/x <<EOF\nhello\nEOF") == ["~/.config/x"]
    assert bash("gh pr create --body-file - <<'EOF'\nbody\nEOF") == ["command:gh"]
    # a body a shell runs is a command line: commands AND paths
    for command in ("bash <<EOF\ngh pr view 3\nEOF", "sh -s <<'X'\necho ok\ngh pr view 3\nX",
                    "zsh <<-EOF\n\tgh pr view 3\n\tEOF", "cat <<EOF | bash\ngh pr view 3\nEOF",
                    "cat <<EOF |\ngh pr view 3\nEOF\nsudo sh", "source /dev/stdin <<EOF\ngh pr view 3\nEOF"):
        assert bash(command) == ["command:gh"], command
    assert bash("bash <<'EOF'\ncat ~/.ssh/id_ed25519\nEOF") == ["~/.ssh/id_ed25519"]
    # with an UNQUOTED delimiter the shell runs the body's command substitutions
    assert bash("cat > f.txt <<EOF\nsha: $(gh pr view 1 --json sha)\nEOF") == ["command:gh"]
    assert bash("cat > f.txt <<EOF\nnow `curl -s x`\nEOF") == ["command:curl"]
    assert bash("cat > f.txt <<EOF\n$(cat ~/.ssh/id_ed25519)\nEOF") == ["~/.ssh/id_ed25519"]
    # what follows the delimiter line is audited again; an unclosed heredoc is audited as it is
    assert bash("cat > a.txt <<EOF\ntext\nEOF\ncurl -s x") == ["command:curl"]
    assert bash("cat > a.txt <<EOF\ntext\ngh pr view") == ["command:gh"]
    # ``<<`` inside quotes or arithmetic is not a heredoc: what follows is still audited
    assert bash("echo $((1 << 2))\ngh pr view\n2))") == ["command:gh"]
    assert bash("echo 'a <<EOF'\ngh pr view\nEOF") == ["command:gh"]
    # a here-string (``<<<``) has no body: the next lines are commands
    assert bash("cat <<<EOF\ngh pr view\nEOF") == ["command:gh"]
    # the Sonnet division case stays clean
    assert bash("python3 - <<'PY'\nprint(sum([1, 2]) / len([1, 2]), 7 // 2)\nPY") == []


@pytest.mark.parametrize("shape,name", [("claude", "Bash"), ("omp", "bash")])
def test_pat111_fix4_same_class_quoted_separators_and_comments_are_text(tmp_path, shape, name):
    """Same class as B2, found in review: a ``|`` or ``;`` inside quotes (a grep pattern, a commit message)
    does not start a command, a ``#`` comment is not run; what a shell really runs is still caught."""
    audit, home, bundle, _ = _audit_kw(tmp_path)

    def bash(command):
        return audit((shape, name, {"command": command}))

    for command in ('grep -rnE "claude|codex|omp" plugins/foundry/tooling', "rg -n 'gh pr (create|merge)' .",
                    'git commit -qm "routing: drop curl; gh is denied"', "echo 'a; ssh host'",
                    "python3 -m pytest -q  # then gh pr view, see ~/.config/foundry",
                    "# cat ~/.ssh/id_ed25519\nls src", 'echo "${#x} $# a#b"'):
        assert bash(command) == [], command
    for command, expected in (
            ('bash -c "cd src && gh pr merge 3"', ["command:gh"]),
            ("sh -c 'curl x; wget y'", ["command:curl", "command:wget"]),
            ('echo "$(gh pr view 1)"', ["command:gh"]), ("echo \"`curl x`\"", ["command:curl"]),
            ('bash -c "cat ~/.ssh/id_ed25519"', ["~/.ssh/id_ed25519"]),
            ("sh -c 'cat ../../x'", [str(Path(os.path.realpath(tmp_path))) + "/x"]),
            ('echo "$(cat ~/.aws/credentials)"', ["~/.aws/credentials"]),
            ("ls src # ok\ngh pr view", ["command:gh"]), ("cat 2>~/.config/x", ["~/.config/x"]),
            ("if true; then curl x; fi", ["command:curl"]), ("{ gh pr view; }", ["command:gh"]),
            ("while read l; do gh $l; done < f", ["command:gh"]),
            # a path inside a quoted argument is still read piece by piece, as before
            ("python3 -c \"open('../../x').read()\"", [str(Path(os.path.realpath(tmp_path))) + "/x"])):
        assert bash(command) == expected, command


@pytest.mark.parametrize("shape,name,read,key", [("claude", "Bash", "Read", "file_path"),
                                                 ("omp", "bash", "read", "path")])
def test_pat111_fix4_n3_a_local_arm_s_tilde_is_its_isolated_home(tmp_path, shape, name, read, key):
    """N3: a local arm runs with HOME in its attempt directory; its ``~``/``$HOME`` is that one."""
    audit, home, bundle, scratch = _audit_kw(tmp_path)
    isolated = str(scratch / "home")
    for command in ("cat ~/.config/omp/config.yml", "ls $HOME/.cache", "cd && ls .config",
                    "find ~ -name '*.json'", 'cat "${HOME}/.local/state/x"'):
        assert audit((shape, name, {"command": command}), arm_home=isolated) == [], command
        assert audit((shape, name, {"command": command})) != [], command  # the real home: flagged
    assert audit((shape, read, {key: "~/.config/x"}), arm_home=isolated) == []
    # a literal absolute path under the real home stays a hit
    assert audit((shape, name, {"command": f"cat {home}/.ssh/id_ed25519"}), arm_home=isolated) == [
        "~/.ssh/id_ed25519"]
    assert audit((shape, read, {key: f"{home}/.config/foundry/config.json"}), arm_home=isolated) == [
        "~/.config/foundry/config.json"]


def test_pat111_fix4_n3_the_runner_expands_a_local_arm_s_tilde_to_its_isolated_home(tmp_path):
    home = _fake_home(tmp_path)
    runner, campaign, _, _ = make_runner(tmp_path, "compare", FIX_ALL, host_env={**os.environ, "HOME": str(home)})
    bundle, scratch = tmp_path / "work" / "b", tmp_path / "work" / "s"
    bundle.mkdir(parents=True)
    stream = tmp_path / "s.jsonl"
    _stream(stream, ("omp", "bash", {"command": "cat ~/.config/foundry/config.json"}))
    assert runner._audit(stream, bundle, scratch, campaign["drivers"]["local_harness"]) == []
    assert runner._audit(stream, bundle, scratch, campaign["drivers"]["neutral_harness"]) == []
    assert runner._audit(stream, bundle, scratch, campaign["drivers"]["cloud_implementer_current"], SESSION) == [
        "~/.config/foundry/config.json"]  # a cloud arm has the real HOME


@pytest.mark.parametrize("cut", ["after_audit", "after_patch"])
def test_pat111_fix4_n1_a_local_attempt_cut_after_its_audit_stays_contaminated_and_is_never_replayed(
        tmp_path, monkeypatch, sentinel_handlers, cut):
    home = _fake_home(tmp_path)
    first = _make_repo(tmp_path)
    env = {**os.environ, "HOME": str(home)}
    runner, campaign, plan, tasks = make_runner(tmp_path, "screen", _peek_plan(home, local=1),
                                                repo_bundle=first, host_env=env)
    if cut == "after_audit":
        _once(monkeypatch, runner, "_audit", signal.SIGTERM, after=True)
    else:
        _once(monkeypatch, runner, "_patch_of", signal.SIGTERM)
    with pytest.raises(SystemExit):
        runner.screen(tasks[:1], ["cand-a"])
    monkeypatch.undo()
    rec = [r for r in results(runner) if r["record_type"] == "attempt"]
    assert len(rec) == 1 and rec[0]["status"] == "interrupted" and rec[0]["judge"] is None
    assert rec[0]["outcome"] == "contaminated" and rec[0]["contaminated"] is True and rec[0]["accepted"] is None
    assert rec[0]["contamination"]["paths"] == ["~/.claude/plugins/cache/foundry/1.0.0/tests/test_linear_tracker.py"]
    again, _, _, tasks = make_runner(tmp_path, "screen", _peek_plan(home, local=1), repo_bundle=first, host_env=env)
    assert again.screen(tasks[:1], ["cand-a"]) == [] and counts(plan) == {"local": 1}  # never replayed


@pytest.mark.parametrize("cut", ["after_audit", "after_patch"])
def test_pat111_fix4_n1_a_cloud_round_cut_after_its_audit_stays_contaminated_and_is_never_replayed(
        tmp_path, monkeypatch, sentinel_handlers, cut):
    home = _fake_home(tmp_path)
    first = _make_repo(tmp_path)
    env = {**os.environ, "HOME": str(home)}
    runner, campaign, plan, tasks = make_runner(tmp_path, "compare", _peek_plan(home, implementer=1),
                                                repo_bundle=first, host_env=env)
    if cut == "after_audit":
        _once(monkeypatch, runner, "_audit", signal.SIGTERM, after=True)
    else:
        _once(monkeypatch, runner, "_patch_of", signal.SIGTERM)
    with pytest.raises(SystemExit):
        runner.compare(tasks[:1], "cand-a", ("A",))
    monkeypatch.undo()
    rec = [r for r in results(runner) if r["record_type"] == "attempt"]
    assert len(rec) == 1 and rec[0]["status"] == "interrupted" and rec[0]["judge"] is None
    assert rec[0]["outcome"] == "contaminated" and rec[0]["contaminated"] is True
    assert rec[0]["contamination"]["paths"] == ["~/.claude/plugins/cache/foundry/1.0.0/tests/test_linear_tracker.py"]
    again, _, _, tasks = make_runner(tmp_path, "compare", _peek_plan(home, implementer=1), repo_bundle=first,
                                     host_env=env)
    assert again.compare(tasks[:1], "cand-a", ("A",)) == [] and counts(plan) == {"implementer": 1}


def test_pat111_fix4_n1_a_review_of_path_c_cut_after_its_audit_leaves_the_local_record_contaminated(
        tmp_path, monkeypatch, sentinel_handlers):
    home = _fake_home(tmp_path)
    env = {**os.environ, "HOME": str(home)}
    runner, campaign, plan, tasks = make_runner(tmp_path, "compare", _peek_plan(home, reviewer=1), host_env=env)
    _nth(monkeypatch, runner, "_audit", 2, signal.SIGTERM, after=True)  # 1: the local arm, 2: its review
    with pytest.raises(SystemExit):
        runner.compare(tasks[:1], "cand-a", ("C",))
    monkeypatch.undo()
    rec = [r for r in results(runner) if r["record_type"] == "attempt"][0]
    assert rec["segment"] == "local" and rec["status"] == "interrupted" and rec["outcome"] == "contaminated"
    assert rec["contaminated"] is True and rec["accepted"] is None and rec["judge"]["verdict"] == "ACCEPTED"


def test_pat111_fix4_n1_a_settled_contaminated_local_attempt_flushed_by_a_cut_keeps_its_outcome(
        tmp_path, monkeypatch, sentinel_handlers):
    home = _fake_home(tmp_path)
    runner, campaign, plan, tasks = make_runner(tmp_path, "screen", _peek_plan(home, local=1),
                                                host_env={**os.environ, "HOME": str(home)})
    _once(monkeypatch, runner, "_emit", signal.SIGTERM)  # the settled record is not written yet
    with pytest.raises(SystemExit):
        runner.screen(tasks[:1], ["cand-a"])
    monkeypatch.undo()
    rec = [r for r in results(runner) if r["record_type"] == "attempt"][0]
    assert rec["status"] == "interrupted" and rec["outcome"] == "contaminated" and rec["contaminated"] is True


# ---- N4: a bundle file of the base that records a real-home path (PR 83: ``/Users/<u>/.codex/...``)

def _literal_repo(tmp_path):
    home = _fake_home(tmp_path)
    literal = f"{home}/.codex/worktrees/pat68-resume/patolabs-plugins"
    first = _make_repo(tmp_path, base_files={
        "plugins/foundry/docs/obs.json": f'{{\n  "root": "{literal}",\n  "n": 1\n}}\n'})
    return home, literal, first, {**os.environ, "HOME": str(home)}


def test_pat111_fix4_n4_base_literals_are_read_from_the_bundle_before_the_arm(tmp_path):
    home, literal, first, env = _literal_repo(tmp_path)
    runner, _, _, tasks = make_runner(tmp_path, "screen", FIX_ALL, repo_bundle=first, host_env=env)
    bundle, attempt_dir = runner._bundle(tasks[0], "probe")
    try:
        assert runner.literals[bundle] == frozenset({literal})
        assert lfr.base_literals(bundle, str(home)) == frozenset({literal})
        (bundle / "planted.md").write_text(f"{home}/.ssh/id_ed25519\n")  # what the arm writes later
        assert runner.literals[bundle] == frozenset({literal})  # computed once, before the arm
    finally:
        runner._discard(bundle, attempt_dir)
    assert bundle not in runner.literals
    # a correction round: the previous arm's patch is applied AFTER the scan, so it cannot add literals
    planted = f"{home}/.ssh/id_ed25519"
    patch = (f"diff --git a/planted.md b/planted.md\nnew file mode 100644\n--- /dev/null\n+++ b/planted.md\n"
             f"@@ -0,0 +1 @@\n+{planted}\n").encode()
    bundle, attempt_dir = runner._bundle(tasks[0], "probe2", patch)
    try:
        assert (bundle / "planted.md").read_text().strip() == planted
        assert runner.literals[bundle] == frozenset({literal})
    finally:
        runner._discard(bundle, attempt_dir)


@pytest.mark.parametrize("segment", ["local", "implementer"])
def test_pat111_fix4_n4_a_bundle_file_that_names_a_home_path_is_text_in_a_result_not_in_a_call(
        tmp_path, segment):
    home, literal, first, env = _literal_repo(tmp_path)
    cases = {"show:plugins/foundry/docs/obs.json": [],  # the arm reads the bundle file: clean
             f"say:see {literal}/x": ["tool_result:~/.codex/worktrees/pat68-resume/patolabs-plugins/x"],
             f"cmd:cat {literal}": ["~/.codex/worktrees/pat68-resume/patolabs-plugins"],
             f"peek:{literal}": ["~/.codex/worktrees/pat68-resume/patolabs-plugins"]}
    for n, (behavior, expected) in enumerate(cases.items()):
        root = _sub(tmp_path, f"r{n}")
        runner, _, _, tasks = make_runner(root, "compare", {**FIX_ALL, segment: [behavior]},
                                          repo_bundle=first, host_env=env)
        if segment == "local":
            rec = runner.hybrid_path(tasks[0], "cand-a", "local_harness", "comparison")[0]
        else:
            rec = runner.cloud_path(tasks[0], "A", "comparison")[0]
        found = rec.get("contamination", {}).get("paths", [])
        assert found == expected, behavior
        assert (rec["outcome"] == "contaminated") is bool(expected), behavior


@pytest.mark.parametrize("kind,tool,key", [("omp", "bash", "command"), ("claude", "Bash", "command")])
def test_pat111_fix5_a_read_the_local_sandbox_refused_is_blocked_not_a_contamination(tmp_path, kind, tool, key):
    """Under the deny-home profile a local arm's read of the real home (outside the allow list) or of
    an explicitly denied path fails (EPERM): a blocked attempt, nothing reached the arm. One honest
    ``ls ~/.config`` must not make the whole screening incomplete. Readable places stay audited."""
    audit, home, bundle, _ = _audit_kw(tmp_path)
    venv, state = home / ".venvs" / "mini", tmp_path / "state"
    walled = (str(home), [str(venv)], [str(state)])
    cache = home / ".claude/plugins/cache/foundry/1.0.0/tests/test_linear_tracker.py"
    denial = {"type": "tool_execution_end", "result": f"cat: {cache}: Operation not permitted"}
    read = (kind, tool, {key: f"cat {cache}"})
    assert audit(read, raw=[denial], sandbox_denied=walled) == []
    assert audit(read, raw=[denial]) != []  # without the sandbox (a cloud arm) it is an access
    assert audit((kind, tool, {key: f"ls {state}"}), sandbox_denied=walled) == []
    assert audit((kind, tool, {key: "gh pr view 3"}), sandbox_denied=walled) == ["command:gh"]
    assert audit((kind, tool, {key: f"cat {venv}/../../.ssh/id_ed25519"}), sandbox_denied=walled) == []
    assert audit((kind, tool, {key: f"cat {venv}/lib/site.py"}), sandbox_denied=walled) != []  # readable


def test_pat111_fix4_n4_audit_literals_cover_results_only(tmp_path):
    audit, home, bundle, _ = _audit_kw(tmp_path)
    literal = f"{home}/.codex/worktrees/x"
    lit = frozenset({literal})
    result = {"type": "user", "message": {"content": [{"type": "tool_result", "content": f'"root": "{literal}",'}]}}
    assert audit(raw=[result], literals=lit) == []
    assert audit(raw=[result]) == ["tool_result:~/.codex/worktrees/x"]
    other = {"type": "user", "message": {"content": [{"type": "tool_result", "content": f"{literal}/sub"}]}}
    assert audit(raw=[other], literals=lit) == ["tool_result:~/.codex/worktrees/x/sub"]
    assert audit(("claude", "Bash", {"command": f"cat {literal}"}), literals=lit) == ["~/.codex/worktrees/x"]
    assert audit(("claude", "Read", {"file_path": literal}), literals=lit) == ["~/.codex/worktrees/x"]
