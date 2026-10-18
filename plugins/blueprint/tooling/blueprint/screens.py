"""Link screens to Witness proofs (PAT-260). Blueprint never runs the simulator: it reads reports Witness wrote.

Manifest (JSON, written by the maintainer or the agent after `witness prove --navigation`):
    {"format": 1, "screens": [{"name": "...", "report": "path/to/report-....json"}]}
A relative `report` path is relative to the manifest. The document written by `build` carries one `evidence_id` per
screen (`wit_` plus the screenshot sha256) for `spec check`.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

FORMAT = 1
WITNESS_SCHEMAS = ("witness-proof.v1",)


class ScreensError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(manifest_path: Path) -> dict:
    """The validated, normalised document; ScreensError lists every problem found."""
    try:
        man = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ScreensError([f"cannot read {manifest_path.name} as JSON"]) from None
    if not isinstance(man, dict) or man.get("format") != FORMAT or not isinstance(man.get("screens"), list):
        raise ScreensError([f"the manifest must be an object with format {FORMAT} and a `screens` list"])
    problems, out, names = [], [], set()
    for i, s in enumerate(man["screens"], 1):
        where = f"screen {i}"
        if not isinstance(s, dict) or not isinstance(s.get("name"), str) or not s["name"].strip() \
                or not isinstance(s.get("report"), str):
            problems.append(f"{where}: `name` and `report` are required strings")
            continue
        name = s["name"].strip()
        where = f"screen {name!r}"
        if name.lower() in names:
            problems.append(f"{where}: duplicate screen name")
        names.add(name.lower())
        rp = Path(s["report"]).expanduser()
        rp = rp if rp.is_absolute() else manifest_path.parent / rp
        try:
            rep = json.loads(rp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            problems.append(f"{where}: the report {s['report']!r} is missing or is not JSON")
            continue
        if not isinstance(rep, dict) or rep.get("schema") not in WITNESS_SCHEMAS:
            problems.append(f"{where}: not a Witness proof report (schema {WITNESS_SCHEMAS[0]} expected)")
            continue
        shot = (rep.get("paths") or {}).get("screenshot")
        want = rep.get("screenshot_sha256")
        if not isinstance(shot, str) or not isinstance(want, str):
            problems.append(f"{where}: the report records no screenshot path or sha256")
            continue
        sp = Path(shot)
        if not sp.is_file():
            problems.append(f"{where}: the screenshot file {sp.name!r} does not exist at the recorded path")
            continue
        if _sha256(sp) != want:
            problems.append(f"{where}: the screenshot sha256 differs from the one in the Witness report")
            continue
        declared = (rep.get("navigation") or {}).get("screen")
        if isinstance(declared, str) and declared.strip().lower() != name.lower():
            problems.append(f"{where}: the report declares the screen {declared!r} (navigation file), not {name!r}")
            continue
        out.append({"name": name, "report": str(rp.resolve()), "report_sha256": _sha256(rp), "screenshot": str(sp),
                    "screenshot_sha256": want, "declared_screen": declared if isinstance(declared, str) else "unknown",
                    "outcome": rep.get("outcome", "unknown"), "commit": (rep.get("checkout") or {}).get("commit", "unknown"),
                    "platform": rep.get("platform", "ios"), "evidence_id": "wit_" + want})
    if problems:
        raise ScreensError(problems)
    return {"format": FORMAT, "screens": sorted(out, key=lambda x: x["name"].lower())}
