"""Provision a fresh tracker project through its optional capability and register it.

Provider-specific creation belongs to the active ``Tracker`` adapter. This module owns
only provider-neutral orchestration, registry persistence and optional ADR replication.

CLI: python3 -m foundry.setup_project "<Project Name>" <SHORT> <repo-basename> \
         [--import-adrs <dir>]    # replicate repo bootstrap ADRs into the KB
"""
from __future__ import annotations

import os
import re
import sys

import foundry
from foundry import registry


def _parse_adr_md(text: str, filename: str):
    """(title, status, body) from a repo ADR markdown file (frontmatter or heading)."""
    title, status, body = None, "proposed", text
    m = re.match(r"\A---\n(.*?)\n---\n(.*)\Z", text, re.S)
    if m:
        fm, body = m.group(1), m.group(2)
        tm = re.search(r'(?m)^title:\s*"?(.+?)"?\s*$', fm)
        if tm:
            title = tm.group(1)
        sm = re.search(r"(?m)^status:\s*(\w+)", fm)
        if sm:
            status = sm.group(1)
    if not title:
        hm = re.search(r"(?m)^#\s+(.+)$", body)
        title = hm.group(1).strip() if hm else os.path.splitext(filename)[0]
    return title, status, body.strip()


def import_adrs(tracker, project, adr_dir):
    """Replicate repo bootstrap ADRs into the tracker KB without renumbering them.

    IDs come from filenames and are the durable identity. Titles can legitimately
    evolve; using them for idempotence duplicated ADR-0001 after the Foundry repo was
    renamed, then shifted every subsequent ID. Preflight the whole batch before the
    first write because the tracker allocates the next ID and cannot insert a gap.
    """
    existing = {a.id: a for a in tracker.list_adrs(project)}
    existing_titles = {a.title.split("—", 1)[-1].strip(): a.id for a in existing.values()}
    records = []
    skipped = 0
    for fn in sorted(os.listdir(adr_dir)):
        if not fn.endswith(".md"):
            continue
        with open(os.path.join(adr_dir, fn)) as f:
            title, status, body = _parse_adr_md(f.read(), fn)
        match = re.match(rf"^({re.escape(project.key)}-ADR-\d{{4}})(?:-|\.md$)", fn)
        expected_id = match.group(1) if match else None
        if not expected_id:
            raise RuntimeError(
                f"fichier ADR sans identifiant {project.key}-ADR-NNNN : {fn}"
            )
        if expected_id and expected_id in existing:
            print(f"  = déjà en KB, ignoré par id : {expected_id}")
            skipped += 1
            continue
        if expected_id and title in existing_titles:
            raise RuntimeError(
                f"titre ADR déjà présent sous {existing_titles[title]}, attendu {expected_id}"
            )
        records.append((expected_id, title, status, body))

    prefix = f"{project.key}-ADR-"
    next_number = max(
        (int(adr_id.removeprefix(prefix)) for adr_id in existing if adr_id.startswith(prefix)),
        default=0,
    ) + 1
    for expected_id, title, _status, _body in records:
        expected_number = int(expected_id.removeprefix(prefix))
        if expected_number != next_number:
            raise RuntimeError(
                "numérotation ADR non contiguë : "
                f"le tracker créera {prefix}{next_number:04d}, mais le repo attend {expected_id}"
            )
        next_number += 1

    created = 0
    for expected_id, title, status, body in records:
        adr = tracker.create_adr(project, title, body, status=status)
        if expected_id and adr.id != expected_id:
            raise RuntimeError(f"collision ADR : créé {adr.id}, attendu {expected_id}")
        print(f"  📐 {adr.id} importé ({status}) — {title}")
        created += 1
    print(f"📚 import ADR : {created} créé(s), {skipped} ignoré(s) (déjà en KB).")


def setup(name, short, repo, adr_dir=None):
    tracker = foundry.tracker()
    if not tracker.project_provisioning_supported:
        raise SystemExit(
            f"Le tracker '{tracker.name}' ne prend pas en charge le provisionnement "
            "de projet. Crée le projet avec l'outil du provider, puis utilise "
            "'registry register', ou configure un tracker compatible."
        )

    canonical_repository = None
    if tracker.project_provisioning_requires_repository:
        try:
            canonical_repository = registry.checkout_repository_identity()
        except ValueError:
            raise SystemExit(
                f"Le tracker '{tracker.name}' exige le remote origin canonique du "
                "dépôt courant avant tout provisionnement."
            ) from None

    project = tracker.provision_project(name, short, canonical_repository)
    if project.key != short:
        raise RuntimeError(
            f"le tracker {tracker.name} a retourné le projet {project.key}, attendu {short}"
        )
    registry.register(tracker.name, repo, project.key, project.id, **project.extra)
    print(f"📇 enregistré : {repo} → {project.key} ({project.id}) [{tracker.name}]")
    if adr_dir:
        import_adrs(tracker, project, adr_dir)
    print(f"✅ setup {short} terminé.")


if __name__ == "__main__":
    argv = sys.argv[1:]
    adr_dir = None
    if "--import-adrs" in argv:
        i = argv.index("--import-adrs")
        if i + 1 >= len(argv):
            raise SystemExit("usage: --import-adrs <dir>")
        adr_dir = argv[i + 1]
        del argv[i:i + 2]
    if len(argv) != 3:
        raise SystemExit('usage: setup_project "<Name>" <SHORT> <repo> [--import-adrs <dir>]')
    setup(argv[0], argv[1], argv[2], adr_dir)
