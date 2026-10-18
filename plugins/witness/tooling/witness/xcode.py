"""xcodebuild test and build, app discovery, test summary (PAT-254)."""
from __future__ import annotations

import plistlib
import re
from pathlib import Path

from . import proc

TIMEOUT_TEST = 3600.0
TIMEOUT_BUILD = 3600.0

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
              test_plan: str | None, only: list[str]) -> dict:
    cmd = _base(project, scheme, udid, derived)
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
