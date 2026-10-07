"""PAT-121: protocol v4 (the v3 instrument with two changes: hidden-test feedback to the corrector, a private root per
attempt, a fixed candidate compared on the six v3 screening tasks, arms A and L).

Fake arms only (the helpers of the v2 exploration tests): no model, no cloud, no network, and never the real
``~/.config/foundry``. Everything the v4 keys add is off when they are absent (the v1 to v3 tests cover that)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from foundry import local_first_corpus as lfc
from foundry import local_first_runner as lfr
from test_local_first_corpus import MOD
from test_local_first_exploration_runner import GOOD_REPORT, PLAN, make_runner
from test_local_first_runner import _stream, counts, ledger_kinds, results

QUALIFICATION = Path(__file__).resolve().parents[1] / "docs" / "qualification"
V3_PATH = QUALIFICATION / "pat-19-campaign-v3.json"
V4_PATH = QUALIFICATION / "pat-19-campaign-v4.json"
FEEDBACK = {"correction_feedback": {"hidden_test_failures": True, "max_failures": 20, "max_message_chars": 300,
                                        "max_name_chars": 200}}
PRIVATE = {"isolation": {"private_attempt_root": True}}
REFUSED_THEN_FIXED = {**PLAN, "implementer": ["partial", "fix", "partial", "fix"], "reviewer": ["PASS"]}


def _feedbacks(plan_path):
    path = Path(str(plan_path) + ".feedback")
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()] if path.exists() else []


# ------------------------------------------------------------------------- the frozen v4 configuration

def test_the_v4_campaign_pins_the_validated_values_and_leaves_v3_alone():
    v3, v4 = lfr.load_campaign(V3_PATH), lfr.load_campaign(V4_PATH)
    assert v4["schema"] == lfr.CAMPAIGN_SCHEMA_V2 and v4["protocol"] == "pat-19-protocol-v4"
    one = "qwen3.6-35b-a3b-mlx-4bit"
    assert list(v4["candidates"]) == [one] and v4["candidates"][one] == v3["candidates"][one]
    assert v4["rules"]["exploration_screening"]["candidates"] == [one] and v4["exploration"]["fixed_candidate"] == one
    assert v4["bounds"]["explorer_max_steps"] == 60 and v4["bounds"]["explorer_max_seconds"] == 900
    assert v4["bounds"]["max_correction_rounds"] == 2 and v4["exploration"]["one_task_per_launch"] is True
    assert v4["exploration"]["comparison_task_group"] == "screening"
    manifest = json.loads((QUALIFICATION / "pat-19-corpus-manifest-v1.json").read_text("utf-8"))
    assert [t["pr"] for t in manifest["screening"]] == [30, 83, 27, 24, 48, 19]  # the six tasks of the v3 screening
    assert v4["correction_feedback"] == {**FEEDBACK["correction_feedback"], "note": v4["correction_feedback"]["note"]}
    assert v4["isolation"]["private_attempt_root"] is True
    assert "cloud_explorer_economy" not in v4["drivers"]  # no Haiku arm E
    assert v4["envelope_recommended"]["compare_exploration"]["caps"]["cloud_executions"] == 80
    assert set(v4["envelope_recommended"]) == {"compare_exploration"}
    # the decision rule, the ground truth, the drivers that stay and the machine are the v3 ones
    for name in ("exploration_screening", "exploration_comparison"):
        a, b = v4["rules"][name], v3["rules"][name]
        assert {k: v for k, v in a.items() if k not in ("note", "candidates")} == {
            k: v for k, v in b.items() if k not in ("note", "candidates")}
    assert v4["rules"]["exploration_comparison"]["premium_per_accepted_ratio_max"] == 0.85
    assert v4["exploration"]["ground_truth"] == v3["exploration"]["ground_truth"]
    for name in ("local_explorer", "cloud_implementer_current", "cloud_reviewer"):
        def bare(driver):  # the only difference: the wording of the log layout note (three drivers in v3)
            log = {k: v for k, v in driver.get("session_log", {}).items() if k != "layout_note"}
            return {**driver, "session_log": log}
        assert bare(v4["drivers"][name]) == bare(v3["drivers"][name])
    for key in ("frozen_machine", "dedicated_machine", "prompts", "statement_footer", "cloud_bash_deny"):
        assert {k: v for k, v in v4[key].items() if k != "note"} == {
            k: v for k, v in v3[key].items() if k != "note"} if isinstance(v4[key], dict) else v4[key] == v3[key]
    # v3 is untouched: none of the v4 keys exists there
    assert "correction_feedback" not in v3 and "private_attempt_root" not in v3["isolation"]
    assert not {"comparison_task_group", "fixed_candidate"} & set(v3["exploration"])


def test_the_loader_pins_the_v4_coordinates_and_validates_the_new_keys(tmp_path):
    spec = json.loads(V4_PATH.read_text("utf-8"))["exploration"]["ground_truth"]
    (tmp_path / spec["file"]).write_bytes((QUALIFICATION / spec["file"]).read_bytes())

    def broken(edit, name="v4.json"):
        data = json.loads(V4_PATH.read_text("utf-8"))
        edit(data)
        path = tmp_path / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    for edit, message in (
            (lambda d: d["bounds"].update(explorer_max_steps=25), "explorer bounds"),
            (lambda d: d["bounds"].update(max_correction_rounds=3), "explorer bounds"),
            (lambda d: d["exploration"].update(fixed_candidate="x"), "declared candidate"),
            (lambda d: d["exploration"].update(comparison_task_group="comparison"), "comparison set"),
            (lambda d: d["exploration"].update(one_task_per_launch=False), "one_task_per_launch true"),
            (lambda d: d["correction_feedback"].update(max_failures=21), "correction_feedback"),
            (lambda d: d["correction_feedback"].update(max_message_chars=299), "correction_feedback"),
            (lambda d: d["correction_feedback"].update(max_name_chars=201), "correction_feedback"),
            (lambda d: d["correction_feedback"].pop("max_name_chars"), "correction_feedback|positive integers"),
            (lambda d: d.pop("correction_feedback"), "correction_feedback"),
            (lambda d: d["isolation"].update(private_attempt_root=False), "private_attempt_root true"),
            (lambda d: d["isolation"].update(private_attempt_root="yes"), "must be a boolean"),
            (lambda d: d["correction_feedback"].update(max_failures=0), "positive integers"),
            (lambda d: d["correction_feedback"].update(hidden_test_failures="yes"), "boolean"),
            (lambda d: d["correction_feedback"].update(source_code=True), "correction_feedback")):
        with pytest.raises(lfr.RunnerError, match=message):
            lfr.load_campaign(broken(edit))
    lfr.load_campaign(broken(lambda d: None))


def test_a_loaded_campaign_cannot_forward_or_set_a_foundry_variable(tmp_path):
    spec = json.loads(V4_PATH.read_text("utf-8"))["exploration"]["ground_truth"]
    (tmp_path / spec["file"]).write_bytes((QUALIFICATION / spec["file"]).read_bytes())
    for field, value, message in (("env_allow", ["FOUNDRY_DATA"], "may not forward FOUNDRY_DATA"),
                                  ("env_set", {"FOUNDRY_DATA": "/x"}, "bad env_set")):
        data = json.loads(V4_PATH.read_text("utf-8"))
        data["drivers"]["cloud_reviewer"][field] = value
        (tmp_path / "bad.json").write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(lfr.RunnerError, match=message):
            lfr.load_campaign(tmp_path / "bad.json")


# ------------------------------------------------------------------ hidden-test feedback to the corrector

def test_the_feedback_text_is_bounded_and_names_tests_never_their_source():
    spec = FEEDBACK["correction_feedback"]
    many = [{"name": f"tests.test_x::test_{n}", "message": "assert 1 == 2 " + "x" * 400 + "\n + where\n 1 = f()"}
            for n in range(25)]
    text = lfr._failure_feedback({"failures": many}, spec)
    lines = text.splitlines()
    assert "20 shown of 25" in lines[2] and len([x for x in lines if x.startswith("- ")]) == 20
    assert all(len(x.split(": ", 1)[1]) <= 300 for x in lines if x.startswith("- "))
    assert "test_20" not in text and "test_19" in text
    long_name = lfr._failure_feedback({"failures": [{"name": "t::" + "p" * 500, "message": "m"}]}, spec)
    assert long_name.splitlines()[-1] == "- " + ("t::" + "p" * 500)[:200] + ": m"
    assert lfr._failure_feedback({"failures": []}, spec) == "" and lfr._failure_feedback({}, spec) == ""
    assert lfr._failure_feedback({"failures": [{"name": "a::b", "message": ""}]}, spec).endswith("- a::b")


def test_the_judge_returns_failure_names_and_messages_only_when_asked(tmp_path):
    from test_local_first_corpus import _make_repo
    repo, snap, _, _ = _make_repo(tmp_path)
    task = snap["prs"][0]
    plain = lfc.build_bundle(repo, task, tmp_path / "plain")
    asked = lfc.build_bundle(repo, task, tmp_path / "asked")
    verdict, with_failures = lfc.judge(repo, task, plain), lfc.judge(repo, task, asked, failures=True)
    assert "failures" not in verdict  # the verdict of v1 to v3 is byte-for-byte what it was
    assert {k: v for k, v in with_failures.items() if k != "failures"} == verdict
    names = [f["name"] for f in with_failures["failures"]]
    assert sorted(n.rsplit("::", 1)[1] for n in names) == ["test_added", "test_changed"]
    blob = json.dumps(with_failures["failures"])
    # what is guaranteed: no test source code and not the candidate's location; the test module and name ARE
    # visible (junit classname::name), and so is any other path a message may carry
    assert str(asked) not in blob and "def test_" not in blob
    assert all(n.startswith("tests.test_m::") for n in names)
    assert lfc._mask("x /a/b/c y", {Path("/a/b"): "<bundle>"}) == "x <bundle>/c y"
    nested = {Path("/a/b"): "<bundle>", Path("/a/b/c"): "<tmp>"}  # the longest path first, whatever the order
    assert lfc._mask("/a/b/c/x /a/b/y", nested) == "<tmp>/x <bundle>/y"
    assert lfc._mask("/a/b/c/x", dict(reversed(list(nested.items())))) == "<tmp>/x"
    junit = tmp_path / "j.xml"
    junit.write_text('<testsuite><testcase classname="c" name="n"><failure message="at %s/home/x and %s/f"/>'
                     '</testcase></testsuite>' % (tmp_path / "tmpdir", asked), encoding="utf-8")
    (tmp_path / "tmpdir").mkdir()
    assert lfc._junit_failures(junit, asked, tmp_path / "tmpdir") == [
        {"name": "c::n", "message": "at <tmp>/home/x and <bundle>/f"}]


def test_after_a_refusal_the_corrector_of_both_arms_gets_the_same_failing_tests_and_two_rounds_at_most(tmp_path):
    runner, _, plan, tasks = make_runner(
        tmp_path, "compare_exploration", REFUSED_THEN_FIXED, campaign_over={**FEEDBACK, "exploration": {"fixed_candidate": "cand-a"}},
        require_screening=True)
    runner.compare_exploration(tasks[:1], "cand-a", ("A", "L"))
    told = _feedbacks(plan)
    assert len(told) == 2 and told[0] == told[1]  # one correction per arm, the very same text
    text = told[0]
    assert text.startswith("The mechanical acceptance check refused the change:")
    assert "Failing hidden tests (2 shown of 2):" in text
    assert "::test_added: " in text and "::test_changed: " in text
    assert str(tmp_path) not in text and "def test_" not in text  # source code and the bundle location only
    assert "tests.test_m::test_added" in text  # the module and test name are visible
    recs = [r for r in results(runner) if r.get("segment") == "cloud" and r["path"] in ("A", "L")]
    # counters only, on the corrector's record (what it was told), never on the implementer's
    assert [r.get("feedback") for r in recs] == [None, {"failing_shown": 2, "failing_total": 2,
                                                       "collection_failure": False}] * 2
    assert "test_added" not in json.dumps(recs)
    assert [(r["path"], r["outcome"]) for r in recs] == [("A", "judge_refused"), ("A", "accepted"),
                                                         ("L", "judge_refused"), ("L", "accepted")]


def test_at_most_two_corrections_and_the_feedback_reaches_each_of_them(tmp_path):
    runner, _, plan, tasks = make_runner(
        tmp_path, "compare_exploration", {**PLAN, "implementer": ["partial"]}, campaign_over=FEEDBACK)
    runner.compare_exploration(tasks[:1], "cand-a", ("A",))
    assert [r["outcome"] for r in results(runner)] == ["judge_refused"] * 3
    assert len(_feedbacks(plan)) == 2 and all("Failing hidden tests" in t for t in _feedbacks(plan))
    assert counts(plan)["implementer"] == 3


def test_without_the_key_the_corrector_is_told_exactly_what_it_was_before(tmp_path):
    runner, _, plan, tasks = make_runner(tmp_path, "compare_exploration", REFUSED_THEN_FIXED)
    runner.compare_exploration(tasks[:1], "cand-a", ("A",))
    told, = _feedbacks(plan)
    assert not any("feedback" in r for r in results(runner))  # the key is absent when the feedback is off
    assert told.startswith("The mechanical acceptance check refused the change:") and told.endswith(
        "(passed 0, failed 2, errors 0).") and "Failing hidden tests" not in told


def test_the_judge_is_called_as_before_when_the_feedback_is_off(tmp_path, monkeypatch):
    runner, _, _, tasks = make_runner(tmp_path, "compare_exploration", PLAN)
    seen = []
    real = lfc.judge
    monkeypatch.setattr(lfc, "judge", lambda *a, **kw: seen.append(kw) or real(*a, **kw))
    runner.compare_exploration(tasks[:1], "cand-a", ("A",))
    assert seen == [{}]


# ------------------------------------------------------------------------------ private root per attempt

def test_each_attempt_has_a_private_parent_holding_nothing_but_itself(tmp_path, monkeypatch):
    runner, _, _, tasks = make_runner(tmp_path, "compare_exploration", REFUSED_THEN_FIXED,
                                      campaign_over={**PRIVATE, **FEEDBACK})
    seen = []
    real = lfr.execute_driver

    def spy(driver, values, **kw):
        attempt = Path(kw["workdir"]).parent
        seen.append((sorted(p.name for p in attempt.parent.iterdir()), attempt.name))
        return real(driver, values, **kw)

    monkeypatch.setattr(lfr, "execute_driver", spy)
    runner.compare_exploration(tasks[:1], "cand-a", ("A", "L"))
    assert len(seen) >= 6 and all(listing == [name] for listing, name in seen)  # ``ls ..``: the attempt only
    assert list(runner.work_root.iterdir()) == []  # everything is gone, private roots included


def test_without_the_key_the_layout_is_the_v3_one(tmp_path, monkeypatch):
    runner, _, _, tasks = make_runner(tmp_path, "compare_exploration", PLAN)
    seen = []
    real = lfr.execute_driver
    monkeypatch.setattr(lfr, "execute_driver", lambda d, v, **kw: seen.append(Path(kw["workdir"]).parent.parent)
                        or real(d, v, **kw))
    runner.compare_exploration(tasks[:1], "cand-a", ("A",))
    assert seen and all(p == runner.work_root for p in seen)


def _audit(tmp_path, command, *, private):
    work = tmp_path / "work"
    root = work / "private-attempt-1"
    attempt = root / "attempt-1"
    bundle, scratch = attempt / "bundle", attempt / "scratch"
    bundle.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(exist_ok=True)
    (work / "private-attempt-2" / "attempt-2" / "bundle").mkdir(parents=True, exist_ok=True)
    home = tmp_path / "home"
    (home / ".config" / "foundry").mkdir(parents=True, exist_ok=True)
    stream = tmp_path / "stream.jsonl"
    _stream(stream, ("claude", "Bash", {"command": command}))
    sensitive = [home / ".config" / "foundry", *( [work] if private else [])]
    return lfr.audit_transcript(stream, bundle=bundle, scratch=scratch, sensitive=sensitive, home=str(home),
                                attempt_dir=attempt, private_root=root if private else None)


@pytest.mark.parametrize("command", ["ls ..", "ls ../..", "ls ../../private-attempt-1", "cat ../scratch/x"])
def test_listing_the_private_parents_is_clean(tmp_path, command):
    assert _audit(tmp_path, command, private=True) == []


def test_the_private_root_does_not_weaken_the_audit_of_accesses_outside_the_bundle(tmp_path):
    work = tmp_path / "work"
    for command in ("ls ../../..",  # the work root: it lists the other attempts
                    f"ls {work}", f"ls {work}/", "cat ../../../private-attempt-2/attempt-2/bundle/x",
                    f"cat {work}/private-attempt-2/attempt-2/bundle/x",
                    f"grep -r secret {work}/private-attempt-2",
                    f"cat {tmp_path}/home/.config/foundry/registry.json", "cat ~/.config/foundry/registry.json"):
        assert _audit(tmp_path, command, private=True) != [], command
    # an arm of a v3 config (no private root) is judged exactly as before
    assert _audit(tmp_path, "ls ../..", private=False) != []
    assert _audit(tmp_path, "ls ..", private=False) == []


def test_a_cloud_arm_that_lists_its_private_parent_is_not_contaminated_but_one_that_lists_the_work_root_is(tmp_path):
    for command, flagged in (("ls ../..", False), ("ls ../../..", True)):
        plan = {**PLAN, "implementer": [f"cmd:{command}"], "reviewer": ["PASS"]}
        runner, _, _, tasks = make_runner(_sub(tmp_path, "x" if flagged else "y"), "compare_exploration", plan,
                                          campaign_over=PRIVATE)
        runner.compare_exploration(tasks[:1], "cand-a", ("A",))
        first = results(runner)[0]
        assert bool(first.get("contaminated")) is flagged, command
        if flagged:
            assert first["outcome"] == "contaminated" and first["contamination"]["paths"]


def _sub(tmp_path, name):
    (tmp_path / name).mkdir()
    return tmp_path / name


# ----------------------------------------------------------------- a fixed candidate, no screening

def test_a_campaign_that_fixes_its_candidate_compares_it_without_a_screening_and_refuses_any_other(tmp_path):
    over = {"exploration": {"fixed_candidate": "cand-a"}}
    runner, _, plan, tasks = make_runner(tmp_path, "compare_exploration", PLAN, campaign_over=over,
                                         require_screening=True)  # no screening results anywhere
    runner.compare_exploration(tasks[:1], "cand-a", ("A", "L"))
    assert counts(plan)["xlocal"] == 1 and {r["path"] for r in results(runner)} >= {"A", "L"}
    other, _, _, tasks2 = make_runner(_sub(tmp_path, "o"), "compare_exploration", PLAN, campaign_over=over,
                                      require_screening=True)
    with pytest.raises(lfr.RunnerError, match="not the candidate this campaign fixes"):
        other.compare_exploration(tasks2[:1], "cand-b", ("A", "L"))
    screen, _, plan3, tasks3 = make_runner(_sub(tmp_path, "s"), "screen_exploration", PLAN, campaign_over=over,
                                           caps={"cloud_executions": 0})
    with pytest.raises(lfr.RunnerError, match="no screening"):
        screen.screen_exploration(tasks3, ["cand-a"])
    assert counts(plan3) == {}


def test_the_cli_takes_the_comparison_tasks_from_the_group_the_config_names(tmp_path, monkeypatch):
    from test_local_first_corpus import _make_repo
    repo, snap, _, _ = _make_repo(tmp_path)
    snap = {**snap, "prs": [snap["prs"][0], {**snap["prs"][0], "pr": 2}]}
    manifest = {"screening": [{"pr": 2}], "comparison": [{"pr": 1}]}
    seen = {}
    monkeypatch.setattr(lfr.Runner, "compare_exploration", lambda self, tasks, cand, paths: seen.update(
        prs=[t["pr"] for t in tasks], paths=list(paths)))
    monkeypatch.setenv("HOME", str(tmp_path / "host-home"))
    data = _v4_data(tmp_path)  # the real v4 config: group "screening", default arms A,L
    for name, body in (("c.json", data), ("snap.json", snap), ("manifest.json", manifest)):
        (tmp_path / name).write_text(json.dumps(body), encoding="utf-8")
    env = tmp_path / "envelope.json"
    env.write_text(json.dumps({"schema": lfr.ENVELOPE_SCHEMA, "campaign_id": "cli", "expires_on": "2026-12-31",
                               "allowed_modes": ["compare_exploration"],
                               "caps": {"cloud_executions": 10, "premium_tokens": 10**6,
                                        "wall_clock_seconds": 10**5}}), encoding="utf-8")
    argv = ["compare-exploration", "--campaign", str(tmp_path / "c.json"), "--dry-run",
            "--candidate", "qwen3.6-35b-a3b-mlx-4bit", "--envelope", str(env), "--state-dir", str(tmp_path / "st"),
            "--work-root", str(tmp_path / "work"), "--repo", str(repo), "--snapshot", str(tmp_path / "snap.json"),
            "--manifest", str(tmp_path / "manifest.json")]
    assert lfr.main(argv, today=__import__("datetime").date(2026, 10, 6)) == 0
    assert seen == {"prs": [2], "paths": ["A", "L"]}


# ----------------------------------------------------------- review round 1 (R1, R2, R6)

def _v4_data(tmp_path):
    spec = json.loads(V4_PATH.read_text("utf-8"))["exploration"]["ground_truth"]
    (tmp_path / spec["file"]).write_bytes((QUALIFICATION / spec["file"]).read_bytes())
    return json.loads(V4_PATH.read_text("utf-8"))


def test_the_v4_keys_are_accepted_only_under_protocol_v4(tmp_path):
    spec = json.loads(V3_PATH.read_text("utf-8"))["exploration"]["ground_truth"]
    (tmp_path / spec["file"]).write_bytes((QUALIFICATION / spec["file"]).read_bytes())
    for edit in (lambda d: d["exploration"].update(fixed_candidate="qwen3.6-35b-a3b-mlx-4bit"),
                 lambda d: d["exploration"].update(comparison_task_group="screening"),
                 lambda d: d.update(correction_feedback=FEEDBACK["correction_feedback"]),
                 lambda d: d["isolation"].update(private_attempt_root=True)):
        for protocol in ("pat-19-protocol-v3", None):  # v3, and a config with no protocol field
            data = json.loads(V3_PATH.read_text("utf-8"))
            edit(data)
            data.pop("protocol") if protocol is None else None
            (tmp_path / "c.json").write_text(json.dumps(data), encoding="utf-8")
            with pytest.raises(lfr.RunnerError, match="accepted only under protocol pat-19-protocol-v4"):
                lfr.load_campaign(tmp_path / "c.json")
    lfr.load_campaign(V4_PATH)


def test_protocol_v4_refuses_arm_e_before_any_claim_or_spend(tmp_path):
    runner, campaign, plan, tasks = make_runner(tmp_path, "compare_exploration", PLAN)
    runner.campaign = {**campaign, "protocol": lfr.PROTOCOL_V4}
    for arms in (("A", "L", "E"), ("E",), ("A", "B")):
        with pytest.raises(lfr.RunnerError, match="arms \\['A', 'L'\\] only"):
            runner.compare_exploration(tasks[:1], "cand-a", arms)
    assert counts(plan) == {} and not runner.results_path.exists()
    assert "cloud_started" not in ledger_kinds(runner)


def test_report_of_a_fixed_candidate_campaign_says_no_screening(tmp_path):
    from test_local_first_runner import ledger_of
    runner, campaign, _, tasks = make_runner(
        tmp_path, "compare_exploration", PLAN, campaign_over={"exploration": {"fixed_candidate": "cand-a"}})
    runner.compare_exploration(tasks[:1], "cand-a", ("A", "L"))
    out = lfr.report(campaign, results(runner), ledger_of(runner))
    assert out["exploration_screening"] == {"state": "no_screening", "selected": "cand-a",
                                            "reason": "no screening: candidate fixed by the protocol"}
    assert out["exploration_comparison"]["screening_selected"] == "fixed_by_protocol:cand-a"
    assert "incomplete_screening" not in json.dumps(out) and "screening_results_not_available" not in json.dumps(out)


# ------------------------------------------------------------------------------- review round 2

def test_feedback_counters_cover_a_collection_failure_and_the_cap():
    spec = FEEDBACK["correction_feedback"]
    many = [{"name": f"t{n}", "message": "m"} for n in range(25)]
    assert lfr._feedback_counters({"failures": many}, spec) == {
        "failing_shown": 20, "failing_total": 25, "collection_failure": False}
    assert lfr._feedback_counters({"failures": [{"name": "m", "message": "collection failure\nImportError"}]},
                                  spec) == {"failing_shown": 1, "failing_total": 1, "collection_failure": True}
    assert lfr._feedback_counters({}, spec) == {"failing_shown": 0, "failing_total": 0,
                                                "collection_failure": False}


def test_an_empty_paths_option_is_refused_not_defaulted(tmp_path, monkeypatch, capsys):
    from test_local_first_corpus import _make_repo
    repo, snap, _, _ = _make_repo(tmp_path)
    (tmp_path / "c.json").write_text(json.dumps(_v4_data(tmp_path)), encoding="utf-8")
    (tmp_path / "snap.json").write_text(json.dumps(snap), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"screening": [{"pr": 1}], "comparison": [{"pr": 1}]}),
                                            encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path / "host-home"))
    env = tmp_path / "envelope.json"
    env.write_text(json.dumps({"schema": lfr.ENVELOPE_SCHEMA, "campaign_id": "e", "expires_on": "2026-12-31",
                               "allowed_modes": ["compare_exploration"],
                               "caps": {"cloud_executions": 10, "premium_tokens": 10**6,
                                        "wall_clock_seconds": 10**5}}), encoding="utf-8")
    code = lfr.main(["compare-exploration", "--campaign", str(tmp_path / "c.json"), "--dry-run", "--paths", "",
                     "--candidate", "qwen3.6-35b-a3b-mlx-4bit", "--envelope", str(env),
                     "--state-dir", str(tmp_path / "st"), "--work-root", str(tmp_path / "work"),
                     "--repo", str(repo), "--snapshot", str(tmp_path / "snap.json"),
                     "--manifest", str(tmp_path / "manifest.json")], today=__import__("datetime").date(2026, 10, 6))
    assert code == 2 and "unknown path" in capsys.readouterr().err and not (tmp_path / "st").exists()


def test_a_schema_v1_config_cannot_declare_protocol_v4(tmp_path):
    data = json.loads((QUALIFICATION / "pat-19-campaign-v1.json").read_text("utf-8"))
    data["protocol"] = "pat-19-protocol-v4"
    (tmp_path / "c.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(lfr.RunnerError, match="requires the exploration schema"):
        lfr.load_campaign(tmp_path / "c.json")


# ----------------------------------------------------------------------- review round 3 (W1)

CITING = {**GOOD_REPORT, "rationale": "see {workdir}/plugins/foundry/tooling/foundry/m.py, in {workdir}"}
READS_STATEMENT = {**PLAN, "xlocal": [{"mode": "final", "report": CITING}], "implementer": ["show:TASK.md"],
                   "reviewer": ["PASS"]}


def test_a_report_citing_the_explorers_own_bundle_does_not_flag_arm_l_under_a_private_root(tmp_path, monkeypatch):
    runner, _, plan, tasks = make_runner(tmp_path, "compare_exploration", READS_STATEMENT, campaign_over=PRIVATE)
    runner.compare_exploration(tasks[:1], "cand-a", ("A", "L"))
    recs = {r["path"]: r for r in results(runner) if r.get("segment") == "cloud"}
    assert not recs["L"].get("contaminated") and recs["L"]["outcome"] == "accepted"
    told = [json.loads(line)["text"] for line in Path(str(plan) + ".texts").read_text("utf-8").splitlines()
            if json.loads(line)["role"] == "implementer"]
    statement = told[1]  # the second implementer run is arm L's
    assert "plugins/foundry/tooling/foundry/m.py, in ." in statement and str(tmp_path / "work") not in statement
    # without the masking the very same run IS flagged (the test would not prove anything otherwise)
    other, _, _, tasks2 = make_runner(_sub(tmp_path, "unmasked"), "compare_exploration", READS_STATEMENT,
                                      campaign_over=PRIVATE)
    monkeypatch.setattr(lfr.Runner, "_report_for_arm", lambda self, report: report)
    other.compare_exploration(tasks2[:1], "cand-a", ("L",))
    flagged = [r for r in results(other) if r.get("segment") == "cloud"][0]
    assert flagged["outcome"] == "contaminated"


def test_the_report_rendering_is_untouched_without_a_private_root(tmp_path):
    runner, _, _, _ = make_runner(tmp_path, "compare_exploration", PLAN)
    report = {**GOOD_REPORT, "rationale": f"see {tmp_path}/work/attempt-x/bundle/m.py"}
    assert runner._report_for_arm(report) is report  # the v1 to v3 rendering is byte for byte what it was
    private, _, _, _ = make_runner(_sub(tmp_path, "p"), "compare_exploration", PLAN, campaign_over=PRIVATE)
    work = str(private.work_root)
    masked = private._report_for_arm({**GOOD_REPORT, "functions": [{"file": MOD, "name": "add"}],
                                      "rationale": f"{work}/private-a/a/bundle/x.py {work}/private-a/a/bundle {work}"})
    assert masked["rationale"] == "x.py . <work-root>" and masked["functions"] == [{"file": MOD, "name": "add"}]
