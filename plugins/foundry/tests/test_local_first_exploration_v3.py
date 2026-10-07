"""PAT-116: protocol v3 (the v2 exploration modes, a wider budget, two candidates, one task per launch).

Fake arms only (the helpers of the v2 exploration tests): no model, no cloud, no network. The launcher
loads no model; the one-task-per-launch rule is what lets the operator reload it between launches."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from foundry import local_first_runner as lfr
from test_local_first_exploration_runner import (COMPARE_PLAN, PLAN, _resumed, make_runner)
from test_local_first_runner import counts, ledger_of, results

QUALIFICATION = Path(__file__).resolve().parents[1] / "docs" / "qualification"
V2_PATH = QUALIFICATION / "pat-19-campaign-v2.json"
V3_PATH = QUALIFICATION / "pat-19-campaign-v3.json"
ONE = {"exploration": {"one_task_per_launch": True}}


def _sub(tmp_path, name):
    (tmp_path / name).mkdir()
    return tmp_path / name


def _three(tasks):
    return [*tasks, {**tasks[0], "pr": 3, "issue": "PAT-3"}]


# ------------------------------------------------------------------ the frozen v3 configuration

def test_the_v3_campaign_pins_the_validated_values_and_inherits_the_rest_from_v2():
    v2 = json.loads(V2_PATH.read_text("utf-8"))
    v3 = lfr.load_campaign(V3_PATH)
    assert v3["schema"] == lfr.CAMPAIGN_SCHEMA_V2 and v3["protocol"] == "pat-19-protocol-v3"
    assert v3["bounds"]["explorer_max_steps"] == 60 and v3["bounds"]["explorer_max_seconds"] == 900
    assert v3["exploration"]["one_task_per_launch"] is True
    frozen = ["qwen3.6-35b-a3b-mlx-4bit", "qwen3-coder-30b-a3b-mlx-4bit"]
    assert v3["rules"]["exploration_screening"]["candidates"] == frozen and list(v3["candidates"]) == frozen
    for key in frozen:  # same keys, digests and pinned load commands as v1 and v2
        assert v3["candidates"][key] == v2["candidates"][key]
    # the one change is the budget: everything else that decides is identical to v2
    for key in ("frozen_machine", "cloud_bash_deny", "dedicated_machine", "statement_footer", "prompts",
                "drivers", "isolation"):
        if key == "dedicated_machine":
            assert {k: v for k, v in v3[key].items() if k != "note"} == {k: v for k, v in v2[key].items() if k != "note"}
        else:
            assert v3[key] == v2[key], key
    assert {k: v for k, v in v3["bounds"].items() if not k.startswith("explorer") and k != "note"} == {
        k: v for k, v in v2["bounds"].items() if not k.startswith("explorer") and k != "note"}
    for name in ("exploration_screening", "exploration_comparison"):
        a, b = v3["rules"][name], v2["rules"][name]
        assert {k: v for k, v in a.items() if k not in ("note", "candidates")} == {
            k: v for k, v in b.items() if k not in ("note", "candidates")}
    assert v3["exploration"]["ground_truth"] == v2["exploration"]["ground_truth"]  # same file, same sha256
    assert v3["exploration"]["report_render_limits"] == v2["exploration"]["report_render_limits"]
    coords3, coords2 = v3["exploration"]["protocol_coordinates"], v2["exploration"]["protocol_coordinates"]
    assert {k: v for k, v in coords3.items() if k != "note"} == {k: v for k, v in coords2.items() if k != "note"}
    assert "protocol v4" in coords3["note"]
    assert "requested in the campaign report" not in v3["dedicated_machine"]["note"]
    assert v3["envelope_recommended"]["compare_exploration"]["caps"]["cloud_executions"] == 120
    assert "one_task_per_launch" not in v2["exploration"] and v2["bounds"]["explorer_max_seconds"] == 600


def test_the_loader_pins_the_v3_coordinates(tmp_path):
    spec = json.loads(V3_PATH.read_text("utf-8"))["exploration"]["ground_truth"]
    (tmp_path / spec["file"]).write_bytes((QUALIFICATION / spec["file"]).read_bytes())

    def broken(edit):
        data = json.loads(V3_PATH.read_text("utf-8"))
        edit(data)
        path = tmp_path / "v3.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    one = "qwen3.6-35b-a3b-mlx-4bit"
    for edit, message in (
            (lambda d: d["bounds"].update(explorer_max_seconds=600), "explorer bounds"),
            (lambda d: d["bounds"].update(explorer_max_steps=25), "explorer bounds"),
            (lambda d: d["rules"]["exploration_screening"].update(candidates=[one]), "frozen candidates|exactly the candidates"),
            (lambda d: d["candidates"].pop("qwen3-coder-30b-a3b-mlx-4bit"), "frozen candidates|exactly the candidates"),
            (lambda d: d["rules"]["exploration_screening"].update(candidates=list(reversed(
                d["rules"]["exploration_screening"]["candidates"]))), "frozen candidates|exactly the candidates"),
            (lambda d: d["exploration"].update(one_task_per_launch=False), "one_task_per_launch true"),
            (lambda d: d["exploration"].pop("one_task_per_launch"), "one_task_per_launch true"),
            (lambda d: d["exploration"].update(one_task_per_launch="yes"), "must be a boolean")):
        with pytest.raises(lfr.RunnerError, match=message):
            lfr.load_campaign(broken(edit))
    lfr.load_campaign(broken(lambda d: None))


def test_the_v3_campaign_is_refused_by_the_v1_modes_and_v1_v2_files_are_untouched(tmp_path):
    v3 = lfr.load_campaign(V3_PATH)
    with pytest.raises(lfr.RunnerError, match="does not match the campaign schema"):
        lfr.Runner(repo=tmp_path, campaign=v3, envelope={"sha256": "x"}, state_dir=tmp_path,
                   work_root=tmp_path / "w", mode="screen", dry_run=True, sandbox=False)
    assert lfr.load_campaign(V2_PATH)["bounds"]["explorer_max_steps"] == 25  # v2 loads as before


def test_a_v3_screening_needs_both_candidates_to_be_complete(tmp_path):
    runner, campaign, plan, tasks = make_runner(tmp_path, "screen_exploration", PLAN, caps={"cloud_executions": 0},
                                                frozen=("cand-a", "cand-b"), campaign_over=ONE)
    for _ in range(2):  # candidate cand-a only: its two tasks, one per launch
        runner.screen_exploration(tasks, ["cand-a"])
        runner, campaign, plan, tasks = _resumed(tmp_path, runner, tasks, "screen_exploration", PLAN,
                                                 caps={"cloud_executions": 0}, frozen=("cand-a", "cand-b"),
                                                 campaign_over=ONE)
    table = lfr.report(campaign, results(runner), ledger_of(runner))["exploration_screening"]
    assert table["complete"] is False and table["selected"] is None


# ------------------------------------------------------------------ one task per launch: screening

def _screen_launches(tmp_path, tasks_n, candidates, plan=PLAN):
    runner, campaign, plan_path, tasks = make_runner(tmp_path, "screen_exploration", plan,
                                                     caps={"cloud_executions": 0}, frozen=tuple(candidates),
                                                     campaign_over=ONE)
    tasks = _three(tasks)[:tasks_n]
    launches = []
    for _ in range(20):
        out = runner.screen_exploration(tasks, candidates)
        launches.append((out, runner.work_remains))
        if not runner.work_remains:
            return launches, runner, campaign, plan_path, tasks
        runner, campaign, plan_path, _ = _resumed(tmp_path, runner, tasks, "screen_exploration", plan,
                                                   caps={"cloud_executions": 0}, frozen=tuple(candidates),
                                                   campaign_over=ONE)
    raise AssertionError("the loop does not end")


def test_screening_plays_one_task_per_launch_and_the_loop_gives_the_records_of_one_v2_launch(tmp_path):
    launches, runner, campaign, plan_path, tasks = _screen_launches(_sub(tmp_path, "v3"), 2, ["cand-a", "cand-b"])
    assert [len(out) for out, _ in launches] == [1] * 4 and [w for _, w in launches] == [True] * 3 + [False]
    assert counts(plan_path) == {"xlocal": 4}
    # the same screening in ONE launch under a v2 config (flag off) gives the same records
    runner2, campaign2, plan2, tasks2 = make_runner(_sub(tmp_path, "v2"), "screen_exploration", PLAN,
                                                    caps={"cloud_executions": 0}, frozen=("cand-a", "cand-b"))
    out2 = runner2.screen_exploration(tasks2, ["cand-a", "cand-b"])
    assert runner2.work_remains is None and len(out2) == 4 and counts(plan2) == {"xlocal": 4}

    def key(r):
        return (r["local"]["candidate"], r["task"]["pr"], r["path"], r["judge"]["verdict"],
                r["judge"]["file_recall"], r["judge"]["file_precision"], r["judge"]["function_recall"])
    assert [key(r) for r in results(runner)] == [key(r) for r in out2]
    assert lfr.report(campaign, results(runner), ledger_of(runner))["exploration_screening"]["complete"] is True
    # one preflight per task: the ledger shows one session per launch
    assert [e["kind"] for e in ledger_of(runner)].count("preflight") == 4


def test_a_decided_task_is_skipped_and_a_launch_with_nothing_left_plays_nothing(tmp_path):
    launches, runner, campaign, plan_path, tasks = _screen_launches(tmp_path, 2, ["cand-a"])
    assert counts(plan_path) == {"xlocal": 2}
    rerun, _, _, _ = _resumed(tmp_path, runner, tasks, "screen_exploration", PLAN, caps={"cloud_executions": 0},
                              frozen=("cand-a",), campaign_over=ONE)
    assert rerun.screen_exploration(tasks, ["cand-a"]) == [] and rerun.work_remains is False
    assert counts(plan_path) == {"xlocal": 2}


def test_a_v2_config_still_runs_every_task_in_one_launch(tmp_path):
    runner, _, plan_path, tasks = make_runner(tmp_path, "screen_exploration", PLAN, caps={"cloud_executions": 0})
    assert len(runner.screen_exploration(tasks, ["cand-a"])) == 2 and runner.work_remains is None
    assert counts(plan_path) == {"xlocal": 2}


# ------------------------------------------------------------------ one task per launch: comparison

def _compare_launches(tmp_path, tasks_n, plan=COMPARE_PLAN):
    runner, campaign, plan_path, tasks = make_runner(tmp_path, "compare_exploration", plan, campaign_over=ONE)
    tasks = _three(tasks)[:tasks_n]
    launches = []
    for _ in range(10):
        out = runner.compare_exploration(tasks, "cand-a")
        launches.append((out, runner.work_remains))
        if not runner.work_remains:
            return launches, runner, campaign, plan_path, tasks
        runner, campaign, plan_path, _ = _resumed(tmp_path, runner, tasks, "compare_exploration", plan,
                                                   campaign_over=ONE)
    raise AssertionError("the loop does not end")


def test_comparison_plays_one_task_all_arms_per_launch_and_the_loop_gives_the_v2_records(tmp_path):
    launches, runner, campaign, plan_path, tasks = _compare_launches(_sub(tmp_path, "v3"), 2)
    assert [w for _, w in launches] == [True, False]
    for (out, _), task in zip(launches, tasks):
        assert {r["task"]["pr"] for r in out} == {task["pr"]} and {r["path"] for r in out} == {"A", "L", "E"}
    runner2, _, plan2, tasks2 = make_runner(_sub(tmp_path, "v2"), "compare_exploration", COMPARE_PLAN)
    out2 = runner2.compare_exploration(tasks2, "cand-a")
    assert runner2.work_remains is None

    def key(r):
        return (r["task"]["pr"], r["path"], r["segment"], r.get("outcome"), r["accepted"])
    assert sorted(map(key, results(runner))) == sorted(map(key, out2))
    assert counts(plan_path)["xlocal"] == counts(plan2)["xlocal"] == 2


def test_a_decided_comparison_task_is_skipped_and_the_next_one_is_played(tmp_path):
    launches, runner, campaign, plan_path, tasks = _compare_launches(tmp_path, 2)
    assert len(launches) == 2
    before = dict(counts(plan_path))
    rerun, _, _, _ = _resumed(tmp_path, runner, tasks, "compare_exploration", COMPARE_PLAN, campaign_over=ONE)
    assert rerun.compare_exploration(tasks, "cand-a") == [] and rerun.work_remains is False
    assert counts(plan_path) == before


def test_a_comparison_task_cut_midway_resumes_without_replaying_its_exploration(tmp_path):
    runner, campaign, plan_path, tasks = make_runner(tmp_path, "compare_exploration", COMPARE_PLAN, campaign_over=ONE)

    def boom(*args, **kwargs):
        raise KeyboardInterrupt

    runner.cloud_path = boom  # the launch is cut right after the exploration of arm L of the first task
    with pytest.raises(KeyboardInterrupt):
        runner.compare_exploration(tasks, "cand-a", arms=("L",))
    assert runner.work_remains is None and counts(plan_path) == {"xlocal": 1}
    resumed, _, _, _ = _resumed(tmp_path, runner, tasks, "compare_exploration", COMPARE_PLAN, campaign_over=ONE)
    first = resumed.compare_exploration(tasks, "cand-a")  # relaunch: the kept report is reused
    assert {r["task"]["pr"] for r in first} == {1} and resumed.work_remains is True
    assert counts(plan_path)["xlocal"] == 1
    again, _, _, _ = _resumed(tmp_path, resumed, tasks, "compare_exploration", COMPARE_PLAN, campaign_over=ONE)
    second = again.compare_exploration(tasks, "cand-a")
    assert {r["task"]["pr"] for r in second} == {2} and again.work_remains is False
    assert counts(plan_path)["xlocal"] == 2


def test_the_operator_script_is_syntactically_valid_and_never_loads_from_the_launcher():
    script = QUALIFICATION / "pat-19-v3-operator.sh"
    text = script.read_text("utf-8")
    assert "set -euo pipefail" in text and "lms unload --all" in text and "< /dev/null" in text
    assert not re.search(r"\$\{?ENV\b", text)
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash available")
    assert subprocess.run([bash, "-n", str(script)], capture_output=True).returncode == 0
    # an unknown mode, or a missing argument, is a usage error before anything runs
    assert subprocess.run([bash, str(script), "nope"], capture_output=True).returncode == 64


# ------------------------------------------------------------------ the command line

def test_the_cli_prints_whether_work_remains_and_exits_zero(tmp_path, capsys):
    from test_local_first_exploration_runner import _envelope
    import hashlib
    from foundry import local_first_exploration as lfe
    from test_local_first_corpus import _make_repo
    from test_local_first_exploration_runner import TODAY, v2_campaign
    repo, snap, _, _ = _make_repo(tmp_path)
    campaign, plan_path = v2_campaign(tmp_path, PLAN)
    campaign["exploration"]["one_task_per_launch"] = True
    truth = {"schema": "x", "tasks": {"1": lfe.ground_truth(repo, snap["prs"][0])}}
    (tmp_path / "truth.json").write_text(json.dumps(truth), encoding="utf-8")
    campaign["exploration"]["ground_truth"] = {"file": "truth.json", "sha256": hashlib.sha256(
        (tmp_path / "truth.json").read_bytes()).hexdigest()}
    (tmp_path / "c.json").write_text(json.dumps(campaign), encoding="utf-8")
    (tmp_path / "snap.json").write_text(json.dumps(snap), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"screening": [{"pr": 1}], "comparison": [{"pr": 1}]}),
                                            encoding="utf-8")
    env = tmp_path / "env"
    env.mkdir()
    args = ["screen-exploration", "--campaign", str(tmp_path / "c.json"), "--dry-run", "--state-dir",
            str(tmp_path / "state"), "--work-root", str(tmp_path / "work"), "--repo", str(repo), "--snapshot",
            str(tmp_path / "snap.json"), "--manifest", str(tmp_path / "manifest.json"), "--envelope",
            str(_envelope(env, "screen_exploration", "cli-v3", cloud_executions=0)), "--candidate", "cand-a", "cand-b"]
    campaign["rules"]["exploration_screening"]["candidates"] = ["cand-a", "cand-b"]
    (tmp_path / "c.json").write_text(json.dumps(campaign), encoding="utf-8")
    assert lfr.main(args, today=TODAY) == 0
    assert capsys.readouterr().out.strip() == "pat19-v3: work_remains=yes"
    assert lfr.main(args, today=TODAY) == 0
    assert capsys.readouterr().out.strip() == "pat19-v3: work_remains=no"
    assert lfr.main(args, today=TODAY) == 0
    assert capsys.readouterr().out.strip() == "pat19-v3: work_remains=no"
    assert counts(plan_path) == {"xlocal": 2}
