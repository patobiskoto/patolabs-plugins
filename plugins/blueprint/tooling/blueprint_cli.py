#!/usr/bin/env python3
"""blueprint: pinned REA/Ghidra tooling, authorised-targets registry, reproducible inventory (PAT-252)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from blueprint import inventory, paths, registry, setup

EXIT_USAGE, EXIT_REFUSED, EXIT_NO_TOOLS, EXIT_FAILED = 2, 3, 4, 5


def fail(msg: str, code: int = EXIT_USAGE) -> int:
    print(f"blueprint: {msg}", file=sys.stderr)
    return code


def cmd_setup(args: argparse.Namespace) -> int:
    d = paths.data_dir()
    if args.plan or not args.yes:
        print(setup.render_plan(d))
        if not args.plan:
            print("\nNothing was done. Re-run with --yes to perform these steps.")
        return 0
    print(setup.render_plan(d))
    try:
        setup.perform(d)
    except setup.SetupError as exc:
        return fail(f"setup stopped: {exc}", EXIT_FAILED)
    return 0


def cmd_targets(args: argparse.Namespace) -> int:
    reg = paths.registry_file(paths.data_dir())
    try:
        if args.action == "add":
            entry = registry.add(reg, args.target, args.reason, args.date)
            print(f"blueprint: authorised {entry['target']} ({entry['date']}): {entry['reason']}")
            return 0
        entries = registry.load(reg)
    except registry.RegistryError as exc:
        return fail(str(exc))
    for e in entries:
        print(f"{e['date']}  {e['target']}  {e['reason']}")
    if not entries:
        print("blueprint: no authorised target.")
    return 0


def cmd_inventory(args: argparse.Namespace) -> int:
    d = paths.data_dir()
    try:
        entry = registry.find(paths.registry_file(d), args.target)
    except registry.RegistryError as exc:
        return fail(str(exc), EXIT_REFUSED)
    if entry is None:
        return fail("refused: this target is not in the registry of authorised targets "
                    "(only the maintainer authorises one, with `targets add`)", EXIT_REFUSED)
    missing = setup.tools_ready(d)
    if missing:
        return fail("tooling not ready (missing " + ", ".join(missing) + "); run `setup` first", EXIT_NO_TOOLS)
    try:
        doc = inventory.build(Path(registry.canonical(args.target)), entry, d)
    except inventory.InventoryError as exc:
        return fail(f"inventory failed: {exc}", EXIT_FAILED)
    out = Path(args.output) if args.output else (
        paths.inventories(d) / f"{doc['target']['name']}-{doc['target']['sha256'][:16]}.json")
    inventory.write(doc, out)
    print(f"blueprint: inventory written to {out}")
    for k in ("analysis", "modules", "types", "ui_strings", "resources"):
        s = doc[k]
        print(f"  {k}: {s['status']}" + (f" ({s['reason']})" if s["reason"] else f", {len(s['items'])} item(s)"))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="blueprint_cli.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="prepare the pinned tooling (prints the plan unless --yes)")
    s.add_argument("--plan", action="store_true", help="print the plan only")
    s.add_argument("--yes", action="store_true", help="perform the plan")
    s.set_defaults(fn=cmd_setup)
    t = sub.add_parser("targets", help="manage the registry of authorised targets")
    ts = t.add_subparsers(dest="action", required=True)
    a = ts.add_parser("add", help="authorise a target (only on the maintainer's explicit instruction)")
    a.add_argument("target")
    a.add_argument("--reason", required=True)
    a.add_argument("--date", help="YYYY-MM-DD (default: today)")
    ts.add_parser("list", help="list authorised targets")
    t.set_defaults(fn=cmd_targets)
    i = sub.add_parser("inventory", help="inventory an authorised target")
    i.add_argument("target")
    i.add_argument("--output", help="inventory JSON path (default: <data dir>/inventories/)")
    i.set_defaults(fn=cmd_inventory)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
