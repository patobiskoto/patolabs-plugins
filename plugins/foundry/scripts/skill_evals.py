#!/usr/bin/env python3
"""Launcher of the Foundry skill-selection evals (PAT-184), on top of `claude plugin eval`.

It builds no runner.  It prints the plan and the exact argv, refuses to start a paid run
unless the operator authorises the budget on the terminal, and reads the JSON result of
`claude plugin eval` back into verdicts.  A gap is a "regression to examine", never a
promotion or a removal.  See docs/skill-evals.md.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
EVALS_DIR = PLUGIN_ROOT / "evals"
DEFAULT_MODEL = "haiku"
# Outside `evals/`: `claude plugin eval` walks that folder, and an installed plugin dir is a cache (docs/skill-evals.md).
DEFAULT_OUT = Path(tempfile.gettempdir()) / "foundry-skill-evals" / "skill-evals.json"
PASS_THRESHOLD = 1.0  # a case passes only at this score; a lower `suite.threshold` in the result never relaxes it
# The one run error that is the expected end of a `max_turns: 1` run (observed 2026-10-17, docs/skill-evals.md).
MAX_TURNS_ERROR = re.compile(r"(?:exit \d+: )?Reached maximum number of turns \(\d+\)")
RESULT_SCHEMA_VERSION = 1
# Documented prompt.md frontmatter keys and the graders that cost no model call.
ALLOWED_PROMPT_KEYS = frozenset(
    {"max_turns", "timeout_seconds", "runs", "allowed_tools", "model", "tags", "plugins", "env"}
)
DETERMINISTIC_GRADERS = frozenset({"regex", "tool_used", "tool_order", "file_exists"})

PASS = "pass"
REGRESSION = "regression to examine"
UNKNOWN = "unknown"


class Refused(Exception):
    """The launch is refused; the message says why."""


def parse_frontmatter(text: str) -> dict[str, str]:
    """Flat `key: value` lines between the first two `---` lines (raw string values)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing frontmatter")
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return out
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep or line[0] in " \t":
            raise ValueError(f"unsupported frontmatter line: {line!r}")
        out[key.strip()] = value.strip()
    raise ValueError("unterminated frontmatter")


def discover_cases(evals_dir: Path = EVALS_DIR) -> list[dict]:
    """One entry per case directory: name, runs, max_turns."""
    cases = []
    for prompt in sorted(evals_dir.glob("*/prompt.md")):
        front = parse_frontmatter(prompt.read_text(encoding="utf-8"))
        cases.append({
            "name": prompt.parent.name,
            "runs": int(front.get("runs", "3")),  # assumed default when absent, not sourced; every shipped case sets runs: 1
            "max_turns": int(front.get("max_turns", "10")),
        })
    return cases


def build_argv(model: str, max_cost: str, out: Path, plugin_dir: Path = PLUGIN_ROOT) -> list[str]:
    return [
        "claude", "plugin", "eval", str(plugin_dir),
        "--model", model,
        "--ablation", "none",
        "--runs", "1",
        "--no-publish",
        "--json", str(out),
        "--max-cost-usd", max_cost,
        "--trust-plugin",
    ]


def parse_amount(raw: str | None) -> Decimal:
    if raw is None:
        raise Refused("--max-cost-usd is required: no budget, no launch")
    try:
        amount = Decimal(raw.strip())
    except InvalidOperation:
        raise Refused(f"--max-cost-usd is not a number: {raw!r}") from None
    if not amount.is_finite() or amount <= 0:
        raise Refused("--max-cost-usd must be a finite amount greater than 0")
    return amount


def agent_markers(environ=None) -> list[str]:
    """Names (never values) of agent-session markers: CLAUDECODE, CODEX_*."""
    env = os.environ if environ is None else environ
    return sorted(name for name in env if name == "CLAUDECODE" or name.startswith("CODEX_"))


def _open_tty():
    return open("/dev/tty", "r", encoding="utf-8"), open("/dev/tty", "w", encoding="utf-8")


def _stdio_is_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def confirm_budget(amount: Decimal, environ=None) -> None:
    """The operator retypes the amount on the controlling terminal, or the launch is refused."""
    markers = agent_markers(environ)
    if markers:
        raise Refused("refused under an agent session (" + ", ".join(markers) + ")")
    if not _stdio_is_tty():
        raise Refused("refused: stdin and stdout must be a terminal")
    try:
        reader, writer = _open_tty()
    except OSError:
        raise Refused("refused: no controlling terminal (/dev/tty)") from None
    with reader, writer:
        writer.write(f"This run may bill up to {amount} USD (list-price estimate).\n"
                     f"Retype the amount to authorise it: ")
        writer.flush()
        typed = reader.readline().strip()
    try:
        same = Decimal(typed) == amount
    except InvalidOperation:
        same = False
    if not same:
        raise Refused("the retyped amount does not match: nothing launched")


def _case_verdict(case: dict, threshold: float, partial: bool) -> tuple[str, str]:
    runs = case.get("arms", {}).get("with", [])
    if not runs:
        return UNKNOWN, "no run recorded"
    errors = [r["error"] for r in runs if r.get("error")]
    graders = [g for r in runs for g in r.get("graders", [])]
    all_passed = bool(graders) and all(g.get("passed") is True for g in graders)
    # The max-turns error ends a max_turns: 1 run after the Skill call the grader checks: expected, but only when
    # every grader passed; any other error stays unknown.
    expected_end = bool(errors) and all(isinstance(e, str) and MAX_TURNS_ERROR.fullmatch(e) for e in errors)
    if errors and not (expected_end and all_passed):
        return UNKNOWN, f"run error: {errors[0]}"
    score = case.get("aggregates", {}).get("score")
    if not isinstance(score, (int, float)) or isinstance(score, bool) or math.isnan(score):
        return UNKNOWN, "no score"
    if score >= threshold and all_passed:
        if partial:
            return UNKNOWN, f"score {score:g} but the run is partial"
        return PASS, f"score {score:g}" + (" (run ended on the expected max-turns limit)" if errors else "")
    if score >= threshold:
        return UNKNOWN, f"score {score:g} but no grader result shows every grader passed"
    failed = sorted({g["name"] for r in runs for g in r.get("graders", []) if not g.get("passed")})
    return REGRESSION, f"score {score:g} < {threshold:g}; failing graders: {', '.join(failed) or '?'}"


def _unreadable(expected: list[str], note: str) -> dict:
    return {"overall": UNKNOWN, "note": note, "cases": {name: (UNKNOWN, "no readable result") for name in expected}}


def interpret(result: object, expected: list[str]) -> dict:
    """Verdicts of one aggregate-result.json.  Anything not shown to pass is `unknown`, a result of an unexpected
    shape included."""
    try:
        return _interpret(result, expected)
    except (AttributeError, TypeError, KeyError, ValueError):
        return _unreadable(expected, "result of an unexpected shape")


def _interpret(result: object, expected: list[str]) -> dict:
    if not isinstance(result, dict) or result.get("schemaVersion") != RESULT_SCHEMA_VERSION:
        return _unreadable(expected, "unreadable result or unexpected schemaVersion")
    if not expected:
        return _unreadable(expected, "no eval case found (no prompt.md): nothing was checked")
    partial = bool(result.get("partial"))
    declared = result.get("suite", {}).get("threshold")
    threshold = max(PASS_THRESHOLD, float(declared)) if isinstance(declared, (int, float)) and not isinstance(
        declared, bool) else PASS_THRESHOLD
    by_name = {c.get("name"): c for c in result.get("cases", []) if isinstance(c, dict)}
    verdicts = {}
    for name in expected:
        case = by_name.get(name)
        verdicts[name] = (UNKNOWN, "case absent from the result") if case is None else _case_verdict(
            case, threshold, partial)
    kinds = {v for v, _ in verdicts.values()}
    overall = REGRESSION if REGRESSION in kinds else UNKNOWN if UNKNOWN in kinds or partial else PASS
    note = f"partial run ({result.get('partialReason', 'reason unknown')})" if partial else ""
    return {"overall": overall, "note": note, "cases": verdicts,
            "costUsd": result.get("costUsd"), "claudeVersion": result.get("claudeVersion")}


def report(summary: dict) -> str:
    out = []
    for name, (verdict, detail) in summary["cases"].items():
        suffix = "  <- nothing is promoted or removed automatically" if verdict == REGRESSION else ""
        out.append(f"  {name}: {verdict} ({detail}){suffix}")
    out.append(f"overall: {summary['overall']}")
    for key, label in (("note", "note"), ("costUsd", "cost (USD, as reported)"),
                       ("claudeVersion", "claude version")):
        if summary.get(key) not in (None, ""):
            out.append(f"{label}: {summary[key]}")
    return "\n".join(out)


def print_plan(argv: list[str], cases: list[dict], model: str, max_cost: str | None) -> None:
    print("Foundry skill evals (claude plugin eval, deterministic graders only)")
    print(f"model (pinned): {model}")
    print("max cost: " + (f"{max_cost} USD" if max_cost is not None else "NOT SET - a launch is refused"))
    for case in cases:
        print(f"  case {case['name']}: runs={case['runs']} max_turns={case['max_turns']}")
    print("argv: " + " ".join(argv))


def main(args: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-cost-usd", help="budget ceiling, retyped on the terminal to launch")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--dry-run", action="store_true", help="print the plan only")
    parser.add_argument("--read", type=Path, help="interpret an existing result file, launch nothing")
    ns = parser.parse_args(args)
    try:
        cases = discover_cases()
    except (OSError, ValueError) as exc:
        print(f"unknown: the eval cases cannot be read ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 2
    names = [c["name"] for c in cases]
    if ns.read is not None:
        try:
            summary = interpret(json.loads(ns.read.read_text(encoding="utf-8")), names)
        except (OSError, ValueError):
            summary = interpret(None, names)
    else:
        argv = build_argv(ns.model, ns.max_cost_usd or "<amount>", ns.out)
        print_plan(argv, cases, ns.model, ns.max_cost_usd)
        if ns.dry_run:
            return 0
        try:
            amount = parse_amount(ns.max_cost_usd)
            argv = build_argv(ns.model, format(amount, "f"), ns.out)
            confirm_budget(amount)
        except Refused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 3
        try:
            ns.out.parent.mkdir(parents=True, exist_ok=True)
            ns.out.unlink(missing_ok=True)  # never read a stale result
            subprocess.run(argv, check=False)
        except OSError as exc:  # no result directory, or `claude` absent from PATH: nothing ran, not a regression
            print(f"unknown: the eval could not be started ({type(exc).__name__})", file=sys.stderr)
            return 2
        try:
            summary = interpret(json.loads(ns.out.read_text(encoding="utf-8")), names)
        except (OSError, ValueError):
            summary = interpret(None, names)
    print(report(summary))
    return {PASS: 0, REGRESSION: 1}.get(summary["overall"], 2)


if __name__ == "__main__":
    sys.exit(main())
