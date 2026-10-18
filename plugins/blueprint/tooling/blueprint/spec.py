"""Product-language specification: check its evidence, turn it into Foundry payloads (PAT-253).

Spec file (JSON, written by the agent from `groups` and `decompile` outputs):

    {"format": 1, "title": "...", "summary": "...",
     "features": [{"name": "...", "summary": "...",
                   "points": [{"text": "...", "evidence": ["ev_..."]}, {"text": "...", "evidence": "unknown"}]}]}

Format 2 (PAT-260) adds the sections `coverage`, `constants`, `data_model`, `resources`, `screens` (`check_sections`):
the spec of a rebuild-grade analysis. Evidence ids come from the evidence files: `ev_` (REA), `gh_` (Ghidra: slice
sha256 and address), `res_` (a bundle file's sha256), `wit_` (a Witness screenshot's sha256).

`check` is the only gate between the spec and the payloads; `to_foundry` never calls a tracker.
"""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

FORMAT = 1
FORMAT_REBUILD = 2  # format 1 plus the five sections of `SECTIONS` (PAT-260)
SECTIONS = ("coverage", "constants", "data_model", "resources", "screens")
COVERAGE_COUNTS = ("groups", "screens", "functions")
UNKNOWN = "unknown"
EPIC_FIELDS = {"Type": "Epic", "State": "backlog"}
# A point is product language: no mangled Swift symbol and no raw address (heuristic, not proof).
_LEAK = re.compile(r"_\$s|\b0x[0-9a-fA-F]{4,}\b|_Tt[CV]|\bin _[0-9A-F]{32}\b")


class SpecError(Exception):
    pass


def evidence_ids(*docs) -> set[str]:
    """Every REA evidence id found in the given JSON documents (inventory, groups, decompilations)."""
    found: set[str] = set()

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in ("rea_evidence_id", "evidence_id") and isinstance(v, str):
                    found.add(v)
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    for d in docs:
        walk(d)
    return found


def evidence_targets(*docs) -> set[str]:
    """The arm64 slice sha256 each document declares (`target` of an inventory, `source` of a groups, decompilation,
    analysis or rebuild document). A document that declares none (a screens document) is not counted."""
    out: set[str] = set()
    for d in docs:
        if not isinstance(d, dict):
            continue
        for key in ("target", "source"):
            node = d.get(key)
            sha = node.get("arm64_sha256") if isinstance(node, dict) else None
            if isinstance(sha, str) and sha:
                out.add(sha)
    return out


def evidence_errors(where: str, ev: object, known: set[str]) -> list[str]:
    if ev == UNKNOWN:
        return []
    if not isinstance(ev, list) or not ev or not all(isinstance(e, str) for e in ev):
        return [f"{where}: `evidence` must be a non-empty list of evidence ids, or \"{UNKNOWN}\""]
    missing = sorted(set(ev) - known)
    return [f"{where}: evidence id not present in the evidence files provided: {', '.join(missing)}"] if missing else []


def check(spec: object, known: set[str], rebuild: dict | None = None) -> list[str]:
    """Problems found, [] when the spec is acceptable. `rebuild` is the rebuild document among the evidence files,
    when there is one: the coverage numbers and the resource fingerprints are then compared with it."""
    if not isinstance(spec, dict) or spec.get("format") not in (FORMAT, FORMAT_REBUILD):
        return [f"the spec must be an object with format {FORMAT} or {FORMAT_REBUILD}"]
    errs = []
    if spec["format"] == FORMAT_REBUILD:
        errs += check_sections(spec, known, rebuild)
    for k in ("title", "summary"):
        if not isinstance(spec.get(k), str) or not spec[k].strip():
            errs.append(f"`{k}` is required")
        else:
            errs += _leak(f"`{k}`", spec[k])
    feats = spec.get("features")
    if not isinstance(feats, list) or not feats:
        return errs + ["`features` must be a non-empty list"]
    names = set()
    for i, f in enumerate(feats, 1):
        where = f"feature {i}"
        if not isinstance(f, dict) or not isinstance(f.get("name"), str) or not f["name"].strip():
            errs.append(f"{where}: `name` is required")
            continue
        where = f"feature {f['name']!r}"
        if f["name"] in names:
            errs.append(f"{where}: duplicate feature name")
        names.add(f["name"])
        errs += _leak(where + " name", f["name"])
        if not isinstance(f.get("summary"), str) or not f["summary"].strip():
            errs.append(f"{where}: `summary` is required")
        else:
            errs += _leak(where + " summary", f["summary"])
        pts = f.get("points")
        if not isinstance(pts, list) or not pts:
            errs.append(f"{where}: `points` must be a non-empty list")
            continue
        for j, p in enumerate(pts, 1):
            w = f"{where} point {j}"
            if not isinstance(p, dict) or not isinstance(p.get("text"), str) or not p["text"].strip():
                errs.append(f"{w}: `text` is required")
                continue
            errs += _leak(w, p["text"])
            errs += evidence_errors(w, p.get("evidence"), known)
    return errs


def _leak(where: str, *texts: object) -> list[str]:
    return [f"{where}: not product language (mangled symbol or raw address in the text)"
            for t in texts if isinstance(t, str) and _LEAK.search(t)][:1]


def _items(spec: dict, key: str, errs: list[str]) -> list[dict]:
    v = spec.get(key)
    if not isinstance(v, list):
        errs.append(f"section `{key}` is required and must be a list")
        return []
    bad = [i for i, x in enumerate(v, 1) if not isinstance(x, dict)]
    errs += [f"{key} item {i}: must be an object" for i in bad]
    return [x for x in v if isinstance(x, dict)]


def check_sections(spec: dict, known: set[str], rebuild: dict | None) -> list[str]:
    """The sections of a rebuild spec: coverage, constants, data_model, resources, screens."""
    errs: list[str] = []
    cov = spec.get("coverage")
    if not isinstance(cov, dict):
        errs.append("section `coverage` is required and must be an object")
    else:
        for k in COVERAGE_COUNTS:
            c = cov.get(k)
            ok = isinstance(c, dict) and all(isinstance(c.get(x), int) and not isinstance(c.get(x), bool)
                                             and c[x] >= 0 for x in ("analysed", "detected"))
            if not ok:
                errs.append(f"coverage.{k}: `analysed` and `detected` must be non-negative integers")
                continue
            if c["analysed"] > c["detected"]:
                errs.append(f"coverage.{k}: analysed ({c['analysed']}) exceeds detected ({c['detected']})")
            ref = ((rebuild or {}).get("coverage") or {}).get(k)
            if ref and (ref.get("analysed"), ref.get("detected")) != (c["analysed"], c["detected"]):
                errs.append(f"coverage.{k}: {c['analysed']}/{c['detected']} differs from the rebuild file's "
                            f"{ref.get('analysed')}/{ref.get('detected')}")
        nc = cov.get("not_covered")
        if not isinstance(nc, list) or not nc or not all(isinstance(x, str) and x.strip() for x in nc):
            errs.append("coverage.not_covered: a non-empty list of sentences saying what remains uncovered is required")
    for c in _items(spec, "constants", errs):
        w = f"constant {c.get('name')!r}"
        if not isinstance(c.get("name"), str) or not c["name"].strip():
            errs.append("constants: every item needs a `name`")
            continue
        if "value" not in c:
            errs.append(f"{w}: `value` is required (a value, or \"{UNKNOWN}\")")
        elif c["value"] != UNKNOWN and c.get("evidence") == UNKNOWN:
            errs.append(f"{w}: a known value needs evidence ids")
        errs += evidence_errors(w, c.get("evidence"), known) + _leak(w, c["name"], c.get("meaning"))
    for m in _items(spec, "data_model", errs):
        w = f"data_model {m.get('name')!r}"
        if not isinstance(m.get("name"), str) or not m["name"].strip() or not isinstance(m.get("properties"), list):
            errs.append("data_model: every entity needs a `name` and a `properties` list")
            continue
        errs += evidence_errors(w, m.get("evidence"), known)
        for p in m["properties"]:
            if not isinstance(p, dict) or not isinstance(p.get("name"), str) or not isinstance(p.get("type"), str):
                errs.append(f"{w}: every property needs a `name` and a `type` (or \"{UNKNOWN}\")")
                continue
            errs += evidence_errors(f"{w} property {p['name']!r}", p.get("evidence"), known)
    ref_res = {r["path"]: r for r in ((rebuild or {}).get("resources") or {}).get("items") or []}
    for r in _items(spec, "resources", errs):
        w = f"resource {r.get('path')!r}"
        sha = r.get("sha256")
        if not isinstance(r.get("path"), str) or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            errs.append("resources: every item needs a `path` and a 64-hex `sha256`")
            continue
        if not isinstance(r.get("usage"), str) or not r["usage"].strip():
            errs.append(f"{w}: `usage` is required (a sentence, or \"{UNKNOWN}\")")
        errs += evidence_errors(w, r.get("evidence"), known)
        if ref_res and (r["path"] not in ref_res or ref_res[r["path"]]["sha256"] != sha):
            errs.append(f"{w}: not in the rebuild file with this sha256")
    for s in _items(spec, "screens", errs):
        w = f"screen {s.get('name')!r}"
        if not isinstance(s.get("name"), str) or not s["name"].strip():
            errs.append("screens: every item needs a `name`")
            continue
        nav = s.get("navigation")
        if nav != UNKNOWN and not (isinstance(nav, list) and all(isinstance(x, str) for x in nav)):
            errs.append(f"{w}: `navigation` must be a list of screen names, or \"{UNKNOWN}\"")
        errs += evidence_errors(w + " capture", s.get("capture"), known) + _leak(w, s.get("summary"))
    return errs


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "feature"


def _point_lines(points: list[dict]) -> tuple[list[str], list[str], list[str]]:
    ac, evid, unk = [], [], []
    for p in points:
        if p["evidence"] == UNKNOWN:
            unk.append(f"- {p['text']}")
        else:
            ac.append(f"- [ ] {p['text']}")
            evid.append(f"- {p['text']} ← " + ", ".join(f"`{e}`" for e in p["evidence"]))
    return ac, evid, unk


def payloads(spec: dict, epic_id: str | None) -> tuple[dict, list[dict]]:
    """(epic payload, ticket payloads). Tickets carry `parent` only when `epic_id` is given.
    A feature with no evidenced point has nothing testable: SpecError."""
    epic_body = [spec["summary"].strip(), "", "## Features"]
    tickets = []
    for f in spec["features"]:
        ac, evid, unk = _point_lines(f["points"])
        if not ac:
            raise SpecError(f"feature {f['name']!r} has no point with REA evidence: nothing testable "
                            "to put in an acceptance criterion")
        body = [f["summary"].strip(), "", "## Acceptance criteria", *ac, "", "## REA evidence", *evid]
        if unk:
            body += ["", "## Unknown (no REA evidence; not acceptance criteria)", *unk]
        t = {"title": f["name"], "body": "\n".join(body) + "\n"}
        if epic_id:
            t["parent"] = epic_id
        tickets.append(t)
        epic_body.append(f"- {f['name']}")
    nc = (spec.get("coverage") or {}).get("not_covered")
    if isinstance(nc, list) and nc:
        epic_body += ["", "## Not covered by the analysis", *[f"- {x}" for x in nc]]
    epic = {"title": spec["title"].strip(), "body": "\n".join(epic_body) + "\n", "fields": dict(EPIC_FIELDS)}
    return epic, tickets


def to_foundry(spec: dict, out: Path, epic_id: str | None) -> list[str]:
    """Write the payload files into `out` and return the Foundry commands to run, in order.
    Without `epic_id`: the Epic payload only (its identifier is not known before it is created)."""
    epic, tickets = payloads(spec, epic_id)
    out.mkdir(mode=0o700, parents=True, exist_ok=True)

    def put(name: str, doc: dict) -> str:
        p = out / name
        p.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        p.chmod(0o600)
        return f'python3 <foundry-root>/tooling/foundry_cli.py edit create-issue "$(cat {shlex.quote(str(p))})"'
    if not epic_id:
        return [put("epic.json", epic)]
    return [put(f"ticket-{i:02d}-{slug(t['title'])}.json", t) for i, t in enumerate(tickets, 1)]
