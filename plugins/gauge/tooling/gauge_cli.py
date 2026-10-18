#!/usr/bin/env python3
"""gauge: read-only quota counters (PAT-246)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gauge import check as gcheck
from gauge import readers
from gauge.render import render


def collect(args: argparse.Namespace) -> list[dict]:
    if args.probe_claude:
        claude = readers.read_claude_probe(args.probe_budget_usd)
    elif args.claude_statusline_json:
        claude = readers.read_claude_statusline(args.claude_statusline_json)
    else:
        claude = readers.read_claude_streams(args.claude_stream)
    return [
        r.as_dict()
        for r in (
            readers.read_codex(),
            claude,
            readers.read_linear(),
            readers.read_github_actions(),
        )
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gauge_cli")
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="print the four quota counters")
    status.add_argument("--json", action="store_true")
    status.add_argument("--claude-stream", action="append", default=[], metavar="PATH")
    status.add_argument("--claude-statusline-json", metavar="PATH")
    status.add_argument("--probe-claude", action="store_true",
                        help="paid, opt-in: run one minimal claude -p call")
    status.add_argument("--probe-budget-usd", metavar="AMOUNT",
                        help="required with --probe-claude (passed as --max-budget-usd)")
    chk = sub.add_parser("check", help="warn when a counter reaches its threshold (exit 0)")
    chk.add_argument("--config", metavar="PATH", help="configuration file (default: see README)")
    args = parser.parse_args(argv)
    if args.command == "check":
        cfg, problems = gcheck.load_config(
            Path(args.config) if args.config else gcheck.config_path())
        warnings, monitored = gcheck.check(cfg)
        lines = [f"gauge config: {p}" for p in problems] + [f"gauge: {w}" for w in warnings]
        if not lines:
            lines = ["gauge: all monitored counters read and below threshold"
                     if monitored else "gauge: no counter monitored (all thresholds null)"]
        print("\n".join(lines))
        return 0
    readings = collect(args)
    print(json.dumps(readings, indent=2) if args.json else render(readings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
