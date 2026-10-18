#!/usr/bin/env python3
"""witness: prove a change of an iOS app in the simulator with xcodebuild and xcrun simctl only (PAT-254)."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from witness import checkout, compare, prove, simulator

EXIT_USAGE, EXIT_REFUSED, EXIT_FAILED, EXIT_TESTS_FAILED = 2, 3, 5, 6


def fail(msg: str, code: int) -> int:
    print(f"witness: {msg}", file=sys.stderr)
    return code


def cmd_prove(args: argparse.Namespace) -> int:
    if args.reset and args.state_description:
        return fail("--reset and --state-description are exclusive", EXIT_USAGE)
    try:
        rep, j, m, code = prove.prove(
            project=Path(args.project), scheme=args.scheme, sim_name=args.simulator,
            runtime=args.runtime, label=args.label, out=Path(args.out) if args.out else None,
            skip_tests=args.skip_tests, test_plan=args.test_plan, only=args.only_testing,
            reset=args.reset, state_description=args.state_description)
    except (checkout.Refused, simulator.SimulatorError) as exc:
        return fail(f"refused: {exc}", EXIT_REFUSED)
    print(f"witness: {rep['outcome']} on {rep['simulator']['name']} {rep['simulator']['udid']}")
    print(f"report: {j}\nreport: {m}")
    return code


def cmd_compare(args: argparse.Namespace) -> int:
    try:
        before, after = compare.load(Path(args.before)), compare.load(Path(args.after))
        compare.check(before, after)
    except compare.Incomparable as exc:
        return fail(f"refused: {exc}", EXIT_REFUSED)
    result = compare.build(before, after)
    out = Path(args.out) if args.out else Path(args.after).resolve().parent
    out.mkdir(parents=True, exist_ok=True)
    stem = "compare-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (out / f"{stem}.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / f"{stem}.md").write_text(compare.markdown(result), encoding="utf-8")
    print(f"report: {out / (stem + '.json')}\nreport: {out / (stem + '.md')}")
    print(f"screenshots identical: {result['screenshots_identical']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="witness_cli.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("prove", help="test, install, launch and screenshot an app in a named simulator")
    pr.add_argument("--project", required=True, help=".xcodeproj or .xcworkspace inside a linked worktree or a directory outside git")
    pr.add_argument("--scheme", required=True)
    pr.add_argument("--simulator", required=True, help="simulator name (or UDID)")
    pr.add_argument("--runtime", help="runtime filter (e.g. 27.0) when several simulators share the name")
    pr.add_argument("--label", default="proof", help="report and screenshot name prefix (e.g. before, after)")
    pr.add_argument("--out", help="output directory (default: a new directory in the system temp dir)")
    pr.add_argument("--skip-tests", action="store_true")
    pr.add_argument("--test-plan")
    pr.add_argument("--only-testing", action="append", default=[], metavar="TARGET[/CLASS[/TEST]]")
    pr.add_argument("--reset", action="store_true", help="simctl erase the (shut down) simulator first")
    pr.add_argument("--state-description", help="describe the starting state instead of resetting")
    pr.set_defaults(fn=cmd_prove)
    cp = sub.add_parser("compare", help="compare a before and an after proof report")
    cp.add_argument("--before", required=True)
    cp.add_argument("--after", required=True)
    cp.add_argument("--out", help="output directory (default: the directory of the after report)")
    cp.set_defaults(fn=cmd_compare)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
