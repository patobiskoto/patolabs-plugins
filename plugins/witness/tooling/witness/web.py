"""Local web page proof: headless Chromium-family browser with a throwaway profile (PAT-255).

No agent-host browser tool is used: the same command line runs under Claude Code and Codex.
"""
from __future__ import annotations

import hashlib
import http.client
import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from . import checkout, proc, report
from .prove import ProofFailed, now, run_step

ENV_BROWSER = "WITNESS_BROWSER"
KNOWN_BROWSERS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)
PATH_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
LOCAL_HOSTS = ("localhost", "127.0.0.1")
DEFAULT_VIEWPORT = "1280x800"
VIRTUAL_TIME_BUDGET_MS = 5000  # page settle budget passed to the browser; not an observed readiness
TIMEOUT_VERSION = 15.0
TIMEOUT_SCREENSHOT = 120.0
TIMEOUT_PROBE = 10.0
PROFILE_FRESH = "fresh temporary"
_VIEWPORT = re.compile(r"^(\d{2,5})x(\d{2,5})$")


class WebRefused(Exception):
    pass


def check_url(url: str) -> str:
    """Return the path (and query) of a local http URL; raise WebRefused for anything else."""
    try:
        parts = urlsplit(url)
        host, _ = parts.hostname, parts.port
    except ValueError:
        raise WebRefused("the URL cannot be parsed") from None
    if parts.scheme != "http" or host not in LOCAL_HOSTS or parts.username or parts.password:
        raise WebRefused(f"only http://localhost and http://127.0.0.1 URLs are accepted, got host {host!r}"
                         f" and scheme {parts.scheme!r}")
    return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


def check_viewport(value: str) -> tuple[int, int]:
    m = _VIEWPORT.match(value)
    if not m:
        raise WebRefused("viewport must look like 1280x800")
    return int(m.group(1)), int(m.group(2))


def locate_browser() -> Path:
    """WITNESS_BROWSER if set (no fallback when it is wrong), else the first known install path or PATH name."""
    explicit = os.environ.get(ENV_BROWSER)
    if explicit:
        p = Path(explicit)
        if not (p.is_file() and os.access(p, os.X_OK)):
            raise WebRefused(f"{ENV_BROWSER}={explicit!r} is not an executable file")
        return p
    for cand in KNOWN_BROWSERS:
        if Path(cand).is_file() and os.access(cand, os.X_OK):
            return Path(cand)
    for name in PATH_NAMES:
        found = shutil.which(name)
        if found:
            return Path(found)
    raise WebRefused("no Chromium-family browser found: install Google Chrome, Chromium, Brave or Edge, "
                     f"or set {ENV_BROWSER} to its executable (no agent-host browser tool is used)")


def browser_version(browser: Path) -> str:
    rc, out, _ = proc.run([str(browser), "--version"], TIMEOUT_VERSION)
    return out.strip().splitlines()[0][:100] if rc == 0 and out.strip() else "unknown"


def probe(url: str) -> int:
    """One GET of the URL (the request the browser makes next), body not read, redirects not followed: the HTTP
    status. Raises WebRefused when nothing answers or the server answers an error (>= 400): a headless browser
    given such a page would still exit 0 with a screenshot of the error page (deduced, not observed)."""
    parts = urlsplit(url)
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=TIMEOUT_PROBE)
    try:
        conn.request("GET", target, headers={"User-Agent": "witness-probe"})
        status = conn.getresponse().status
    except (OSError, http.client.HTTPException) as exc:
        raise WebRefused(f"nothing usable answers at {url} ({type(exc).__name__}): start the local server first") from None
    finally:
        conn.close()
    if status >= 400:
        raise WebRefused(f"the server answers HTTP {status} at {url}: a screenshot would show the error page")
    return status


def capture(browser: Path, url: str, viewport: tuple[int, int], shot: Path) -> str:
    """Screenshot `shot`; returns the profile state recorded in the report: PROFILE_FRESH, or that text plus the path
    when the temporary profile directory is still there after `rmtree` (cause unknown, perhaps browser children)."""
    profile = Path(tempfile.mkdtemp(prefix="witness-profile-"))
    try:
        cmd = [str(browser), "--headless=new", f"--user-data-dir={profile}", "--no-first-run",
               "--no-default-browser-check", "--disable-extensions", "--hide-scrollbars",
               "--force-device-scale-factor=1", f"--virtual-time-budget={VIRTUAL_TIME_BUDGET_MS}",
               f"--window-size={viewport[0]},{viewport[1]}", f"--screenshot={shot}", url]
        rc, _, err = proc.run(cmd, TIMEOUT_SCREENSHOT)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
        left = profile.exists()
    state = f"{PROFILE_FRESH}; NOT removed, still at {profile}" if left else PROFILE_FRESH
    if rc != 0:
        raise OSError(f"browser failed (rc={rc}): {(err or 'no output').strip()[-200:]}"
                      + (f"; profile left at {profile}" if left else ""))
    if not shot.is_file() or shot.stat().st_size == 0:
        raise OSError("the browser reported success but the screenshot file is missing or empty")
    return state


def prove_web(*, project: Path, url: str, viewport: str, label: str, out: Path | None,
              ) -> tuple[dict, Path, Path, int]:
    """Same return shape as prove.prove. Raises checkout.Refused or WebRefused before anything is run."""
    path = check_url(url)  # URL and viewport first: a refusal runs nothing, not even git
    size = check_viewport(viewport)
    project = project.resolve()
    if not project.exists():
        raise checkout.Refused(f"project path {project} does not exist")
    kind = checkout.guard(project)
    if out:
        checkout.guard_out(out)
    browser = locate_browser()
    status = probe(url)
    out = (out or Path(tempfile.mkdtemp(prefix="witness-"))).resolve()
    out.mkdir(parents=True, exist_ok=True)
    started = now()
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    shot = out / f"{label}-{stamp}.png"
    rep: dict = {
        "schema": report.SCHEMA, "platform": "web", "label": label,
        "generated_at": started.isoformat(timespec="seconds"), "project": project.name,
        "checkout": {"kind": kind, **checkout.describe(project)},
        "browser": {"path": str(browser), "version": browser_version(browser), "profile": PROFILE_FRESH},
        "viewport": f"{size[0]}x{size[1]}", "url": url,
        "navigation": {"mechanism": "web", "path": path, "probe_status": status},
        "start_state": {"mode": "fresh-profile", "tests_selection": "none"}, "tests": {"result": "skipped"},
        "steps": [], "paths": {"out": str(out)}, "token_cost": report.TOKEN_COST_UNKNOWN, "outcome": "failed",
    }
    code = 5
    try:
        rep["browser"]["profile"] = run_step(rep, "screenshot", lambda: capture(browser, url, size, shot))
        rep["paths"]["screenshot"] = str(shot)
        rep["screenshot_sha256"] = hashlib.sha256(shot.read_bytes()).hexdigest()
        rep["outcome"], code = "proved", 0
    except ProofFailed as exc:
        rep["outcome"] = f"failed at {exc}"
    rep["total_seconds"] = round(sum(s["seconds"] for s in rep["steps"]), 1)
    j, m = report.write(rep, out, f"report-{label}-{stamp}")
    return rep, j, m, code
