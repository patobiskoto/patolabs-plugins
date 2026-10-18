"""xcodebuild test and build, app discovery, test summary (PAT-254)."""
from __future__ import annotations

import json
import plistlib
import re
import shutil
from pathlib import Path

from . import proc

TIMEOUT_TEST = 3600.0
TIMEOUT_BUILD = 3600.0
TIMEOUT_EXPORT = 300.0
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".heic")

_EXECUTED = re.compile(r"Executed (\d+) tests?, with (\d+) failures? \((\d+) unexpected\)")
_SWIFT_TESTING = re.compile(r"Test run with (\d+) tests?.*?(passed|failed)")


class XcodeError(Exception):
    pass


def _base(project: Path, scheme: str, udid: str, derived: Path) -> list[str]:
    flag = "-workspace" if project.suffix == ".xcworkspace" else "-project"
    return ["xcodebuild", flag, str(project), "-scheme", scheme, "-destination", f"id={udid}",
            "-derivedDataPath", str(derived)]


def summarize(output: str, returncode: int | None) -> dict:
    """Test summary from xcodebuild output; fields that cannot be read are "unknown"."""
    summary: dict = {"result": "unknown", "executed": "unknown", "failures": "unknown"}
    if "** TEST SUCCEEDED **" in output:
        summary["result"] = "passed"
    elif "** TEST FAILED **" in output or (returncode not in (0, None)):
        summary["result"] = "failed"
    matches = _EXECUTED.findall(output)
    if matches:  # the last "Executed" line is the grand total
        summary["executed"], summary["failures"] = int(matches[-1][0]), int(matches[-1][1])
    else:
        st = _SWIFT_TESTING.findall(output)
        if st:
            summary["executed"] = int(st[-1][0])
            summary["failures"] = 0 if st[-1][1] == "passed" else "unknown"
    return summary


def run_tests(project: Path, scheme: str, udid: str, derived: Path, log: Path,
              test_plan: str | None, only: list[str], result_bundle: Path | None = None) -> dict:
    cmd = _base(project, scheme, udid, derived)
    if result_bundle:
        cmd += ["-resultBundlePath", str(result_bundle)]
    if test_plan:
        cmd += ["-testPlan", test_plan]
    cmd += [f"-only-testing:{o}" for o in only]
    cmd.append("test")
    rc, out, err = proc.run(cmd, TIMEOUT_TEST)
    log.write_text(out + ("\n" + err if err else ""), encoding="utf-8")
    if rc is None:
        raise XcodeError(f"xcodebuild test did not finish: {err}")
    summary = summarize(out, rc)
    summary["log"] = str(log)
    return summary


def build(project: Path, scheme: str, udid: str, derived: Path, log: Path) -> None:
    rc, out, err = proc.run(_base(project, scheme, udid, derived) + ["build"], TIMEOUT_BUILD)
    log.write_text(out + ("\n" + err if err else ""), encoding="utf-8")
    if rc != 0:
        raise XcodeError(f"xcodebuild build failed (rc={rc}); see {log}")


def find_app(derived: Path, scheme: str) -> tuple[Path, str]:
    """The built simulator .app under <derived>/Build/Products/*-iphonesimulator/ and its bundle id."""
    candidates = sorted(p for p in (derived / "Build" / "Products").glob("*-iphonesimulator/*.app")
                        if p.is_dir() and not p.name.endswith("-Runner.app"))
    if len(candidates) > 1:
        named = [p for p in candidates if p.stem == scheme]
        candidates = named or candidates
    if len(candidates) != 1:
        raise XcodeError(f"expected one built .app, found {len(candidates)}: "
                         + ", ".join(p.name for p in candidates))
    app = candidates[0]
    try:
        info = plistlib.loads((app / "Info.plist").read_bytes())
        bundle_id = info["CFBundleIdentifier"]
    except (OSError, ValueError, KeyError, plistlib.InvalidFileException):
        raise XcodeError(f"{app.name} has no readable CFBundleIdentifier in its Info.plist") from None
    if not isinstance(bundle_id, str) or not bundle_id:
        raise XcodeError(f"{app.name} has an invalid CFBundleIdentifier")
    return app, bundle_id


def export_screenshot(bundle: Path, work: Path, name_filter: str | None, dest_stem: Path) -> Path:
    """Extract the one image attachment of a result bundle (`xcresulttool export attachments`) to
    `<dest_stem><suffix>`. Several images need `name_filter` (substring of the attachment name): never a guess."""
    work.mkdir(parents=True, exist_ok=True)
    rc, _, err = proc.run(["xcrun", "xcresulttool", "export", "attachments", "--path", str(bundle),
                           "--output-path", str(work)], TIMEOUT_EXPORT)
    if rc != 0:
        raise XcodeError(f"xcresulttool export failed (rc={rc}): {(err or 'no output').strip()[-200:]}")
    try:
        manifest = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
        images = [(a.get("suggestedHumanReadableName") or a["exportedFileName"], work / a["exportedFileName"])
                  for test in manifest for a in test["attachments"]
                  if a["exportedFileName"].lower().endswith(IMAGE_SUFFIXES)]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise XcodeError("the attachments manifest is missing or has an unexpected shape") from None
    if name_filter:
        images = [i for i in images if name_filter in i[0]]
    if not images:
        raise XcodeError("the test attached no image" + (f" whose name contains {name_filter!r}" if name_filter else "")
                         + ": it must attach an XCTAttachment(screenshot:)")
    if len(images) > 1:
        raise XcodeError(f"{len(images)} image attachments match ({', '.join(n for n, _ in images)[:200]}); "
                         "set 'attachment' in the navigation file")
    src = images[0][1]
    if not src.is_file() or src.stat().st_size == 0:
        raise XcodeError("the exported attachment is missing or empty")
    dest = dest_stem.with_name(dest_stem.name + src.suffix.lower())
    shutil.copyfile(src, dest)
    return dest
