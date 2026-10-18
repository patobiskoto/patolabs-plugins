#!/usr/bin/env python3
"""blueprint: pinned REA/Ghidra tooling, authorised-targets registry, reproducible inventory (PAT-252),
groups, targeted decompilation and spec-to-Foundry payloads (PAT-253), one Ghidra analysis reused by every
decompilation, rebuild-grade coverage, constants, data model, resources and screens (PAT-260)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from blueprint import (
    analysis,
    decompile,
    ghidra,
    groups,
    inventory,
    paths,
    pins,
    rebuild,
    registry,
    screens,
    setup,
    spec,
)

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


def _load_json(path: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SystemExit(fail(f"cannot read {path} as JSON"))


def cmd_groups(args: argparse.Namespace) -> int:
    try:
        doc = groups.build(_load_json(args.inventory))
    except groups.GroupsError as exc:
        return fail(str(exc))
    text = inventory.render(doc)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        out.chmod(0o600)
    else:
        print(text, end="")
    a = doc["accounting"]
    print(f"blueprint: {len(doc['groups'])} group(s); ungrouped: {len(doc['ungrouped']['types'])} type(s), "
          f"{a['ui_strings_ungrouped']} string(s)" + (f"; written to {args.output}" if args.output else ""),
          file=sys.stderr)
    return 0


def cmd_decompile(args: argparse.Namespace) -> int:
    d = paths.data_dir()
    try:
        entry = registry.find(paths.registry_file(d), args.target)
    except registry.RegistryError as exc:
        return fail(str(exc), EXIT_REFUSED)
    if entry is None:
        return fail("refused: this target is not in the registry of authorised targets "
                    "(only the maintainer authorises one, with `targets add`)", EXIT_REFUSED)
    missing = [] if args.analysis else setup.tools_ready(d)
    if missing:
        return fail("tooling not ready (missing " + ", ".join(missing) + "); run `setup` first", EXIT_NO_TOOLS)
    inv = _load_json(args.inventory)
    try:
        gdoc = groups.build(inv)
    except groups.GroupsError as exc:
        return fail(str(exc))
    group = None
    if args.group:
        group = groups.find(gdoc, args.group)
        if group is None:
            return fail(f"no group named {args.group!r}; groups: " + ", ".join(g["name"] for g in gdoc["groups"]))
    try:
        if args.analysis:
            doc = decompile.from_analysis(_load_json(args.analysis), inv, group=group, entry=args.entry,
                                          list_only=args.list, maximum=args.max)
        else:
            sl = decompile.slice_for(Path(registry.canonical(args.target)), inv, d)
            doc = decompile.run(inventory.Rea(d), sl, group=group, entry=args.entry, list_only=args.list,
                                maximum=args.max, budget=args.budget)
    except (decompile.DecompileError, inventory.InventoryError) as exc:
        return fail(f"decompile failed: {exc}", EXIT_FAILED)
    doc["source"] = gdoc["source"]
    name = group["name"] if group else f"entry-{args.entry}"
    out = Path(args.output) if args.output else paths.decompilations(d) / f"{gdoc['source']['name']}-{name}.json"
    inventory.write(doc, out)
    c = doc.get("candidates")
    print(f"blueprint: {len(doc['entries'])} entry point(s) decompiled"
          + (f" of {c['total']} candidate(s)" if c else "") + f"; written to {out}")
    for k, why in doc["unknown_parts"].items():
        print(f"  unknown {k}: {why}")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
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
    inv = _load_json(args.inventory)
    try:
        sl = decompile.slice_for(Path(registry.canonical(args.target)), inv, d)
        doc = analysis.build(inv, sl, d, module=args.module)
    except (decompile.DecompileError, inventory.InventoryError, analysis.AnalysisError, ghidra.GhidraError) as exc:
        return fail(f"analyze failed: {exc}", EXIT_FAILED)
    out = Path(args.output) if args.output else (
        paths.analyses(d) / f"{inv['target']['name']}-{inv['target']['sha256'][:16]}" / "analysis.json")
    inventory.write(doc, out)
    t, f = doc["timing"], doc["functions"]
    print(f"blueprint: analysis written to {out}")
    print(f"  ghidra {doc['tools']['ghidra']}, slice {doc['source']['arm64_sha256'][:16]}, program id "
          f"{doc['ghidra']['program_id']}")
    print(f"  analysis {t['analysis_seconds']:g}s ({'reused, not run' if t['analysis_reused'] else 'run now'}); export "
          f"{t['export_seconds']:g}s; demangle {t['demangle_seconds']:g}s; total this run {t['total_seconds']:g}s")
    print(f"  functions: {f['decompiled']} decompiled, {f['failed']} failed, {f['truncated']} truncated, of "
          f"{f['app']} in the app module ({f['total']} in the binary)")
    for k, why in doc["unknown_parts"].items():
        print(f"  unknown {k}: {why}")
    return 0


def cmd_screens(args: argparse.Namespace) -> int:
    try:
        doc = screens.build(Path(args.manifest))
    except screens.ScreensError as exc:
        for p in exc.problems:
            print(f"blueprint: screens: {p}", file=sys.stderr)
        return EXIT_USAGE
    if args.output:
        inventory.write(doc, Path(args.output))
    print(f"blueprint: {len(doc['screens'])} screen capture(s) valid"
          + (f"; written to {args.output}" if args.output else ""))
    return 0


def cmd_rebuild(args: argparse.Namespace) -> int:
    scr = None
    if args.screens:
        try:
            scr = screens.build(Path(args.screens))
        except screens.ScreensError as exc:
            for p in exc.problems:
                print(f"blueprint: screens: {p}", file=sys.stderr)
            return EXIT_USAGE
    try:
        doc = rebuild.build(_load_json(args.analysis), _load_json(args.inventory), scr)
    except (ValueError, KeyError) as exc:
        return fail(f"rebuild failed: {exc}")
    text = inventory.render(doc)
    out = Path(args.output) if args.output else None
    if out:
        inventory.write(doc, out)
    else:
        print(text, end="")
    c = doc["coverage"]
    print(f"blueprint: coverage groups {c['groups']['analysed']}/{c['groups']['detected']}, screens "
          f"{c['screens']['analysed']}/{c['screens']['detected']} (captured {c['screens_captured']['captured']}), "
          f"functions {c['functions']['analysed']}/{c['functions']['detected']}; constants "
          f"{len(doc['constants']['items'])} ({doc['constants']['unknown_count']} unknown); models "
          f"{len(doc['data_model']['items'])}; resources {len(doc['resources']['items'])}"
          + (f"; written to {out}" if out else ""), file=sys.stderr)
    for line in c["not_covered"]:
        print(f"  not covered: {line}", file=sys.stderr)
    return 0


def cmd_spec(args: argparse.Namespace) -> int:
    sp = _load_json(args.spec)
    docs = [_load_json(p) for p in args.evidence]
    known = spec.evidence_ids(*docs)
    errs = spec.check(sp, known, next((x for x in docs if isinstance(x, dict) and "coverage" in x and "resources" in x
                                       and "data_model" in x), None))
    if errs:
        for e in errs:
            print(f"blueprint: spec: {e}", file=sys.stderr)
        return EXIT_USAGE
    if args.action == "check":
        print(f"blueprint: spec ok ({len(sp['features'])} feature(s), {len(known)} evidence id(s) provided)")
        return 0
    out = Path(args.output) if args.output else paths.payloads(paths.data_dir())
    try:
        cmds = spec.to_foundry(sp, out, args.epic_id)
    except spec.SpecError as exc:
        return fail(str(exc))
    print(f"blueprint: payloads written to {out}. Nothing was sent to the tracker.")
    print("Show the payloads to the maintainer; run the commands below only after their explicit confirmation:")
    for c in cmds:
        print(c)
    if not args.epic_id:
        print("Then re-run with --epic-id <ID of the created Epic> to produce the ticket payloads.")
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
    g = sub.add_parser("groups", help="group an inventory into candidate screens and features (offline)")
    g.add_argument("inventory")
    g.add_argument("--output", help="groups JSON path (default: standard output)")
    g.set_defaults(fn=cmd_groups)
    c = sub.add_parser("decompile", help="decompile the entry points of one group of an authorised target")
    c.add_argument("target")
    c.add_argument("--inventory", required=True, help="inventory JSON of this target")
    what = c.add_mutually_exclusive_group(required=True)
    what.add_argument("--group", help="group name from `groups`")
    what.add_argument("--entry", help="one procedure address (0x...)")
    c.add_argument("--list", action="store_true", help="only list the entry-point candidates of the group")
    c.add_argument("--max", type=int, default=pins.MAX_DECOMPILE, help="entry points to decompile")
    c.add_argument("--budget", type=float, default=pins.BUDGET_DECOMPILE, help="total seconds")
    c.add_argument("--analysis", help="analysis JSON from `analyze`: read the pseudo-code from it, no Ghidra or REA call")
    c.add_argument("--output", help="decompilation JSON path (default: <data dir>/decompilations/)")
    c.set_defaults(fn=cmd_decompile)
    an = sub.add_parser("analyze", help="one Ghidra analysis of an authorised target and a batch export of its app functions")
    an.add_argument("target")
    an.add_argument("--inventory", required=True, help="inventory JSON of this target")
    an.add_argument("--module", help="Swift module of the app (default: the one owning most classes in the inventory)")
    an.add_argument("--output", help="analysis JSON path (default: <data dir>/analyses/<name>-<sha>/analysis.json)")
    an.set_defaults(fn=cmd_analyze)
    sc = sub.add_parser("screens", help="validate a manifest linking screens to Witness proofs (offline)")
    sc.add_argument("manifest")
    sc.add_argument("--output", help="normalised screens JSON, an evidence file for `spec check`")
    sc.set_defaults(fn=cmd_screens)
    rb = sub.add_parser("rebuild", help="coverage, constants, data model, resources and screens from an analysis (offline)")
    rb.add_argument("analysis")
    rb.add_argument("--inventory", required=True)
    rb.add_argument("--screens", help="screens manifest (validated as by `screens`)")
    rb.add_argument("--output", help="rebuild JSON path (default: standard output)")
    rb.set_defaults(fn=cmd_rebuild)
    sp = sub.add_parser("spec", help="check a product-language spec, or turn it into Foundry payloads")
    ss = sp.add_subparsers(dest="action", required=True)
    k = ss.add_parser("check", help="refuse unsupported evidence ids and points with neither evidence nor unknown")
    k.add_argument("spec")
    k.add_argument("evidence", nargs="+", help="inventory and decompilation JSON files the evidence ids come from")
    t = ss.add_parser("to-foundry", help="write `edit create-issue` payloads and print the commands (no tracker call)")
    t.add_argument("spec")
    t.add_argument("evidence", nargs="+", help="same evidence files as `check`, which is run first")
    t.add_argument("--output", help="payload directory (default: <data dir>/payloads/)")
    t.add_argument("--epic-id", help="identifier of the created Epic: produces the ticket payloads")
    sp.set_defaults(fn=cmd_spec)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
