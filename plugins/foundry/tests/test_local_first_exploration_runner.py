"""PAT-114: deterministic tests of the protocol v2 (read-only exploration) modes of the PAT-19 launcher.

Only FAKE arms run (small Python scripts in temp dirs): the fake explorers write or print a report, the
fake implementers and reviewers are the v1 ones. No model, no cloud, no network; machine facts injected."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from foundry import local_first_corpus as lfc
from foundry import local_first_exploration as lfe
from foundry import local_first_runner as lfr
from test_local_first_corpus import MOD, _make_repo
from test_local_first_runner import (FAKE_ARM, TODAY, _arm, counts, ledger_of, results, write_envelope)

QUALIFICATION = Path(__file__).resolve().parents[1] / "docs" / "qualification"
V2 = json.loads((QUALIFICATION / "pat-19-campaign-v2.json").read_text("utf-8"))
GOOD_REPORT = {"files": [MOD], "functions": [{"file": MOD, "name": "add"}], "rationale": "add is wrong"}
WRONG_REPORT = {"files": ["plugins/foundry/tooling/foundry/elsewhere.py"], "functions": [], "rationale": "?"}
SWAP = "total = 2048.00M  used = 100.00M  free = 1948.00M"

FAKE_V2 = r'''
import argparse, json, os, sys, time
ap = argparse.ArgumentParser()
ap.add_argument("--role"); ap.add_argument("--workdir"); ap.add_argument("--plan")
ap.add_argument("--scratch", default=""); ap.add_argument("--statement", default="")
ap.add_argument("--session-id", default=""); ap.add_argument("--projects-dir", default="")
ap.add_argument("--init-tools", default=None)
a, _ = ap.parse_known_args()
with open(a.plan + ".texts", "a") as handle:  # what every arm was told
    handle.write(json.dumps({"role": a.role, "text": open(a.statement).read(),
                             "workdir_writable": os.access(a.workdir, os.W_OK)}) + "\n")
if a.role not in ("xlocal", "xcloud"):
    exec(compile(open(V1_ARM).read(), V1_ARM, "exec"))
    sys.exit(0)
counter = a.plan + ".count"
counts = json.load(open(counter)) if os.path.exists(counter) else {}
i = counts.get(a.role, 0)
counts[a.role] = i + 1
json.dump(counts, open(counter, "w"))
steps = json.load(open(a.plan))[a.role]
step = steps[min(i, len(steps) - 1)]
mode, report = step["mode"], step.get("report")
def event(kind, **kw):
    print(json.dumps({"type": kind, **kw}), flush=True)
text = json.dumps(report) if mode in ("final", "file", "draft_then_hang", "draft_then_steps") else "I could not find anything."
if a.init_tools is not None:
    event("system", subtype="init", tools=[t for t in a.init_tools.split(",") if t])
if mode == "peek":
    event("tool_execution_start", toolName="read", args={"path": step["target"]})
    event("assistant", message={"content": [{"type": "tool_use", "name": "Read",
                                             "input": {"file_path": step["target"]}}]})
if mode == "write_bundle":  # a read-only role that changes the bundle (the sandbox is off in these tests)
    open(os.path.join(a.workdir, "stray.txt"), "w").write("x")
    mode = "final"
if mode in ("draft_then_hang", "draft_then_steps"):  # a usable draft appears, then a bound cuts the arm
    if a.role == "xlocal":
        event("message_end", message={"role": "assistant", "content": [{"type": "text", "text": text}]})
    else:
        event("result", result=text)
    if mode == "draft_then_steps":
        for _ in range(200):
            event("tool_execution_start", tool="read")
    time.sleep(60)
if mode == "hang":
    time.sleep(60)
if mode == "many_steps":
    for _ in range(200):
        event("tool_execution_start", tool="read")
    time.sleep(60)
if mode == "file":
    open(os.path.join(a.scratch, "report.json"), "w").write(text)
if mode == "garbage_file":
    open(os.path.join(a.scratch, "report.json"), "w").write("{not json")
if mode == "crash":
    sys.exit(3)
if a.role == "xlocal":
    for _ in range(3):
        event("tool_execution_start", tool="read")
    event("message_end", message={"role": "assistant", "content": [{"type": "text", "text": text}],
                                  "usage": {"input": 1000, "output": 50, "reasoningTokens": 7}})
else:
    folder = os.path.join(a.projects_dir, "proj")
    os.makedirs(folder, exist_ok=True)
    line = {"type": "assistant", "timestamp": "2026-10-05T10:00:00Z", "sessionId": a.session_id,
            "message": {"model": "claude-haiku-4-5-20251001", "id": "m1", "stop_reason": "end_turn",
                        "usage": {"input_tokens": 20, "cache_read_input_tokens": 0,
                                  "cache_creation_input_tokens": 0, "output_tokens": 5}},
            "requestId": "r1"}
    with open(os.path.join(folder, a.session_id + ".jsonl"), "w") as out:
        out.write(json.dumps(line) + "\n")
    event("result", result=text)
'''

FINAL = {"mode": "final", "report": GOOD_REPORT}
PLAN = {"xlocal": [FINAL], "xcloud": [FINAL], "local": ["fix"], "neutral": ["fix"], "implementer": ["fix"],
        "economy": ["fix"], "reviewer": ["PASS"]}


def v2_campaign(tmp_path, plan, frozen=("cand-a",), **overrides):
    v1_arm = tmp_path / "v1_arm.py"
    v1_arm.write_text(FAKE_ARM, encoding="utf-8")
    script = tmp_path / "fake_v2.py"
    script.write_text(f"V1_ARM = {str(v1_arm)!r}\n" + FAKE_V2, encoding="utf-8")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    projects = tmp_path / "claude-projects"
    base = json.loads(json.dumps(V2))
    base["bounds"].update({"local_max_seconds": 20, "cloud_max_seconds": 60, "explorer_max_seconds": 20,
                           "explorer_max_steps": 25})
    base["rules"]["exploration_screening"]["tasks"] = 2
    base["rules"]["exploration_comparison"]["tasks"] = 2
    base["rules"]["exploration_screening"]["candidates"] = list(frozen)  # the frozen candidates of the fake run
    impl = _arm("implementer", script, plan_path, projects)
    review = _arm("reviewer", script, plan_path, projects)
    local = _arm("local", script, plan_path, projects)
    local.update(kind="local_explorer", stream={"format": "omp-json"})
    local["argv"] = [local["argv"][0], str(script), "--role", "xlocal", *local["argv"][4:],
                     "--scratch", "{scratch}", "--tools=read,grep,glob"]
    cloud = _arm("implementer", script, plan_path, projects)
    cloud.update(kind="cloud_explorer", stream={"format": "claude-stream-json"},
                 allowed_tools=["Bash", "Glob", "Grep", "Read"])
    cloud["argv"] = [cloud["argv"][0], str(script), "--role", "xcloud", *cloud["argv"][4:],
                     "--scratch", "{scratch}", "--init-tools", "Read,Bash",
                     "--disallowedTools", "Edit", "Write"]
    for driver in (impl, review):  # the cloud implementer and reviewer run the v1 fake arm
        driver["argv"][1] = str(script)
    base["drivers"] = {"local_explorer": local, "cloud_implementer_current": impl,
                       "cloud_explorer_economy": cloud, "cloud_reviewer": review}
    base["candidates"] = {"cand-a": {"model": "fake/model-a"}, "cand-b": {"model": "fake/model-b"}}
    for key, value in overrides.items():
        base[key].update(value)
    return base, plan_path


def facts_with(campaign, model, ps=None, pressure=None):
    canned = lfr.dry_run_facts(campaign, model)

    def run(argv):
        if tuple(argv) == ("ps", "-axo", "rss=,command=") and ps is not None:
            return ps
        if tuple(argv) == ("memory_pressure",) and pressure is not None:
            return pressure
        return canned(argv)
    return run


def make_runner(tmp_path, mode, plan=None, *, repo_bundle=None, caps=None, campaign_over=None, ps=None,
                pressure=None, campaign_id="test-v2", require_screening=False, swap=True, frozen=("cand-a",)):
    repo, snap, _, _ = repo_bundle or _make_repo(tmp_path)
    campaign, plan_path = v2_campaign(tmp_path, plan or PLAN, frozen=frozen, **(campaign_over or {}))
    envelope = lfr.load_envelope(_envelope(tmp_path, lfr.EXPLORE_MODES, campaign_id, **(caps or {})), mode, TODAY)
    runner = lfr.Runner(
        repo=repo, campaign=campaign, envelope=envelope, state_dir=tmp_path / "state",
        work_root=tmp_path / "work", mode=mode, dry_run=True, sandbox=False,
        run=(lambda argv: SWAP if swap and tuple(argv) == ("sysctl", "-n", "vm.swapusage") else None),
        preflight_run=lambda cid: facts_with(campaign, campaign["candidates"][cid]["model"], ps, pressure),
        disk_free_gib=lambda: 500.0, host_env=dict(os.environ), today=lambda: TODAY,
        require_screening=require_screening)
    runner.quiescent_wait = 0.05
    task = snap["prs"][0]
    return runner, campaign, plan_path, [task, {**task, "pr": 2, "issue": "PAT-2"}]


def _envelope(tmp_path, modes, campaign_id, **caps):
    """One envelope per campaign id, naming the v2 modes (a screening and its comparison share it)."""
    path = write_envelope(tmp_path, campaign_id, **caps)
    body = json.loads(path.read_text("utf-8"))
    body["allowed_modes"] = [modes] if isinstance(modes, str) else list(modes)
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def texts(plan_path):
    path = Path(str(plan_path) + ".texts")
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()] if path.exists() else []


# ----------------------------------------------------------------- configuration and modes

def test_the_v2_campaign_loads_and_pins_the_validated_values():
    campaign = lfr.load_campaign(QUALIFICATION / "pat-19-campaign-v2.json")
    assert campaign["bounds"]["explorer_max_seconds"] == 600 and campaign["bounds"]["explorer_max_steps"] == 25
    local, cloud = campaign["drivers"]["local_explorer"], campaign["drivers"]["cloud_explorer_economy"]
    assert "--tools=read,grep,glob" in local["argv"] and local["kind"] == "local_explorer"
    assert cloud["model"] == "claude-haiku-4-5-20251001" and {"Edit", "Write"} <= set(cloud["argv"])
    # trial-run for real on 2026-10-06: verified, each with a reference into the evidence file
    evidence = json.loads((QUALIFICATION / "pat-19-preflight-v2-2026-10-06.json").read_text("utf-8"))
    for name in ("local_explorer", "cloud_explorer_economy"):
        driver = campaign["drivers"][name]
        assert driver["verified"] is True and driver["evidence"] == (
            f"pat-19-preflight-v2-2026-10-06.json#drivers.{name}")
        assert evidence["drivers"][name]["passed"] is True
        lfr._check_driver_usable(name, driver, dry_run=False)  # a real run accepts them
    assert evidence["drivers"]["local_explorer"]["rejected_argv"]["tools_flag"] == "--tools=read,grep,find,ls"
    assert "--tools=read,grep,glob" in local["argv"] and "--tools=read,grep,find,ls" not in local["argv"]
    assert evidence["drivers"]["cloud_explorer_economy"]["result"]["init_tools"] == ["Bash", "Read"]
    assert evidence["drivers"]["cloud_explorer_economy"]["limit"].startswith("read-only behaviour is not enforced")
    # the five v1 candidates are unchanged (same keys, digests, load commands, context); Devstral stays unused
    v1 = json.loads((QUALIFICATION / "pat-19-campaign-v1.json").read_text("utf-8"))
    assert campaign["candidates"] == v1["candidates"] and campaign["candidates"]["devstral-small-2-24b-gguf"]["unused"]
    used = {k: c for k, c in campaign["candidates"].items() if not c.get("unused")}
    assert len(used) == 5 and all(c["min_context"] == 65536 for c in used.values())
    assert campaign["frozen_machine"] == v1["frozen_machine"] and campaign["cloud_bash_deny"] == v1["cloud_bash_deny"]
    # the v1 implementer and reviewer drivers are inherited as they are
    for name in ("cloud_implementer_current", "cloud_reviewer"):
        assert campaign["drivers"][name] == v1["drivers"][name]


def test_the_loader_refuses_a_v2_campaign_that_loses_its_read_only_pins(tmp_path):
    spec = V2["exploration"]["ground_truth"]
    (tmp_path / spec["file"]).write_bytes((QUALIFICATION / spec["file"]).read_bytes())  # beside the config

    def broken(edit):
        data = json.loads(json.dumps(V2))
        edit(data)
        path = tmp_path / "broken.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def argv_edit(edit):
        return lambda d: edit(d["drivers"]["local_explorer"]["argv"])

    def tools(value):
        def edit(d):
            argv = d["drivers"]["local_explorer"]["argv"]
            argv[argv.index("--tools=read,grep,glob")] = value
        return edit

    for edit, message in (
            (tools("--tools=read,grep,find,ls"), "--tools="),  # names omp refused in the trial
            (tools("--tools=read,grep,glob,write"), "--tools="),
            (tools("--tools=read,bash"), "--tools="),
            (lambda d: d["drivers"]["local_explorer"]["argv"].remove("--tools=read,grep,glob"), "--tools="),
            # the flag given as two arguments, or a second tools flag, is refused as well
            (argv_edit(lambda a: a.__setitem__(slice(a.index("--tools=read,grep,glob"),
                                                      a.index("--tools=read,grep,glob") + 1),
                                               ["--tools", "read,bash"])), "exactly one --tools="),
            (argv_edit(lambda a: a.extend(["--tools", "read,bash"])), "exactly one --tools="),
            (argv_edit(lambda a: a.append("--tools=read")), "exactly one --tools="),
            (lambda d: d["exploration"]["ground_truth"].update(sha256="0" * 64), "sha256"),
            (lambda d: d["exploration"].pop("ground_truth"), "ground_truth"),
            (lambda d: d["exploration"]["report_render_limits"].update(max_functions=40), "max_functions"),
            (lambda d: d["rules"]["exploration_screening"].update(candidates=["nope"]), "frozen candidates"),
            (lambda d: d["rules"]["exploration_screening"].pop("candidates"), "frozen candidates"),
            (lambda d: d["drivers"]["cloud_explorer_economy"]["argv"].remove("Write"), "deny Edit and Write"),
            (lambda d: d.pop("dedicated_machine"), "dedicated_machine"),
            (lambda d: d["dedicated_machine"].update(allowed_command_patterns={"x": ["("]}), "not a regex"),
            (lambda d: d["bounds"].pop("explorer_max_steps"), "explorer_max_steps"),
            (lambda d: d["prompts"].pop("explore"), "prompts.explore"),
            (lambda d: d["rules"].pop("exploration_screening"), "exploration_screening"),
            (lambda d: d["exploration"].pop("report_render_limits"), "report_render_limits")):
        with pytest.raises(lfr.RunnerError, match=message):
            lfr.load_campaign(broken(edit))
    lfr.load_campaign(broken(lambda d: None))  # the untouched copy loads


def test_v2_modes_need_a_v2_campaign_and_v1_modes_a_v1_campaign(tmp_path):
    with pytest.raises(lfr.RunnerError, match="does not match the campaign schema"):
        lfr.Runner(repo=tmp_path, campaign={"schema": lfr.CAMPAIGN_SCHEMA}, envelope={"sha256": "x"},
                   state_dir=tmp_path, work_root=tmp_path / "w", mode="screen_exploration", dry_run=True,
                   sandbox=False)
    with pytest.raises(lfr.RunnerError, match="does not match the campaign schema"):
        lfr.Runner(repo=tmp_path, campaign={"schema": lfr.CAMPAIGN_SCHEMA_V2}, envelope={"sha256": "x"},
                   state_dir=tmp_path, work_root=tmp_path / "w", mode="screen", dry_run=True, sandbox=False)


def test_the_envelope_names_the_v2_modes(tmp_path):
    path = _envelope(tmp_path, "screen_exploration", "e1")  # one mode only
    assert lfr.load_envelope(path, "screen_exploration", TODAY)["campaign_id"] == "e1"
    with pytest.raises(lfr.EnvelopeError, match="does not allow mode"):
        lfr.load_envelope(path, "compare_exploration", TODAY)
    with pytest.raises(lfr.EnvelopeError, match="does not allow mode"):
        lfr.load_envelope(write_envelope(tmp_path, "e2"), "screen_exploration", TODAY)  # a v1 envelope


# -------------------------------------------------------------------- screening (no cloud)

def test_screen_exploration_scores_localization_and_spends_no_cloud(tmp_path):
    plan = {**PLAN, "xlocal": [FINAL, {"mode": "file", "report": WRONG_REPORT}, FINAL, FINAL]}
    runner, campaign, plan_path, tasks = make_runner(tmp_path, "screen_exploration", plan,
                                                     caps={"cloud_executions": 0}, frozen=("cand-a", "cand-b"))
    spent = []
    runner.cloud_execution = lambda *a, **k: spent.append(a)
    out = runner.screen_exploration(tasks, ["cand-a", "cand-b"])
    assert not spent and counts(plan_path) == {"xlocal": 4} and lfr.Ledger.totals(runner.ledger)["cloud_started"] == 0
    scores = [(r["local"]["candidate"], r["task"]["pr"], r["judge"]["verdict"], r["judge"]["file_recall"],
               r["judge"]["file_precision"], r["judge"]["function_recall"]) for r in out]
    assert scores == [("cand-a", 1, "SCORED", 1.0, 1.0, 1.0), ("cand-a", 2, "SCORED", 0.0, 0.0, 0.0),
                      ("cand-b", 1, "SCORED", 1.0, 1.0, 1.0), ("cand-b", 2, "SCORED", 1.0, 1.0, 1.0)]
    first = out[0]
    assert first["path"] == "XS" and first["task"]["set"] == "screening" and first["accepted"] is None
    assert first["cloud_executions"] == 0 and first["premium"]["billing_total"] == 0
    assert first["exploration"]["report_source"] == "final_message" and out[1]["exploration"]["report_source"] == "file"
    assert first["local"]["harness"] == "local_explorer" and first["local"]["steps"] == 3
    assert first["machine"]["dedicated"]["free_percent"] == 80  # the observed values are on the record
    assert first["local"]["max_steps"] == 25 and first["local"]["nominal_max_seconds"] == 20
    assert not list((tmp_path / "work").iterdir())
    rep = lfr.report(campaign, results(runner), ledger_of(runner))
    table = rep["exploration_screening"]
    assert rep["promotion"] is False and "screening" not in rep and "comparison" not in rep
    assert table["selected"] == "cand-b" and table["complete"] and table["reason"] == "highest_mean_function_recall"
    assert table["candidates"]["cand-a"]["mean_file_recall"] == 0.5 and table["candidates"]["cand-b"]["mean_function_recall"] == 1.0
    assert table["candidates"]["cand-a"]["per_task"][1]["verdict"] == "SCORED"
    assert set(table["candidates"]["cand-b"]) >= {"mean_file_precision", "total_seconds", "peak_swap_used_mib"}


def test_screen_exploration_stops_on_keep_cloud_below_the_recall_threshold(tmp_path):
    plan = {**PLAN, "xlocal": [{"mode": "final", "report": WRONG_REPORT}]}
    runner, campaign, _, tasks = make_runner(tmp_path, "screen_exploration", plan, caps={"cloud_executions": 0})
    runner.screen_exploration(tasks, ["cand-a"])
    table = lfr.report(campaign, results(runner), ledger_of(runner))["exploration_screening"]
    assert table["selected"] is None and table["stop"] == "keep_cloud"
    assert table["reason"] == "no_candidate_meets_precision_floor: keep_cloud"


def _resumed(tmp_path, runner, tasks, mode, plan, **kw):
    """A second launcher on the same state (same repository, campaign id, envelope and configuration)."""
    return make_runner(tmp_path, mode, plan, repo_bundle=(runner.repo, {"prs": tasks[:1]}, None, None), **kw)


def test_a_missing_or_unparsable_report_is_a_refusal_scored_zero_and_never_replayed(tmp_path):
    steps = [{"mode": "none"}, {"mode": "garbage_file"}, {"mode": "final", "report": {"files": "x"}},
             {"mode": "crash"}]
    runner, campaign, plan_path, tasks = make_runner(tmp_path, "screen_exploration", {**PLAN, "xlocal": steps},
                                                     caps={"cloud_executions": 0})
    tasks = [*tasks, {**tasks[0], "pr": 3}, {**tasks[0], "pr": 4}]
    out = runner.screen_exploration(tasks, ["cand-a"])
    assert [r["judge"]["verdict"] for r in out] == ["REFUSED"] * 4 and counts(plan_path) == {"xlocal": 4}
    assert all(r["judge"]["file_recall"] == 0.0 and r.get("outcome") is None for r in out)
    assert "final message" in out[0]["exploration"]["refusal"]
    assert "report.json unusable" in out[1]["exploration"]["refusal"] and out[2]["exploration"]["report"] is None
    assert out[0]["unknown"]["exploration.report"] == out[0]["judge"]["note"]
    # a relaunch under the same campaign never replays them: decided, whatever the plan would now say
    rerun, _, _, _ = _resumed(tmp_path, runner, tasks, "screen_exploration", {**PLAN, "xlocal": [FINAL]},
                              caps={"cloud_executions": 0})
    assert rerun.screen_exploration(tasks, ["cand-a"]) == [] and counts(plan_path) == {"xlocal": 4}


def _sub(tmp_path, name):
    (tmp_path / name).mkdir()
    return tmp_path / name


def test_a_contaminated_exploration_counts_as_zero_and_is_decided(tmp_path):
    peek = {"mode": "peek", "target": os.path.expanduser("~/.claude/plugins/cache/x/y.py"), "report": GOOD_REPORT}
    runner, campaign, plan_path, tasks = make_runner(
        tmp_path, "screen_exploration", {**PLAN, "xlocal": [peek, FINAL]}, caps={"cloud_executions": 0})
    # the fake arm reports "final" only through the stream; the peek mode prints no message, so give it one
    out = runner.screen_exploration(tasks, ["cand-a"])
    assert out[0]["contaminated"] is True and out[0]["outcome"] == "contaminated"
    assert out[0]["judge"]["verdict"] == "CONTAMINATED" and out[0]["judge"]["file_recall"] == 0.0
    assert out[0]["exploration"]["report"] is None and out[1]["judge"]["verdict"] == "SCORED"
    table = lfr.report(campaign, results(runner), ledger_of(runner))["exploration_screening"]
    assert table["complete"] and table["undecided_tasks"] == []  # one flag never blocks the screening
    row = table["candidates"]["cand-a"]
    assert row["contaminated"] == 1 and row["mean_function_recall"] == 0.5 and row["mean_file_recall"] == 0.5
    assert table["selected"] == "cand-a" and table["stop"] is None  # 0.5 is inclusive: retained
    assert lfr.report(campaign, results(runner), ledger_of(runner))["contaminated"][0]["path"] == "XS"
    assert counts(plan_path) == {"xlocal": 2}


def test_an_exploration_cut_by_a_bound_is_a_refusal_even_with_a_draft(tmp_path):
    steps = [{"mode": "hang"}, {"mode": "many_steps"}, {"mode": "draft_then_hang", "report": GOOD_REPORT},
             {"mode": "draft_then_steps", "report": GOOD_REPORT}]
    runner, _, plan, tasks = make_runner(
        tmp_path, "screen_exploration", {**PLAN, "xlocal": steps},
        campaign_over={"bounds": {"explorer_max_seconds": 2}}, caps={"cloud_executions": 0})
    tasks = [*tasks, {**tasks[0], "pr": 3}, {**tasks[0], "pr": 4}]
    out = runner.screen_exploration(tasks, ["cand-a"])
    assert out[0]["local"]["timed_out"] and out[1]["local"]["step_limit_hit"]
    assert out[1]["local"]["steps"] > 25  # the kill can lag the 26th step
    assert out[2]["local"]["timed_out"] and out[3]["local"]["step_limit_hit"]
    for record in out:  # a JSON draft in a message does not save an exploration cut by a bound
        assert record["judge"]["verdict"] == "REFUSED" and record["judge"]["note"] == lfr.BOUND_HIT
        assert record["judge"]["file_recall"] == 0.0 and record["exploration"]["report"] is None
        assert record["local"]["ended_by_external_signal"] is False and record.get("outcome") is None
    rerun, _, _, _ = _resumed(tmp_path, runner, tasks, "screen_exploration", {**PLAN, "xlocal": steps},
                              campaign_over={"bounds": {"explorer_max_seconds": 2}}, caps={"cloud_executions": 0})
    assert rerun.screen_exploration(tasks, ["cand-a"]) == []  # decided: never replayed


def test_a_cloud_explorer_cut_by_the_time_bound_is_refused_and_its_cost_stays_unknown(tmp_path):
    plan = {**PLAN, "xcloud": [{"mode": "draft_then_hang", "report": GOOD_REPORT}], "reviewer": ["PASS"]}
    runner, _, plan_path, tasks = make_runner(tmp_path, "compare_exploration", plan,
                                              campaign_over={"bounds": {"explorer_max_seconds": 2}})
    out = runner.compare_exploration(tasks[:1], "cand-a", arms=("E",))
    explore = [r for r in out if r["segment"] == "explore"][0]
    assert explore["exploration"]["refusal"] == lfr.BOUND_HIT and explore["exploration"]["report"] is None
    assert explore["exploration"]["score"]["verdict"] == "REFUSED"
    # the arm never reached the log: its tokens are unknown, so the campaign stops (v1 cap rule), never zero
    assert explore["premium"]["billing_total"] is None
    assert results(runner)[-1]["reason"] == "cap_reached:premium_tokens_unmeasurable"


def test_an_exploration_the_envelope_would_cut_short_is_not_started_and_not_scored(tmp_path):
    # explorer bound 20 s, 5 s of wall clock left: a cap stop, not a model refusal
    runner, _, plan, tasks = make_runner(tmp_path, "screen_exploration", caps={"cloud_executions": 0,
                                                                              "wall_clock_seconds": 5})
    assert runner.screen_exploration(tasks, ["cand-a"]) == []
    assert results(runner)[-1]["reason"] == "cap_reached:wall_clock_seconds"
    assert [r for r in results(runner) if r.get("record_type") == "attempt"] == [] and counts(plan) == {}
    assert "attempt_started" not in [e["kind"] for e in ledger_of(runner)]
    cloud, _, plan2, tasks2 = make_runner(_sub(tmp_path, "cloud"), "compare_exploration",
                                          {**PLAN, "reviewer": ["PASS"]}, caps={"wall_clock_seconds": 5})
    assert cloud.compare_exploration(tasks2[:1], "cand-a", arms=("E",)) == []
    assert results(cloud)[-1]["reason"] == "cap_reached:wall_clock_seconds" and counts(plan2) == {}


def test_the_prompt_announces_the_bounds_and_the_function_cap(tmp_path):
    prompt = V2["prompts"]["explore"]
    assert "{max_duration}" in prompt and "{max_steps}" in prompt and "BEFORE reaching them" in prompt
    assert "where the harness counts them" in prompt  # the cloud explorer is not step-bounded: truthful for both
    assert "at most 10 functions, most relevant first" in prompt
    runner, _, _, _ = make_runner(tmp_path, "screen_exploration")
    values = runner._values(tmp_path / "bundle", tmp_path / "attempt")
    assert (values["max_duration"], values["max_steps"]) == ("20s", "25")  # the explorer's bounds, not the local ones
    rendered = lfr._substitute(prompt, values)
    assert "{max_" not in rendered and "25 tool steps" in rendered


def test_the_explorer_prompt_carries_the_report_format_and_the_read_only_instruction(tmp_path):
    runner, campaign, plan, tasks = make_runner(tmp_path, "screen_exploration", caps={"cloud_executions": 0})
    runner.screen_exploration(tasks[:1], ["cand-a"])
    prompt = campaign["prompts"]["explore"]
    assert "READ-ONLY" in prompt and '"files"' in prompt and '"functions"' in prompt and '"rationale"' in prompt
    assert all(t["role"] == "xlocal" for t in texts(plan))


# ----------------------------------------------------------- the read-only bundle (profile)

def test_the_explorer_bundle_is_read_only_in_the_sandbox_profile(tmp_path, monkeypatch):
    runner, _, _, tasks = make_runner(tmp_path, "screen_exploration", caps={"cloud_executions": 0})
    seen = []

    def spy(driver, writable, deny_read, deny_home=None, allow_read=()):
        seen.append({"writable": [Path(p).resolve() for p in writable], "allow_read": [Path(p).resolve() for p in allow_read]})
        return "(version 1)\n"

    monkeypatch.setattr(lfr, "sandbox_text", spy)
    fake_bin = tmp_path / "fake-bin"  # a stand-in `sandbox-exec -f <profile> argv...` (none on the Linux CI)
    fake_bin.mkdir()
    (fake_bin / "sandbox-exec").write_text('#!/bin/sh\nshift 2\nexec "$@"\n', encoding="utf-8")
    (fake_bin / "sandbox-exec").chmod(0o755)
    runner.host_env["PATH"] = f"{fake_bin}{os.pathsep}{runner.host_env['PATH']}"  # the arm's PATH comes from host_env
    runner.sandbox = True  # execute_driver is asked to sandbox: only the generated profile is checked
    runner.screen_exploration(tasks[:1], ["cand-a"])
    assert seen, "no profile was generated"
    for call in seen:
        assert len(call["writable"]) == 1 and call["writable"][0].name == "scratch"  # only the attempt scratch
        assert any(p.name == "bundle" for p in call["allow_read"])  # the bundle stays readable


def test_the_generated_profile_writes_only_the_scratch_and_still_reads_the_bundle(tmp_path):
    scratch, bundle = tmp_path / "attempt" / "scratch", tmp_path / "attempt" / "bundle"
    for path in (scratch, bundle):
        path.mkdir(parents=True)
    home = tmp_path / "home"
    (home / "work").mkdir(parents=True)
    inside = home / "work" / "bundle"
    inside.mkdir()
    text = lfr.sandbox_profile(writable=[scratch], deny_read=[], network="loopback", deny_home=home,
                               allow_read=[inside])
    writes = [line for line in text.splitlines() if line.startswith("(allow file-write*")]
    assert len(writes) == 1 and str(scratch.resolve()) in writes[0] and str(bundle.resolve()) not in writes[0]
    assert str(inside.resolve()) not in writes[0]
    reads = [line for line in text.splitlines() if line.startswith("(allow file-read*")]
    assert str(inside.resolve()) in reads[0] and "(deny network*)" in text


def test_a_fake_explorer_sees_the_bundle_writable_only_without_the_sandbox_flag(tmp_path):
    # the v1 profile makes the workdir writable; v2 passes workdir_writable=False (checked on the call)
    runner, _, plan, tasks = make_runner(tmp_path, "screen_exploration", caps={"cloud_executions": 0})
    calls = []
    real = lfr.execute_driver

    def spy(driver, values, **kw):
        calls.append(kw["workdir_writable"])
        return real(driver, values, **kw)

    lfr.execute_driver = spy
    try:
        runner.screen_exploration(tasks[:1], ["cand-a"])
    finally:
        lfr.execute_driver = real
    assert calls == [False]


# ------------------------------------------------------------- dedicated machine (preflight)

BIG = "{} {}\n".format(3 * 1024 * 1024, "/Applications/Slack.app/Contents/MacOS/Slack --secret=hunter2")


def test_a_busy_machine_is_refused_before_any_exploration_starts(tmp_path):
    runner, _, plan, tasks = make_runner(tmp_path, "screen_exploration", ps=BIG, caps={"cloud_executions": 0})
    with pytest.raises(lfr.PreflightRefused) as info:
        runner.screen_exploration(tasks, ["cand-a"])
    assert info.value.refusals == ["dedicated_machine_process_over_2gib:Slack:3072MiB"]
    assert counts(plan) == {} and "attempt_started" not in [e["kind"] for e in ledger_of(runner)]
    entry = [e for e in ledger_of(runner) if e["kind"] == "preflight"][0]
    assert entry["ok"] is False and entry["dedicated_machine"]["largest_other_processes"][0] == {
        "process": "Slack", "rss_mib": 3072}
    assert "hunter2" not in json.dumps(ledger_of(runner))


def test_low_free_memory_is_refused_and_a_dedicated_machine_passes(tmp_path):
    runner, _, plan, tasks = make_runner(tmp_path, "screen_exploration", caps={"cloud_executions": 0},
                                         pressure="System-wide memory free percentage: 34%")
    with pytest.raises(lfr.PreflightRefused, match="free_memory_below_minimum:34<35"):
        runner.screen_exploration(tasks, ["cand-a"])
    ok, _, plan2, tasks2 = make_runner(_sub(tmp_path, "ok"), "screen_exploration", caps={"cloud_executions": 0},
                                       ps="100 /bin/zsh\n", pressure="System-wide memory free percentage: 35%")
    assert len(ok.screen_exploration(tasks2[:1], ["cand-a"])) == 1


def test_the_v1_modes_do_not_run_the_dedicated_check(tmp_path):
    repo, snap, _, _ = _make_repo(tmp_path)
    campaign, _ = v2_campaign(tmp_path, PLAN)
    campaign["schema"] = lfr.CAMPAIGN_SCHEMA
    envelope = lfr.load_envelope(write_envelope(tmp_path), "screen", TODAY)
    runner = lfr.Runner(repo=repo, campaign=campaign, envelope=envelope, state_dir=tmp_path / "state",
                        work_root=tmp_path / "work", mode="screen", dry_run=True, sandbox=False,
                        preflight_run=lambda cid: facts_with(campaign, "fake/model-a", ps=BIG),
                        disk_free_gib=lambda: 500.0)
    assert runner.preflight("cand-a")["ok"] is True  # a 3 GiB other process does not refuse a v1 run


def test_the_cli_preflight_can_check_the_dedicated_machine_in_a_dry_run(tmp_path, capsys):
    path = tmp_path / "campaign.json"
    path.write_text(json.dumps(V2), encoding="utf-8")
    spec = V2["exploration"]["ground_truth"]  # the frozen truth file sits beside the configuration
    (tmp_path / spec["file"]).write_bytes((QUALIFICATION / spec["file"]).read_bytes())
    assert lfr.main(["preflight", "--campaign", str(path), "--dry-run", "--dedicated",
                     "--candidate", "qwen3.8-27b-mlx-4bit"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["facts"]["dedicated_machine"]["free_percent"] == 80 and out["ok"] is True


# ----------------------------------------------------------------------------- comparison

COMPARE_PLAN = {**PLAN, "reviewer": ["BLOCK", "PASS"]}


def _screened(runner, tasks, candidate="cand-a"):
    runner.screen_exploration(tasks, [candidate])


def test_compare_exploration_runs_a_l_e_with_the_report_appended_to_the_statement(tmp_path):
    runner, campaign, plan, tasks = make_runner(tmp_path, "compare_exploration", COMPARE_PLAN)
    out = runner.compare_exploration(tasks, "cand-a")
    by = {}
    for rec in out:
        by.setdefault((rec["task"]["pr"], rec["path"]), []).append(rec)
    assert [r["segment"] for r in by[(1, "L")]] == ["explore", "cloud"]
    assert [r["segment"] for r in by[(1, "E")]] == ["explore", "cloud"] and [r["segment"] for r in by[(1, "A")]] == ["cloud", "cloud"]
    texts_by_role = {}
    for item in texts(plan):
        texts_by_role.setdefault(item["role"], []).append(item["text"])
    plain = lfc.task_statement(runner_task := tasks[0]) + campaign["statement_footer"]
    assert runner_task
    statements = texts_by_role["implementer"]
    assert len(statements) == 7  # A: task 1 twice (a correction after the first BLOCK) + task 2; L and E: once per task
    section = lfe.render_report_section(lfe.validate_report(GOOD_REPORT), campaign["exploration"]["report_render_limits"])
    with_report = [t for t in statements if t.endswith(section)]
    assert with_report and all(t.startswith(plain) or t.startswith(lfc.task_statement(tasks[1]) + campaign["statement_footer"])
                               for t in statements)
    assert all(not t.endswith(section) for t in texts_by_role["reviewer"])  # the review is identical for every arm
    # the explorers did not see a report; their bundle is the plain statement
    assert texts_by_role["xlocal"][0] == plain and texts_by_role["xcloud"][0] == plain
    assert [r["path"] for r in out if r["segment"] == "explore"] == ["L", "E", "L", "E"]
    assert counts(plan)["xlocal"] == 2 and counts(plan)["xcloud"] == 2


def test_the_a_arm_gets_the_plain_statement_and_l_and_e_the_same_report(tmp_path):
    runner, campaign, plan, tasks = make_runner(tmp_path, "compare_exploration", {**PLAN, "reviewer": ["PASS"]})
    runner.compare_exploration(tasks[:1], "cand-a")
    implementers = [t["text"] for t in texts(plan) if t["role"] == "implementer"]
    assert len(implementers) == 3
    plain, with_l, with_e = implementers  # order A, L, E
    assert "Exploration report" not in plain and with_l == with_e != plain
    assert with_l.startswith(plain) and with_l.endswith("\n")
    assert lfe.render_report_section(lfe.validate_report(GOOD_REPORT), campaign["exploration"]["report_render_limits"]) == with_l[len(plain):]


def test_the_explorer_premium_is_counted_haiku_as_premium_local_not(tmp_path):
    runner, _, _, tasks = make_runner(tmp_path, "compare_exploration", {**PLAN, "reviewer": ["PASS"]})
    out = runner.compare_exploration(tasks[:1], "cand-a")
    explore = {r["path"]: r for r in out if r["segment"] == "explore"}
    assert explore["L"]["premium"]["billing_total"] == 0 and explore["L"]["cloud_executions"] == 0
    assert explore["L"]["local"]["stream_tokens"] == {"input": 1000, "output": 50, "reasoning": 7}
    assert explore["E"]["premium"]["billing_total"] == 25 and explore["E"]["cloud_executions"] == 1
    assert explore["E"]["premium"]["by_role"]["explorer"]["input_tokens"] == 20
    assert "claude-haiku-4-5-20251001" in explore["E"]["premium"]["by_model"]
    assert lfr.Ledger.totals(runner.ledger)["premium_tokens"] == 175 * 3 + 25  # A, L, E arms + the explorer


def test_the_three_verdicts_and_the_rule_on_the_comparison(tmp_path):
    # A needs a correction on task 1 (blocked once), L and E do not: L and E cost less per accepted task
    plan = {**PLAN, "reviewer": ["BLOCK", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS"]}
    runner, campaign, _, tasks = make_runner(tmp_path, "compare_exploration", plan)
    _screened_runner, _, _, _ = make_runner(_sub(tmp_path, "scr"), "screen_exploration", caps={"cloud_executions": 0})
    runner.compare_exploration(tasks, "cand-a")
    rep = lfr.report(campaign, results(runner), ledger_of(runner))
    comparison = rep["exploration_comparison"]
    arm_l, arm_e = comparison["arms"]["L"], comparison["arms"]["E"]
    # A: task 1 = 2 x (135 + 40) = 350, task 2 = 175: 525 for 2 accepted = 262.5
    assert arm_l["economy_detail"]["reference_premium_billing_tokens"] == 525
    assert arm_l["economy_detail"]["reference_premium_per_accepted"] == 262.5
    assert arm_l["economy_detail"]["premium_billing_tokens"] == 350 and arm_l["economy_detail"]["premium_per_accepted"] == 175.0
    assert (arm_l["compatibility"], arm_l["quality"], arm_l["economy"]) == ("pass", "pass", "pass")
    assert arm_l["economy_detail"]["local_explorer_wall_seconds"] > 0 and arm_l["informative"] is False
    assert arm_e["economy_detail"]["premium_billing_tokens"] == 400 and arm_e["informative"] is True  # explorer included
    assert (arm_e["compatibility"], arm_e["quality"], arm_e["economy"]) == ("pass", "pass", "pass")
    assert comparison["decision"] == "retained" and comparison["recommendation"] == "L"
    assert comparison["campaign_conclusion"] == "retain_local_explorer"
    assert comparison["informative_arms"] == ["E"] and rep["promotion"] is False
    assert comparison["complete"] is True and comparison["warnings"] == []
    assert [x["file_recall"] for x in arm_l["explorer_localization"]] == [1.0, 1.0]
    assert comparison["screening_selected"] == "screening_results_not_available"


def test_l_fails_the_economy_rule_when_it_is_not_cheaper_per_accepted_task(tmp_path):
    runner, campaign, _, tasks = make_runner(tmp_path, "compare_exploration", {**PLAN, "reviewer": ["PASS"]})
    runner.compare_exploration(tasks, "cand-a")
    comparison = lfr.report(campaign, results(runner), ledger_of(runner))["exploration_comparison"]
    assert comparison["arms"]["L"]["economy"] == "fail"  # 175 vs 175: ratio 1.0 > 0.85
    assert comparison["arms"]["E"]["economy"] == "fail"  # the Haiku explorer makes E dearer than A
    assert comparison["decision"] == "keep_cloud" and comparison["recommendation"] == "A"
    assert comparison["campaign_conclusion"] == "keep_cloud"


def test_a_complete_comparison_with_an_unavailable_verdict_and_no_fail_keeps_the_cloud(tmp_path):
    plan = {**PLAN, "reviewer": ["BLOCK", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS"]}
    runner, campaign, _, tasks = make_runner(tmp_path, "compare_exploration", plan, swap=False)  # swap not measured
    runner.compare_exploration(tasks, "cand-a")
    comparison = lfr.report(campaign, results(runner), ledger_of(runner))["exploration_comparison"]
    arm = comparison["arms"]["L"]
    assert (arm["compatibility"], arm["quality"], arm["economy"]) == ("unavailable", "pass", "pass")
    assert comparison["complete"] is True and comparison["decision"] == "inconclusive"
    # PAT-ADR-0015: insufficient proof keeps the cloud; no replay, no extra task under v2
    assert comparison["campaign_conclusion"] == "keep_cloud_insufficient_evidence"
    assert comparison["recommendation"] is None


def test_an_unknown_premium_makes_the_economy_unavailable_and_the_decision_inconclusive(tmp_path):
    runner, campaign, _, tasks = make_runner(tmp_path, "compare_exploration", {**PLAN, "reviewer": ["PASS"]})
    runner.campaign["drivers"]["cloud_explorer_economy"]["session_log"]["projects_dir"] = str(tmp_path / "nowhere")
    out = runner.compare_exploration(tasks[:1], "cand-a", arms=("A", "E"))
    explore = [r for r in out if r["segment"] == "explore"][0]
    assert explore["premium"]["billing_total"] is None and explore["unknown"]["premium.explorer"] == "session_log_not_found"
    # the campaign stops: an unmeasured spend makes every later cloud execution unsafe (cap rule of v1)
    assert results(runner)[-1]["reason"] == "cap_reached:premium_tokens_unmeasurable"
    comparison = lfr.report(campaign, results(runner), ledger_of(runner))["exploration_comparison"]
    assert comparison["arms"]["E"]["economy"] == "unavailable"
    assert comparison["arms"]["E"]["economy_detail"]["premium_billing_tokens"] is None  # unknown, never 0
    assert comparison["decision"] == "inconclusive" and comparison["recommendation"] is None


def test_the_cloud_execution_cap_stops_the_v2_comparison(tmp_path):
    runner, campaign, plan, tasks = make_runner(tmp_path, "compare_exploration", {**PLAN, "reviewer": ["PASS"]},
                                                caps={"cloud_executions": 4})
    runner.compare_exploration(tasks, "cand-a")
    started = [e for e in ledger_of(runner) if e["kind"] == "cloud_started"]
    assert len(started) == 4  # A impl, A review, then L impl, L review: the 5th (E explorer) never starts
    assert results(runner)[-1]["reason"] == "cap_reached:cloud_executions"
    last = [r for r in results(runner) if r.get("record_type") == "attempt"][-1]
    assert last["path"] == "E" and last["outcome"] == "stopped_by_cap" and last["segment"] == "explore"
    assert counts(plan).get("xcloud") is None
    comparison = lfr.report(campaign, results(runner), ledger_of(runner))["exploration_comparison"]
    # a partial campaign concludes nothing, whatever the verdicts of the tasks played so far
    assert comparison["complete"] is False and comparison["decision"] == "inconclusive"
    assert comparison["campaign_conclusion"] == "incomplete_campaign"


def test_screen_exploration_can_never_start_a_cloud_execution(tmp_path):
    runner, _, plan, tasks = make_runner(tmp_path, "screen_exploration", caps={"cloud_executions": 0})
    with pytest.raises(lfr.EnvelopeError, match="can never start a cloud execution"):
        runner.cloud_execution("explorer", "cloud_explorer_economy", tmp_path, tmp_path, "explore")
    with pytest.raises(lfr.EnvelopeError):
        runner.ledger.reserve_cloud("explorer", "s")
    assert "cloud_started" not in [e["kind"] for e in ledger_of(runner)] and counts(plan) == {}


def test_an_explorer_without_a_usable_report_runs_the_implementer_on_the_plain_statement(tmp_path):
    plan = {**PLAN, "xlocal": [{"mode": "none"}], "reviewer": ["PASS"]}
    runner, campaign, plan_path, tasks = make_runner(tmp_path, "compare_exploration", plan)
    out = runner.compare_exploration(tasks[:1], "cand-a", arms=("A", "L"))
    explore = [r for r in out if r["segment"] == "explore"][0]
    assert explore["judge"]["verdict"] == "REFUSED"
    implementers = [t["text"] for t in texts(plan_path) if t["role"] == "implementer"]
    assert len(implementers) == 2 and implementers[0] == implementers[1]  # no report section
    assert [r["outcome"] for r in out if r["path"] == "L" and r["segment"] == "cloud"] == ["accepted"]


def test_a_contaminated_explorer_leaves_the_arm_undecided_without_spending_an_implementer(tmp_path):
    peek = {"mode": "peek", "target": os.path.expanduser("~/.claude/plugins/cache/x/y.py"), "report": GOOD_REPORT}
    runner, campaign, plan_path, tasks = make_runner(tmp_path, "compare_exploration",
                                                     {**PLAN, "xlocal": [peek], "reviewer": ["PASS"]})
    out = runner.compare_exploration(tasks[:1], "cand-a", arms=("A", "L"))
    assert [(r["path"], r["segment"]) for r in out if r["path"] == "L"] == [("L", "explore")]
    assert counts(plan_path).get("implementer") == 1  # only A's
    comparison = lfr.report(campaign, results(runner), ledger_of(runner))["exploration_comparison"]
    assert comparison["arms"]["L"]["quality"] == "unavailable" and comparison["arms"]["L"]["economy"] == "unavailable"
    assert comparison["decision"] == "inconclusive"


def test_arm_l_needs_the_candidate_the_screening_selected(tmp_path):
    runner, _, _, tasks = make_runner(tmp_path, "compare_exploration", require_screening=True)
    with pytest.raises(lfr.RunnerError, match="no screening results"):
        runner.compare_exploration(tasks, "cand-a", arms=("L",))
    assert not [e for e in ledger_of(runner) if e["kind"] in ("cloud_started", "attempt_started")]
    # A alone needs neither the screening nor the local model
    runner.compare_exploration(tasks[:1], "cand-a", arms=("A",))
    # a completed screening that retained another candidate refuses this one
    shared = _sub(tmp_path, "shared")
    plan = {**PLAN, "reviewer": ["PASS"], "xlocal": [FINAL, FINAL, {"mode": "final", "report": WRONG_REPORT},
                                                      {"mode": "final", "report": WRONG_REPORT}]}
    screen, campaign, _, stasks = make_runner(shared, "screen_exploration", plan, campaign_id="shared-id",
                                              frozen=("cand-a", "cand-b"))
    screen.screen_exploration(stasks, ["cand-a", "cand-b"])
    assert lfr.report(campaign, results(screen), ledger_of(screen))["exploration_screening"]["selected"] == "cand-a"
    follow, _, _, ftasks = _resumed(shared, screen, stasks, "compare_exploration", plan, campaign_id="shared-id",
                                    require_screening=True, frozen=("cand-a", "cand-b"))
    with pytest.raises(lfr.RunnerError, match="not the one the screening selected"):
        follow.compare_exploration(ftasks, "cand-b", arms=("L",))
    assert len(follow.compare_exploration(ftasks[:1], "cand-a", arms=("L",))) == 2  # exploration + implementation


def test_a_resume_reuses_the_kept_report_and_never_replays_a_decided_exploration(tmp_path):
    plan = {**PLAN, "reviewer": ["PASS"]}
    runner, campaign, plan_path, tasks = make_runner(tmp_path, "compare_exploration", plan)

    def boom(*args, **kwargs):
        raise KeyboardInterrupt

    real = runner.cloud_path
    runner.cloud_path = boom  # the launcher is interrupted right after each exploration
    for arm in ("L", "E"):
        with pytest.raises(KeyboardInterrupt):
            runner.compare_exploration(tasks[:1], "cand-a", arms=(arm,))
    runner.cloud_path = real
    assert counts(plan_path) == {"xlocal": 1, "xcloud": 1}
    for arm in ("L", "E"):  # the relaunch resumes: the kept report is reused, no exploration runs again
        resumed, _, _, rtasks = _resumed(tmp_path, runner, tasks, "compare_exploration", plan)
        out = resumed.compare_exploration(rtasks[:1], "cand-a", arms=(arm,))
        assert [(r["path"], r["segment"], r["outcome"]) for r in out] == [(arm, "cloud", "accepted")]
    assert counts(plan_path)["xlocal"] == 1 and counts(plan_path)["xcloud"] == 1
    implementers = [t["text"] for t in texts(plan_path) if t["role"] == "implementer"]
    assert len(implementers) == 2 and all("Exploration report" in t and "add is wrong" in t for t in implementers)


def test_a_launcher_failure_before_the_arm_ran_is_void_and_replayed_once(tmp_path, monkeypatch):
    runner, campaign, plan_path, tasks = make_runner(tmp_path, "screen_exploration", caps={"cloud_executions": 0})
    real = lfr.execute_driver

    def failing(*args, **kwargs):
        raise lfr.RunnerError("the launcher failed before the arm started")

    monkeypatch.setattr(lfr, "execute_driver", failing)
    with pytest.raises(lfr.RunnerError):
        runner.screen_exploration(tasks[:1], ["cand-a"])
    void = results(runner)[0]
    assert void["outcome"] == "tool_error" and void["judge"] is None and counts(plan_path) == {}
    monkeypatch.setattr(lfr, "execute_driver", real)
    again, _, _, atasks = _resumed(tmp_path, runner, tasks, "screen_exploration", PLAN, caps={"cloud_executions": 0})
    out = again.screen_exploration(atasks[:1], ["cand-a"])
    assert out[0]["replay_of"]["outcome"] == "tool_error" and out[0]["judge"]["verdict"] == "SCORED"
    table = lfr.report(campaign, results(again), ledger_of(again))["exploration_screening"]
    assert table["undecided_tasks"] == [] and len(table["candidates"]["cand-a"]["per_task"]) == 1
    assert lfr.report(campaign, results(again), ledger_of(again))["replays"][0]["path"] == "XS"


def test_a_cloud_explorer_with_a_tool_outside_the_allowlist_is_refused_but_its_cost_is_counted(tmp_path):
    runner, campaign, _, tasks = make_runner(tmp_path, "compare_exploration", {**PLAN, "reviewer": ["PASS"]})
    argv = runner.campaign["drivers"]["cloud_explorer_economy"]["argv"]
    argv[argv.index("Read,Bash")] = "Read,Bash,Write"  # the host offers a writing tool the allowlist refuses
    with pytest.raises(lfr.RunnerError, match="outside the allowlist"):
        runner.compare_exploration(tasks[:1], "cand-a", arms=("E",))
    record = [r for r in results(runner) if r.get("record_type") == "attempt"][0]
    assert record["outcome"] == "tool_error" and record["path"] == "E" and record["segment"] == "explore"
    assert lfr.Ledger.totals(runner.ledger)["premium_tokens"] == 25  # the spend stays counted
    assert counts(_plan_of(runner)).get("implementer") is None  # no implementer ran


def _plan_of(runner):
    return Path(runner.campaign["drivers"]["cloud_explorer_economy"]["argv"][
        runner.campaign["drivers"]["cloud_explorer_economy"]["argv"].index("--plan") + 1])


def test_a_cloud_explorer_cut_before_its_record_is_replayed_once_and_counts_its_spend(tmp_path, monkeypatch):
    runner, campaign, plan_path, tasks = make_runner(tmp_path, "compare_exploration", {**PLAN, "reviewer": ["PASS"]})
    real = runner.cloud_execution

    def failing(*args, **kwargs):
        raise lfr.RunnerError("cut")

    runner.cloud_execution = failing
    with pytest.raises(lfr.RunnerError):
        runner.compare_exploration(tasks[:1], "cand-a", arms=("E",))
    runner.cloud_execution = real
    again, _, _, atasks = _resumed(tmp_path, runner, tasks, "compare_exploration", {**PLAN, "reviewer": ["PASS"]})
    out = again.compare_exploration(atasks[:1], "cand-a", arms=("E",))
    assert out[0]["replay_of"]["outcome"] == "tool_error" and out[0]["premium"]["billing_total"] == 25
    assert counts(plan_path)["xcloud"] == 1 and [r["segment"] for r in out] == ["explore", "cloud"]


def test_an_explorer_that_changes_the_bundle_is_refused_local_and_cloud(tmp_path):
    steps = [{"mode": "write_bundle", "report": GOOD_REPORT}, FINAL]
    runner, _, _, tasks = make_runner(tmp_path, "screen_exploration", {**PLAN, "xlocal": steps},
                                      caps={"cloud_executions": 0})
    bad, good = runner.screen_exploration(tasks, ["cand-a"])
    assert bad["judge"]["verdict"] == "REFUSED" and bad["judge"]["note"] == lfr.BUNDLE_MODIFIED
    assert bad["judge"]["file_recall"] == 0.0 and bad["exploration"]["bundle_modified"] is True
    assert bad["exploration"]["report"] is None  # a good-looking report is not used
    assert good["judge"]["verdict"] == "SCORED" and good["exploration"]["bundle_modified"] is False
    cloud_plan = {**PLAN, "xcloud": [{"mode": "write_bundle", "report": GOOD_REPORT}], "reviewer": ["PASS"]}
    runner2, campaign, plan, tasks2 = make_runner(_sub(tmp_path, "cloud"), "compare_exploration", cloud_plan)
    out = runner2.compare_exploration(tasks2[:1], "cand-a", arms=("E",))
    explore = [r for r in out if r["segment"] == "explore"][0]
    assert explore["exploration"]["bundle_modified"] is True and explore["exploration"]["report"] is None
    assert explore["exploration"]["score"]["verdict"] == "REFUSED" and explore["premium"]["billing_total"] == 25
    implementers = [t["text"] for t in texts(plan) if t["role"] == "implementer"]
    assert len(implementers) == 1 and "Exploration report" not in implementers[0]  # plain statement


def test_a_subset_of_the_frozen_candidates_never_retains_one_and_blocks_the_comparison(tmp_path):
    runner, campaign, _, tasks = make_runner(tmp_path, "screen_exploration", caps={"cloud_executions": 0},
                                             frozen=("cand-a", "cand-b"), campaign_id="subset")
    runner.screen_exploration(tasks, ["cand-a"])  # cand-a is perfect, cand-b was never screened
    table = lfr.report(campaign, results(runner), ledger_of(runner))["exploration_screening"]
    assert table["candidates"]["cand-a"]["mean_function_recall"] == 1.0
    assert table["selected"] is None and table["reason"] == "incomplete_screening"
    assert table["complete"] is False and table["missing_candidates"] == ["cand-b"]
    follow, _, _, ftasks = _resumed(tmp_path, runner, tasks, "compare_exploration", PLAN, campaign_id="subset",
                                    require_screening=True, frozen=("cand-a", "cand-b"),
                                    caps={"cloud_executions": 0})
    with pytest.raises(lfr.RunnerError, match="incomplete_screening"):
        follow.compare_exploration(ftasks, "cand-a", arms=("L",))
    assert counts(_plan_of(follow)).get("xlocal") == 2  # nothing ran for the comparison
    runner.screen_exploration(tasks, ["cand-b"])  # the missing candidate completes it
    done = lfr.report(campaign, results(runner), ledger_of(runner))["exploration_screening"]
    assert done["complete"] and done["missing_candidates"] == [] and done["selected"] in ("cand-a", "cand-b")
    assert done["reason"] == "tie_broken_by_frozen_candidate_order" or done["reason"] == "tie_broken_by_total_duration"


def test_the_dedicated_machine_is_rechecked_right_before_each_local_exploration_of_arm_l(tmp_path):
    runner, _, _, tasks = make_runner(tmp_path, "compare_exploration", {**PLAN, "reviewer": ["PASS"]})
    events = []
    real_pre, real_path = runner.preflight, runner.cloud_path
    runner.preflight = lambda cid, **k: (events.append("preflight"), real_pre(cid, **k))[1]
    runner.cloud_path = lambda t, path, *a, **k: (events.append(f"cloud:{path}"), real_path(t, path, *a, **k))[1]
    runner.compare_exploration(tasks[:1], "cand-a", arms=("A", "L"))
    # once at the very start (a busy machine is refused before any cloud spend), again after A's long cloud
    # work and right before the exploration
    assert events == ["preflight", "cloud:A", "preflight", "cloud:L"]
    busy, _, plan, tasks2 = make_runner(_sub(tmp_path, "busy"), "compare_exploration", {**PLAN, "reviewer": ["PASS"]},
                                        ps=BIG)
    with pytest.raises(lfr.PreflightRefused):
        busy.compare_exploration(tasks2[:1], "cand-a", arms=("A", "L"))
    assert counts(plan) == {}  # the refusal comes before any cloud spend: A did not run either
    assert "cloud_started" not in [e["kind"] for e in ledger_of(busy)]


RETAINING = {**PLAN, "reviewer": ["BLOCK", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS"]}


def _preflights(runner):
    return [(e.get("phase"), e["ok"]) for e in ledger_of(runner) if e["kind"] == "preflight"]


def test_arm_l_alone_runs_one_preflight_before_its_first_exploration_and_leaves_no_warning(tmp_path):
    runner, campaign, _, tasks = make_runner(tmp_path, "compare_exploration", {**PLAN, "reviewer": ["PASS"]})
    runner.compare_exploration(tasks, "cand-a", arms=("L",))
    rep = lfr.report(campaign, results(runner), ledger_of(runner))
    assert rep["exploration_comparison"]["warnings"] == []
    assert [h for h in rep["ledger"]["unsettled_starts"] if h["kind"] == "preflight"] == []
    # the start preflight IS the one before the first exploration (nothing was written since): not run twice;
    # the second task gets its own, after the cloud work of the first
    assert _preflights(runner) == [("start", True), (None, True)]


def test_a_resume_after_a_refused_preflight_before_l_does_not_degrade_a_complete_comparison(tmp_path):
    runner, campaign, _, tasks = make_runner(tmp_path, "compare_exploration", RETAINING)
    machine = {"ps": None}
    runner.preflight_run = lambda cid: facts_with(campaign, campaign["candidates"][cid]["model"], machine["ps"])
    real = runner.cloud_path

    def then_busy(*args, **kwargs):  # the machine gets busy during A's cloud work
        out = real(*args, **kwargs)
        machine["ps"] = BIG
        return out

    runner.cloud_path = then_busy
    with pytest.raises(lfr.PreflightRefused):
        runner.compare_exploration(tasks, "cand-a", arms=("A", "L"))
    assert [ok for _, ok in _preflights(runner)] == [True, False]  # refused right before the exploration
    resumed, _, _, rtasks = _resumed(tmp_path, runner, tasks, "compare_exploration", RETAINING)
    resumed.compare_exploration(rtasks, "cand-a", arms=("A", "L"))  # the documented resume: machine freed
    comparison = lfr.report(campaign, results(resumed), ledger_of(resumed))["exploration_comparison"]
    arm = comparison["arms"]["L"]
    assert (arm["compatibility"], arm["quality"], arm["economy"]) == ("pass", "pass", "pass")
    assert comparison["warnings"] == [] and comparison["complete"] is True
    assert comparison["decision"] == "retained" and comparison["campaign_conclusion"] == "retain_local_explorer"
    assert _preflights(resumed) == [("start", True), (None, False), ("start", True), (None, True)]


def test_a_resume_after_a_void_local_exploration_does_not_degrade_a_complete_comparison(tmp_path, monkeypatch):
    runner, campaign, _, tasks = make_runner(tmp_path, "compare_exploration", RETAINING)
    real = lfr.execute_driver

    def no_explorer(driver, *args, **kwargs):
        if driver["kind"] == "local_explorer":
            raise lfr.RunnerError("the launcher failed before the explorer started")
        return real(driver, *args, **kwargs)

    monkeypatch.setattr(lfr, "execute_driver", no_explorer)
    with pytest.raises(lfr.RunnerError):
        runner.compare_exploration(tasks, "cand-a", arms=("A", "L"))
    monkeypatch.setattr(lfr, "execute_driver", real)
    resumed, _, _, rtasks = _resumed(tmp_path, runner, tasks, "compare_exploration", RETAINING)
    out = resumed.compare_exploration(rtasks, "cand-a", arms=("A", "L"))
    assert out[0]["segment"] == "explore" and out[0]["replay_of"]["outcome"] == "tool_error"
    comparison = lfr.report(campaign, results(resumed), ledger_of(resumed))["exploration_comparison"]
    assert comparison["warnings"] == [] and comparison["complete"] is True
    assert comparison["decision"] == "retained" and comparison["campaign_conclusion"] == "retain_local_explorer"
    # A of task 1 is done: nothing lies between the start preflight of the relaunch and the replayed exploration
    assert _preflights(resumed) == [("start", True), (None, True), ("start", True), (None, True)]


def test_a_start_preflight_followed_by_another_is_no_hole_but_a_killed_one_still_is():
    def entry(kind, **kw):
        return {"kind": kind, **kw}

    session = entry("session_started", mode="compare_exploration")
    start = entry("preflight", ok=True, candidate="c1", phase="start")
    again = entry("preflight", ok=True, candidate="c1")
    begun = entry("attempt_started", attempt_dir="d1", path="L", candidate="c1")
    done = entry("settled", cloud=False, attempt_dir="d1")
    assert lfr._unsettled_starts([session, start, again, begun, done]) == []
    assert lfr._unsettled_starts([session, start, entry("preflight", ok=False, candidate="c1")]) == []
    assert lfr._unsettled_starts([session, start, begun, done]) == []  # the start one right before the attempt
    # killed after the start preflight, or after the one before the exploration: still listed
    killed = lfr._unsettled_starts([session, start, session, start, begun, done])
    assert [h["warning"] for h in killed] == ["preflight_without_attempt:c1@ledger_line_2"]
    killed = lfr._unsettled_starts([session, start, again])
    assert [h["warning"] for h in killed] == ["preflight_without_attempt:c1@ledger_line_3"]
    # an unmarked preflight (v1, and the v2 screening) keeps the v1 reading
    v1 = lfr._unsettled_starts([entry("session_started", mode="compare"), again, again, begun, done])
    assert [h["warning"] for h in v1] == ["preflight_without_attempt:c1@ledger_line_2"]


def test_a_task_with_its_exploration_but_no_implementer_yet_is_undecided_not_rejected(tmp_path):
    plan = {**PLAN, "reviewer": ["PASS"]}
    runner, campaign, _, tasks = make_runner(tmp_path, "compare_exploration", plan)
    campaign["rules"]["exploration_comparison"]["tasks"] = 1  # a one-task corpus: only the cut makes it partial
    runner.compare_exploration(tasks[:1], "cand-a", arms=("A",))
    real = runner.cloud_path

    def boom(*args, **kwargs):
        raise KeyboardInterrupt

    runner.cloud_path = boom  # cut between the exploration and the implementer
    with pytest.raises(KeyboardInterrupt):
        runner.compare_exploration(tasks[:1], "cand-a", arms=("L",))
    runner.cloud_path = real
    comparison = lfr.report(campaign, results(runner), ledger_of(runner))["exploration_comparison"]
    arm = comparison["arms"]["L"]
    assert arm["awaiting_implementer"] == [1] and arm["quality"] == "unavailable" and arm["economy"] == "unavailable"
    assert comparison["complete"] is False and comparison["campaign_conclusion"] == "incomplete_campaign"
    assert comparison["decision"] == "inconclusive"
    resumed, _, _, rtasks = _resumed(tmp_path, runner, tasks, "compare_exploration", plan)
    resumed.campaign["rules"]["exploration_comparison"]["tasks"] = 1
    resumed.compare_exploration(rtasks[:1], "cand-a", arms=("L",))  # the relaunch plays the missing implementer
    after = lfr.report(resumed.campaign, results(resumed), ledger_of(resumed))["exploration_comparison"]
    assert after["arms"]["L"]["awaiting_implementer"] == [] and after["complete"] is True
    assert after["arms"]["L"]["quality"] == "pass" and after["campaign_conclusion"] != "incomplete_campaign"


def test_a_screening_that_can_no_longer_be_completed_keeps_the_cloud_for_lack_of_evidence(tmp_path, monkeypatch):
    caps = {"cloud_executions": 0}
    runner, campaign, _, tasks = make_runner(tmp_path, "screen_exploration", caps=caps)
    real = lfr.execute_driver

    def failing(*args, **kwargs):
        raise lfr.RunnerError("the launcher failed before the arm started")

    def screening(of):
        return lfr.report(campaign, results(of), ledger_of(of))["exploration_screening"]

    monkeypatch.setattr(lfr, "execute_driver", failing)
    with pytest.raises(lfr.RunnerError):
        runner.screen_exploration(tasks, ["cand-a"])
    once = screening(runner)  # cut once: a relaunch replays it, nothing is concluded
    assert once["campaign_conclusion"] == "incomplete_screening" and once["not_completable_tasks"] == []
    second, _, _, stasks = _resumed(tmp_path, runner, tasks, "screen_exploration", PLAN, caps=caps)
    with pytest.raises(lfr.RunnerError):
        second.screen_exploration(stasks, ["cand-a"])
    twice = screening(second)  # cut twice, but the other task was never played: still completable in part
    assert twice["not_completable_tasks"] == [{"candidate": "cand-a", "pr": 1}]
    assert twice["campaign_conclusion"] == "incomplete_screening"
    monkeypatch.setattr(lfr, "execute_driver", real)
    third, _, plan_path, ttasks = _resumed(tmp_path, runner, tasks, "screen_exploration", PLAN, caps=caps)
    out = third.screen_exploration(ttasks, ["cand-a"])
    assert [r["task"]["pr"] for r in out] == [2] and counts(plan_path) == {"xlocal": 1}  # task 1 is never replayed
    final = screening(third)
    assert final["complete"] is False and final["selected"] is None and final["reason"] == "incomplete_screening"
    assert final["not_completable_tasks"] == [{"candidate": "cand-a", "pr": 1}]
    assert final["campaign_conclusion"] == "keep_cloud_insufficient_evidence"  # PAT-ADR-0015; a retry needs a v3
    fourth, _, _, ftasks = _resumed(tmp_path, runner, tasks, "compare_exploration", PLAN, caps=caps,
                                    require_screening=True)
    with pytest.raises(lfr.RunnerError, match="incomplete_screening"):
        fourth.compare_exploration(ftasks, "cand-a", arms=("L",))


def test_a_bound_reached_by_the_harness_itself_is_a_refusal_and_a_start_error_is_void(tmp_path, monkeypatch):
    runner, _, plan, tasks = make_runner(tmp_path, "screen_exploration", caps={"cloud_executions": 0})
    real = lfr.execute_driver

    def at_the_bound(*args, **kwargs):  # the harness stopped by itself at --max-time: timed_out stays false
        out = real(*args, **kwargs)
        return {**out, "wall_seconds": runner.campaign["bounds"]["explorer_max_seconds"], "timed_out": False}

    monkeypatch.setattr(lfr, "execute_driver", at_the_bound)
    record = runner.screen_exploration(tasks[:1], ["cand-a"])[0]
    assert record["local"]["timed_out"] is False and record["judge"]["note"] == lfr.BOUND_HIT
    assert record["judge"]["verdict"] == "REFUSED" and record["exploration"]["report"] is None
    monkeypatch.setattr(lfr, "execute_driver", real)
    # an arm that never started (the executable is missing) is a launcher failure: void, replayed once
    sub = _sub(tmp_path, "nostart")
    broken, campaign, _, tasks2 = make_runner(sub, "screen_exploration", caps={"cloud_executions": 0})
    good_argv = list(broken.campaign["drivers"]["local_explorer"]["argv"])
    broken.campaign["drivers"]["local_explorer"]["argv"] = ["/nonexistent/explorer", *good_argv[1:]]
    with pytest.raises(lfr.RunnerError, match="could not be started"):
        broken.screen_exploration(tasks2[:1], ["cand-a"])
    void = [r for r in results(broken) if r.get("record_type") == "attempt"][0]
    assert void["outcome"] == "tool_error" and void["judge"] is None
    again, _, _, atasks = _resumed(sub, broken, tasks2, "screen_exploration", PLAN, caps={"cloud_executions": 0})
    out = again.screen_exploration(atasks[:1], ["cand-a"])
    assert out[0]["replay_of"]["outcome"] == "tool_error" and out[0]["judge"]["verdict"] == "SCORED"


def test_the_implementer_driver_default_of_v1_paths_is_kept():
    assert lfr.IMPLEMENTER_DRIVER.get("C", lfr.IMPLEMENTER_DRIVER["A"]) == "cloud_implementer_current"
    assert lfr.IMPLEMENTER_DRIVER["B"] == "cloud_implementer_economy"
    assert lfr.IMPLEMENTER_DRIVER["A"] == lfr.IMPLEMENTER_DRIVER["L"] == lfr.IMPLEMENTER_DRIVER["E"]


# --------------------------------------------------------------------------- v1 unchanged

def test_the_v1_drivers_modes_and_paths_are_unchanged():
    assert lfr.MODES[:2] == ("screen", "compare") and lfr.PATHS == ("A", "B", "C", "N")
    assert lfr.IMPLEMENTER_DRIVER["A"] == "cloud_implementer_current" and lfr.IMPLEMENTER_DRIVER["B"] == "cloud_implementer_economy"
    assert lfr.CAMPAIGN_SCHEMA == "foundry.local-first-campaign.v1"
    v1 = lfr.load_campaign(QUALIFICATION / "pat-19-campaign-v1.json")
    assert v1["schema"] == lfr.CAMPAIGN_SCHEMA and "dedicated_machine" not in v1 and "explore" not in v1["prompts"]
    assert set(v1["drivers"]) == {"local_harness", "neutral_harness", "cloud_implementer_current",
                                  "cloud_implementer_economy", "cloud_reviewer"}


def test_cli_dry_run_of_the_two_v2_modes(tmp_path, capsys):
    repo, snap, _, _ = _make_repo(tmp_path)
    campaign, plan_path = v2_campaign(tmp_path, {**PLAN, "reviewer": ["PASS"]})
    truth = {"schema": "x", "tasks": {"1": lfe.ground_truth(repo, snap["prs"][0])}}  # the frozen truth of the fake task
    (tmp_path / "truth.json").write_text(json.dumps(truth), encoding="utf-8")
    campaign["exploration"]["ground_truth"] = {"file": "truth.json", "sha256": hashlib.sha256(
        (tmp_path / "truth.json").read_bytes()).hexdigest()}
    (tmp_path / "c.json").write_text(json.dumps(campaign), encoding="utf-8")
    (tmp_path / "snap.json").write_text(json.dumps(snap), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"screening": [{"pr": 1}], "comparison": [{"pr": 1}]}),
                                            encoding="utf-8")
    env = tmp_path / "env"
    env.mkdir()
    common = ["--campaign", str(tmp_path / "c.json"), "--dry-run", "--state-dir", str(tmp_path / "state"),
              "--work-root", str(tmp_path / "work"), "--repo", str(repo), "--snapshot", str(tmp_path / "snap.json"),
              "--manifest", str(tmp_path / "manifest.json")]
    screen_env = _envelope(env, "screen_exploration", "cli-v2", cloud_executions=0)
    assert lfr.main(["screen-exploration", *common, "--envelope", str(screen_env), "--candidate", "cand-a"],
                    today=TODAY) == 0
    assert counts(plan_path) == {"xlocal": 1}
    results_path = tmp_path / "state" / "results-cli-v2.jsonl"
    capsys.readouterr()
    assert lfr.main(["report", "--campaign", str(tmp_path / "c.json"), "--results", str(results_path)]) == 0
    assert json.loads(capsys.readouterr().out)["exploration_screening"]["candidates"]["cand-a"]["tasks"] == 1
    # a v1 mode with a v2 campaign, and a v2 mode with a v1 envelope, are refused with code 2
    assert lfr.main(["screen", *common, "--envelope", str(write_envelope(env, "cli-v1")),
                     "--candidate", "cand-a"], today=TODAY) == 2
    assert "does not match the campaign schema" in capsys.readouterr().err
    assert lfr.main(["compare-exploration", *common, "--envelope", str(write_envelope(env, "cli-v1b")),
                     "--candidate", "cand-a", "--paths", "A"], today=TODAY) == 2
    assert lfr.main(["compare-exploration", *common, "--envelope", str(screen_env), "--candidate", "cand-a",
                     "--paths", "A,B"], today=TODAY) == 2  # B is not a v2 arm
    # a frozen truth file that changed is refused (code 2) before any attempt
    (tmp_path / "truth.json").write_text(json.dumps({**truth, "tasks": {}}), encoding="utf-8")
    capsys.readouterr()
    assert lfr.main(["screen-exploration", *common, "--envelope", str(screen_env), "--candidate", "cand-a"],
                    today=TODAY) == 2
    assert "sha256" in capsys.readouterr().err and counts(plan_path) == {"xlocal": 1}


def test_module_path_is_the_corpus_path():  # the fake repository's module is the one the truth is built on
    assert lfe.is_product_path(MOD)
