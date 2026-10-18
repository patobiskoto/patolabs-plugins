"""Before/after comparison of two proof reports (PAT-254, AC 2)."""
from __future__ import annotations

import json
from pathlib import Path

from . import report

SCHEMA = "witness-compare.v1"


class Incomparable(Exception):
    pass


def load(path: Path) -> dict:
    try:
        r = json.loads(path.read_text(encoding="utf-8"))
        assert r["schema"] == report.SCHEMA
        r["simulator"]["udid"], r["simulator"]["runtime"], r["start_state"]["mode"]
        r["checkout"]["commit"], r["scheme"], r["outcome"]
    except (OSError, ValueError, KeyError, TypeError, AssertionError):
        raise Incomparable(f"{path} is not a readable {report.SCHEMA} report") from None
    return r


def check(before: dict, after: dict) -> None:
    """Compared sets: simulator udid, runtime, scheme and starting state (mode and description)."""
    problems = []
    for key in ("udid", "runtime"):
        if before["simulator"][key] != after["simulator"][key]:
            problems.append(f"simulator {key} differs: {before['simulator'][key]!r} vs {after['simulator'][key]!r}")
    if before["scheme"] != after["scheme"]:
        problems.append("scheme differs")
    bs, a_s = before["start_state"], after["start_state"]
    if "unspecified" in (bs["mode"], a_s["mode"]):
        problems.append("a proof has no declared starting state (use --reset or --state-description)")
    elif (bs["mode"], bs.get("description")) != (a_s["mode"], a_s.get("description")):
        problems.append(f"starting state differs: {bs} vs {a_s}")
    for name, r in (("before", before), ("after", after)):
        if not r.get("paths", {}).get("screenshot"):
            problems.append(f"the {name} proof has no screenshot (outcome: {r['outcome']})")
    if problems:
        raise Incomparable("; ".join(problems))


def build(before: dict, after: dict) -> dict:
    b, a = before.get("screenshot_sha256"), after.get("screenshot_sha256")
    same = (b == a) if b and a else "unknown"
    notes = []
    if before["checkout"]["commit"] == after["checkout"]["commit"]:
        notes.append("both proofs share one commit")
    for name, r in (("before", before), ("after", after)):
        if r["checkout"]["dirty"] is not False:
            notes.append(f"the {name} checkout was dirty or its state is unknown: the commit alone does not identify the build")
    return {
        "schema": SCHEMA, "simulator": after["simulator"], "start_state": after["start_state"],
        "before": _side(before), "after": _side(after),
        "screenshots_identical": same, "notes": notes,
        "token_cost": report.TOKEN_COST_UNKNOWN,
    }


def _side(r: dict) -> dict:
    return {"generated_at": r["generated_at"], "commit": r["checkout"]["commit"],
            "dirty": r["checkout"]["dirty"], "outcome": r["outcome"], "tests": r["tests"]["result"],
            "screenshot": r["paths"]["screenshot"], "total_seconds": r.get("total_seconds", "unknown")}


def markdown(c: dict) -> str:
    sim, st = c["simulator"], c["start_state"]
    lines = ["# witness before/after", "",
             f"- Simulator: {sim['name']} `{sim['udid']}`, {sim['runtime']}",
             f"- Shared starting state: {st['mode']}" + (f" - {st['description']}" if st.get("description") else ""),
             f"- Screenshots pixel-identical (sha256): {c['screenshots_identical']}",
             f"- Coordinator token cost: {c['token_cost']}", "",
             "| | Commit | Dirty | Tests | Outcome | Seconds | Screenshot |", "|---|---|---|---|---|---|---|"]
    for k in ("before", "after"):
        s = c[k]
        lines.append(f"| {k} | `{s['commit']}` | {s['dirty']} | {s['tests']} | {s['outcome']} | "
                     f"{s['total_seconds']} | `{s['screenshot']}` |")
    lines += [""] + [f"- Note: {n}" for n in c["notes"]]
    return "\n".join(lines) + "\n"
