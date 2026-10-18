"""Product-language specification: check its evidence, turn it into Foundry payloads (PAT-253).

Spec file (JSON, written by the agent from `groups` and `decompile` outputs):

    {"format": 1, "title": "...", "summary": "...",
     "features": [{"name": "...", "summary": "...",
                   "points": [{"text": "...", "evidence": ["ev_..."]}, {"text": "...", "evidence": "unknown"}]}]}

`check` is the only gate between the spec and the payloads; `to_foundry` never calls a tracker.
"""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

FORMAT = 1
UNKNOWN = "unknown"
EPIC_FIELDS = {"Type": "Epic", "State": "backlog"}
# A point is product language: no mangled Swift symbol and no raw address (heuristic, not proof).
_LEAK = re.compile(r"_\$s|\b0x[0-9a-fA-F]{4,}\b|_Tt[CV]")


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


def check(spec: object, known: set[str]) -> list[str]:
    """Problems found, [] when the spec is acceptable."""
    if not isinstance(spec, dict) or spec.get("format") != FORMAT:
        return [f"the spec must be an object with format {FORMAT}"]
    errs = []
    for k in ("title", "summary"):
        if not isinstance(spec.get(k), str) or not spec[k].strip():
            errs.append(f"`{k}` is required")
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
        if not isinstance(f.get("summary"), str) or not f["summary"].strip():
            errs.append(f"{where}: `summary` is required")
        pts = f.get("points")
        if not isinstance(pts, list) or not pts:
            errs.append(f"{where}: `points` must be a non-empty list")
            continue
        for j, p in enumerate(pts, 1):
            w = f"{where} point {j}"
            if not isinstance(p, dict) or not isinstance(p.get("text"), str) or not p["text"].strip():
                errs.append(f"{w}: `text` is required")
                continue
            if _LEAK.search(p["text"]) or _LEAK.search(f.get("summary") or ""):
                errs.append(f"{w}: not product language (mangled symbol or raw address in the text)")
            ev = p.get("evidence")
            if ev == UNKNOWN:
                continue
            if not isinstance(ev, list) or not ev or not all(isinstance(e, str) for e in ev):
                errs.append(f"{w}: `evidence` must be a non-empty list of REA evidence ids, or \"{UNKNOWN}\"")
                continue
            missing = sorted(set(ev) - known)
            if missing:
                errs.append(f"{w}: evidence id not present in the evidence files provided: {', '.join(missing)}")
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
