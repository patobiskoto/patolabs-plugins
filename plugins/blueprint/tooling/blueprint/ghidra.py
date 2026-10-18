"""One Ghidra analysis per slice, reused by every export (PAT-260).

`analyzeHeadless -import` analyses the slice once into a persistent project under the data directory and a marker
file records what was analysed, with which Ghidra, and how long it took. Every later export runs
`analyzeHeadless -process -noanalysis -readOnly` with the batch script `ghidra_scripts/BlueprintExport.java` on that
project, so no export re-analyses the binary.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import paths, pins, proc, setup

MARKER = "blueprint-analysis.json"
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "ghidra_scripts"


class GhidraError(Exception):
    pass


def project_dir(data: Path, slice_sha256: str) -> Path:
    return paths.ghidra_projects(data) / slice_sha256[:16]


def headless(data: Path) -> Path:
    return paths.ghidra_dir(data, pins.GHIDRA_DIRNAME) / "support" / "analyzeHeadless"


def read_marker(proj: Path, slice_sha256: str) -> dict | None:
    """The marker when this project holds a complete analysis of exactly this slice with this Ghidra."""
    try:
        m = json.loads((proj / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ok = (isinstance(m, dict) and m.get("slice_sha256") == slice_sha256 and m.get("ghidra_version") == pins.GHIDRA_VERSION
          and isinstance(m.get("analysis_seconds"), (int, float)) and isinstance(m.get("program"), str))
    return m if ok else None


def ensure_analysis(data: Path, slice_path: Path, slice_sha256: str, runner=proc.run, clock=time.monotonic) -> dict:
    """Import and analyse once. Returns the marker plus `reused` (True when nothing was run)."""
    proj = project_dir(data, slice_sha256)
    marker = read_marker(proj, slice_sha256)
    if marker is not None:
        return {**marker, "reused": True}
    proj.mkdir(mode=0o700, parents=True, exist_ok=True)
    (proj / MARKER).unlink(missing_ok=True)
    cmd = [str(headless(data)), str(proj), pins.GHIDRA_PROJECT_NAME, "-import", str(slice_path), "-overwrite"]
    t0 = clock()
    rc, out, err = runner(cmd, pins.TIMEOUT_GHIDRA_IMPORT, env=setup.tool_env(data))
    seconds = round(clock() - t0, 1)
    if rc is None:
        raise GhidraError(f"Ghidra import: {err}")
    log = out + err
    if rc != 0 or "Analysis succeeded" not in log or "Import succeeded" not in log:
        raise GhidraError(f"Ghidra import or analysis did not succeed (exit {rc}): {log.strip()[-300:]}")
    marker = {"slice_sha256": slice_sha256, "ghidra_version": pins.GHIDRA_VERSION, "program": slice_path.name,
              "analysis_seconds": seconds}
    tmp = proj / (MARKER + ".part")
    tmp.write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    tmp.replace(proj / MARKER)
    return {**marker, "reused": False}


def export(data: Path, marker: dict, out: Path, *, include: str, data_pattern: str | None = None,
           xrefs_file: Path | None = None, maximum: int | None = None, runner=proc.run,
           clock=time.monotonic) -> tuple[dict, float]:
    """Run the batch script on the saved project; (its JSON document, seconds). The program is never re-analysed
    (`-noanalysis`) and the project is not modified (`-readOnly`)."""
    proj = project_dir(data, marker["slice_sha256"])
    args = [f"out={out}", f"include={include}", f"timeout={pins.DECOMPILE_FUNCTION_SECONDS}", "swift=1"]
    if data_pattern:
        args.append(f"data={data_pattern}")
    if xrefs_file is not None:
        args.append(f"xrefs={xrefs_file}")
    if maximum is not None:
        args.append(f"max={maximum}")
    cmd = [str(headless(data)), str(proj), pins.GHIDRA_PROJECT_NAME, "-process", marker["program"], "-noanalysis",
           "-readOnly", "-scriptPath", str(SCRIPTS_DIR), "-postScript", pins.GHIDRA_SCRIPT, *args]
    out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    t0 = clock()
    rc, so, se = runner(cmd, pins.TIMEOUT_GHIDRA_EXPORT, env=setup.tool_env(data))
    seconds = round(clock() - t0, 1)
    if rc is None:
        raise GhidraError(f"Ghidra export: {se}")
    try:
        doc = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise GhidraError(f"the export script wrote no readable document (exit {rc}): {(so + se).strip()[-300:]}") from None
    if not isinstance(doc, dict) or doc.get("format") != 1:
        raise GhidraError("unexpected export document")
    return doc, seconds
