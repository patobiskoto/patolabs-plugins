"""PAT-126: protocol v5 (DRAFT until its freeze) and its one-task pilot: the PAT-121 instrument (feedback, private root,
fixed candidate, arms A and L) with the PAT-123 audit revision 2 and the PAT-124 native sandbox switched on, a
comparison on an explicit ordered list of the 12 corpus tasks recorded under a label of its own, the git-excludes
read, and Claude Code pinned.

Fake arms only: no ``claude``, no model, no ``lms``, no cloud, never the real HOME or ``~/.config/foundry``. The
``claude`` of the version tests is a stand-in script on the runner's ``host_env`` PATH. Everything the v5 keys add is
off when they are absent (the v1 to v4 tests cover that, and the frozen files are hashed here)."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from foundry import local_first_runner as lfr
from test_local_first_audit_revision import R2
from test_local_first_exploration_runner import PLAN, make_runner
from test_local_first_exploration_v3 import _sub
from test_local_first_exploration_v4 import FEEDBACK
from test_local_first_native_sandbox import KEY, _fake_runner, _native_records
from test_local_first_runner import counts, ledger_of, ledger_kinds, results

QUALIFICATION = Path(__file__).resolve().parents[1] / "docs" / "qualification"
V4_PATH = QUALIFICATION / "pat-19-campaign-v4.json"
V5_PATH = QUALIFICATION / "pat-19-campaign-v5.json"
PILOT_PATH = QUALIFICATION / "pat-19-campaign-v5-pilot.json"
TWELVE = [26, 38, 25, 42, 33, 37, 30, 83, 27, 24, 48, 19]
LABEL = "pat-19-v5"
GIT_IGNORE = ".config/git/ignore"
FROZEN_SHA256 = {  # the frozen v1 to v4 configurations are never edited (PAT-ADR-0015)
    "pat-19-campaign-v1.json": "a678dc6781b84058c20727a9002b55c84319bf43d90a3f6e70eb1a079d962ffc",
    "pat-19-campaign-v2.json": "6830629ccd385847ca6b88c730b706407517ac009cb3715daf8c6e7a61ec8d16",
    "pat-19-campaign-v3.json": "95b7a0717ecfcc28b6595e4a88dee108d158ff7d85dddc84f7d1c56de8d5fd1d",
    "pat-19-campaign-v4.json": "3bc88cf496f9838779adae431e72754615576c276033cc7436c151d3a10b8605",
    "pat-19-campaign-v5.json": "af2b257099b287314ba5fc3e7a8afff5380d293cfefd7c058ce5f09d44c3cbc4",  # frozen 2026-10-08 (PAT-126); the pilot config is NOT pinned
}


def _load(path):
    return json.loads(path.read_text("utf-8"))


def _staged(tmp_path, source=V5_PATH):
    spec = _load(source)["exploration"]["ground_truth"]
    (tmp_path / spec["file"]).write_bytes((QUALIFICATION / spec["file"]).read_bytes())
    return _load(source)


def _broken(tmp_path, edit, source=V5_PATH):
    data = _staged(tmp_path, source)
    edit(data)
    path = tmp_path / "c.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ------------------------------------------------------------------------------------- the DRAFT configurations

def test_the_frozen_configurations_are_byte_for_byte_untouched():  # v1 to v5; the pilot config is not pinned
    for name, digest in FROZEN_SHA256.items():
        assert hashlib.sha256((QUALIFICATION / name).read_bytes()).hexdigest() == digest, name
    v4 = lfr.load_campaign(V4_PATH)
    assert not {"comparison_tasks", "comparison_task_set"} & set(v4["exploration"])
    assert KEY not in v4["isolation"] and "audit_revision" not in v4["isolation"]
    assert v4["isolation"]["allow_read_home"] == [] and "binary_version" not in v4["drivers"]["cloud_reviewer"]


def test_the_v5_campaign_pins_the_decided_values_and_says_it_is_a_draft():
    v4, v5 = _load(V4_PATH), lfr.load_campaign(V5_PATH)
    assert v5["schema"] == lfr.CAMPAIGN_SCHEMA_V2 and v5["protocol"] == lfr.PROTOCOL_V5
    assert v5["note"].startswith("Frozen campaign config") and "2026-10-08" in v5["note"] and "mandate of 2026-10-08" in v5["note"]
    assert "DRAFT" not in json.dumps(v5) and "DRAFT" not in json.dumps(_load(PILOT_PATH))
    assert lfr.PROTOCOL_V5 in lfr.FROZEN_PROTOCOLS and lfr.PROTOCOL_V5_PILOT not in lfr.FROZEN_PROTOCOLS
    one = "qwen3.6-35b-a3b-mlx-4bit"
    assert list(v5["candidates"]) == [one] and v5["candidates"] == v4["candidates"]
    assert v5["exploration"]["fixed_candidate"] == one and "comparison_task_group" not in v5["exploration"]
    assert v5["exploration"]["comparison_tasks"] == TWELVE and v5["exploration"]["comparison_task_set"] == LABEL
    assert v5["exploration"]["one_task_per_launch"] is True
    assert v5["bounds"]["explorer_max_steps"] == 60 and v5["bounds"]["explorer_max_seconds"] == 900
    assert v5["bounds"]["max_correction_rounds"] == 2
    assert {k: v5["correction_feedback"][k] for k in lfr.V4_FEEDBACK} == lfr.V4_FEEDBACK
    iso = v5["isolation"]
    assert iso["private_attempt_root"] is True and iso[KEY] is True and iso["audit_revision"] == R2
    assert iso["allow_read_home"] == [GIT_IGNORE]
    assert v5["rules"]["exploration_comparison"]["tasks"] == 12
    assert v5["rules"]["exploration_comparison"]["premium_per_accepted_ratio_max"] == 0.85
    assert "cloud_explorer_economy" not in v5["drivers"]  # no Haiku arm
    caps = v5["envelope_recommended"]["compare_exploration"]["caps"]
    assert caps == {"cloud_executions": 150, "premium_tokens": 75000000, "wall_clock_seconds": 100000}
    assert 2 * 12 * 6 <= caps["cloud_executions"]
    manifest = _load(QUALIFICATION / "pat-19-corpus-manifest-v1.json")
    assert TWELVE == [t["pr"] for t in manifest["comparison"]] + [t["pr"] for t in manifest["screening"]]
    for name in ("cloud_implementer_current", "cloud_reviewer"):
        pin = v5["drivers"][name]["binary_version"]
        assert pin["command"] == ["claude", "--version"] and pin["version"] == "2.1.285"
        # the pinned argv of v4 is kept as it is: the launcher swaps the permission mode itself
        assert v5["drivers"][name]["argv"] == v4["drivers"][name]["argv"]
    # the decision rule, the ground truth, the machine and the explorer are those of v4
    for name in ("exploration_screening", "exploration_comparison"):
        a, b = v5["rules"][name], v4["rules"][name]
        assert {k: v for k, v in a.items() if k not in ("note", "tasks")} == {
            k: v for k, v in b.items() if k not in ("note", "tasks")}
    assert v5["exploration"]["ground_truth"] == v4["exploration"]["ground_truth"]
    assert v5["drivers"]["local_explorer"] == v4["drivers"]["local_explorer"]
    assert v5["frozen_machine"] == v4["frozen_machine"] and v5["dedicated_machine"] == v4["dedicated_machine"]
    assert v5["prompts"] == v4["prompts"] and v5["statement_footer"] == v4["statement_footer"]


def test_the_pilot_config_is_the_same_instrument_on_one_task_under_names_of_its_own():
    v5, pilot = _load(V5_PATH), lfr.load_campaign(PILOT_PATH)
    assert pilot["protocol"] == lfr.PROTOCOL_V5_PILOT != v5["protocol"] and "PILOT" in pilot["note"]
    assert pilot["exploration"]["comparison_tasks"] == [27] and pilot["exploration"]["comparison_task_set"] == (
        "pat-19-v5-pilot") != v5["exploration"]["comparison_task_set"]
    assert pilot["rules"]["exploration_comparison"]["tasks"] == 1
    assert pilot["envelope_recommended"]["compare_exploration"]["caps"]["cloud_executions"] == 16

    def bare(config):  # everything but the names, the notes, the task list and the pilot's own caps
        out = json.loads(json.dumps(config))
        for k in ("protocol", "note", "envelope_recommended"):
            out.pop(k)
        out["exploration"] = {k: v for k, v in out["exploration"].items() if not k.startswith(("comparison_task", "v5"))}
        out["rules"]["exploration_comparison"].pop("tasks")
        out["rules"]["exploration_comparison"].pop("note")
        return out
    assert bare(pilot) == bare(v5)


# ------------------------------------------------------------------------------------------- the loader pins

def test_the_loader_pins_the_v5_coordinates(tmp_path):
    for edit, message in (
            (lambda d: d["bounds"].update(explorer_max_steps=25), "explorer bounds"),
            (lambda d: d["bounds"].update(max_correction_rounds=3), "explorer bounds"),
            (lambda d: d["exploration"].update(fixed_candidate="x"), "declared candidate"),
            (lambda d: d["exploration"].update(one_task_per_launch=False), "one_task_per_launch true"),
            (lambda d: d["exploration"].update(comparison_tasks=TWELVE[::-1]), "comparison_tasks"),
            (lambda d: d["exploration"].update(comparison_tasks=TWELVE[:11]), "comparison_tasks"),
            (lambda d: d["exploration"].update(comparison_tasks=[27]), "comparison_tasks"),
            (lambda d: d["exploration"].update(comparison_task_set="pat-19-v5-other"), "comparison_task_set"),
            (lambda d: d["exploration"].update(comparison_task_set="comparison"), "plain identifier of its own"),
            (lambda d: d["exploration"].update(comparison_task_set="screening"), "plain identifier of its own"),
            (lambda d: d["exploration"].pop("comparison_task_set"), "go together"),
            (lambda d: d["exploration"].pop("comparison_tasks"), "go together"),
            (lambda d: d["exploration"].update(comparison_task_group="screening"), "replaces exploration.comparison"),
            (lambda d: d["correction_feedback"].update(max_failures=21), "correction_feedback"),
            (lambda d: d["correction_feedback"].update(max_message_chars=299), "correction_feedback"),
            (lambda d: d["correction_feedback"].update(max_name_chars=201), "correction_feedback"),
            (lambda d: d.pop("correction_feedback"), "correction_feedback"),
            (lambda d: d["isolation"].update(private_attempt_root=False), "private_attempt_root true"),
            (lambda d: d["isolation"].update({KEY: False}), "audit_revision 2 is accepted only together|requires"),
            (lambda d: d["isolation"].pop(KEY), "only together with isolation.cloud_native_sandbox"),
            (lambda d: d["isolation"].update(audit_revision=1), "needs isolation.cloud_native_sandbox true and"),
            (lambda d: d["isolation"].pop("audit_revision"), "needs isolation.cloud_native_sandbox true and"),
            (lambda d: d["isolation"].update(allow_read_home=[".config"]), "allows reading nothing of the home"),
            (lambda d: d["isolation"].update(allow_read_home=[GIT_IGNORE, ".ssh"]), "allows reading nothing"),
            (lambda d: d["isolation"].update(allow_read_home=["/etc/hosts"]), "relative home entries"),
            (lambda d: d["isolation"].update(allow_read_home=["../x"]), "relative home entries"),
            (lambda d: d["rules"]["exploration_comparison"].update(tasks=6), "exploration_comparison.tasks 12"),
            (lambda d: d["rules"]["exploration_comparison"].update(premium_per_accepted_ratio_max=0.9), "0.85"),
            (lambda d: d["drivers"]["cloud_reviewer"].pop("binary_version"), "pins Claude Code 2.1.285"),
            (lambda d: d["drivers"]["cloud_reviewer"]["binary_version"].update(version="2.1.290"), "pins Claude Code"),
            (lambda d: d["drivers"]["cloud_implementer_current"]["binary_version"].update(
                command=["claude", "-v"]), "pins Claude Code"),
            (lambda d: d["drivers"].update(cloud_explorer_economy={**d["drivers"]["cloud_reviewer"],
                                                                     "kind": "cloud_explorer"}), "no cloud explorer")):
        with pytest.raises(lfr.RunnerError, match=message):
            lfr.load_campaign(_broken(tmp_path, edit))
    lfr.load_campaign(_broken(tmp_path, lambda d: None))
    # the pilot pins its own task and label, and nothing of the campaign's
    for edit, message in ((lambda d: d["exploration"].update(comparison_tasks=TWELVE), "comparison_tasks \\[27\\]"),
                          (lambda d: d["exploration"].update(comparison_task_set=LABEL), "pat-19-v5-pilot")):
        with pytest.raises(lfr.RunnerError, match=message):
            lfr.load_campaign(_broken(tmp_path, edit, PILOT_PATH))
    # a v5 config needs the exploration schema
    with pytest.raises(lfr.RunnerError, match="protocol v5 requires the exploration schema"):
        lfr.load_campaign(_broken(tmp_path, lambda d: d.update(schema=lfr.CAMPAIGN_SCHEMA)))


def test_the_comparison_list_is_validated(tmp_path):
    for bad in ([], [27, 27], [True], ["27"], [0], [-3], 27, [27.0]):
        with pytest.raises(lfr.RunnerError, match="non-empty list of distinct PR numbers|comparison_tasks"):
            lfr.load_campaign(_broken(tmp_path, lambda d: d["exploration"].update(comparison_tasks=bad), PILOT_PATH))
    with pytest.raises(lfr.RunnerError, match="plain identifier"):
        lfr.load_campaign(_broken(tmp_path, lambda d: d["exploration"].update(comparison_task_set="a b"), PILOT_PATH))


def test_the_v4_keys_move_to_v5_and_the_list_keys_exist_only_there(tmp_path):
    spec = _load(QUALIFICATION / "pat-19-campaign-v3.json")["exploration"]["ground_truth"]
    (tmp_path / spec["file"]).write_bytes((QUALIFICATION / spec["file"]).read_bytes())
    for protocol in ("pat-19-protocol-v3", "pat-19-protocol-v4", "pat-19-protocol-v4x", "pat-19-protocol-v5-pilotx",
                     "other", None):
        for key, value in (("comparison_tasks", [1]), ("comparison_task_set", "x-set")):
            data = _load(QUALIFICATION / "pat-19-campaign-v3.json")
            data["exploration"][key] = value
            data.pop("protocol") if protocol is None else data.update(protocol=protocol)
            (tmp_path / "c.json").write_text(json.dumps(data), encoding="utf-8")
            with pytest.raises(lfr.RunnerError, match="accepted only under a protocol after v4"):
                lfr.load_campaign(tmp_path / "c.json")
    # the v4 keys stay refused before v4 (message unchanged), and a later protocol accepts them
    for edit in (lambda d: d["exploration"].update(fixed_candidate="qwen3.6-35b-a3b-mlx-4bit"),
                 lambda d: d.update(correction_feedback=FEEDBACK["correction_feedback"]),
                 lambda d: d["isolation"].update(private_attempt_root=True)):
        data = _load(QUALIFICATION / "pat-19-campaign-v3.json")
        edit(data)
        (tmp_path / "c.json").write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(lfr.RunnerError, match="accepted only under protocol pat-19-protocol-v4"):
            lfr.load_campaign(tmp_path / "c.json")
        data["protocol"] = "pat-19-protocol-v6"  # unpinned and after v4
        (tmp_path / "c.json").write_text(json.dumps(data), encoding="utf-8")
        assert lfr.load_campaign(tmp_path / "c.json")
    for protocol in ("pat-19-protocol-v5", "pat-19-protocol-v5-pilot"):
        assert lfr._PROTOCOL_AFTER_V4.fullmatch(protocol)


# ----------------------------------------------------------------------- task list, label, arms, pilot isolation

def test_the_explicit_list_is_resolved_in_order_and_refused_when_a_pr_is_unknown():
    manifest = {"comparison": [{"pr": 1}], "screening": [{"pr": 2}], "protected": [{"pr": 3}]}
    snap = {"prs": [{"pr": 1, "x": "a"}, {"pr": 2, "x": "b"}, {"pr": 3, "x": "c"}]}
    assert [t["x"] for t in lfr._listed_tasks(manifest, snap, [2, 1])] == ["b", "a"]
    for prs in ([2, 9], [3], [1, 3]):  # absent from the corpus groups, or from the snapshot
        with pytest.raises(lfr.RunnerError, match="not in the corpus manifest and the snapshot"):
            lfr._listed_tasks(manifest, snap, prs)
    with pytest.raises(lfr.RunnerError, match="not in the corpus"):
        lfr._listed_tasks(manifest, {"prs": [{"pr": 1}]}, [1, 2])


def _cli(tmp_path, monkeypatch, source, campaign_id, prs):
    from test_local_first_corpus import _make_repo
    repo, snap, _, _ = _make_repo(tmp_path)
    snap = {**snap, "prs": [{**snap["prs"][0], "pr": n} for n in prs]}
    manifest = {"comparison": [], "screening": [{"pr": n} for n in prs]}
    seen = {}
    monkeypatch.setattr(lfr.Runner, "compare_exploration", lambda self, tasks, cand, paths: seen.update(
        prs=[t["pr"] for t in tasks], paths=list(paths), label=self.compare_set))
    monkeypatch.setenv("HOME", str(tmp_path / "host-home"))
    (tmp_path / "c.json").write_text(json.dumps(_staged(tmp_path, source)), encoding="utf-8")
    for name, body in (("snap.json", snap), ("manifest.json", manifest)):
        (tmp_path / name).write_text(json.dumps(body), encoding="utf-8")
    (tmp_path / "envelope.json").write_text(json.dumps({
        "schema": lfr.ENVELOPE_SCHEMA, "campaign_id": campaign_id, "expires_on": "2026-12-31",
        "allowed_modes": ["compare_exploration"],
        "caps": {"cloud_executions": 10, "premium_tokens": 10**6, "wall_clock_seconds": 10**5}}), encoding="utf-8")
    argv = ["compare-exploration", "--campaign", str(tmp_path / "c.json"), "--dry-run",
            "--candidate", "qwen3.6-35b-a3b-mlx-4bit", "--envelope", str(tmp_path / "envelope.json"),
            "--state-dir", str(tmp_path / "st"), "--work-root", str(tmp_path / "work"), "--repo", str(repo),
            "--snapshot", str(tmp_path / "snap.json"), "--manifest", str(tmp_path / "manifest.json")]
    code = lfr.main(argv, today=__import__("datetime").date(2026, 10, 6))
    return code, seen


def test_the_cli_plays_the_pilot_list_with_arms_a_and_l_and_the_label_of_the_pilot(tmp_path, monkeypatch):
    code, seen = _cli(tmp_path, monkeypatch, PILOT_PATH, "pat-19-x5pilot-1", [27])
    assert code == 0 and seen == {"prs": [27], "paths": ["A", "L"], "label": "pat-19-v5-pilot"}


def test_the_cli_plays_the_twelve_tasks_in_the_listed_order(tmp_path, monkeypatch):
    code, seen = _cli(tmp_path, monkeypatch, V5_PATH, "pat-19-x5compare-1", TWELVE)
    assert code == 0 and seen == {"prs": TWELVE, "paths": ["A", "L"], "label": LABEL}


def test_the_pilot_and_the_campaign_cannot_share_a_campaign_id(tmp_path, monkeypatch, capsys):
    for source, campaign_id, message in ((PILOT_PATH, "pat-19-x5compare-1", "containing 'pilot'"),
                                         (V5_PATH, "pat-19-x5pilot-1", "NOT containing 'pilot'")):
        (tmp_path / campaign_id).mkdir()
        code, seen = _cli(tmp_path / campaign_id, monkeypatch, source, campaign_id, TWELVE if source == V5_PATH else [27])
        assert code == 2 and seen == {} and message in capsys.readouterr().err
        assert not (tmp_path / campaign_id / "st").exists() or not list((tmp_path / campaign_id / "st").glob("results-*"))


def test_the_label_replaces_comparison_in_every_record_and_the_report_reads_it(tmp_path):
    runner, campaign, _, tasks = make_runner(
        tmp_path, "compare_exploration", {**PLAN, "reviewer": ["PASS"]},
        campaign_over={"exploration": {"comparison_task_set": LABEL}})
    runner.compare_exploration(tasks, "cand-a", ("A", "L"))
    recs = [r for r in results(runner) if r.get("record_type") == "attempt"]
    assert recs and {r["task"]["set"] for r in recs} == {LABEL}
    assert {e.get("set") for e in ledger_of(runner) if e.get("kind") == "attempt_started"} == {LABEL}
    comparison = lfr.report(campaign, results(runner), ledger_of(runner))["exploration_comparison"]
    assert comparison["complete"] is True and comparison["arms"]["L"]["tasks_compared"] == 2
    assert comparison["arms"]["L"]["accepted"] == 2 and comparison["arms"]["L"]["reference_accepted"] == 2
    # the same records read under the default label are not the comparison: a campaign counts only its own set
    other = {**campaign, "exploration": {k: v for k, v in campaign["exploration"].items()
                                         if k != "comparison_task_set"}}
    assert lfr.report(other, results(runner), ledger_of(runner))["exploration_comparison"]["campaign_conclusion"] \
        == "incomplete_campaign"


def test_without_the_label_the_records_keep_the_comparison_set(tmp_path):
    runner, _, _, tasks = make_runner(tmp_path, "compare_exploration", PLAN)
    runner.compare_exploration(tasks[:1], "cand-a", ("A", "L"))
    assert {r["task"]["set"] for r in results(runner) if r.get("record_type") == "attempt"} == {"comparison"}
    assert runner.compare_set == lfr.COMPARISON_SET == "comparison"


def test_protocol_v5_and_its_pilot_refuse_arm_e_before_any_claim_or_spend(tmp_path):
    for protocol in lfr.V5_PROTOCOLS:
        runner, campaign, plan, tasks = make_runner(_sub(tmp_path, protocol), "compare_exploration", PLAN)
        runner.campaign = {**campaign, "protocol": protocol}
        for arms in (("A", "L", "E"), ("E",), ("A", "B")):
            with pytest.raises(lfr.RunnerError, match="protocol v5 has arms \\['A', 'L'\\] only"):
                runner.compare_exploration(tasks[:1], "cand-a", arms)
        assert counts(plan) == {} and not runner.results_path.exists()
        assert "cloud_started" not in ledger_kinds(runner)


def _runner_for(tmp_path, protocol, campaign_id):
    """``make_runner`` with a campaign that carries ``protocol`` from the start (the constructor reads it)."""
    from test_local_first_corpus import _make_repo
    from test_local_first_exploration_runner import TODAY, _envelope, facts_with, v2_campaign
    repo, snap, _, _ = _make_repo(tmp_path)
    campaign, _ = v2_campaign(tmp_path, PLAN)
    campaign["protocol"] = protocol
    envelope = lfr.load_envelope(_envelope(tmp_path, lfr.EXPLORE_MODES, campaign_id), "compare_exploration", TODAY)
    return lfr.Runner(repo=repo, campaign=campaign, envelope=envelope, state_dir=tmp_path / "state",
                      work_root=tmp_path / "work", mode="compare_exploration", dry_run=True, sandbox=False,
                      preflight_run=lambda cid: facts_with(campaign, campaign["candidates"][cid]["model"]),
                      disk_free_gib=lambda: 500.0, host_env=dict(os.environ), today=lambda: TODAY)


def test_the_runner_keeps_the_pilot_and_the_campaign_apart_by_campaign_id(tmp_path):
    cases = ((lfr.PROTOCOL_V5_PILOT, "pat-19-x5pilot-1", True), (lfr.PROTOCOL_V5_PILOT, "pat-19-x5compare-1", False),
             (lfr.PROTOCOL_V5, "pat-19-x5compare-1", True), (lfr.PROTOCOL_V5, "pat-19-x5pilot-1", False),
             ("pat-19-protocol-v4", "pat-19-x5pilot-1", True), (None, "pat-19-x4compare-1", True))
    for n, (protocol, campaign_id, accepted) in enumerate(cases):
        where = _sub(tmp_path, f"r{n}")
        if accepted:
            assert _runner_for(where, protocol, campaign_id).envelope["campaign_id"] == campaign_id
        else:
            with pytest.raises(lfr.RunnerError, match="the pilot is never mixed with the campaign"):
                _runner_for(where, protocol, campaign_id)
            assert not (where / "state").exists() or not list((where / "state").iterdir())


# ----------------------------------------------------------------------------- Claude Code version pin

PIN = {"command": ["claude", "--version"], "pattern": r"^(\d+\.\d+\.\d+) \(Claude Code\)$", "version": "2.1.285"}


def _stand_in(tmp_path, output, code=0):
    """A ``claude`` that only prints a version (and counts how often it is asked): never a real Claude Code."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "claude"
    script.write_text(f'#!/bin/sh\necho x >> "{tmp_path}/asked"\nprintf "%s\\n" "{output}"\nexit {code}\n', encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def _pinned_runner(tmp_path, bin_dir, pin=PIN):
    runner, campaign, plan, tasks = make_runner(tmp_path, "compare_exploration", PLAN)
    for name in ("cloud_implementer_current", "cloud_reviewer"):
        runner.campaign["drivers"][name]["binary_version"] = dict(pin) if pin else None
        if pin is None:
            runner.campaign["drivers"][name].pop("binary_version")
    runner.host_env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    return runner, plan, tasks


def test_the_pinned_claude_code_version_is_checked_once_before_the_first_claim(tmp_path):
    runner, plan, tasks = _pinned_runner(tmp_path, _stand_in(tmp_path, "2.1.285 (Claude Code)"))
    runner.compare_exploration(tasks[:1], "cand-a", ("A", "L"))
    assert (tmp_path / "asked").read_text().count("x") == 1  # one command, whatever the number of drivers and arms
    assert {r["path"] for r in results(runner)} >= {"A", "L"}


@pytest.mark.parametrize("output, code, message", [
    ("2.1.290 (Claude Code)", 0, "on PATH is 2.1.290, the pinned version is 2.1.285"),
    ("2.1.284 (Claude Code)", 0, "on PATH is 2.1.284"),
    ("claude 2.1.285", 0, "unreadable or unparsable"),
    ("", 0, "unreadable or unparsable"),
    ("2.1.285 (Claude Code)", 3, "unreadable or unparsable")])
def test_another_or_an_unreadable_claude_code_version_is_refused_before_any_claim_or_spend(tmp_path, output, code,
                                                                                         message):
    runner, plan, tasks = _pinned_runner(tmp_path, _stand_in(tmp_path, output, code))
    with pytest.raises(lfr.RunnerError, match=message):
        runner.compare_exploration(tasks[:1], "cand-a", ("A", "L"))
    assert counts(plan) == {} and not runner.results_path.exists()  # no arm ran, nothing was claimed or recorded
    assert "cloud_started" not in ledger_kinds(runner) and "preflight" not in ledger_kinds(runner)


def test_a_missing_claude_executable_is_refused_and_a_driver_without_the_pin_never_asks(tmp_path):
    runner, plan, tasks = _pinned_runner(tmp_path, tmp_path / "empty-bin")
    runner.host_env = {**os.environ, "PATH": str(tmp_path / "empty-bin")}
    with pytest.raises(lfr.RunnerError, match="claude not found on PATH"):
        runner.compare_exploration(tasks[:1], "cand-a", ("A", "L"))
    assert counts(plan) == {}
    free = _sub(tmp_path, "nopin")
    runner2, _, tasks2 = _pinned_runner(free, _stand_in(free, "9.9.9 (Claude Code)"), pin=None)
    runner2.compare_exploration(tasks2[:1], "cand-a", ("A", "L"))  # v1 to v4: no pin, nothing is asked
    assert not (free / "asked").exists() and {r["path"] for r in results(runner2)} >= {"A", "L"}


# -------------------------------------------------------------- the git excludes file (item 4 of the brief)

def _settings_of(tmp_path, monkeypatch, allow):
    runner, plan_path, tasks = _fake_runner(tmp_path, monkeypatch)
    runner.campaign["isolation"]["allow_read_home"] = list(allow)
    runner.compare_exploration(tasks[:1], "cand-a", ("A",))
    return runner, _native_records(plan_path)


def test_the_cloud_shell_may_read_the_one_git_excludes_file_and_nothing_else_of_the_home(tmp_path, monkeypatch):
    (tmp_path / "plain").mkdir()
    (tmp_path / "git").mkdir()
    base_runner, base = _settings_of(tmp_path / "plain", monkeypatch, [])
    runner, given = _settings_of(tmp_path / "git", monkeypatch, [GIT_IGNORE])
    home = os.path.realpath(tmp_path / "git" / "home")
    assert len(given) == len(base) == 2
    for with_file, without in zip(given, base):
        a, b = with_file["settings"], without["settings"]
        fs, fs0 = a["sandbox"]["filesystem"], b["sandbox"]["filesystem"]
        attempt = a["permissions"]["additionalDirectories"][0]
        assert fs["allowRead"] == [attempt, f"{home}/{GIT_IGNORE}"] and fs0["allowRead"] == [
            b["permissions"]["additionalDirectories"][0]]
        # the home is still denied to the shell, and the Read/Edit tools stay denied on it (and on the file)
        assert home in fs["denyRead"] and fs["denyRead"] == [x.replace(os.path.realpath(tmp_path / "plain"),
                                                                       os.path.realpath(tmp_path / "git"))
                                                             for x in fs0["denyRead"]]
        assert f"Read(//{home.lstrip('/')}/**)" in a["permissions"]["deny"]
        assert not any(GIT_IGNORE in r for r in a["permissions"]["allow"] + a["permissions"]["deny"])
        assert a["permissions"]["allow"] == [f"Read(//{attempt.lstrip('/')}/**)", f"Edit(//{attempt.lstrip('/')}/**)"]
        assert fs["allowWrite"] == [attempt]  # reading the file opens no write
    # the claude process keeps its identity: the allow-list of AGENTS.md R6 is untouched by the file
    for g in given:
        assert {"HOME", "FOUNDRY_DATA"} <= set(g["env"]) and g["mode"] == "dontAsk"


def test_the_local_profile_still_denies_the_config_directory_last(tmp_path):
    home = tmp_path / "home"
    ignore = home / GIT_IGNORE
    profile = lfr.sandbox_profile(writable=[tmp_path / "w"], deny_read=[home / ".config", home / ".ssh"],
                                  network="loopback", deny_home=home, allow_read=[ignore])
    lines = profile.splitlines()
    allow = next(i for i, x in enumerate(lines) if x.startswith("(allow file-read*") and "ignore" in x)
    deny = next(i for i, x in enumerate(lines) if x.startswith("(deny file-read*") and ".config" in x)
    assert deny > allow  # the last matching rule wins: a local arm still cannot read ~/.config


def test_allow_read_home_takes_a_single_relative_file_path_and_refuses_the_rest(tmp_path):
    for entry in (GIT_IGNORE, ".config/git"):
        data = _broken(tmp_path, lambda d: d["isolation"].update(allow_read_home=[entry]), V4_PATH)
        assert lfr.load_campaign(data)["isolation"]["allow_read_home"] == [entry]
    for entry in ("/etc/hosts", "../x", "", 3):
        with pytest.raises(lfr.RunnerError, match="relative home entries"):
            lfr.load_campaign(_broken(tmp_path, lambda d: d["isolation"].update(allow_read_home=[entry]), V4_PATH))


def test_the_settings_of_v4_style_configs_carry_no_extra_read(tmp_path, monkeypatch):
    _, given = _settings_of(tmp_path, monkeypatch, [])
    for g in given:
        assert g["settings"]["sandbox"]["filesystem"]["allowRead"] == [
            g["settings"]["permissions"]["additionalDirectories"][0]]


# ------------------------------------------------------------- twelve tasks, one per launch, in order, and report

TWELVE_PLAN = {**PLAN, "reviewer": ["PASS", "PASS", "garbage"] + ["PASS"]}  # the 3rd review: no verdict at all


def _twelve(task):
    return [{**task, "pr": n, "issue": f"PAT-{n}"} for n in TWELVE]


def test_twelve_tasks_play_one_per_launch_in_the_listed_order_and_the_report_reads_them(tmp_path, monkeypatch):
    over = {"exploration": {"comparison_task_set": LABEL, "one_task_per_launch": True},
            "isolation": {"private_attempt_root": True}}
    caps = {"cloud_executions": 200, "premium_tokens": 10**8}
    runner, campaign, plan, tasks = make_runner(tmp_path, "compare_exploration", TWELVE_PLAN, campaign_over=over,
                                                caps=caps)
    campaign["rules"]["exploration_comparison"]["tasks"] = 12
    listed = _twelve(tasks[0])
    played = []
    for launch in range(13):
        out = runner.compare_exploration(listed, "cand-a", ("A", "L"))
        played.append(sorted({r["task"]["pr"] for r in out}))
        if not runner.work_remains:
            break
        from test_local_first_exploration_runner import _resumed
        runner, campaign, plan, _ = _resumed(tmp_path, runner, listed, "compare_exploration", TWELVE_PLAN,
                                             campaign_over=over, caps=caps)
        campaign["rules"]["exploration_comparison"]["tasks"] = 12
    else:
        raise AssertionError("the loop does not end")
    assert played == [[n] for n in TWELVE]  # one task per launch, in the order of the list, none twice
    recs = [r for r in results(runner) if r.get("record_type") == "attempt"]
    assert {r["task"]["set"] for r in recs} == {LABEL}
    report = lfr.report(campaign, results(runner), ledger_of(runner))
    comparison = report["exploration_comparison"]
    assert report["promotion"] is False and comparison["complete"] is True
    assert comparison["arms"]["L"]["tasks_compared"] == 12
    # the third review (task 38, arm A) gave no verdict: undecided on its record, never a verdict
    unreadable = [r for r in recs if r.get("outcome") == "review_unreadable"]
    assert [(r["path"], r["task"]["pr"], r["accepted"]) for r in unreadable] == [("A", 38, None)]
    # an undecided task is not a failed one, and an economy verdict is never given over an undecided task
    assert comparison["arms"]["L"]["economy"] == "unavailable"
    assert comparison["decision"] != "retained" and comparison["campaign_conclusion"] == (
        "keep_cloud_insufficient_evidence")
    assert "confined" not in json.dumps(report)  # nothing reads as "confined"


# ------------------------------------------------------------------------------------- the operator script

SCRIPT = QUALIFICATION / "pat-19-v5-operator.sh"


def _operator(tmp_path, mode, *, campaign_id, claude="2.1.285 (Claude Code)", state_files=()):
    """Run the script with a stand-in ``claude`` and ``lms`` on PATH (the stand-in ``lms`` only logs and fails):
    nothing real is started, and the script never reaches the launcher."""
    import shutil
    import subprocess
    checkout, runs = tmp_path / "checkout", tmp_path / "runs"
    qual = checkout / "plugins" / "foundry" / "docs" / "qualification"
    qual.mkdir(parents=True)
    for name in ("pat-19-campaign-v5.json", "pat-19-campaign-v5-pilot.json", "pat-19-corpus-snapshot-v1.json",
                 "pat-19-corpus-manifest-v1.json"):
        (qual / name).symlink_to(QUALIFICATION / name)
    (runs / "state").mkdir(parents=True)
    for name in state_files:
        (runs / "state" / name).write_text("", encoding="utf-8")
    (runs / "envelope.json").write_text(json.dumps({"campaign_id": campaign_id}), encoding="utf-8")
    bin_dir = tmp_path / "stand-in"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(f'#!/bin/sh\nprintf "%s\\n" "{claude}"\n', encoding="utf-8")
    (bin_dir / "lms").write_text(f'#!/bin/sh\necho "$@" >> "{tmp_path}/lms.log"\nexit 7\n', encoding="utf-8")
    for tool in bin_dir.iterdir():
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "HOME": str(tmp_path / "home")}
    done = subprocess.run([shutil.which("bash"), str(SCRIPT), mode, "qwen3.6-35b-a3b-mlx-4bit", str(checkout),
                           str(runs), str(tmp_path / "work"), str(tmp_path / "repo")],
                          capture_output=True, text=True, env=env)
    lms = tmp_path / "lms.log"
    return done, (lms.read_text() if lms.exists() else "")


def test_the_operator_script_is_valid_and_refuses_a_bad_call_before_anything_runs():
    import shutil
    import subprocess
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash available")
    text = SCRIPT.read_text("utf-8")
    assert "set -euo pipefail" in text and "lms unload --all" in text and "< /dev/null" in text
    assert "--paths A,L" in text and "pat-19-campaign-v5-pilot.json" in text and "pat-19-campaign-v4" not in text
    assert subprocess.run([bash, "-n", str(SCRIPT)], capture_output=True).returncode == 0
    assert os.access(SCRIPT, os.X_OK)
    for args in (["nope"], [], ["compare"], ["pilot", "a", "b"]):
        assert subprocess.run([bash, str(SCRIPT), *args], capture_output=True).returncode == 64


def test_the_operator_script_keeps_the_pilot_and_the_campaign_apart_and_checks_claude_before_any_model(tmp_path):
    cases = (
        ("pilot", "pat-19-x5compare-1", {}, "must contain 'pilot'"),
        ("compare", "pat-19-x5pilot-1", {}, "must not contain 'pilot'"),
        ("pilot", "pat-19-x5pilot-1", {"state_files": ["results-pat-19-x5compare-1.jsonl"]}, "is not a pilot file"),
        ("compare", "pat-19-x5compare-1", {"state_files": ["ledger-pat-19-x5pilot-1.jsonl"]}, "is a pilot file"),
        ("compare", "pat-19-x5compare-1", {"claude": "2.1.290 (Claude Code)"}, "config pins 2.1.285"),
        ("pilot", "pat-19-x5pilot-1", {"claude": ""}, "'unreadable', the config pins 2.1.285"))
    for n, (mode, campaign_id, extra, message) in enumerate(cases):
        where = _sub(tmp_path, f"c{n}")
        done, lms = _operator(where, mode, campaign_id=campaign_id, **extra)
        assert done.returncode == 65 and message in done.stderr, (n, done.stderr)
        assert lms == "" and not list((where / "runs").glob("operator-*"))  # no model touched, nothing logged
    for n, (mode, campaign_id) in enumerate((("pilot", "pat-19-x5pilot-1"), ("compare", "pat-19-x5compare-1"))):
        done, lms = _operator(_sub(tmp_path, f"ok{n}"), mode, campaign_id=campaign_id)
        assert lms.startswith("unload --all") and done.returncode == 7  # past every check, stopped by the stand-in lms


# ------------------------------------------------------------ a judge that cannot run is not a verdict (pilot 1)

def _broken_python(tmp_path):
    """An interpreter that cannot import pytest (the Homebrew python3 of the void pilot)."""
    script = tmp_path / "broken-python"
    script.write_text('#!/bin/sh\necho "No module named pytest" >&2\nexit 1\n', encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_a_judge_that_cannot_run_pytest_is_an_instrument_error_under_v5_and_the_old_verdict_before(tmp_path,
                                                                                                   monkeypatch):
    from foundry import local_first_corpus as lfc
    from test_local_first_corpus import _make_repo
    repo, snap, _, _ = _make_repo(tmp_path)
    task = snap["prs"][0]
    ok = lfc.judge(repo, task, lfc.build_bundle(repo, task, tmp_path / "ok"), strict_report=True)
    assert ok["verdict"] == "REFUSED" and ok["failed"] + ok["errors"] >= 1  # a real refusal is still a verdict
    monkeypatch.setattr(lfc.sys, "executable", _broken_python(tmp_path))
    # v1 to v4 (strict off, the default): the verdict that exists in their records, unchanged
    old = lfc.judge(repo, task, lfc.build_bundle(repo, task, tmp_path / "old"))
    assert (old["verdict"], old["passed"], old["failed"], old["errors"]) == ("REFUSED", 0, 0, 0)
    with pytest.raises(lfc.JudgeInstrumentError, match="No module named pytest"):
        lfc.judge(repo, task, lfc.build_bundle(repo, task, tmp_path / "new"), strict_report=True)


def test_the_runner_records_an_unrunnable_judge_as_a_tool_error_never_a_refusal(tmp_path, monkeypatch):
    from foundry import local_first_corpus as lfc
    runner, _, plan, tasks = make_runner(tmp_path, "compare_exploration", PLAN)
    runner.strict_judge = True
    runner._check_interpreters = lambda: None  # the preflight would refuse first (tested below): reach the judge
    monkeypatch.setattr(lfc.sys, "executable", _broken_python(tmp_path))
    with pytest.raises(lfc.CorpusError, match="judge cannot run"):
        runner.compare_exploration(tasks[:1], "cand-a", ("A",))
    recs = [r for r in results(runner) if r.get("record_type") == "attempt"]
    assert [r["outcome"] for r in recs] == ["tool_error"] and recs[0]["accepted"] is None
    assert not recs[0].get("judge")  # no verdict, so no corrector was ever told "failed 0"
    assert counts(plan) == {"implementer": 1}  # no correction round
    # the same unrunnable judge under a v1 to v4 campaign keeps its old behaviour (a 0/0/0 refusal)
    other, _, _, tasks2 = make_runner(_sub(tmp_path, "old"), "compare_exploration", PLAN)
    other.compare_exploration(tasks2[:1], "cand-a", ("A",))
    assert [r["outcome"] for r in results(other) if r.get("path") == "A"][0] == "judge_refused"


# --------------------------------------------------------------------------------- interpreter preflight

def _fake_python(directory, name, *, works, version="8.3.0"):
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / name
    script.write_text(f'#!/bin/sh\n{"echo " + version if works else "echo nope >&2; exit 1"}\n', encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)


def _strict_runner(tmp_path, bin_dir):
    runner, campaign, plan, tasks = make_runner(tmp_path, "compare_exploration", PLAN)
    runner.strict_judge = True
    runner.host_env = {**os.environ, "HOME": str(tmp_path / "home"), "PATH": f"{bin_dir}"}
    return runner, plan, tasks


def test_the_probe_names_the_interpreter_that_cannot_import_pytest_and_masks_the_home(tmp_path):
    home = tmp_path / "home"
    _fake_python(home / "bin", "python3", works=False)
    _fake_python(home / "bin", "python", works=True)
    driver = {"kind": "cloud_implementer", "home": "real"}
    out = lfr.probe_interpreters({"HOME": str(home), "PATH": str(home / "bin")}, driver)
    assert out["refusals"] == ["arm_python3_cannot_import_pytest:~/bin/python3"]
    assert out["arm_python3"] == {"path": "~/bin/python3", "pytest": None}
    assert out["arm_python"] == {"path": "~/bin/python", "pytest": "8.3.0"} and out["launcher"]["pytest"]
    assert str(home) not in json.dumps(out)
    gone = lfr.probe_interpreters({"HOME": str(home), "PATH": str(tmp_path / "nothing")}, driver)
    assert gone["refusals"] == ["arm_python3_not_found_on_the_arm_path", "arm_python_not_found_on_the_arm_path"]


def test_a_launcher_interpreter_without_pytest_is_named(tmp_path, monkeypatch):
    monkeypatch.setattr(lfr.sys, "executable", _broken_python(tmp_path))
    out = lfr.probe_interpreters({"HOME": str(tmp_path)}, None)
    assert out["refusals"] and out["refusals"][0].startswith("launcher_python_cannot_import_pytest:")


def test_a_broken_arm_interpreter_refuses_before_any_claim_model_or_cloud_reservation(tmp_path):
    bin_dir = tmp_path / "bin"
    _fake_python(bin_dir, "python3", works=False)
    _fake_python(bin_dir, "python", works=True)
    runner, plan, tasks = _strict_runner(tmp_path, bin_dir)
    with pytest.raises(lfr.PreflightRefused, match="arm_python3_cannot_import_pytest"):
        runner.compare_exploration(tasks[:1], "cand-a", ("A", "L"))
    assert counts(plan) == {} and not runner.results_path.exists()
    kinds = ledger_kinds(runner)
    assert "cloud_started" not in kinds and "attempt_started" not in kinds
    entry = [e for e in ledger_of(runner) if e["kind"] == "preflight"][0]
    assert entry["ok"] is False and entry["refusals"][0].startswith("arm_python3_cannot_import_pytest")
    assert entry["interpreters"]["arm_python"]["pytest"] == "8.3.0"


def test_working_interpreters_are_recorded_in_the_preflight_entry_and_old_protocols_are_not_probed(tmp_path):
    bin_dir = tmp_path / "bin"
    _fake_python(bin_dir, "python3", works=True, version="8.3.1")
    _fake_python(bin_dir, "python", works=True, version="8.3.2")
    runner, plan, tasks = _strict_runner(tmp_path, bin_dir)
    runner.compare_exploration(tasks[:1], "cand-a", ("A", "L"))
    entries = [e for e in ledger_of(runner) if e["kind"] == "preflight" and e["ok"]]
    probe = entries[0]["interpreters"]
    assert probe["arm_python3"]["pytest"] == "8.3.1" and probe["arm_python"]["pytest"] == "8.3.2"
    assert probe["refusals"] == [] and str(tmp_path / "home") not in json.dumps(probe)
    old, _, _, tasks2 = make_runner(_sub(tmp_path, "old"), "compare_exploration", PLAN)
    old.host_env = {**os.environ, "PATH": str(tmp_path / "nothing")}  # would fail the probe; v1 to v4 never run it
    old.compare_exploration(tasks2[:1], "cand-a", ("A",))
    assert all("interpreters" not in e for e in ledger_of(old))


# ------------------------------------------------------------------------------ the offline golden self-check

def test_the_golden_check_passes_on_a_working_judge_and_fails_on_one_that_cannot_run(tmp_path, monkeypatch):
    import datetime
    from test_local_first_corpus import _make_repo
    from test_local_first_exploration_runner import v2_campaign
    repo, snap, _, _ = _make_repo(tmp_path)
    campaign, _ = v2_campaign(tmp_path, PLAN)
    campaign["protocol"] = "pat-19-protocol-v6"  # unpinned, after v4: the strict judge
    task = snap["prs"][0]
    day = datetime.date(2026, 10, 6)
    out = lfr.golden_check(campaign, tmp_path / "c.json", [task], repo=repo, work_root=tmp_path / "w1", today=day)
    row = out["tasks"][0]
    assert out["all_ok"] is True and row["ok"] and row["untouched"]["verdict"] == "REFUSED"
    assert row["untouched"]["failed"] + row["untouched"]["errors"] >= 1 and row["solved"]["verdict"] == "ACCEPTED"
    assert not list((tmp_path / "w1").iterdir())  # throwaway bundles are gone
    monkeypatch.setattr(lfr.lfc.sys, "executable", _broken_python(tmp_path))
    bad = lfr.golden_check(campaign, tmp_path / "c.json", [task], repo=repo, work_root=tmp_path / "w2", today=day)
    assert bad["all_ok"] is False and "judge cannot run" in bad["tasks"][0]["untouched"]["instrument_error"]
    campaign.pop("protocol")  # even a v1 to v4 judge: 0/0/0 is never the expected refusal
    old = lfr.golden_check(campaign, tmp_path / "c.json", [task], repo=repo, work_root=tmp_path / "w3", today=day)
    assert old["all_ok"] is False and old["tasks"][0]["untouched"]["failed"] == 0


def test_the_golden_verb_writes_its_result_once_and_never_overwrites(tmp_path, monkeypatch, capsys):
    import datetime
    from test_local_first_corpus import _make_repo
    repo, snap, _, _ = _make_repo(tmp_path)
    snap = {**snap, "prs": [{**snap["prs"][0], "pr": 27}]}
    (tmp_path / "c.json").write_text(json.dumps(_staged(tmp_path, PILOT_PATH)), encoding="utf-8")
    (tmp_path / "snap.json").write_text(json.dumps(snap), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"comparison": [], "screening": [{"pr": 27}]}), encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path / "host-home"))
    argv = ["golden-check", "--campaign", str(tmp_path / "c.json"), "--repo", str(repo),
            "--work-root", str(tmp_path / "work"), "--snapshot", str(tmp_path / "snap.json"),
            "--manifest", str(tmp_path / "manifest.json"), "--out", str(tmp_path / "golden.json")]
    day = datetime.date(2026, 10, 6)
    assert lfr.main(argv, today=day) == 0 and "PR 27: untouched REFUSED" in capsys.readouterr().out
    result = json.loads((tmp_path / "golden.json").read_text("utf-8"))
    assert result["all_ok"] is True and result["protocol"] == "pat-19-protocol-v5-pilot"
    assert lfr.main(argv, today=day) == 2 and "never overwritten" in capsys.readouterr().err
    assert json.loads((tmp_path / "golden.json").read_text("utf-8")) == result


def test_the_pilot_ids_of_a_second_pilot_are_accepted_by_the_loader_and_the_script_checks(tmp_path):
    for n in (1, 2):
        runner = _runner_for(_sub(tmp_path, f"p{n}"), lfr.PROTOCOL_V5_PILOT, f"pat-19-x5pilot-{n}")
        assert runner.envelope["campaign_id"] == f"pat-19-x5pilot-{n}"
    done, lms = _operator(_sub(tmp_path, "s2"), "pilot", campaign_id="pat-19-x5pilot-2")
    assert lms.startswith("unload --all") and done.returncode == 7


def test_the_operator_script_refuses_a_python3_without_pytest_before_any_model(tmp_path):
    import shutil
    import subprocess
    import sys
    where = _sub(tmp_path, "nopytest")
    # a ``python3`` that behaves as the real one except that it cannot import pytest
    qual, runs = where / "checkout" / "plugins" / "foundry" / "docs" / "qualification", where / "runs"
    qual.mkdir(parents=True)
    for name in ("pat-19-campaign-v5.json", "pat-19-campaign-v5-pilot.json", "pat-19-corpus-snapshot-v1.json",
                 "pat-19-corpus-manifest-v1.json"):
        (qual / name).symlink_to(QUALIFICATION / name)
    (runs / "state").mkdir(parents=True)
    (runs / "envelope.json").write_text(json.dumps({"campaign_id": "pat-19-x5pilot-2"}), encoding="utf-8")
    bin_dir = where / "stand-in"
    bin_dir.mkdir()
    (bin_dir / "python3").write_text(
        f'#!/bin/sh\ncase "$*" in *"import pytest"*) echo "No module named pytest" >&2; exit 1;; esac\n'
        f'exec {sys.executable} "$@"\n', encoding="utf-8")
    (bin_dir / "lms").write_text(f'#!/bin/sh\necho "$@" >> "{where}/lms.log"\nexit 7\n', encoding="utf-8")
    for tool in bin_dir.iterdir():
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    done = subprocess.run([shutil.which("bash"), str(SCRIPT), "pilot", "qwen3.6-35b-a3b-mlx-4bit",
                           str(where / "checkout"), str(runs), str(where / "work"), str(where / "repo")],
                          capture_output=True, text=True,
                          env={"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "HOME": str(where / "home")})
    assert done.returncode == 65 and "cannot import pytest" in done.stderr and "clean shell" in done.stderr
    assert not (where / "lms.log").exists() and not list(runs.glob("operator-*"))


# ------------------------------------------------------- audit policy: the OS is the barrier, the audit a journal

POLICY = lfr.AUDIT_POLICY
REFUSAL = ("Permission to use Bash has been denied because Claude Code is running in don't ask mode. IMPORTANT: "
           "You *may* attempt to accomplish this action using other tools.")


class _Zone:
    """work/private-a/a/{bundle,scratch}; the OS denies the home and the work root to the shell, re-allows the attempt;
    ``other`` is a sensitive place the OS does NOT deny."""

    def __init__(self, tmp_path):
        self.work = tmp_path / "work"
        self.attempt = self.work / "private-a" / "a"
        self.bundle, self.scratch = self.attempt / "bundle", self.attempt / "scratch"
        self.home, self.other = tmp_path / "home", tmp_path / "other"
        for d in (self.bundle, self.scratch, self.home / ".config" / "foundry", self.other, self.work / "private-b"):
            d.mkdir(parents=True, exist_ok=True)
        self.tmp = tmp_path
        self.denied = ([self.home, self.work], [self.attempt])

    def audit(self, tmp_path, calls, *, policy=True, version="2.1.285"):
        """``calls``: ``(tool, input, result_text_or_None, is_error)`` -> (decisive, journal, refused)."""
        lines = [{"type": "system", "subtype": "init", "claude_code_version": version, "tools": ["Bash"]}]
        for n, (tool, args, text, is_error) in enumerate(calls):
            lines.append({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": f"t{n}", "name": tool, "input": args}]}})
            if text is not None:
                lines.append({"type": "user", "message": {"content": [
                    {"type": "tool_result", "tool_use_id": f"t{n}", "content": text, "is_error": is_error}]},
                    "tool_use_result": {"interrupted": False}})
        stream = tmp_path / "s.jsonl"
        stream.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
        journal, refused = [], {}
        extra = {"policy_denied": self.denied, "journal": journal, "refused": refused} if policy else {}
        out = lfr.audit_transcript(
            stream, bundle=self.bundle, scratch=self.scratch,
            sensitive=[self.home / ".config" / "foundry", self.work, self.other], home=str(self.home),
            attempt_dir=self.attempt, private_root=self.attempt.parent, revision=lfr.AUDIT_REVISION, **extra)
        return out, journal, refused


def test_a_call_the_host_refused_did_not_run_and_contributes_nothing(tmp_path):
    zone = _Zone(tmp_path)
    call = [("Bash", {"command": f"cat {zone.home}/.config/foundry/registry.json"}, REFUSAL, True)]
    strict, _, _ = zone.audit(tmp_path, call, policy=False)
    assert strict  # revision 2 alone counts it as if it had run
    out, journal, refused = zone.audit(tmp_path, call)
    assert out == [] and journal == [] and refused == {"Bash": 1}  # counted per tool, no text
    # the permission-rule shapes of a file tool are whole-call refusals too
    for text in ("Read denied by your permission settings", "Path blocks reads outside the working directories"):
        out, _, refused = zone.audit(tmp_path, [("Read", {"file_path": f"{zone.work}/private-b/x"}, text, True)])
        assert out == [] and refused == {"Read": 1}
    # a refused call moves no directory: the relative path that follows is read from the bundle, as before it
    moved = [("Bash", {"command": f"cd {zone.other}"}, REFUSAL, True), ("Bash", {"command": "ls src"}, "ok", False)]
    assert zone.audit(tmp_path, moved, policy=False)[0] and zone.audit(tmp_path, moved)[0] == []


def test_only_whole_call_refusals_are_recognised(tmp_path):
    zone = _Zone(tmp_path)
    cmd = {"command": f"cat {zone.home}/.config/foundry/x"}
    for text, is_error in (("cat: x: Operation not permitted", True),  # the OS inside a command that ran
                           (REFUSAL, False),  # the words in a successful result are not a refusal
                           ("Exit code 1\n" + REFUSAL, True), ("anything else failed", True), (None, None)):
        out, journal, refused = zone.audit(tmp_path, [("Bash", cmd, text, is_error)])
        assert refused == {}, text  # nothing is called refused: the call may have run
        assert out == [] and journal, text  # ... and its finding is under a root the OS denies: journaled


def test_a_shell_finding_under_an_os_denied_root_and_the_unknown_directory_are_journaled(tmp_path):
    zone = _Zone(tmp_path)
    calls = [("Bash", {"command": f"ls {zone.work}/private-b; cat ~/.config/foundry/x"}, "ok", False),
             ("Bash", {"command": 'T=$(mktemp -d); cd $T; cat notes'}, "ok", False)]
    strict, _, _ = zone.audit(tmp_path, calls, policy=False)
    out, journal, _ = zone.audit(tmp_path, calls)
    assert out == [] and set(journal) == set(strict) and "/<unknown-working-directory>" in journal
    assert any(j.endswith("private-b") for j in journal) and any(".config/foundry" in j for j in journal)


def test_every_other_finding_stays_decisive(tmp_path):
    zone = _Zone(tmp_path)
    # a path the OS does not deny (a sensitive place outside the denyRead list)
    out, journal, _ = zone.audit(tmp_path, [("Bash", {"command": f"cat {zone.other}/x"}, "ok", False)])
    assert [p for p in out if str(zone.other.name) in p] and journal == []
    # a FILE-TOOL finding, even under a root the OS denies to the shell
    out, journal, _ = zone.audit(tmp_path, [("Read", {"file_path": f"{zone.work}/private-b/x"}, "text", False)])
    assert any("private-b" in p for p in out) and journal == []
    # the same path named by a shell command AND by a file tool is decisive
    both = [("Bash", {"command": f"ls {zone.work}/private-b"}, "ok", False),
            ("Read", {"file_path": f"{zone.work}/private-b"}, "text", False)]
    assert zone.audit(tmp_path, both)[0] and zone.audit(tmp_path, both)[1] == []
    # a forbidden command, and a sensitive path shown by a tool result
    out, _, _ = zone.audit(tmp_path, [("Bash", {"command": "git push origin main"}, "ok", False)])
    assert "command:git push" in out
    shown = [("Bash", {"command": "echo hi"}, f"see {zone.other}/secret.txt", False)]
    assert any(p.startswith("tool_result:") for p in zone.audit(tmp_path, shown)[0])


def test_without_the_policy_the_audit_returns_exactly_what_it_returned(tmp_path):
    zone = _Zone(tmp_path)
    calls = [("Bash", {"command": f"ls {zone.work}/private-b; cat {zone.other}/x"}, "ok", False),
             ("Bash", {"command": f"cat {zone.home}/.config/foundry/x"}, REFUSAL, True)]
    assert zone.audit(tmp_path, calls, policy=False)[0] == zone.audit(tmp_path, calls, policy=False)[0]
    plain = zone.audit(tmp_path, calls, policy=False)[0]
    assert any("private-b" in p for p in plain) and any(".config/foundry" in p for p in plain)  # both counted
    # a stream of another version has no carried directory: the policy still only moves what the OS denies
    odd, journal, _ = zone.audit(tmp_path, calls, version="9.9.9")
    assert any("other" in p for p in odd) and journal


def _policy_runner(tmp_path, monkeypatch, plan, *, version=lfr.OBSERVED_CLAUDE_CODE[0], policy=True):
    from test_local_first_native_sandbox import _fake_runner as fake
    runner, plan_path, tasks = fake(tmp_path, monkeypatch, version=version, plan=plan)
    if policy:
        runner.campaign["isolation"][lfr.AUDIT_POLICY_KEY] = POLICY
        runner.audit_policy = True
    return runner, tasks


CLIMB = {**PLAN, "implementer": ["cmd:ls ../../.."], "reviewer": ["PASS"]}
REVIEWER_CLIMBS = {**PLAN, "implementer": ["fix"], "reviewer": ["cmd:ls ../../.."]}


def _cloud(runner):
    return [r for r in results(runner) if r.get("path") == "A" and r.get("segment") == "cloud"]


def test_under_the_policy_and_an_observed_barrier_a_climb_to_the_work_root_is_journaled_not_contamination(
        tmp_path, monkeypatch):
    runner, tasks = _policy_runner(tmp_path, monkeypatch, CLIMB)
    runner.compare_exploration(tasks[:1], "cand-a", ("A",))
    rec = _cloud(runner)[0]
    assert "contaminated" not in rec and rec["outcome"] == "accepted"
    audit = rec["audit"]
    assert audit["barrier"] == lfr.BARRIER_OBSERVED and audit["policy"] == POLICY
    assert audit["journal"] and audit["journal_by_role"]["arm"] == len(audit["journal"]) and audit["refused_calls"] == {}
    assert str(tmp_path) not in json.dumps(audit["policy"])
    report = lfr.report(runner.campaign, results(runner), ledger_of(runner))
    assert report["contaminated"] == [] and report["audit_journal"]["journal_paths_by_arm"] == {
        "A": len(audit["journal"])}
    assert report["audit_journal"]["host_refused_calls_total"] == 0


def test_without_an_observed_barrier_or_without_the_key_the_audit_is_strict(tmp_path, monkeypatch):
    for n, (kwargs, why) in enumerate((({"version": None}, "settings only"), ({"policy": False}, "no key"))):
        runner, tasks = _policy_runner(_sub(tmp_path, f"s{n}"), monkeypatch, CLIMB, **kwargs)
        runner.compare_exploration(tasks[:1], "cand-a", ("A",))
        rec = _cloud(runner)[0]
        assert rec["outcome"] == "contaminated" and rec["contaminated"] is True, why
        if kwargs.get("policy") is False:
            assert "journal" not in rec["audit"] and "policy" not in rec["audit"], why  # the record shape is the old one
        else:  # the key is on but the barrier is not observed: strict, and the journal stays empty
            assert rec["audit"]["journal"] == [] and rec["audit"]["refused_calls"] == {}, why
        has = "audit_journal" in lfr.report(runner.campaign, results(runner), ledger_of(runner))
        assert has is (kwargs.get("policy") is not False), why


def test_a_reviewer_with_a_journal_only_finding_keeps_its_verdict_one_with_a_decisive_finding_does_not(
        tmp_path, monkeypatch):
    runner, tasks = _policy_runner(tmp_path, monkeypatch, REVIEWER_CLIMBS)
    runner.compare_exploration(tasks[:1], "cand-a", ("A",))
    rec = _cloud(runner)[0]
    assert rec["outcome"] == "accepted" and rec["accepted"] is True and "contamination" not in rec["review"]
    assert rec["audit"]["journal_by_role"]["reviewer"] >= 1
    assert lfr.report(runner.campaign, results(runner), ledger_of(runner))["audit_journal"][
        "journal_paths_by_role"]["reviewer"] >= 1
    # the same reviewer under the strict audit is unreadable (this is what pilot 2 lost)
    strict, tasks2 = _policy_runner(_sub(tmp_path, "strict"), monkeypatch, REVIEWER_CLIMBS, policy=False)
    strict.compare_exploration(tasks2[:1], "cand-a", ("A",))
    assert _cloud(strict)[0]["outcome"] == "review_unreadable"
    # a reviewer that READS the work root with its file tool: decisive, whatever the policy
    target = tmp_path / "peek" / "x" / "work" / "private-zz"  # the work root of the runner below
    again, tasks4 = _policy_runner(_sub(tmp_path, "peek"), monkeypatch, {**PLAN, "implementer": ["fix"],
                                                                       "reviewer": [f"peek:{target}"]})
    again.compare_exploration(tasks4[:1], "cand-a", ("A",))
    rec = _cloud(again)[0]
    assert rec["outcome"] == "review_unreadable" and rec["review"]["contamination"]["paths"]
    assert rec["audit"]["journal"] == [] or all("private-zz" not in j for j in rec["audit"]["journal"])


def test_the_policy_key_is_pinned_and_refused_where_it_cannot_hold(tmp_path):
    for path, protocol in ((V5_PATH, lfr.PROTOCOL_V5), (PILOT_PATH, lfr.PROTOCOL_V5_PILOT)):
        data = _load(path)
        assert data["isolation"][lfr.AUDIT_POLICY_KEY] == POLICY and data["protocol"] == protocol
        with pytest.raises(lfr.RunnerError, match="requires isolation.audit_policy"):
            lfr.load_campaign(_broken(tmp_path, lambda d: d["isolation"].pop(lfr.AUDIT_POLICY_KEY), path))
    with pytest.raises(lfr.RunnerError, match="is 'journal_under_observed_barrier' or absent"):
        lfr.load_campaign(_broken(tmp_path, lambda d: d["isolation"].update(audit_policy="journal_all")))
    with pytest.raises(lfr.RunnerError, match="needs isolation.cloud_native_sandbox true"):
        lfr.load_campaign(_broken(tmp_path, lambda d: d["isolation"].update(audit_revision=1, cloud_native_sandbox=True)))
    # a later unpinned protocol: accepted with the barrier, refused without it; before v5: refused
    spec = _load(QUALIFICATION / "pat-19-campaign-v4.json")["exploration"]["ground_truth"]
    (tmp_path / spec["file"]).write_bytes((QUALIFICATION / spec["file"]).read_bytes())
    for n, (protocol, iso, message) in enumerate((
            ("pat-19-protocol-v6", {"cloud_native_sandbox": True, "audit_revision": 2}, None),
            ("pat-19-protocol-v6", {}, "needs isolation.cloud_native_sandbox true"),
            ("pat-19-protocol-v4", {}, "accepted only under a protocol after v4"),
            (None, {}, "accepted only under"))):
        data = _load(V4_PATH)
        data["isolation"].update(audit_policy=POLICY, **iso)
        data.pop("protocol") if protocol is None else data.update(protocol=protocol)
        (tmp_path / "x.json").write_text(json.dumps(data), encoding="utf-8")
        if message is None:
            assert lfr.load_campaign(tmp_path / "x.json")["isolation"]["audit_policy"] == POLICY
        else:
            with pytest.raises(lfr.RunnerError, match=message):
                lfr.load_campaign(tmp_path / "x.json")
    for v in (1, 2, 3):  # the frozen configs never carry it
        assert lfr.AUDIT_POLICY_KEY not in _load(QUALIFICATION / f"pat-19-campaign-v{v}.json").get("isolation", {})


def test_the_replay_reads_the_policy_per_stream_and_never_recomputes_an_outcome():
    rec = {"outcome": "review_unreadable", "review": {"verdicts": ["PASS"]}}
    streams = [{"role": "arm", "stream": "a", "new": [], "policy": {"decisive": [], "journal": [], "refused_calls": {}}},
               {"role": "reviewer", "stream": "r", "new": ["/x/scratch"],
                "policy": {"decisive": [], "journal": ["/x/scratch"], "refused_calls": {"Bash": 1}}}]
    out = lfr._policy_reading(rec, streams)
    assert [s["class"] for s in out["streams"]] == ["clean", "journal_only"]
    assert out["outcome_recorded"] == "review_unreadable" and out["outcome_reading_non_decisional"] == "accepted"
    streams[1]["policy"] = {"decisive": ["/x/scratch"], "journal": [], "refused_calls": {}}
    out = lfr._policy_reading(rec, streams)
    assert out["streams"][1]["class"] == "decisive_kept" and out["outcome_reading_non_decisional"] == "review_unreadable"
    streams[1]["policy"] = {"decisive": [], "journal": [], "refused_calls": {"Bash": 1}}
    assert lfr._policy_reading(rec, streams)["streams"][1]["class"] == "refused_calls_only"


def test_a_path_echoed_in_an_output_under_an_os_denied_root_is_journaled_not_decisive(tmp_path):
    zone = _Zone(tmp_path)
    work = str(zone.work)
    truncated = f"{work}/priv"  # the beginning of an attempt path, cut by the arm's own sed (pilot 3)
    for shown in (f"{work}/private-b/x.py:12: Error", truncated, f"E   assert '{truncated}'", str(zone.home) + "/.config/foundry/r"):
        calls = [("Bash", {"command": "pytest -q | sed -E 's/x/y/'"}, shown, False)]
        strict, _, _ = zone.audit(tmp_path, calls, policy=False)
        out, journal, _ = zone.audit(tmp_path, calls)
        assert strict and out == [], shown
        assert [j for j in journal if j.startswith("tool_result:")], shown
    # a result path outside every denied root (but sensitive) stays decisive; one under the allowed attempt is no finding
    other = [("Bash", {"command": "echo hi"}, f"see {zone.other}/secret.txt", False)]
    out, journal, _ = zone.audit(tmp_path, other)
    assert any(p.startswith("tool_result:") for p in out) and journal == []
    mine = [("Bash", {"command": "echo hi"}, f"see {zone.scratch}/x", False)]
    assert zone.audit(tmp_path, mine) == ([], [], {})
