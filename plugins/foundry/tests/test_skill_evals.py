"""PAT-184: offline checks of the skill evals and of their launcher.  No `claude` call."""
import importlib.util
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("skill_evals", PLUGIN_ROOT / "scripts" / "skill_evals.py")
se = importlib.util.module_from_spec(_spec)
sys.modules["skill_evals"] = se
_spec.loader.exec_module(se)

FIXTURES = PLUGIN_ROOT / "tests" / "fixtures"
CASE_DIRS = sorted(p.parent for p in (PLUGIN_ROOT / "evals").glob("*/prompt.md"))
NAMES = [d.name for d in CASE_DIRS]


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_between_three_and_five_cases():
    assert 3 <= len(CASE_DIRS) <= 5


@pytest.mark.parametrize("case", CASE_DIRS, ids=NAMES)
def test_case_frontmatter_and_graders_are_valid_and_deterministic(case):
    front = se.parse_frontmatter((case / "prompt.md").read_text(encoding="utf-8"))
    assert set(front) <= se.ALLOWED_PROMPT_KEYS
    assert front["runs"] == "1" and int(front["max_turns"]) <= 5
    graders = sorted((case / "graders").glob("*.md"))
    assert graders
    for grader in graders:
        gfront = se.parse_frontmatter(grader.read_text(encoding="utf-8"))
        assert gfront["type"] in se.DETERMINISTIC_GRADERS


@pytest.mark.parametrize("case", CASE_DIRS, ids=NAMES)
def test_skill_selection_case_targets_an_existing_skill(case):
    grader = (case / "graders" / "skill-invoked.md").read_text(encoding="utf-8")
    assert case.name in grader
    assert (PLUGIN_ROOT / "skills" / case.name / "SKILL.md").is_file()


def test_argv_is_exactly_the_documented_one(tmp_path):
    out = tmp_path / "r.json"
    assert se.build_argv("haiku", "2.5", out, Path("/p")) == [
        "claude", "plugin", "eval", "/p", "--model", "haiku", "--ablation", "none",
        "--runs", "1", "--no-publish", "--json", str(out), "--max-cost-usd", "2.5",
        "--trust-plugin",
    ]


def test_dry_run_prints_the_plan_and_launches_nothing(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("launched"))
    assert se.main(["--dry-run"]) == 0
    printed = capsys.readouterr().out
    assert "plugin eval" in printed and "--ablation none" in printed and "NOT SET" in printed


def test_launch_refused_without_max_cost(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("launched"))
    assert se.main([]) == 3
    assert "--max-cost-usd is required" in capsys.readouterr().err


@pytest.mark.parametrize("raw", ["0", "-1", "abc", "nan", "inf"])
def test_bad_amounts_are_refused(raw):
    with pytest.raises(se.Refused):
        se.parse_amount(raw)


def test_refused_under_an_agent_marker():
    with pytest.raises(se.Refused, match="agent session"):
        se.confirm_budget(Decimal("1"), {"CLAUDECODE": "1"})
    with pytest.raises(se.Refused, match="agent session"):
        se.confirm_budget(Decimal("1"), {"CODEX_HOME": "x"})


def test_refused_without_a_tty(monkeypatch):
    monkeypatch.setattr(se, "_stdio_is_tty", lambda: False)
    with pytest.raises(se.Refused, match="terminal"):
        se.confirm_budget(Decimal("1"), {})


def _fake_tty(monkeypatch, typed):
    import io
    monkeypatch.setattr(se, "_stdio_is_tty", lambda: True)
    monkeypatch.setattr(se, "_open_tty", lambda: (io.StringIO(typed + "\n"), io.StringIO()))


def test_retyped_amount_must_match(monkeypatch):
    _fake_tty(monkeypatch, "2")
    with pytest.raises(se.Refused, match="does not match"):
        se.confirm_budget(Decimal("2.5"), {})
    _fake_tty(monkeypatch, "2.50")
    se.confirm_budget(Decimal("2.5"), {})


def test_launch_runs_the_argv_only_after_confirmation(monkeypatch, tmp_path):
    seen = []
    _fake_tty(monkeypatch, "1")
    monkeypatch.delenv("CLAUDECODE", raising=False)
    for name in [n for n in __import__("os").environ if n.startswith("CODEX_")]:
        monkeypatch.delenv(name)
    out = tmp_path / "r.json"
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: seen.append(argv) or out.write_text(
        (FIXTURES / "skill_evals_pass.json").read_text(encoding="utf-8")))
    assert se.main(["--max-cost-usd", "1", "--out", str(out)]) == 0
    assert len(seen) == 1 and seen[0][seen[0].index("--max-cost-usd") + 1] == "1"


def test_all_pass():
    summary = se.interpret(load("skill_evals_pass.json"), NAMES)
    assert summary["overall"] == se.PASS


def test_a_gap_is_a_regression_to_examine():
    summary = se.interpret(load("skill_evals_regression.json"), NAMES)
    assert summary["overall"] == se.REGRESSION
    assert summary["cases"]["intake"][0] == se.REGRESSION
    text = se.report(summary)
    assert "regression to examine" in text and "nothing is promoted or removed" in text
    assert "promot" not in text.replace("nothing is promoted or removed automatically", "")


def test_partial_and_missing_are_unknown_never_pass():
    summary = se.interpret(load("skill_evals_partial.json"), NAMES)
    assert summary["overall"] == se.UNKNOWN
    assert all(v[0] == se.UNKNOWN for v in summary["cases"].values())
    assert "cost_ceiling" in summary["note"]


@pytest.mark.parametrize("bad", [None, [], {"schemaVersion": 2}])
def test_unreadable_result_is_unknown(bad):
    assert se.interpret(bad, NAMES)["overall"] == se.UNKNOWN


# --- PAT-244: remarks deferred from the review of PAT-184 -------------------------------------------------------
def _result(case_overrides=None, **top):
    """The shape of tests/fixtures/skill_evals_pass.json with one case name list, then overrides applied to every case."""
    result = load("skill_evals_pass.json")
    for case in result["cases"]:
        for key, value in (case_overrides or {}).items():
            case[key] = value
    result.update(top)
    return result


MAX_TURNS = "exit 1: Reached maximum number of turns (1)"


def _with_run(**run):
    arms = {"with": [{"score": 1.0, "passed": True, "error": None,
                      "graders": [{"name": "skill-invoked", "passed": True, "weight": 1}], **run}]}
    return {"arms": arms}


def test_the_observed_max_turns_error_is_expected_when_every_grader_passed():
    # Observed 2026-10-17 (docs/skill-evals.md): all five graders passed, every run carried this error.
    summary = se.interpret(_result(_with_run(error=MAX_TURNS)), NAMES)
    assert summary["overall"] == se.PASS
    assert all(v[0] == se.PASS and "max-turns" in v[1] for v in summary["cases"].values())


def test_the_max_turns_error_with_a_failed_grader_is_not_a_pass():
    run = _with_run(error=MAX_TURNS)
    run["arms"]["with"][0]["graders"][0]["passed"] = False
    summary = se.interpret(_result(run), NAMES)
    assert summary["overall"] == se.UNKNOWN and se.PASS not in {v[0] for v in summary["cases"].values()}


@pytest.mark.parametrize("error", ["exit 1: authentication failed", "Reached maximum number of turns (1) and more",
                                   "exit 2: Reached maximum number of budget", 3])
def test_any_other_run_error_stays_unknown_even_with_a_score_of_one(error):
    summary = se.interpret(_result(_with_run(error=error)), NAMES)
    assert summary["overall"] == se.UNKNOWN
    assert all(v[0] == se.UNKNOWN and v[1].startswith("run error") for v in summary["cases"].values())


def test_a_run_error_with_a_bare_max_turns_text_and_no_grader_result_is_unknown():
    run = {"arms": {"with": [{"error": MAX_TURNS, "graders": []}]}}
    assert se.interpret(_result(run), NAMES)["overall"] == se.UNKNOWN


def test_a_case_without_a_score_is_unknown():
    case = {"aggregates": {}}
    summary = se.interpret(_result(case), NAMES)
    assert summary["overall"] == se.UNKNOWN and all(v[1] == "no score" for v in summary["cases"].values())
    nan = se.interpret(_result({"aggregates": {"score": float("nan")}}), NAMES)
    assert nan["overall"] == se.UNKNOWN


def test_a_case_without_a_run_is_unknown():
    summary = se.interpret(_result({"arms": {"with": []}}), NAMES)
    assert summary["overall"] == se.UNKNOWN and all(v[1] == "no run recorded" for v in summary["cases"].values())


def test_a_low_suite_threshold_never_relaxes_the_pass_condition():
    # A score under 1.0 with a grader that failed: a threshold of 0 in the result must not make it pass.
    case = {"aggregates": {"score": 0.4}, "arms": {"with": [{"error": None, "graders": [
        {"name": "skill-invoked", "passed": False}]}]}}
    for threshold in (0, 0.0, 0.5, -1, "0", None, True):
        result = _result(case)
        result["suite"]["threshold"] = threshold
        summary = se.interpret(result, NAMES)
        assert summary["overall"] == se.REGRESSION, threshold
    assert se.PASS_THRESHOLD == 1.0


def test_a_score_at_the_threshold_without_a_passed_grader_is_unknown():
    case = {"aggregates": {"score": 1.0}, "arms": {"with": [{"error": None, "graders": [
        {"name": "skill-invoked", "passed": False}]}]}}
    summary = se.interpret(_result(case), NAMES)
    assert summary["overall"] == se.UNKNOWN
    assert all("no grader result" in v[1] for v in summary["cases"].values())


def test_a_threshold_above_one_still_applies():
    result = _result()
    result["suite"]["threshold"] = 1.5
    assert se.interpret(result, NAMES)["overall"] == se.REGRESSION


@pytest.mark.parametrize("bad", [{"schemaVersion": 1, "cases": [{"name": "open-pr", "arms": 3}]},
                                 {"schemaVersion": 1, "cases": [{"name": "open-pr", "arms": {"with": [5]}}]},
                                 {"schemaVersion": 1, "cases": 7}, {"schemaVersion": 1, "suite": 3, "cases": []},
                                 {"schemaVersion": 1, "cases": [{"name": "open-pr", "aggregates": {"score": 1},
                                                                 "arms": {"with": [{"graders": [3]}]}}]}])
def test_a_result_of_an_unexpected_shape_is_unknown_not_a_crash(bad):
    assert se.interpret(bad, NAMES)["overall"] == se.UNKNOWN


def test_no_case_found_is_unknown_even_with_a_complete_result():
    summary = se.interpret(load("skill_evals_pass.json"), [])
    assert summary["overall"] == se.UNKNOWN and "no eval case" in summary["note"]


def test_the_default_output_is_outside_the_evals_folder_that_the_tool_walks():
    assert se.EVALS_DIR not in se.DEFAULT_OUT.parents and PLUGIN_ROOT not in se.DEFAULT_OUT.parents


def test_the_plan_names_an_unset_budget_without_a_stray_unit(capsys):
    se.print_plan(["claude"], [], "haiku", None)
    line = next(x for x in capsys.readouterr().out.splitlines() if x.startswith("max cost"))
    assert line == "max cost: NOT SET - a launch is refused"
    se.print_plan(["claude"], [], "haiku", "2")
    assert "max cost: 2 USD" in capsys.readouterr().out


def test_claude_absent_from_the_path_exits_two_not_one(monkeypatch, tmp_path, capsys):
    _fake_tty(monkeypatch, "1")
    for name in [n for n in __import__("os").environ if n == "CLAUDECODE" or n.startswith("CODEX_")]:
        monkeypatch.delenv(name)

    def missing(argv, **kw):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(subprocess, "run", missing)
    assert se.main(["--max-cost-usd", "1", "--out", str(tmp_path / "r.json")]) == 2
    assert "could not be started" in capsys.readouterr().err


def test_an_unreadable_prompt_frontmatter_exits_two_not_one(monkeypatch, capsys):
    def bad(*a, **k):
        raise ValueError("unterminated frontmatter")

    monkeypatch.setattr(se, "discover_cases", bad)
    assert se.main(["--dry-run"]) == 2
    assert "cannot be read" in capsys.readouterr().err


def test_a_result_read_from_a_file_of_an_unexpected_shape_exits_two(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"schemaVersion": 1, "cases": [{"name": "open-pr", "arms": 3}]}), encoding="utf-8")
    assert se.main(["--read", str(path)]) == 2


def test_every_shipped_case_sets_its_own_runs_so_the_assumed_default_is_never_used():
    for case in CASE_DIRS:
        assert "runs" in se.parse_frontmatter((case / "prompt.md").read_text(encoding="utf-8"))
    assert [c["runs"] for c in se.discover_cases()] == [1] * len(CASE_DIRS)


@pytest.mark.parametrize("case", CASE_DIRS, ids=NAMES)
def test_the_input_match_targets_the_json_serialisation_of_the_skill_field(case):
    import re
    pattern = se.parse_frontmatter((case / "graders" / "skill-invoked.md").read_text(encoding="utf-8"))["input_match"]
    pattern = pattern.strip("'")
    for serialised in (f'{{"skill": "{case.name}"}}', f'{{"skill":"foundry:{case.name}"}}'):
        assert re.search(pattern, serialised)
    assert not re.search(pattern, f'{{"skill": "other-{case.name}"}}')
