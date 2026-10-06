"""PAT-114: deterministic tests of the pure pieces of the PAT-19 protocol v2 (read-only exploration).

Ground truth from merged diffs (the 12 real tasks of the corpus and a synthetic repository), the report
format and its parsing, the localization judge, the screening rule, the dedicated-machine admission, the
quality and economy rules and the statement rendering. No model, no cloud, no network."""
from __future__ import annotations

import json
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from foundry import local_first_corpus as lfc
from foundry import local_first_exploration as lfe
from test_local_first_corpus import MOD, _make_repo

QUALIFICATION = Path(__file__).resolve().parents[1] / "docs" / "qualification"
REPO_ROOT = Path(__file__).resolve().parents[3]
TRUTH = json.loads((QUALIFICATION / "pat-19-exploration-truth-v2.json").read_text("utf-8"))
SNAPSHOT = json.loads((QUALIFICATION / "pat-19-corpus-snapshot-v1.json").read_text("utf-8"))
MANIFEST = json.loads((QUALIFICATION / "pat-19-corpus-manifest-v1.json").read_text("utf-8"))
CAMPAIGN = json.loads((QUALIFICATION / "pat-19-campaign-v2.json").read_text("utf-8"))
TASKS = {t["pr"]: t for t in SNAPSHOT["prs"]}
ALL_PRS = [t["pr"] for group in ("screening", "comparison") for t in MANIFEST[group]]


# ------------------------------------------------------------ the 12 real merged diffs

def test_the_truth_file_covers_exactly_the_12_frozen_tasks():
    assert sorted(map(int, TRUTH["tasks"])) == sorted(ALL_PRS) and len(ALL_PRS) == 12
    assert {t["set"] for t in TRUTH["tasks"].values()} == {"screening", "comparison"}
    for pr in ALL_PRS:
        entry, task = TRUTH["tasks"][str(pr)], TASKS[pr]
        assert (entry["base_sha"], entry["head_sha"]) == (task["base_sha"], task["head_sha"])
        assert entry["files"], f"PR {pr}: empty ground truth"  # non-empty for every task
        paths = {f["path"]: f for f in task["files"]}
        for path in entry["files"]:  # product files of the merged diff, never a test or a doc
            assert lfe.is_product_path(path) and paths[path]["status"] in ("M", "D")
        assert not any(f.startswith(lfc.TESTS_PREFIX) or lfc._is_doc(f) for f in entry["files"])
        assert entry["functions"], f"PR {pr}: no function in the truth"  # all 12 touch existing functions
        assert all(fn[0] in entry["files"] for fn in entry["functions"])


@pytest.mark.parametrize("pr", ALL_PRS)
def test_judge_on_the_real_merged_diffs(pr):
    truth = TRUTH["tasks"][str(pr)]
    perfect = {"files": truth["files"], "functions": [{"file": f, "name": n} for f, n in truth["functions"]],
               "rationale": "the merged change"}
    score = lfe.score_report(truth, perfect)
    assert (score["file_recall"], score["file_precision"], score["function_recall"]) == (1.0, 1.0, 1.0)
    empty = lfe.score_report(truth, {"files": [], "functions": [], "rationale": ""})
    assert (empty["file_recall"], empty["file_precision"], empty["function_recall"]) == (0.0, 0.0, 0.0)
    missing = lfe.score_report(truth, None)
    assert (missing["file_recall"], missing["file_precision"], missing["function_recall"]) == (0.0, 0.0, 0.0)
    padded = lfe.score_report(truth, {"files": [*truth["files"], "plugins/foundry/tooling/foundry/zz.py"],
                                      "functions": [], "rationale": ""})
    assert padded["file_recall"] == 1.0 and padded["file_precision"] == round(
        len(truth["files"]) / (len(truth["files"]) + 1), 6) and padded["function_recall"] == 0.0
    wrong = lfe.score_report(truth, {"files": ["plugins/foundry/tooling/foundry/zz.py"], "functions": [],
                                     "rationale": ""})
    assert wrong["file_recall"] == 0.0 and wrong["file_precision"] == 0.0


def _real_commits_available() -> bool:
    for pr in ALL_PRS:
        for sha in (TASKS[pr]["base_sha"], TASKS[pr]["head_sha"]):
            if subprocess.run(["git", "-C", str(REPO_ROOT), "cat-file", "-e", f"{sha}^{{commit}}"],
                              capture_output=True, check=False).returncode != 0:
                return False
    return True


def test_the_committed_truth_is_what_the_merged_diffs_give():
    if not _real_commits_available():  # a shallow clone: the committed file stays the witness
        pytest.skip("the merged commits of the corpus are not in this clone")
    for pr in ALL_PRS:
        live = lfe.ground_truth(REPO_ROOT, TASKS[pr])
        assert live == {k: v for k, v in TRUTH["tasks"][str(pr)].items() if k != "set"}, pr


# ---------------------------------------------------------------- ground truth, synthetic

BASE_PRODUCT = '''"""module."""
LIMIT = 1


def top(a):
    return a


class Box:
    size = 1

    def open(self):
        return 1

    @property
    def label(self):
        return "x"

    class Inner:
        def deep(self):
            def helper():
                return 1
            return helper()


def untouched():
    return 0
'''

HEAD_PRODUCT = '''"""module."""
LIMIT = 2


def top(a):
    return a + 1


class Box:
    size = 1

    def open(self):
        return 1

    @property
    def label(self):
        return "y"

    class Inner:
        def deep(self):
            def helper():
                return 2
            return helper()


def untouched():
    return 0


def brand_new():
    return 5
'''


def test_ground_truth_on_a_synthetic_diff(tmp_path):
    other = "plugins/foundry/tooling/foundry/other.py"
    hook = "plugins/foundry/hooks/guard.sh"
    added = "plugins/foundry/tooling/foundry/added.py"
    gone = "plugins/foundry/tooling/foundry/gone.py"
    repo, snap, _, _ = _make_repo(
        tmp_path, base_files={MOD: BASE_PRODUCT, other: "def f():\n    return 1\n\n\ndef g():\n    return 2\n",
                              hook: "echo a\n"},
        head_files={MOD: HEAD_PRODUCT,
                    other: "def f():\n    return 1\n\n\ndef g():\n    return 3\n\n\ndef h():\n    pass\n",
                    hook: "echo b\n", added: "def a():\n    pass\n", "plugins/foundry/docs/y.md": "d\n"})
    task = snap["prs"][0]
    task = {**task, "files": [*task["files"], {"path": gone, "status": "D", "added": 0, "deleted": 1}]}
    truth = lfe.ground_truth(repo, task)
    # tests and docs are ignored; a modified or deleted product file is truth, an added one only counts for precision
    assert truth["files"] == sorted([MOD, other, hook, gone]) and truth["created_files"] == [added]
    assert not any(f.startswith(lfc.TESTS_PREFIX) or f.endswith(".md") for f in truth["files"])
    # the functions that enclose a changed line at the merged SHA and exist at the base: a function, a
    # decorated method, a method of a nested class (the nested ``helper`` belongs to ``deep``); the module
    # constant belongs to no function; non-Python and deleted files are file-level only
    assert sorted(truth["functions"]) == sorted([[other, "g"], [MOD, "Box.Inner.deep"], [MOD, "Box.label"],
                                                 [MOD, "top"]])
    assert sorted(truth["new_functions"]) == sorted([[other, "h"], [MOD, "brand_new"]])
    # a diff with only tests and docs has an empty truth: the judge refuses to score
    with pytest.raises(lfe.ExplorationError, match="empty ground truth"):
        lfe.score_report({"pr": 9, "files": [], "created_files": [], "functions": []}, None)


def test_a_module_level_change_gives_the_file_and_no_function(tmp_path):
    repo, snap, _, _ = _make_repo(tmp_path, base_files={MOD: BASE_PRODUCT}, head_files={
        MOD: BASE_PRODUCT.replace("LIMIT = 1", "LIMIT = 9")})
    truth = lfe.ground_truth(repo, snap["prs"][0])
    assert truth["files"] == [MOD] and truth["functions"] == [] and truth["new_functions"] == []
    score = lfe.score_report(truth, {"files": [MOD], "functions": [], "rationale": ""})
    assert score["file_recall"] == 1.0 and score["function_recall"] is None  # undefined, never 0


def test_a_pure_deletion_inside_a_function_counts_that_function(tmp_path):
    base = "def f():\n    a = 1\n    b = 2\n    return a + b\n\n\ndef g():\n    return 0\n"
    head = "def f():\n    a = 1\n    return a\n\n\ndef g():\n    return 0\n"
    repo, snap, _, _ = _make_repo(tmp_path, base_files={MOD: base}, head_files={MOD: head})
    assert lfe.ground_truth(repo, snap["prs"][0])["functions"] == [[MOD, "f"]]


def test_function_names_match_by_qualified_name_or_last_component():
    truth = {"pr": 1, "files": ["a.py"], "created_files": [], "functions": [["a.py", "Cls.update"]]}
    for name in ("Cls.update", "update"):
        assert lfe.score_report(truth, {"files": [], "functions": [{"file": "a.py", "name": name}],
                                        "rationale": ""})["function_recall"] == 1.0
    for item in ({"file": "b.py", "name": "update"}, {"file": "a.py", "name": "Other.upd"}):
        assert lfe.score_report(truth, {"files": [], "functions": [item], "rationale": ""})["function_recall"] == 0.0


def test_precision_counts_created_files_and_an_empty_report_scores_zero_precision():
    truth = {"pr": 1, "files": ["a.py", "b.py"], "created_files": ["c.py"], "functions": []}
    score = lfe.score_report(truth, {"files": ["a.py", "c.py", "d.py", "e.py"], "functions": [], "rationale": ""})
    assert (score["file_recall"], score["file_precision"]) == (0.5, 0.5)
    assert score["counts"] == {"truth_files": 2, "found_files": 1, "reported_files": 4, "correct_files": 2,
                               "truth_functions": 0, "found_functions": 0}
    empty = lfe.score_report(truth, {"files": [], "functions": [], "rationale": "nothing"})
    assert empty["file_precision"] == 0.0  # undefined ratio counted as 0


# ------------------------------------------------------------------------------- report

GOOD = {"files": ["./plugins/foundry/a.py", "plugins/foundry/a.py", " b.py "],
        "functions": [{"file": "a.py", "name": "f"}, {"file": "a.py", "name": "f"}], "rationale": " why "}


def test_report_validation_normalizes_and_rejects_bad_shapes():
    out = lfe.validate_report(GOOD)
    assert out == {"files": ["plugins/foundry/a.py", "b.py"], "functions": [{"file": "a.py", "name": "f"}],
                   "rationale": "why"}
    assert lfe.validate_report({"files": ["/work/bundle/x.py"], "functions": [], "rationale": ""},
                               root="/work/bundle")["files"] == ["x.py"]
    for bad in (None, [], {"files": []}, {**GOOD, "files": "a.py"}, {**GOOD, "files": [1]},
                {**GOOD, "functions": ["f"]}, {**GOOD, "functions": [{"file": "a"}]},
                {**GOOD, "rationale": None}):
        with pytest.raises(lfe.ReportError):
            lfe.validate_report(bad)


def test_report_extraction_from_a_final_message():
    body = json.dumps(GOOD)
    assert lfe.extract_report(body)["files"][0] == "plugins/foundry/a.py"
    assert lfe.extract_report(f"Here it is:\n```json\n{body}\n```\nDone.")["rationale"] == "why"
    draft = json.dumps({**GOOD, "files": ["old.py"]})
    assert lfe.extract_report(f"draft {draft} final {body}")["files"][0] == "plugins/foundry/a.py"  # the last wins
    for text in ("", "no json here", '{"files": ["a"]', '{"other": 1}', "[1, 2]"):
        with pytest.raises(lfe.ReportError):
            lfe.extract_report(text)


def _stream(path, events):
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


def test_load_report_sources_and_failures(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    body = json.dumps(GOOD)
    omp = _stream(tmp_path / "omp.jsonl", [
        {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "x"}]}},
        {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": body}]}}])
    claude = _stream(tmp_path / "claude.jsonl", [{"type": "assistant", "message": {"content": [
        {"type": "text", "text": "thinking"}]}}, {"type": "result", "result": body}])
    for stream, fmt in ((omp, "omp-json"), (claude, "claude-stream-json")):
        report, source, refusal = lfe.load_report(scratch, stream, fmt)
        assert report["rationale"] == "why" and source == "final_message" and refusal is None
    # the arm-written file wins over the stream
    (scratch / "report.json").write_text(json.dumps({**GOOD, "rationale": "from file"}), encoding="utf-8")
    assert lfe.load_report(scratch, omp, "omp-json")[:2] == (
        {"files": ["plugins/foundry/a.py", "b.py"], "functions": [{"file": "a.py", "name": "f"}],
         "rationale": "from file"}, "file")
    # an unusable file is a refusal, not a fallback to the stream
    (scratch / "report.json").write_text("{not json", encoding="utf-8")
    report, source, refusal = lfe.load_report(scratch, omp, "omp-json")
    assert report is None and source == "file" and "report.json unusable" in refusal
    (scratch / "report.json").unlink()
    # no file, no stream, an empty stream, a stream without a report: refusals, never an exception
    for stream in (tmp_path / "missing.jsonl", _stream(tmp_path / "empty.jsonl", []),
                   _stream(tmp_path / "prose.jsonl", [{"type": "message_end", "message": {
                       "role": "assistant", "content": "just prose"}}])):
        report, _, refusal = lfe.load_report(scratch, stream, "omp-json")
        assert report is None and refusal


def test_a_refused_or_contaminated_attempt_scores_zero_everywhere():
    truth = {"pr": 1, "files": ["a.py"], "created_files": [], "functions": [["a.py", "f"]]}
    good = {"files": ["a.py"], "functions": [{"file": "a.py", "name": "f"}], "rationale": ""}
    ok = lfe.verdict_of(truth, good, None)
    assert ok["verdict"] == "SCORED" and ok["file_recall"] == 1.0 and ok["note"] is None
    refused = lfe.verdict_of(truth, None, "no report file and no final message")
    assert refused["verdict"] == "REFUSED" and refused["file_recall"] == 0.0 and refused["function_recall"] == 0.0
    contaminated = lfe.verdict_of(truth, good, None, contaminated=True)
    assert contaminated["verdict"] == "CONTAMINATED" and contaminated["file_recall"] == 0.0
    assert contaminated["file_precision"] == 0.0 and "counted as 0" in contaminated["note"]


# --------------------------------------------------------------------- screening rule

RULE = CAMPAIGN["rules"]["exploration_screening"]


def _task(pr, fn, fn_truth=1, files_found=1, reported=1, correct=None, seconds=10.0, verdict="SCORED"):
    """A scored task: ``fn`` of ``fn_truth`` true functions found; one true file, ``files_found`` of it found."""
    correct = files_found if correct is None else correct
    return {"pr": pr, "wall_seconds": seconds, "judge": {
        "verdict": verdict, "file_recall": float(files_found),
        "file_precision": correct / reported if reported else 0.0,
        "function_recall": fn / fn_truth if fn_truth else None,
        "counts": {"truth_files": 1, "found_files": files_found, "reported_files": reported,
                   "correct_files": correct, "truth_functions": fn_truth, "found_functions": fn}}}


def _row(*found, **kw):
    return lfe.candidate_row([_task(i, f, **kw) for i, f in enumerate(found)])


def test_the_screening_values_are_the_validated_ones():
    assert RULE == {**RULE, "tasks": 6, "file_precision_min": 0.5, "retained_function_recall_min": 0.5}
    assert "retained_recall_min" not in RULE  # the file-recall threshold is gone
    assert CAMPAIGN["rules"]["exploration_comparison"]["premium_per_accepted_ratio_max"] == 0.85


def test_screening_selects_the_highest_mean_function_recall():
    rows = {"a": _row(1, 1, 1, 1, 0, 0), "b": _row(1, 1, 1, 1, 1, 0), "c": _row(1, 1, 0, 0, 0, 0)}
    out = lfe.apply_screening_rule(RULE, rows, True)
    assert out == {"selected": "b", "reason": "highest_mean_function_recall", "stop": None}
    assert rows["b"]["mean_function_recall"] == round(5 / 6, 6)
    assert list(rows["b"])[:2] == ["tasks", "mean_function_recall"]  # function recall comes first in the table


def test_perfect_file_recall_with_zero_function_recall_is_not_retained_over_function_recall():
    files_only = lfe.candidate_row([_task(i, 0, files_found=1) for i in range(6)])  # right file, no function
    some = lfe.candidate_row([_task(i, int(i < 4), files_found=0, reported=1, correct=1) for i in range(6)])
    assert files_only["mean_file_recall"] == 1.0 and files_only["mean_function_recall"] == 0.0
    assert some["mean_file_recall"] == 0.0 and some["mean_function_recall"] == round(4 / 6, 6)
    out = lfe.apply_screening_rule(RULE, {"files_only": files_only, "some": some}, True)
    assert out["selected"] == "some"
    alone = lfe.apply_screening_rule(RULE, {"files_only": files_only}, True)  # file recall no longer decides
    assert alone["selected"] is None and alone["stop"] == "keep_cloud"
    assert alone["reason"] == "retained_candidate_function_recall_below_minimum: keep_cloud"


def test_screening_tie_goes_to_the_shortest_total_duration_then_is_unresolved():
    fast = lfe.candidate_row([_task(i, 1, seconds=5.0) for i in range(6)])
    slow = lfe.candidate_row([_task(i, 1, seconds=9.0) for i in range(6)])
    assert lfe.apply_screening_rule(RULE, {"slow": slow, "fast": fast}, True) == {
        "selected": "fast", "reason": "tie_broken_by_total_duration", "stop": None}
    same = lfe.candidate_row([_task(i, 1, seconds=5.0) for i in range(6)])
    out = lfe.apply_screening_rule(RULE, {"x": same, "y": fast}, True)
    assert out["selected"] is None and out["reason"] == "tie_not_resolved" and out["tied"] == ["x", "y"]


def test_the_precision_floor_is_applied_before_the_function_recall_comparison():
    noisy = lfe.candidate_row([_task(i, 1, reported=4, correct=1) for i in range(6)])  # recall 1, precision 1/4
    modest = lfe.candidate_row([_task(i, int(i < 4)) for i in range(6)])  # function recall 2/3, precision 1
    assert noisy["mean_function_recall"] == 1.0 and noisy["mean_file_precision"] == 0.25
    assert lfe.apply_screening_rule(RULE, {"noisy": noisy, "modest": modest}, True)["selected"] == "modest"
    out = lfe.apply_screening_rule(RULE, {"noisy": noisy}, True)
    assert out == {"selected": None, "reason": "no_candidate_meets_precision_floor: keep_cloud", "stop": "keep_cloud"}


def test_the_boundaries_are_inclusive_and_exact():
    # function recall exactly 1/2 is retained (stop only under 0.5); precision exactly 1/2 passes
    exact = lfe.candidate_row([_task(i, 1, fn_truth=2, reported=2, correct=1) for i in range(6)])
    assert exact["mean_function_recall"] == 0.5 and exact["mean_file_precision"] == 0.5
    assert lfe.apply_screening_rule(RULE, {"c": exact}, True)["selected"] == "c"
    just_under_precision = lfe.candidate_row([_task(i, 1, fn_truth=2, reported=3, correct=1) for i in range(6)])
    assert lfe.apply_screening_rule(RULE, {"c": just_under_precision}, True)["stop"] == "keep_cloud"
    low = lfe.candidate_row([_task(i, int(i < 3), fn_truth=3 if i else 2) for i in range(6)])  # (1/2 + 1/3 + 1/3)/6 < 0.5
    out = lfe.apply_screening_rule(RULE, {"c": low}, True)
    assert out["selected"] is None and out["stop"] == "keep_cloud" and out["best_candidate"] == "c"
    assert out["reason"] == "retained_candidate_function_recall_below_minimum: keep_cloud"


def test_a_task_without_a_function_in_its_truth_is_excluded_from_the_function_mean():
    tasks = [_task(0, 1, fn_truth=1), _task(1, 0, fn_truth=0), _task(2, 1, fn_truth=1)]
    row = lfe.candidate_row(tasks)
    assert row["function_tasks"] == 2 and row["mean_function_recall"] == 1.0  # not 2/3
    assert row["tasks"] == 3
    none = lfe.candidate_row([_task(i, 0, fn_truth=0) for i in range(2)])
    assert none["mean_function_recall"] is None
    assert lfe.apply_screening_rule(RULE, {"c": none}, True) == {
        "selected": None, "reason": "no_function_in_the_ground_truth", "stop": None}


def test_a_contaminated_or_refused_attempt_counts_as_zero_and_never_blocks_the_screening():
    truth = {"pr": 1, "files": ["a.py"], "created_files": [], "functions": [["a.py", "f"]]}
    good = {"files": ["a.py"], "functions": [{"file": "a.py", "name": "f"}], "rationale": ""}
    tasks = [{"pr": i, "wall_seconds": 1.0, "judge": lfe.verdict_of(truth, good, None, contaminated=i == 0)}
             for i in range(6)]
    tasks[1]["judge"] = lfe.verdict_of(truth, None, "no report")
    row = lfe.candidate_row(tasks)
    assert row["contaminated"] == 1 and row["refused"] == 1 and row["mean_function_recall"] == round(4 / 6, 6)
    assert lfe.apply_screening_rule(RULE, {"c": row}, True)["selected"] == "c"  # 4/6 >= 0.5, not blocked


def test_nothing_is_selected_before_the_screening_is_complete():
    row = _row(1, 1, 1)
    assert lfe.apply_screening_rule(RULE, {"c": row}, False)["reason"] == "incomplete_screening"
    assert lfe.apply_screening_rule(RULE, {}, False)["reason"] == "no_screening_results"


# ------------------------------------------------------------------ dedicated machine

DEDICATED = CAMPAIGN["dedicated_machine"]
GIB = 1024 * 1024  # KiB


def _ps(*lines):
    return "\n".join(f"{rss} {cmd}" for rss, cmd in lines) + "\n"


def test_the_dedicated_machine_thresholds_are_the_validated_ones():
    assert DEDICATED["max_other_process_rss_gib"] == 2 and DEDICATED["min_free_percent"] == 50
    assert set(DEDICATED["allowed_command_patterns"]) == {"lm_studio", "launcher", "system"}


def test_a_dedicated_machine_passes_with_lm_studio_the_launcher_and_the_system_big():
    ps = _ps((30 * GIB, "/Applications/LM Studio.app/Contents/Frameworks/LM Studio Helper.app/Contents/MacOS/LM Studio Helper --type=x"),
             (4 * GIB, "/Users/x/.lmstudio/.internal/utils/node /Users/x/.lmstudio/server.js"),
             (3 * GIB, "/Users/x/.lmstudio/bin/lms server start"),
             (3 * GIB, "/opt/homebrew/bin/python3 -m foundry.local_first_runner screen-exploration"),
             (5 * GIB, "/System/Library/PrivateFrameworks/SkyLight.framework/Resources/WindowServer -daemon"),
             (3 * GIB, "/usr/libexec/something"), (3 * GIB, "kernel_task"),
             (2 * GIB, "/Applications/Safari.app/Contents/MacOS/Safari"),  # exactly 2 GiB: not over
             (100, "/bin/zsh -l"))
    out = lfe.check_dedicated_machine(DEDICATED, ps, "System-wide memory free percentage: 50%")
    assert out["ok"] and out["refusals"] == []
    assert out["observed"]["free_percent"] == 50 and out["observed"]["processes"] == 9
    assert out["observed"]["allowed_rss_mib"]["lm_studio"] == 37 * 1024
    assert out["observed"]["largest_other_processes"][0] == {"process": "Safari", "rss_mib": 2048}


def test_another_process_over_2_gib_refuses_and_names_only_the_process():
    secret = "sk-ant-secret-token-value"
    ps = _ps((2 * GIB + 1, f"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --token={secret}"),
             (3 * GIB, f"/opt/homebrew/bin/python3 -m other_tool --key {secret}"),
             (1 * GIB, "/usr/local/bin/small"))
    out = lfe.check_dedicated_machine(DEDICATED, ps, "System-wide memory free percentage: 90%")
    assert not out["ok"]
    assert out["refusals"] == ["dedicated_machine_process_over_2gib:python3:3072MiB",
                               "dedicated_machine_process_over_2gib:Google Chrome:2048MiB"]
    assert secret not in json.dumps(out)  # no argument is ever recorded


def test_free_memory_under_50_percent_refuses_and_an_unknown_value_refuses():
    ps = _ps((100, "/bin/zsh"))
    low = lfe.check_dedicated_machine(DEDICATED, ps, "System-wide memory free percentage: 49%")
    assert low["refusals"] == ["dedicated_machine_free_memory_below_minimum:49<50"]
    assert lfe.check_dedicated_machine(DEDICATED, ps, "System-wide memory free percentage: 50%")["ok"]
    assert lfe.check_dedicated_machine(DEDICATED, ps, None)["refusals"] == [
        "dedicated_machine_memory_pressure_unavailable"]
    assert lfe.check_dedicated_machine(DEDICATED, ps, "garbage")["ok"] is False
    gone = lfe.check_dedicated_machine(DEDICATED, None, "System-wide memory free percentage: 80%")
    assert gone["refusals"] == ["dedicated_machine_ps_unavailable"]
    junk = lfe.check_dedicated_machine(DEDICATED, "not a ps line\n12 ok\n", "System-wide memory free percentage: 80%")
    assert junk["ok"] and junk["observed"]["unparsed_lines"] == 1


# ------------------------------------------------------------------- comparison rules

RC = CAMPAIGN["rules"]["exploration_comparison"]


def _t(accepted, premium, unknown=False):
    return {"accepted": accepted, "unknown": unknown, "premium_billing_tokens": premium}


def test_quality_is_the_acceptance_rate_with_undecided_tasks_as_bounds():
    ok = [_t(True, 1)] * 2
    assert lfe.quality_verdict(ok, ok) == "pass"
    assert lfe.quality_verdict(ok, [_t(True, 1), _t(False, 1)]) == "fail"
    assert lfe.quality_verdict([_t(True, 1), _t(False, 1)], ok) == "pass"  # better than A
    undecided = [_t(True, 1), _t(False, None, unknown=True)]
    assert lfe.quality_verdict(ok, undecided) == "unavailable"  # could still reach A's rate
    assert lfe.quality_verdict([_t(True, 1), _t(True, 1)], [_t(False, 1), _t(False, None, unknown=True)]) == "fail"
    assert lfe.quality_verdict([], []) == "unavailable"


def test_economy_is_premium_per_accepted_task_against_085_of_a():
    a = [_t(True, 200), _t(True, 200)]  # 200 per accepted
    assert lfe.economy_verdict(RC, a, [_t(True, 170), _t(True, 170)], False)["verdict"] == "pass"  # exactly 0.85
    out = lfe.economy_verdict(RC, a, [_t(True, 171), _t(True, 171)], False)
    assert out["verdict"] == "fail" and out["detail"]["premium_pass"] is False
    # the per-accepted figure, not the total: fewer accepted tasks cost more per accepted task
    assert lfe.economy_verdict(RC, a, [_t(True, 100), _t(False, 100)], False)["verdict"] == "fail"  # 200 per accepted
    detail = lfe.economy_verdict(RC, a, [_t(True, 100), _t(True, 100)], False)["detail"]
    assert detail["premium_per_accepted"] == 100.0 and detail["reference_premium_per_accepted"] == 200.0
    assert detail["ratio"] == 0.5


def test_unknown_premium_stays_unknown_and_the_economy_is_unavailable_never_zero():
    a = [_t(True, 200), _t(True, 200)]
    for x in ([_t(True, None), _t(True, 100)],  # a premium total that was not read
              [_t(True, 100), _t(False, 100, unknown=True)],  # a task that is undecided
              [_t(False, 100), _t(False, 100)]):  # no accepted task: no per-accepted figure
        assert lfe.economy_verdict(RC, a, x, False)["verdict"] == "unavailable"
    assert lfe.economy_verdict(RC, [_t(True, None), _t(True, 1)], [_t(True, 1)] * 2, False)["verdict"] == "unavailable"
    assert lfe.economy_verdict(RC, a, [_t(True, 1)] * 2, True)["verdict"] == "unavailable"  # unknown spent work
    assert lfe.economy_verdict(RC, a, [_t(True, 1)] * 2, False)["detail"]["premium_billing_tokens"] == 2
    assert lfe.economy_verdict(RC, a, [_t(True, None)] * 2, False)["detail"]["premium_billing_tokens"] is None


def test_the_report_section_is_deterministic_and_says_when_it_cuts():
    limits = CAMPAIGN["exploration"]["report_render_limits"]
    report = {"files": [f"f{i}.py" for i in range(25)], "functions": [{"file": "f0.py", "name": "g"}] * 3,
              "rationale": "r" * 2000}
    text = lfe.render_report_section(report, limits)
    assert text == lfe.render_report_section(report, limits)
    assert "- f19.py" in text and "- f20.py" not in text and "5 more not shown" in text
    assert text.count("r") >= 1500 and "r" * 1501 not in text
    assert "(none reported)" in lfe.render_report_section({"files": [], "functions": [], "rationale": ""}, limits)
    assert "read-only explorer" in text and "can be incomplete or wrong" in text


def test_fraction_arithmetic_is_exact_for_the_ratio_boundary():
    assert Fraction(str(0.85)) == Fraction(17, 20)
    a = [_t(True, 1000)] * 3
    assert lfe.economy_verdict(RC, a, [_t(True, 850)] * 3, False)["verdict"] == "pass"
    assert lfe.economy_verdict(RC, a, [_t(True, 851)] * 3, False)["verdict"] == "fail"
