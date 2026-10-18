"""Dated JSON + Markdown proof report (PAT-254)."""
from __future__ import annotations

import json
from pathlib import Path

SCHEMA = "witness-proof.v1"
TOKEN_COST_UNKNOWN = "unknown (not measured by witness; the coordinator's token cost is not read)"


def write(report: dict, out: Path, stem: str) -> tuple[Path, Path]:
    j, m = out / f"{stem}.json", out / f"{stem}.md"
    j.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    m.write_text(markdown(report), encoding="utf-8")
    return j, m


def markdown(r: dict) -> str:
    sim, co, st = r["simulator"], r["checkout"], r["start_state"]
    lines = [
        f"# witness proof: {r['label']}", "",
        f"- Generated (UTC): {r['generated_at']}",
        f"- Outcome: {r['outcome']}",
        f"- Project: `{r['project']}` (scheme `{r['scheme']}`, {co['kind']})",
        f"- Commit: `{co['commit']}` (dirty: {co['dirty']})",
        f"- Simulator: {sim['name']} `{sim['udid']}`, {sim['runtime']}",
        f"- Starting state: {st['mode']}" + (f" - {st['description']}" if st.get("description") else ""),
        (f"- Tests: {r['tests']['result']} (executed: {r['tests'].get('executed', 'unknown')}, "
         f"failures: {r['tests'].get('failures', 'unknown')})"),
        f"- Coordinator token cost: {r['token_cost']}", "", "## Steps", "",
        "| Step | Status | Seconds |", "|---|---|---|",
    ]
    for s in r["steps"]:
        lines.append(f"| {s['name']} | {s['status']}" + (f": {s['detail']}" if s.get("detail") else "")
                     + f" | {s['seconds']:.1f} |")
    lines += ["", "## Files", ""]
    for k, v in sorted(r["paths"].items()):
        lines.append(f"- {k}: `{v}`")
    if r.get("screenshot_sha256"):
        lines.append(f"- screenshot sha256: `{r['screenshot_sha256']}`")
    return "\n".join(lines) + "\n"
