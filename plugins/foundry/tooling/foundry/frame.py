"""Genesis → historization bridge.

The guided brainstorm (in the /frame skill) converges on a framed concept and emits
a spec JSON. This module MATERIALIZES it deterministically: ADRs into the KB, an epic,
and precise issues linked under it — each issue body citing the ADRs that constrain it.

Separation of concern: the brainstorm is judgment (skill); this write is mechanical.

Spec (stdin):
{
  "adrs":  [{"title": "...", "body": "...", "status": "proposed"}],
  "epic":  {"title": "...", "body": "...", "fields": {"Priority": "P1", "Milestone": "v1"}},
  "issues":[{"title": "...", "body": "...(incl. ## Critères d'acceptation with - [ ] items)",
             "fields": {"Priority": "P1", "Estimate": 3, "Milestone": "v1", "Type": "Feature",
                        "State": "backlog"},
             "constrained_by": ["<ADR title or index>"]}]
}

CLI: cat spec.json | python3 -m foundry.frame
"""
from __future__ import annotations

import json
import sys

import foundry
from foundry import registry, write


def _project(tr):
    binding = write.mutation_project(tr)
    return binding if binding is not None else tr.resolve_project(registry.repo_basename())


def materialize(spec: dict) -> dict:
    tr = foundry.tracker()
    p = _project(tr)
    created = {"adrs": [], "epic": None, "issues": []}

    # 1) ADRs first — they are the frame the issues reference.
    adr_by_key = {}
    adr_by_id = {}
    for idx, a in enumerate(spec.get("adrs", [])):
        adr = tr.create_adr(p, a["title"], a["body"], status=a.get("status", "proposed"))
        adr_by_key[str(idx)] = adr.id
        adr_by_key[a["title"]] = adr.id
        adr_by_id[adr.id] = adr
        created["adrs"].append(adr.id)
        print(f"📐 {adr.id} — {a['title']}")

    # 2) Epic
    epic_id = None
    if spec.get("epic"):
        e = spec["epic"]
        fields = {"Type": "Epic", "State": "backlog", **(e.get("fields") or {})}
        epic = tr.create_issue(p, e["title"], e.get("body", ""), fields=fields)
        epic_id = epic.id
        created["epic"] = epic_id
        print(f"🏛️  epic {epic_id} — {e['title']}")

    # 3) Issues, linked under the epic, citing their ADRs.
    for it in spec.get("issues", []):
        body = it.get("body", "")
        refs = [adr_by_key.get(str(k), str(k)) for k in it.get("constrained_by", [])]
        if refs:
            body += "\n\n---\n**Cadre (ADR) :** " + ", ".join(refs)
        fields = {"Type": "Feature", "State": "backlog", **(it.get("fields") or {})}
        if fields.get("Estimate") is not None:
            fields["Estimate"] = int(fields["Estimate"])
        issue = tr.create_issue(p, it["title"], body, fields=fields, parent=epic_id)
        if getattr(tr, "adr_issue_link_supported", False):
            for ref in dict.fromkeys(refs):
                current = adr_by_id.get(ref)
                if current is None:
                    current = next((a for a in tr.list_adrs(p) if a.id == ref), None)
                if current is None:
                    raise ValueError(f"ADR inconnue pour relation Linear : {ref}")
                adr_by_id[ref] = write.link_adr_issue(tr, current, issue.id)
        created["issues"].append(issue.id)
        print(f"   ✓ {issue.id} — {it['title']}  [{fields.get('Priority','?')} · "
              f"est {fields.get('Estimate','?')}]")

    print(f"\n✅ frame matérialisé : {len(created['adrs'])} ADR · "
          f"epic {epic_id or '—'} · {len(created['issues'])} issues.")
    return created


if __name__ == "__main__":
    materialize(json.load(sys.stdin))
