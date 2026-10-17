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
