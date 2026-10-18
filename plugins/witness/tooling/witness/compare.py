"""Before/after comparison of two proof reports (PAT-254, AC 2)."""
from __future__ import annotations

import json
from pathlib import Path

from . import navigation, report

SCHEMA = "witness-compare.v1"


class Incomparable(Exception):
    pass


def platform(r: dict) -> str:
    return r.get("platform", "ios")  # a 0.1.0 report has no platform field and is an iOS proof


def nav_identity(r: dict) -> str:
    """iOS: mechanism and navigation file sha256 (or none); web: the URL path and query."""
    nav = r.get("navigation")
    if platform(r) == "web":
        return f"web:{nav['path']}"
    return navigation.identity(nav)


def _need(r: dict, *paths: tuple[str, ...]) -> None:
    """Each path must lead to a string (explicit checks: no `assert`, which `python -O` removes)."""
    for path in paths:
        node = r
        for key in path:
            node = node[key]
        if not isinstance(node, str):
            raise TypeError(".".join(path))


def load(path: Path) -> dict:
    """A report read back for comparison; every field `check`, `build` and `markdown` use is validated here."""
    try:
        r = json.loads(path.read_text(encoding="utf-8"))
        if r["schema"] != report.SCHEMA:
            raise ValueError("schema")
        _need(r, ("generated_at",), ("outcome",), ("tests", "result"), ("checkout", "commit"),
              ("start_state", "mode"), ("start_state", "tests_selection"), ("project",))
        if not isinstance(r["checkout"]["dirty"], bool) and r["checkout"]["dirty"] != "unknown":
            raise TypeError("checkout.dirty")
        if not isinstance(r["paths"], dict):
            raise TypeError("paths")
        if platform(r) == "web":
            _need(r, ("browser", "version"), ("browser", "path"), ("browser", "profile"), ("viewport",),
                  ("navigation", "path"))
        elif platform(r) == "ios":
            _need(r, ("simulator", "udid"), ("simulator", "runtime"), ("simulator", "name"), ("scheme",))
        else:
            raise ValueError("platform")
    except (OSError, ValueError, KeyError, TypeError):
        raise Incomparable(f"{path} is not a readable {report.SCHEMA} report") from None
    return r


def check(before: dict, after: dict) -> None:
    """Compared sets: platform; navigation identity; then iOS: project name, simulator udid, runtime, scheme and
    starting state (mode, description, tests selection); web: viewport, browser path and browser version (an
    `unknown` version is never comparable). The web project directory name is not compared: before and after
    may be served from two worktrees with different directory names (deduced)."""
    if platform(before) != platform(after):
        raise Incomparable(f"platform differs: {platform(before)!r} vs {platform(after)!r}")
    problems = []
    if nav_identity(before) != nav_identity(after):
        problems.append(f"navigation differs: {nav_identity(before)!r} vs {nav_identity(after)!r}")
    bs, a_s = before["start_state"], after["start_state"]
    if platform(before) == "web":
        for key in ("viewport",):
            if before[key] != after[key]:
                problems.append(f"{key} differs: {before[key]!r} vs {after[key]!r}")
        bv, av = before["browser"]["version"], after["browser"]["version"]
        if "unknown" in (bv, av):
            problems.append("a browser version is unknown (its --version answer was unreadable): not comparable")
        elif bv != av:
            problems.append(f"browser version differs: {bv!r} vs {av!r}")
        bp, ap = before["browser"]["path"], after["browser"]["path"]
        if bp != ap:
            problems.append(f"browser path differs: {bp!r} vs {ap!r}")
    else:
        if before["project"] != after["project"]:
            problems.append(f"project differs: {before['project']!r} vs {after['project']!r}")
        for key in ("udid", "runtime"):
            if before["simulator"][key] != after["simulator"][key]:
                problems.append(f"simulator {key} differs: {before['simulator'][key]!r} vs {after['simulator'][key]!r}")
        if before["scheme"] != after["scheme"]:
            problems.append("scheme differs")
        if "unspecified" in (bs["mode"], a_s["mode"]):
            problems.append("a proof has no declared starting state (use --reset or --state-description)")
        elif (bs["mode"], bs.get("description")) != (a_s["mode"], a_s.get("description")):
            problems.append(f"starting state differs: {bs} vs {a_s}")
        if bs["tests_selection"] != a_s["tests_selection"]:
            problems.append(f"tests selection differs: {bs['tests_selection']!r} vs {a_s['tests_selection']!r}"
                            " (a test run can install and launch the app after the reset)")
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
    target = ({"browser": after["browser"], "viewport": after["viewport"], "url_path": after["navigation"]["path"]}
              if platform(after) == "web" else {"simulator": after["simulator"]})
    return {
        "schema": SCHEMA, "platform": platform(after), **target, "navigation": nav_identity(after),
        "start_state": after["start_state"],
        "before": _side(before), "after": _side(after),
        "screenshots_identical": same, "notes": notes,
        "token_cost": report.TOKEN_COST_UNKNOWN,
    }


def _side(r: dict) -> dict:
    return {"generated_at": r["generated_at"], "commit": r["checkout"]["commit"],
            "dirty": r["checkout"]["dirty"], "outcome": r["outcome"], "tests": r["tests"]["result"],
            "screenshot": r["paths"]["screenshot"], "total_seconds": r.get("total_seconds", "unknown")}


def markdown(c: dict) -> str:
    st = c["start_state"]
    if c["platform"] == "web":
        where = (f"- Browser: {c['browser']['version']} (profile: {c['browser']['profile']}), viewport "
                 f"{c['viewport']}, URL path `{c['url_path']}`")
    else:
        sim = c["simulator"]
        where = f"- Simulator: {sim['name']} `{sim['udid']}`, {sim['runtime']}"
    lines = ["# witness before/after", "", f"- Platform: {c['platform']}", where,
             f"- Shared navigation: `{c['navigation']}`",
             f"- Shared starting state: {st['mode']}" + (f" - {st['description']}" if st.get("description") else "")
             + f" (tests: {st['tests_selection']})",
             f"- Screenshots pixel-identical (sha256): {c['screenshots_identical']}",
             f"- Coordinator token cost: {c['token_cost']}", "",
             "| | Commit | Dirty | Tests | Outcome | Seconds | Screenshot |", "|---|---|---|---|---|---|---|"]
    for k in ("before", "after"):
        s = c[k]
        lines.append(f"| {k} | `{s['commit']}` | {s['dirty']} | {s['tests']} | {s['outcome']} | "
                     f"{s['total_seconds']} | `{s['screenshot']}` |")
    lines += [""] + [f"- Note: {n}" for n in c["notes"]]
    return "\n".join(lines) + "\n"
