"""Simulator lookup and control through `xcrun simctl` only (PAT-254)."""
from __future__ import annotations

import json
from pathlib import Path

from . import proc

TIMEOUT_LIST = 60.0
TIMEOUT_CONTROL = 120.0
TIMEOUT_BOOT = 300.0
TIMEOUT_ERASE = 300.0
TIMEOUT_SCREENSHOT = 60.0


class SimulatorError(Exception):
    pass


def _simctl(args: list[str], timeout: float) -> tuple[int | None, str, str]:
    return proc.run(["xcrun", "simctl", *args], timeout)


def _json(args: list[str]) -> dict:
    rc, out, err = _simctl(args, TIMEOUT_LIST)
    if rc != 0:
        raise SimulatorError(f"simctl {' '.join(args[:2])} failed: {(err or 'no output').strip()[:200]}")
    try:
        data = json.loads(out)
    except ValueError:
        raise SimulatorError("simctl list answered something that is not JSON") from None
    if not isinstance(data, dict):
        raise SimulatorError("simctl list answered an unexpected JSON shape")
    return data


def _runtime_label(identifier: str, runtimes: dict[str, str]) -> str:
    if identifier in runtimes:
        return runtimes[identifier]
    tail = identifier.rsplit(".", 1)[-1]  # "iOS-27-0" -> "iOS 27.0" (patch level unknown here)
    parts = tail.split("-")
    return f"{parts[0]} {'.'.join(parts[1:])}" if len(parts) > 1 else tail


def resolve(name: str, runtime: str | None = None) -> dict:
    """Resolve a simulator name (or UDID) to {udid, name, runtime, runtime_id, state}.

    Refuses zero or several matches: the caller then names the runtime (`--runtime`).
    """
    devices = _json(["list", "devices", "--json"]).get("devices")
    if not isinstance(devices, dict):
        raise SimulatorError("simctl list devices has no 'devices' object")
    try:
        rts = _json(["list", "runtimes", "--json"]).get("runtimes", [])
        labels = {r["identifier"]: f"{r['name']} ({r['version']})" for r in rts
                  if isinstance(r, dict) and "identifier" in r and "name" in r and "version" in r}
    except (SimulatorError, TypeError):
        labels = {}
    found = []
    for rid, items in devices.items():
        if not isinstance(items, list) or "iOS" not in rid:
            continue
        for d in items:
            if not isinstance(d, dict) or d.get("isAvailable") is not True:
                continue
            if name not in (d.get("name"), d.get("udid")):
                continue
            label = _runtime_label(rid, labels)
            if runtime and runtime not in rid and runtime not in label:
                continue
            found.append({"udid": d.get("udid"), "name": d.get("name"), "runtime": label,
                          "runtime_id": rid, "state": d.get("state", "unknown")})
    if not found:
        raise SimulatorError(f"no available iOS simulator named {name!r}"
                             + (f" for runtime {runtime!r}" if runtime else ""))
    if len(found) > 1:
        listing = ", ".join(f"{f['udid']} ({f['runtime']})" for f in found)
        raise SimulatorError(f"{len(found)} simulators match {name!r}: {listing}; pass --runtime")
    return found[0]


def state(udid: str) -> str:
    for items in _json(["list", "devices", "--json"]).get("devices", {}).values():
        for d in items if isinstance(items, list) else []:
            if isinstance(d, dict) and d.get("udid") == udid:
                return str(d.get("state", "unknown"))
    return "unknown"


def _must(args: list[str], timeout: float) -> str:
    rc, out, err = _simctl(args, timeout)
    if rc != 0:
        why = (err or out or "no output").strip().splitlines()[-1:] or ["no output"]
        raise SimulatorError(f"simctl {args[0]} failed (rc={rc}): {why[0][:200]}")
    return out


def erase(udid: str) -> None:
    """`simctl erase`; only when the simulator is shut down (never shuts it down itself)."""
    current = state(udid)
    if current != "Shutdown":
        raise SimulatorError(f"cannot reset: the simulator state is {current!r}, not 'Shutdown'; "
                             "shut it down yourself first")
    _must(["erase", udid], TIMEOUT_ERASE)


def ensure_booted(udid: str) -> None:
    if state(udid) != "Booted":
        _must(["boot", udid], TIMEOUT_BOOT)
    _must(["bootstatus", udid], TIMEOUT_BOOT)


def install(udid: str, app: Path) -> None:
    _must(["install", udid, str(app)], TIMEOUT_CONTROL)


def launch(udid: str, bundle_id: str) -> None:
    _must(["launch", "--terminate-running-process", udid, bundle_id], TIMEOUT_CONTROL)


def screenshot(udid: str, path: Path) -> None:
    _must(["io", udid, "screenshot", str(path)], TIMEOUT_SCREENSHOT)
    if not path.is_file() or path.stat().st_size == 0:
        raise SimulatorError("simctl reported success but the screenshot file is missing or empty")
