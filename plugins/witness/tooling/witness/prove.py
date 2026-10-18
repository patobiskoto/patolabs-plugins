"""One iOS proof (web proofs: web.py): reset, test, build, install, launch, settle, screenshot, report (PAT-254)."""
from __future__ import annotations

import hashlib
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from . import checkout, report, simulator, xcode

SETTLE_SECONDS = 5.0  # fixed wait between launch and screenshot; not adaptive


class ProofFailed(Exception):
    pass


def now() -> datetime:
    return datetime.now(timezone.utc)


def run_step(rep: dict, name: str, fn):
    """Time one step, append it to the report, raise ProofFailed on a known failure."""
    t0 = time.monotonic()
    try:
        result = fn()
    except (xcode.XcodeError, simulator.SimulatorError, OSError) as exc:
        rep["steps"].append({"name": name, "status": "failed", "detail": str(exc)[:300],
                             "seconds": time.monotonic() - t0})
        raise ProofFailed(name) from exc
    rep["steps"].append({"name": name, "status": "ok", "seconds": time.monotonic() - t0})
    return result


def prove(*, project: Path, scheme: str, sim_name: str, runtime: str | None, label: str,
          out: Path | None, skip_tests: bool, test_plan: str | None, only: list[str],
          reset: bool, state_description: str | None,
          nav: dict | None = None) -> tuple[dict, Path, Path, int]:
    """Returns (report, json path, markdown path, exit code); raises checkout.Refused or
    simulator.SimulatorError before anything is run when the preconditions fail."""
    project = project.resolve()
    if not project.exists():
        raise checkout.Refused(f"project path {project} does not exist")
    kind = checkout.guard(project)
    if out:
        checkout.guard_out(out)
    sim = simulator.resolve(sim_name, runtime)
    out = (out or Path(tempfile.mkdtemp(prefix="witness-"))).resolve()
    out.mkdir(parents=True, exist_ok=True)
    derived = out / "derived-data"
    started = now()
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    shot = out / f"{label}-{stamp}.png"
    start_state = ({"mode": "reset"} if reset else
                   {"mode": "described", "description": state_description} if state_description
                   else {"mode": "unspecified"})
    uitest = bool(nav and nav["mechanism"] == "uitest")
    selection = ("navigation-uitest" if uitest else "skipped" if skip_tests else
                 "test-plan+only-testing" if test_plan and only else
                 "test-plan" if test_plan else "only-testing" if only else "all")
    start_state["tests_selection"] = selection
    rep: dict = {
        "schema": report.SCHEMA, "platform": "ios", "navigation": nav, "label": label, "generated_at": started.isoformat(timespec="seconds"),
        "project": project.name, "scheme": scheme, "checkout": {"kind": kind, **checkout.describe(project)},
        "simulator": {k: sim[k] for k in ("name", "udid", "runtime")},
        "start_state": start_state, "tests": {"result": "skipped" if skip_tests and not uitest else "unknown"},
        "steps": [], "paths": {"out": str(out)}, "token_cost": report.TOKEN_COST_UNKNOWN,
        "outcome": "failed",
    }
    code = 5

    def step(name: str, fn):
        return run_step(rep, name, fn)

    try:
        if reset:
            step("reset", lambda: simulator.erase(sim["udid"]))
        if uitest:
            step("boot", lambda: simulator.ensure_booted(sim["udid"]))
            log = out / f"test-{label}-{stamp}.log"
            bundle = out / f"navigation-{label}-{stamp}.xcresult"
            rep["tests"] = step("uitest", lambda: xcode.run_tests(
                project, scheme, sim["udid"], derived, log, None, [nav["test"]], bundle))
            rep["paths"].update(test_log=str(log), result_bundle=str(bundle))
            if rep["tests"]["result"] != "passed":
                rep["steps"][-1]["status"] = "tests-" + rep["tests"]["result"]
            shot = step("extract-screenshot", lambda: xcode.export_screenshot(
                bundle, out / f"attachments-{label}-{stamp}", nav.get("attachment"),
                out / f"{label}-{stamp}"))
        else:
            if skip_tests:
                rep["steps"].append({"name": "test", "status": "skipped", "seconds": 0.0})
            else:
                log = out / f"test-{label}-{stamp}.log"
                rep["tests"] = step("test", lambda: xcode.run_tests(project, scheme, sim["udid"], derived,
                                                                    log, test_plan, only))
                rep["paths"]["test_log"] = str(log)
                if rep["tests"]["result"] != "passed":
                    rep["steps"][-1]["status"] = "tests-" + rep["tests"]["result"]
            blog = out / f"build-{label}-{stamp}.log"
            step("build", lambda: xcode.build(project, scheme, sim["udid"], derived, blog))
            rep["paths"]["build_log"] = str(blog)
            app, bundle_id = step("find-app", lambda: xcode.find_app(derived, scheme))
            rep["app"] = {"bundle_id": bundle_id, "path": str(app)}
            step("boot", lambda: simulator.ensure_booted(sim["udid"]))
            step("install", lambda: simulator.install(sim["udid"], app))
            step("launch", lambda: simulator.launch(sim["udid"], bundle_id))
            step("settle", lambda: time.sleep(SETTLE_SECONDS))
            if nav:
                step("deeplink", lambda: simulator.open_url(sim["udid"], nav["url"]))
                step("settle-after-link", lambda: time.sleep(SETTLE_SECONDS))
            step("screenshot", lambda: simulator.screenshot(sim["udid"], shot))
        rep["paths"]["screenshot"] = str(shot)
        rep["screenshot_sha256"] = hashlib.sha256(shot.read_bytes()).hexdigest()
        code = 0 if rep["tests"]["result"] in ("passed", "skipped") else 6
        rep["outcome"] = "proved" if code == 0 else "captured-with-tests-" + rep["tests"]["result"]
    except ProofFailed as exc:
        rep["outcome"] = f"failed at {exc}"
    rep["total_seconds"] = round(sum(s["seconds"] for s in rep["steps"]), 1)
    j, m = report.write(rep, out, f"report-{label}-{stamp}")
    return rep, j, m, code
