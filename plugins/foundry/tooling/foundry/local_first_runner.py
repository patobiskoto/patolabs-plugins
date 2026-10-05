"""PAT-108: comparison launcher of the PAT-19 local-first qualification (protocol v1).

A campaign tool, not a product role (FOUNDRY-ADR-0007): it never touches routing, mappings, roles
or defaults and promotes nothing. It plays one task per path (A current cloud, B economy cloud,
C hybrid = one bounded local attempt then cloud validation, with the A path taking over) from the
PAT-107 bundles, judged by ``local_first_corpus``; premium tokens are read from the host session
logs (``cost_attribution``, FOUNDRY-ADR-0015) and attached to an execution by the session id the
launcher itself hands to the driver.

The tool loads no model and calls no tracker. A real arm runs only from a campaign config whose
driver is marked ``verified``, under an authorization envelope (FOUNDRY-ADR-0010) and a passing
machine preflight; ``--dry-run`` accepts only ``fake`` drivers. See
``docs/qualification/pat-19-launcher-v1.md``.

Run as ``python3 -m foundry.local_first_runner {preflight,screen,compare,report} --help`` from
``plugins/foundry/tooling``.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import glob
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from foundry import cost_attribution as ca
from foundry import local_first_corpus as lfc

CAMPAIGN_SCHEMA = "foundry.local-first-campaign.v1"
ENVELOPE_SCHEMA = "foundry.local-first-envelope.v1"
RESULT_SCHEMA = "foundry.local-first-result.v1"
MODES = ("screen", "compare")
DRIVER_KINDS = ("local_harness", "neutral_harness", "cloud_implementer", "cloud_reviewer")
LOCAL_KINDS = ("local_harness", "neutral_harness")
PATHS = ("A", "B", "C", "N")
IMPLEMENTER_DRIVER = {"A": "cloud_implementer_current", "B": "cloud_implementer_economy"}
REVIEWER_DRIVER = "cloud_reviewer"
TOKEN_CLASSES = ca.TOKEN_KEYS
# The only external commands the preflight and the machine probes may run (read-only).
READ_ONLY_COMMANDS = frozenset({
    ("sysctl", "-n", "machdep.cpu.brand_string"), ("sysctl", "-n", "hw.memsize"),
    ("sysctl", "-n", "vm.swapusage"), ("sw_vers", "-productVersion"), ("memory_pressure",),
    ("lms", "version"), ("lms", "runtime", "ls"), ("lms", "ps", "--json"),
    ("ps", "-axo", "rss=,command=")})
_SECRET_NAME = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD|CREDENTIAL|SSH_AUTH_SOCK|^FOUNDRY_)", re.I)
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class RunnerError(RuntimeError):
    pass


class EnvelopeError(RunnerError):
    """No (valid) authorization envelope, or the action is outside it."""


class CapReached(RunnerError):
    """An envelope ceiling is reached: the campaign stops."""


class PreflightRefused(RunnerError):
    def __init__(self, refusals: Sequence[str]):
        super().__init__("preflight refused: " + ", ".join(refusals))
        self.refusals = list(refusals)


# ------------------------------------------------------------------------ campaign config

def load_campaign(path: Path) -> dict[str, Any]:
    """The frozen campaign config. Fails closed on any missing piece."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != CAMPAIGN_SCHEMA:
        raise RunnerError(f"{path}: unexpected schema")
    for key in ("frozen_machine", "bounds", "statement_footer", "prompts", "rules", "drivers",
                "candidates"):
        if key not in data:
            raise RunnerError(f"{path}: missing {key}")
    for key in ("local_max_seconds", "local_max_steps", "cloud_max_seconds",
                "max_correction_rounds"):
        if type(data["bounds"].get(key)) is not int or data["bounds"][key] < 0:
            raise RunnerError(f"{path}: bounds.{key} must be a non-negative integer")
    for key in ("implement", "correct", "review"):
        if not isinstance(data["prompts"].get(key), str):
            raise RunnerError(f"{path}: prompts.{key} missing")
    for name, driver in data["drivers"].items():
        if driver.get("kind") not in DRIVER_KINDS or not isinstance(driver.get("argv"), list) \
                or not all(isinstance(a, str) for a in driver["argv"]):
            raise RunnerError(f"{path}: driver {name} needs a kind in {DRIVER_KINDS} and an argv list")
        if driver.get("home", "isolated") not in ("isolated", "real"):
            raise RunnerError(f"{path}: driver {name}: home must be isolated or real")
        if driver.get("network", "loopback") not in ("loopback", "open"):
            raise RunnerError(f"{path}: driver {name}: network must be loopback or open")
        if driver["kind"] in LOCAL_KINDS and (driver.get("home", "isolated") != "isolated"
                                              or driver.get("network", "loopback") != "loopback"):
            raise RunnerError(f"{path}: local driver {name} must use an isolated home and loopback")
        for env_name in driver.get("env_allow", []):
            if _SECRET_NAME.search(env_name):
                raise RunnerError(f"{path}: driver {name} may not forward {env_name}")
    for cid, cand in data["candidates"].items():
        if not _ID.match(cid) or not isinstance(cand.get("model"), str):
            raise RunnerError(f"{path}: candidate {cid} needs an id and a model")
    return data


def _check_driver_usable(driver_id: str, driver: Mapping[str, Any], dry_run: bool) -> None:
    """A real arm needs a verified driver; a dry run needs a fake one (never a real arm)."""
    if dry_run:
        if not driver.get("fake"):
            raise RunnerError(f"dry run refuses the non-fake driver {driver_id}")
    elif driver.get("fake") or driver.get("verified") is not True:
        raise RunnerError(f"driver {driver_id} is not verified (or is fake): "
                          "PAT-109's preflight must smoke-test and pin it before a real run")


# ------------------------------------------------------------------- envelope and ledger

def load_envelope(path: Path | None, mode: str, today: dt.date) -> dict[str, Any]:
    """The operator's authorization envelope. Mandatory; the launcher never writes one."""
    if not path or not Path(path).is_file():
        raise EnvelopeError("no authorization envelope: refusing to start")
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except ValueError as exc:
        raise EnvelopeError(f"envelope is not valid JSON: {exc}") from None
    if not isinstance(data, dict) or data.get("schema") != ENVELOPE_SCHEMA:
        raise EnvelopeError("envelope: unexpected schema")
    if not isinstance(data.get("campaign_id"), str) or not _ID.match(data["campaign_id"]):
        raise EnvelopeError("envelope: campaign_id missing or not a plain identifier")
    try:
        expires = dt.date.fromisoformat(str(data.get("expires_on")))
    except ValueError:
        raise EnvelopeError("envelope: expires_on must be an ISO date") from None
    if expires < today:
        raise EnvelopeError(f"envelope expired on {expires}")
    modes = data.get("allowed_modes")
    if not isinstance(modes, list) or not set(modes) <= set(MODES) or mode not in modes:
        raise EnvelopeError(f"envelope does not allow mode {mode!r}")
    caps = data.get("caps")
    if not isinstance(caps, dict):
        raise EnvelopeError("envelope: caps missing")
    for key in ("cloud_executions", "premium_tokens", "wall_clock_seconds"):
        if type(caps.get(key)) is not int or caps[key] < 0:
            raise EnvelopeError(f"envelope: caps.{key} must be a non-negative integer")
    data["sha256"] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return data


class Ledger:
    """Append-only consumption ledger. A cloud execution is recorded BEFORE it starts."""

    def __init__(self, state_dir: Path, envelope: Mapping[str, Any], mode: str,
                 now: Callable[[], float] = time.time):
        self.mode, self.caps, self.now = mode, envelope["caps"], now
        self.path = Path(state_dir) / f"ledger-{envelope['campaign_id']}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.append("session_started", mode=mode, envelope_sha256=envelope["sha256"])

    def append(self, kind: str, **fields: Any) -> None:
        line = json.dumps({"kind": kind, "at": self.now(), **fields}, sort_keys=True) + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def totals(self) -> dict[str, Any]:
        out = {"cloud_started": 0, "premium_tokens": 0, "tokens_unmeasurable": False, "seconds": 0.0}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            if entry["kind"] == "cloud_started":
                out["cloud_started"] += 1
            elif entry["kind"] == "settled":
                out["seconds"] += entry.get("seconds") or 0
                if entry.get("cloud"):
                    if entry.get("premium_tokens") is None:
                        out["tokens_unmeasurable"] = True
                    else:
                        out["premium_tokens"] += entry["premium_tokens"]
        return out

    def check(self, *, cloud: bool) -> None:
        """Raise ``CapReached`` when the next execution is outside the envelope."""
        used = self.totals()
        if used["seconds"] >= self.caps["wall_clock_seconds"]:
            raise CapReached("wall_clock_seconds")
        if cloud:
            if used["cloud_started"] >= self.caps["cloud_executions"]:
                raise CapReached("cloud_executions")
            if used["tokens_unmeasurable"]:
                raise CapReached("premium_tokens_unmeasurable")
            if used["premium_tokens"] >= self.caps["premium_tokens"]:
                raise CapReached("premium_tokens")

    def reserve_cloud(self, role: str, session_id: str) -> None:
        if self.mode != "compare":
            raise EnvelopeError(f"mode {self.mode!r} can never start a cloud execution")
        self.check(cloud=True)
        self.append("cloud_started", role=role, session_id=session_id)

    def remaining_seconds(self) -> float:
        return self.caps["wall_clock_seconds"] - self.totals()["seconds"]

    def settle(self, *, cloud: bool, seconds: float, premium_tokens: int | None) -> None:
        self.append("settled", cloud=cloud, seconds=round(seconds, 3), premium_tokens=premium_tokens)


# ------------------------------------------------------------------------------- preflight

def default_run(argv: Sequence[str]) -> str | None:
    """Run one read-only probe command. Anything outside the allow-list is refused: the launcher
    never loads, unloads or downloads a model."""
    if tuple(argv) not in READ_ONLY_COMMANDS:
        raise RunnerError(f"command not allowed for the launcher: {' '.join(argv)}")
    try:
        proc = subprocess.run(list(argv), capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _swap_used_mib(text: str | None) -> float | None:
    found = re.search(r"used\s*=\s*([\d.]+)([MG])", text or "")
    return None if not found else float(found.group(1)) * (1024 if found.group(2) == "G" else 1)


def _pressure_free_percent(text: str | None) -> int | None:
    found = re.search(r"free percentage:\s*(\d+)%", text or "")
    return int(found.group(1)) if found else None


def machine_snapshot(run: Callable[[Sequence[str]], str | None],
                     server_pattern: str | None) -> dict[str, Any]:
    """Swap, memory pressure and server memory; an unavailable value is None with a reason."""
    unknown: dict[str, str] = {}
    swap = _swap_used_mib(run(["sysctl", "-n", "vm.swapusage"]))
    if swap is None:
        unknown["swap_used_mib"] = "vm.swapusage unavailable or unparsable"
    pressure = _pressure_free_percent(run(["memory_pressure"]))
    if pressure is None:
        unknown["pressure_free_percent"] = "memory_pressure unavailable or unparsable"
    rss: int | None = None
    ps = run(["ps", "-axo", "rss=,command="]) if server_pattern else None
    if ps is not None:
        total, matched = 0, False
        for line in ps.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and parts[0].isdigit() and re.search(server_pattern, parts[1]):
                total, matched = total + int(parts[0]), True
        rss = total if matched else None
    if rss is None:
        unknown["server_rss_kib"] = "no server process matched or ps unavailable"
    return {"swap_used_mib": swap, "pressure_free_percent": pressure, "server_rss_kib": rss,
            "unknown": unknown}


def preflight(campaign: Mapping[str, Any], expected_model: str,
              run: Callable[[Sequence[str]], str | None] = default_run,
              disk_free_gib: Callable[[], float] | None = None) -> dict[str, Any]:
    """Machine facts against the frozen coordinates; refuses on any difference. Loads nothing."""
    frozen, refusals = campaign["frozen_machine"], []
    facts: dict[str, Any] = {}

    def fact(name: str, argv: Sequence[str]) -> str | None:
        value = run(argv)
        facts[name] = value.strip() if value else None
        if not value:
            refusals.append(f"fact_unavailable:{name}")
        return facts[name]

    chip = fact("chip", ["sysctl", "-n", "machdep.cpu.brand_string"])
    if chip and frozen["chip_contains"] not in chip:
        refusals.append("chip_differs")
    mem = fact("memsize_bytes", ["sysctl", "-n", "hw.memsize"])
    if mem and not (mem.isdigit() and int(mem) // 2**30 == frozen["memory_gib"]):
        refusals.append("memory_differs")
    os_version = fact("os_version", ["sw_vers", "-productVersion"])
    if os_version and os_version != frozen["os_version"]:
        refusals.append("os_version_differs")
    lms_version = fact("lm_studio_version", ["lms", "version"])
    if lms_version and frozen["lm_studio_version_contains"] not in lms_version:
        refusals.append("lm_studio_version_differs")
    runtimes = fact("runtimes", ["lms", "runtime", "ls"])
    if runtimes and frozen["mlx_runtime_contains"] not in runtimes:
        refusals.append("mlx_runtime_differs")
    loaded = fact("loaded_models_raw", ["lms", "ps", "--json"])
    facts["loaded_models"] = None
    if loaded:
        try:
            items = json.loads(loaded)
            ids = sorted({str(i.get("identifier") or i.get("modelKey")) for i in items})
        except (ValueError, AttributeError, TypeError):
            refusals.append("lms_ps_unparseable")
        else:
            facts["loaded_models"] = ids
            if expected_model not in ids:
                refusals.append("expected_model_not_loaded")
            refusals.extend(f"other_model_loaded:{i}" for i in ids if i != expected_model)
    facts.pop("loaded_models_raw")
    free = disk_free_gib() if disk_free_gib else shutil.disk_usage(Path.home()).free / 2**30
    facts["free_disk_gib"] = round(free, 1)
    if free < frozen["min_free_disk_gib"]:
        refusals.append("disk_below_minimum")
    facts["baseline"] = machine_snapshot(run, campaign.get("server_process_pattern"))
    return {"ok": not refusals, "refusals": refusals, "facts": facts, "expected_model": expected_model}


def dry_run_facts(campaign: Mapping[str, Any], expected_model: str) -> Callable[[Sequence[str]], str]:
    """Canned machine facts matching the frozen coordinates (dry run only; no command is run)."""
    frozen = campaign["frozen_machine"]
    answers = {
        ("sysctl", "-n", "machdep.cpu.brand_string"): f"Apple {frozen['chip_contains']}",
        ("sysctl", "-n", "hw.memsize"): str(frozen["memory_gib"] * 2**30),
        ("sw_vers", "-productVersion"): frozen["os_version"],
        ("lms", "version"): frozen["lm_studio_version_contains"],
        ("lms", "runtime", "ls"): frozen["mlx_runtime_contains"],
        ("lms", "ps", "--json"): json.dumps([{"identifier": expected_model}]),
        ("sysctl", "-n", "vm.swapusage"): "total = 1024.00M  used = 100.00M  free = 924.00M",
        ("memory_pressure",): "System-wide memory free percentage: 80%",
        ("ps", "-axo", "rss=,command="): ""}
    return lambda argv: answers.get(tuple(argv))


# ------------------------------------------------------------------------------ isolation

def _sb(path: str | Path) -> str:
    real = os.path.realpath(str(path)).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{real}"'


def sandbox_profile(*, writable: Sequence[Path], deny_read: Sequence[Path], network: str) -> str:
    """sandbox-exec profile: file writes only under ``writable``, no read of ``deny_read``, and
    (``network == "loopback"``) no network except loopback."""
    lines = ["(version 1)", "(allow default)", "(deny file-write*)",
             "(allow file-write* " + " ".join(f"(subpath {_sb(p)})" for p in writable)
             + ' (literal "/dev/null") (literal "/dev/dtracehelper") (literal "/dev/tty"))']
    if deny_read:
        lines.append("(deny file-read* " + " ".join(f"(subpath {_sb(p)})" for p in deny_read) + ")")
    if network == "loopback":
        lines += ["(deny network*)", '(allow network* (remote ip "localhost:*"))']
    return "\n".join(lines) + "\n"


def isolated_environment(driver: Mapping[str, Any], scratch: Path,
                         host_env: Mapping[str, str]) -> dict[str, str]:
    """Whitelisted environment: no token, no FOUNDRY_*, no agent socket; isolated HOME/XDG for a
    local arm (a cloud arm keeps the real HOME, the Claude Code OAuth identity: AGENTS.md R6)."""
    names = ("PATH", "LANG", "LC_ALL") if driver.get("home", "isolated") == "isolated" else (
        "PATH", "LANG", "LC_ALL", "HOME", "LOGNAME", "USER", "TMPDIR")
    env = {n: host_env[n] for n in names if n in host_env}
    env.update({n: host_env[n] for n in driver.get("env_allow", []) if n in host_env})
    if driver.get("home", "isolated") == "isolated":
        home = scratch / "home"
        env.update({"HOME": str(home), "TMPDIR": str(scratch / "tmp"), "TERM": "dumb",
                    "XDG_CONFIG_HOME": str(home / ".config"), "XDG_CACHE_HOME": str(home / ".cache"),
                    "XDG_DATA_HOME": str(home / ".local" / "share"),
                    "XDG_STATE_HOME": str(home / ".local" / "state")})
    return env


def sandbox_available() -> bool:
    return sys.platform == "darwin" and shutil.which("sandbox-exec") is not None


# ------------------------------------------------------------------------- stream parsing

class StreamStats:
    """Counters read from a harness event stream. ``format == "none"`` exposes nothing (unknown)."""

    def __init__(self, stream: Mapping[str, Any] | None):
        self.stream = stream or {"format": "none"}
        self.known = self.stream.get("format") == "omp-json"
        self.steps = 0 if self.known else None
        self.tokens: dict[str, int] | None = {"input": 0, "output": 0, "reasoning": 0} if self.known else None
        self.speeds: dict[str, list[float]] = {"prefill": [], "generation": []}

    def feed(self, line: str) -> None:
        if not self.known:
            return
        try:
            event = json.loads(line)
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        if event.get("type") == "tool_execution_start":
            self.steps += 1
        elif event.get("type") == "message_end":
            message = event.get("message") or {}
            usage = message.get("usage") if message.get("role") == "assistant" else None
            if isinstance(usage, dict):
                for name, key in (("input", "input"), ("output", "output"),
                                  ("reasoning", "reasoningTokens")):
                    if type(usage.get(key)) is int:
                        self.tokens[name] += usage[key]
                for kind, key in (self.stream.get("speed_usage_keys") or {}).items():
                    if isinstance(usage.get(key), (int, float)):
                        self.speeds[kind].append(float(usage[key]))

    def summary(self) -> dict[str, Any]:
        unknown: dict[str, str] = {}
        out: dict[str, Any] = {"steps": self.steps, "stream_tokens": self.tokens}
        if not self.known:
            unknown["steps"] = unknown["stream_tokens"] = "stream format exposes no events"
        for kind in ("prefill", "generation"):
            values = self.speeds[kind]
            out[f"{kind}_tokens_per_second"] = round(sum(values) / len(values), 2) if values else None
            if not values:
                unknown[f"{kind}_tokens_per_second"] = "not exposed by the event stream"
        out["unknown"] = unknown
        return out


# -------------------------------------------------------------------------- execution

def _kill_group(proc: subprocess.Popen) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)


def _substitute(template: str, values: Mapping[str, str]) -> str:
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def execute_driver(driver: Mapping[str, Any], values: Mapping[str, str], *, workdir: Path,
                   scratch: Path, stream_log: Path, max_seconds: float, max_steps: int | None,
                   sandbox: bool, deny_read: Sequence[Path], extra_write: Sequence[Path] = (),
                   host_env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Run one arm command: process group killed at ``max_seconds`` (and at ``max_steps`` when
    the event stream exposes steps), stdin closed, whitelisted environment, optional sandbox."""
    host_env = os.environ if host_env is None else host_env
    scratch.mkdir(parents=True, exist_ok=True)
    env = isolated_environment(driver, scratch, host_env)
    if driver.get("home", "isolated") == "isolated":
        home = scratch / "home"
        for sub in (home, scratch / "tmp"):
            sub.mkdir(parents=True, exist_ok=True)
        for rel, content in (driver.get("home_files") or {}).items():
            target = home / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_substitute(content, values), encoding="utf-8")
    argv = [_substitute(a, values) for a in driver["argv"]]
    profile_dir = None
    if sandbox:
        if not sandbox_available():
            raise RunnerError("sandbox-exec is unavailable: isolation cannot be enforced")
        profile_dir = Path(os.path.realpath(__import__("tempfile").mkdtemp(prefix="foundry-sb-")))
        profile = profile_dir / "profile.sb"
        profile.write_text(sandbox_profile(
            writable=[workdir, scratch, *extra_write], deny_read=list(deny_read),
            network=driver.get("network", "loopback")), encoding="utf-8")
        argv = ["sandbox-exec", "-f", str(profile), *argv]
    stats = StreamStats(driver.get("stream"))
    state = {"step_limit": False, "timed_out": False}
    stream_log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        with stream_log.open("wb") as log, (scratch / "stderr.log").open("wb") as err:
            proc = subprocess.Popen(argv, cwd=workdir, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=err, start_new_session=True)

            def pump() -> None:
                for raw in proc.stdout:
                    log.write(raw)
                    stats.feed(raw.decode("utf-8", "replace"))
                    if (max_steps is not None and stats.steps is not None
                            and stats.steps > max_steps and not state["step_limit"]):
                        state["step_limit"] = True
                        _kill_group(proc)

            reader = threading.Thread(target=pump, daemon=True)
            reader.start()
            try:
                proc.wait(timeout=max(max_seconds, 0.001))
            except subprocess.TimeoutExpired:
                state["timed_out"] = True
                _kill_group(proc)
                proc.wait()
            reader.join(timeout=10)
            _kill_group(proc)  # stray children of the group
    except OSError as exc:
        return {"exit_code": None, "signal": None, "timed_out": False, "step_limit_hit": False,
                "wall_seconds": round(time.monotonic() - started, 3), "start_error": str(exc)[:200],
                "stream": stats.summary(), "launcher_kill": False}
    finally:
        if profile_dir is not None:
            shutil.rmtree(profile_dir, ignore_errors=True)
    code = proc.returncode
    launcher_kill = state["timed_out"] or state["step_limit"]
    return {"exit_code": code if code is not None and code >= 0 else None,
            "signal": -code if code is not None and code < 0 else None,
            "timed_out": state["timed_out"], "step_limit_hit": state["step_limit"],
            "launcher_kill": launcher_kill, "start_error": None,
            "wall_seconds": round(time.monotonic() - started, 3), "stream": stats.summary()}


# --------------------------------------------------------------------- premium tokens

def premium_tokens(session_id: str, session_log: Mapping[str, Any]
                   ) -> tuple[dict[str, int | None] | None, str | None]:
    """Token counters of ONE cloud execution, read from the host session log named by the session
    id the launcher gave the driver (never by time window). ``(None, reason)`` when unreadable;
    a class the host does not report (reasoning) is None, never 0."""
    pattern = os.path.expanduser(session_log.get("projects_dir", "~/.claude/projects"))
    found = glob.glob(os.path.join(glob.escape(pattern), "*", f"{glob.escape(session_id)}.jsonl"))
    if not found:
        return None, "session_log_not_found"
    if len(found) > 1:
        return None, "session_log_ambiguous"
    try:
        rows = ca.read_host_log(session_log.get("host", "claude"), found[0])
    except ca.CostAttributionError as exc:
        return None, f"host_log_unreadable:{exc}"[:160]
    rows = [r for r in rows if "tokens" in r]
    if not rows or any(r.get("session_id") != session_id for r in rows):
        return None, "log_session_id_mismatch"
    total: dict[str, int | None] = dict.fromkeys(TOKEN_CLASSES, 0)
    for row in rows:
        for name in TOKEN_CLASSES:
            value = row["tokens"][name]
            total[name] = None if (total[name] is None or not isinstance(value, int)) \
                else total[name] + value
    return total, None


def billing_total(tokens: Mapping[str, int | None] | None) -> int | None:
    if tokens is None or any(tokens.get(k) is None for k in ca.BILLING_TOKEN_KEYS):
        return None
    return sum(tokens[k] for k in ca.BILLING_TOKEN_KEYS)


# ------------------------------------------------------------------------------ runner

class Runner:
    """Plays tasks through the paths. Every argument is injected so a test (or a dry run) never
    needs a real machine, model or cloud."""

    def __init__(self, *, repo: Path, campaign: Mapping[str, Any], envelope: Mapping[str, Any],
                 state_dir: Path, work_root: Path, mode: str, dry_run: bool, sandbox: bool,
                 run: Callable[[Sequence[str]], str | None] = default_run,
                 preflight_run: Callable[[str], Callable[[Sequence[str]], str | None]] | None = None,
                 disk_free_gib: Callable[[], float] | None = None,
                 host_env: Mapping[str, str] | None = None, now: Callable[[], float] = time.time):
        if mode not in MODES:
            raise RunnerError(f"unknown mode {mode!r}")
        self.repo, self.campaign, self.envelope = Path(repo).resolve(), campaign, envelope
        self.state_dir, self.work_root = Path(state_dir).resolve(), Path(work_root).resolve()
        self.mode, self.dry_run, self.sandbox = mode, dry_run, sandbox
        self.run, self.disk_free_gib, self.host_env = run, disk_free_gib, host_env
        self.preflight_run = preflight_run or (lambda _candidate: run)
        if lfc.inside_developer_checkout(self.repo, self.work_root):
            raise RunnerError("work root is inside the developer checkout")
        if not dry_run and not sandbox:
            raise RunnerError("a real run needs the sandbox")
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.ledger = Ledger(self.state_dir, envelope, mode, now)
        self.results_path = self.state_dir / f"results-{envelope['campaign_id']}.jsonl"
        self.counter = 0
        self.stopped: str | None = None
        self.handed: set[Path] = set()
        self.deny_read = [*lfc._checkout_roots(self.repo), self.state_dir]

    # ---- bookkeeping
    def _emit(self, record: dict[str, Any]) -> dict[str, Any]:
        record = {"schema": RESULT_SCHEMA, "campaign_id": self.envelope["campaign_id"],
                  "mode": self.mode, **record}
        with self.results_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        return record

    def _driver(self, driver_id: str) -> Mapping[str, Any]:
        driver = self.campaign["drivers"].get(driver_id)
        if driver is None:
            raise RunnerError(f"campaign declares no driver {driver_id}")
        _check_driver_usable(driver_id, driver, self.dry_run)
        return driver

    def _bundle(self, task: Mapping[str, Any], label: str, patch: bytes | None = None
                ) -> tuple[Path, Path]:
        """A FRESH bundle for one attempt (a judged bundle is never handed back to a candidate)
        and its attempt directory (scratch). The statement carries the launcher footer, identical
        for every arm."""
        self.counter += 1
        attempt_dir = self.work_root / f"attempt-{self.counter:04d}-pr{task['pr']}-{label}"
        bundle = lfc.build_bundle(self.repo, task, attempt_dir / "bundle")
        statement = bundle / "TASK.md"
        statement.write_text(statement.read_text(encoding="utf-8")
                             + self.campaign["statement_footer"], encoding="utf-8")
        if patch:
            _apply_patch(bundle, patch)
        if lfc.is_judged(bundle) or bundle in self.handed:
            raise RunnerError("refusing to hand a judged or reused bundle to a candidate")
        self.handed.add(bundle)
        return bundle, attempt_dir

    def _discard(self, bundle: Path, attempt_dir: Path) -> None:
        lfc.remove_bundle(bundle)
        shutil.rmtree(attempt_dir, ignore_errors=True)

    def _values(self, bundle: Path, attempt_dir: Path, **extra: str) -> dict[str, str]:
        b = self.campaign["bounds"]
        return {"workdir": str(bundle), "statement_file": str(bundle / "TASK.md"),
                "scratch": str(attempt_dir / "scratch"), "max_steps": str(b["local_max_steps"]),
                "max_seconds": str(b["local_max_seconds"]),
                "max_duration": (f"{b['local_max_seconds'] // 60}m" if b["local_max_seconds"] % 60 == 0
                                 else f"{b['local_max_seconds']}s"),
                "review_file": str(attempt_dir / "scratch" / "review.json"),
                "feedback_file": str(attempt_dir / "scratch" / "feedback.md"), **extra}

    def _prompt(self, key: str, values: Mapping[str, str]) -> str:
        return _substitute(self.campaign["prompts"][key], values)

    # ---- one local attempt (screen, path N, first step of path C)
    def local_attempt(self, task: Mapping[str, Any], candidate_id: str, driver_id: str,
                      path: str, attempt: int = 0, task_set: str = "") -> tuple[dict[str, Any], bytes]:
        driver = self._driver(driver_id)
        if driver["kind"] not in LOCAL_KINDS:
            raise RunnerError(f"{driver_id} is not a local harness")
        self.ledger.check(cloud=False)
        bounds = self.campaign["bounds"]
        model = self.campaign["candidates"][candidate_id]["model"]
        bundle, attempt_dir = self._bundle(task, f"{path}-local-{candidate_id}")
        try:
            values = self._values(bundle, attempt_dir, model=model)
            values["prompt"] = self._prompt("implement", values)
            before = machine_snapshot(self.run, self.campaign.get("server_process_pattern"))
            budget = min(bounds["local_max_seconds"], max(self.ledger.remaining_seconds(), 0))
            execution = execute_driver(
                driver, values, workdir=bundle, scratch=attempt_dir / "scratch",
                stream_log=self.state_dir / "streams" / f"{self.envelope['campaign_id']}-"
                                                         f"{attempt_dir.name}.jsonl",
                max_seconds=budget, max_steps=bounds["local_max_steps"], sandbox=self.sandbox,
                deny_read=self.deny_read, host_env=self.host_env)
            after = machine_snapshot(self.run, self.campaign.get("server_process_pattern"))
            patch = _capture_patch(bundle)
            verdict = lfc.judge(self.repo, task, bundle)
        finally:
            self._discard(bundle, attempt_dir)
        self.ledger.settle(cloud=False, seconds=execution["wall_seconds"], premium_tokens=None)
        stream = execution["stream"]
        unknown = {f"local.{k}": v for k, v in stream["unknown"].items()}
        unknown.update({f"machine.{k}": v for k, v in {**before["unknown"], **after["unknown"]}.items()})
        local = {"harness": driver_id, "harness_kind": driver["kind"], "candidate": candidate_id,
                 "max_seconds": bounds["local_max_seconds"], "max_steps": bounds["local_max_steps"],
                 "steps": stream["steps"], "stream_tokens": stream["stream_tokens"],
                 "prefill_tokens_per_second": stream["prefill_tokens_per_second"],
                 "generation_tokens_per_second": stream["generation_tokens_per_second"],
                 "timed_out": execution["timed_out"], "step_limit_hit": execution["step_limit_hit"],
                 "exit_code": execution["exit_code"], "signal": execution["signal"],
                 "ended_by_external_signal": execution["signal"] is not None
                 and not execution["launcher_kill"], "start_error": execution["start_error"]}
        record = {"record_type": "attempt", "task": _task_ref(task, task_set), "path": path,
                  "segment": "local", "attempt": attempt, "judge": _judge_summary(verdict),
                  "local_outcome": "accepted" if verdict["verdict"] == "ACCEPTED" else "refused",
                  "accepted": None, "review": {"rounds": 0, "verdicts": []},
                  "wall_seconds": execution["wall_seconds"], "cloud_executions": 0,
                  "premium": {"by_role": {}, "billing_total": 0}, "local": local,
                  "machine": {"before": _strip(before), "after": _strip(after)}, "unknown": unknown}
        return record, patch

    # ---- one cloud execution
    def cloud_execution(self, role: str, driver_id: str, bundle: Path, attempt_dir: Path,
                        prompt_key: str, feedback: str | None = None
                        ) -> tuple[dict[str, Any], dict[str, int | None] | None, str | None]:
        if self.mode != "compare":
            raise EnvelopeError(f"mode {self.mode!r} can never start a cloud execution")
        driver = self._driver(driver_id)
        if driver["kind"] not in ("cloud_implementer", "cloud_reviewer"):
            raise RunnerError(f"{driver_id} is not a cloud driver")
        session_id = str(uuid.uuid4())
        self.ledger.reserve_cloud(role, session_id)  # recorded BEFORE the execution starts
        scratch = attempt_dir / "scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        if feedback is not None:
            (scratch / "feedback.md").write_text(feedback, encoding="utf-8")
        values = self._values(bundle, attempt_dir, session_id=session_id,
                              model=driver.get("model", ""))
        values["prompt"] = self._prompt(prompt_key, values)
        budget = min(self.campaign["bounds"]["cloud_max_seconds"],
                     max(self.ledger.remaining_seconds(), 0))
        extra = [Path(os.path.expanduser(p)) for p in driver.get("extra_write", [])]
        execution = execute_driver(
            driver, values, workdir=bundle, scratch=scratch,
            stream_log=self.state_dir / "streams" / f"{self.envelope['campaign_id']}-"
                                                     f"{session_id}.jsonl",
            max_seconds=budget, max_steps=None, sandbox=self.sandbox, deny_read=self.deny_read,
            extra_write=extra, host_env=self.host_env)
        tokens, reason = premium_tokens(session_id, driver.get("session_log") or {})
        self.ledger.settle(cloud=True, seconds=execution["wall_seconds"],
                           premium_tokens=billing_total(tokens))
        execution["session_id"] = session_id
        return execution, tokens, reason

    def _review(self, task: Mapping[str, Any], patch: bytes, label: str
                ) -> tuple[str | None, str, dict[str, Any], dict[str, int | None] | None, str | None]:
        """Independent review of ``patch`` on a fresh bundle: ``(verdict, findings, ...)``."""
        bundle, attempt_dir = self._bundle(task, f"{label}-review", patch)
        try:
            execution, tokens, reason = self.cloud_execution(
                "reviewer", REVIEWER_DRIVER, bundle, attempt_dir, "review")
            verdict, findings = None, ""
            with contextlib.suppress(OSError, ValueError):
                data = json.loads((attempt_dir / "scratch" / "review.json").read_text("utf-8"))
                if data.get("verdict") in ("PASS", "BLOCK"):
                    verdict, findings = data["verdict"], "\n".join(map(str, data.get("findings", [])))
        finally:
            self._discard(bundle, attempt_dir)
        return verdict, findings, execution, tokens, reason

    # ---- cloud path (A, B and the takeover of C)
    def cloud_path(self, task: Mapping[str, Any], path: str, task_set: str = "",
                   segment: str = "cloud", first_attempt: int = 0) -> list[dict[str, Any]]:
        """Implementer, mechanical judge, independent review, corrections (bounded)."""
        implementer = IMPLEMENTER_DRIVER["A" if path in ("A", "C") else "B"]
        records: list[dict[str, Any]] = []
        patch: bytes | None = None
        feedback: str | None = None
        for index in range(self.campaign["bounds"]["max_correction_rounds"] + 1):
            role = "implementer" if index == 0 else "corrector"
            bundle, attempt_dir = self._bundle(task, f"{path}-{role}{index}", patch)
            try:
                execution, tokens, reason = self.cloud_execution(
                    role, implementer, bundle, attempt_dir,
                    "implement" if index == 0 else "correct", feedback)
                patch = _capture_patch(bundle)
                verdict = lfc.judge(self.repo, task, bundle)
            finally:
                self._discard(bundle, attempt_dir)
            by_role: dict[str, Any] = {role: tokens}
            unknown = {f"premium.{role}": reason} if tokens is None else {}
            state = {"seconds": execution["wall_seconds"], "count": 1, "rounds": [],
                     "accepted": None, "outcome": "judge_refused"}
            findings = ""

            def emit() -> None:
                totals = [billing_total(t) for t in by_role.values()]
                records.append(self._emit({
                    "record_type": "attempt", "task": _task_ref(task, task_set), "path": path,
                    "segment": segment, "attempt": first_attempt + index, "outcome": state["outcome"],
                    "judge": _judge_summary(verdict), "accepted": state["accepted"],
                    "review": {"rounds": len(state["rounds"]), "verdicts": state["rounds"]},
                    "wall_seconds": round(state["seconds"], 3), "cloud_executions": state["count"],
                    "premium": {"by_role": {r: _classes(t) for r, t in by_role.items()},
                                "billing_total": None if any(t is None for t in totals)
                                else sum(totals)},
                    "local": None, "machine": None, "unknown": unknown}))

            if verdict["verdict"] == "ACCEPTED":
                try:
                    review, findings, rev_exec, rev_tokens, rev_reason = self._review(
                        task, patch, f"{path}-{index}")
                except CapReached:  # the spent implementer execution must not vanish from the results
                    state["outcome"] = "stopped_by_cap"
                    emit()
                    raise
                state["seconds"] += rev_exec["wall_seconds"]
                state["count"] = 2
                by_role["reviewer"] = rev_tokens
                if rev_tokens is None:
                    unknown["premium.reviewer"] = rev_reason or "unknown"
                state["rounds"] = [review]
                state["outcome"] = {"PASS": "accepted", "BLOCK": "review_block",
                                    None: "review_unreadable"}[review]
                state["accepted"] = review == "PASS"
            emit()
            outcome = state["outcome"]
            if outcome in ("accepted", "review_unreadable"):
                break
            feedback = (findings if outcome == "review_block" else
                        "The mechanical acceptance check refused the change: "
                        f"{verdict.get('note') or 'tests failing'} "
                        f"(passed {verdict['passed']}, failed {verdict['failed']}, "
                        f"errors {verdict['errors']}).")
        return records

    # ---- path C
    def hybrid_path(self, task: Mapping[str, Any], candidate_id: str, harness_id: str,
                    task_set: str = "") -> list[dict[str, Any]]:
        local, patch = self.local_attempt(task, candidate_id, harness_id, "C", 0, task_set)
        records = [local]
        takeover = local["local_outcome"] != "accepted"
        if not takeover:
            try:
                review, _, rev_exec, rev_tokens, rev_reason = self._review(task, patch, "C-0")
            except CapReached:
                local["outcome"] = "stopped_by_cap"
                self._emit(local)
                raise
            total = billing_total(rev_tokens)
            local["review"] = {"rounds": 1, "verdicts": [review]}
            local["cloud_executions"] = 1
            local["wall_seconds"] = round(local["wall_seconds"] + rev_exec["wall_seconds"], 3)
            local["premium"] = {"by_role": {"reviewer": _classes(rev_tokens)}, "billing_total": total}
            if rev_tokens is None:
                local["unknown"]["premium.reviewer"] = rev_reason or "unknown"
            local["accepted"] = review == "PASS"
            local["outcome"] = {"PASS": "accepted", "BLOCK": "review_block",
                                None: "review_unreadable"}[review]
            takeover = review == "BLOCK"
        else:
            local["outcome"] = "local_refused"
        self._emit(local)
        if takeover:  # a failed local attempt is neither a failure nor an escalation (ADR-0015)
            records += self.cloud_path(task, "C", task_set, segment="takeover", first_attempt=1)
        return records

    # ---- orchestration
    def preflight(self, candidate_id: str) -> dict[str, Any]:
        model = self.campaign["candidates"][candidate_id]["model"]
        result = preflight(self.campaign, model, self.preflight_run(candidate_id), self.disk_free_gib)
        self.ledger.append("preflight", candidate=candidate_id, ok=result["ok"],
                           refusals=result["refusals"])
        if not result["ok"]:
            raise PreflightRefused(result["refusals"])
        return result

    def screen(self, tasks: Sequence[Mapping[str, Any]], candidate_ids: Sequence[str],
               harness_id: str = "local_harness") -> list[dict[str, Any]]:
        """Local attempts only (candidate x task) and the judge: no cloud, ever."""
        out = []
        try:
            for candidate_id in candidate_ids:
                self.preflight(candidate_id)
                for task in tasks:
                    record, _ = self.local_attempt(task, candidate_id, harness_id, "S", 0, "screening")
                    record["accepted"] = record["local_outcome"] == "accepted"
                    out.append(self._emit(record))
        except CapReached as exc:
            self._stop(f"cap_reached:{exc}")
        return out

    def compare(self, tasks: Sequence[Mapping[str, Any]], candidate_id: str,
                paths: Sequence[str] = ("A", "B", "C"), harness_id: str = "local_harness"
                ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        local_ok = local_fail = 0
        premium = {"A": 0, "C": 0}
        premium_known = {"A": True, "C": True}
        try:
            self.preflight(candidate_id)
            for done, task in enumerate(tasks, 1):
                for path in paths:
                    if path == "A" or path == "B":
                        recs = self.cloud_path(task, path, "comparison")
                    elif path == "C":
                        recs = self.hybrid_path(task, candidate_id, harness_id, "comparison")
                        first = recs[0]
                        local_ok += first["local_outcome"] == "accepted"
                        local_fail += first["local_outcome"] != "accepted"
                    else:  # N: the same local attempt under the neutral harness, no cloud
                        rec, _ = self.local_attempt(task, candidate_id, "neutral_harness", "N", 0,
                                                    "comparison")
                        rec["accepted"] = rec["local_outcome"] == "accepted"
                        recs = [self._emit(rec)]
                    out += recs
                    if path in premium:
                        for rec in recs:
                            total = rec["premium"]["billing_total"]
                            premium_known[path] &= total is not None
                            premium[path] += total or 0
                remaining = len(tasks) - done
                rules = self.campaign["rules"]["comparison"]
                if "C" in paths and local_ok + remaining < rules["min_local_successes"]:
                    return self._stopped(out, "fewer_than_min_local_successes")
                if ("C" in paths and "A" in paths and premium_known["A"] and premium_known["C"]
                        and premium["C"] >= premium["A"] > 0):
                    return self._stopped(out, "premium_c_not_below_a")
        except CapReached as exc:
            self._stop(f"cap_reached:{exc}")
        return out

    def _stopped(self, out: list[dict[str, Any]], reason: str) -> list[dict[str, Any]]:
        self._stop(reason)
        return out

    def _stop(self, reason: str) -> None:
        self.stopped = reason
        self.ledger.append("stopped", reason=reason)
        self._emit({"record_type": "stop", "reason": reason})


# ----------------------------------------------------------------------------- helpers

_PATCH_EXCLUDES = (":(exclude)TASK.md", ":(exclude,glob)**/__pycache__/**", ":(exclude,glob)**/*.pyc",
                   ":(exclude,glob)**/node_modules/**", ":(exclude,glob)**/.venv/**",
                   ":(exclude,glob)**/venv/**")


def _git_in(bundle: Path, *args: str, stdin: bytes | None = None) -> bytes:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", **lfc.BUNDLE_AUTHOR}
    proc = subprocess.run(["git", "-C", str(bundle), *args], input=stdin, capture_output=True,
                          env=env, check=False)
    if proc.returncode != 0:
        raise RunnerError(f"git {args[0]} failed: {proc.stderr.decode('utf-8', 'replace')[:200]}")
    return proc.stdout


def _capture_patch(bundle: Path) -> bytes:
    """The candidate's change as a patch against the root commit, taken BEFORE judging (the judge
    writes the protected tests into the bundle, which is then never reused)."""
    _git_in(bundle, "add", "-A", "-f", "--", ".", *_PATCH_EXCLUDES)
    return _git_in(bundle, "diff", "--cached", "--binary", "HEAD")


def _apply_patch(bundle: Path, patch: bytes) -> None:
    if patch:
        _git_in(bundle, "apply", "--whitespace=nowarn", "-", stdin=patch)


def _classes(tokens: Mapping[str, int | None] | None) -> dict[str, int | None] | None:
    return None if tokens is None else dict(tokens)


def _task_ref(task: Mapping[str, Any], task_set: str) -> dict[str, Any]:
    return {"pr": task["pr"], "issue": task.get("issue"), "set": task_set}


def _judge_summary(verdict: Mapping[str, Any]) -> dict[str, Any]:
    return {k: verdict[k] for k in ("verdict", "passed", "failed", "errors", "skipped", "note")}


def _strip(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in snapshot.items() if k != "unknown"}


# ------------------------------------------------------------------------------- report

def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def _sum_known(values: Sequence[float | int | None]) -> float | int | None:
    return None if any(v is None for v in values) else sum(values)


def report(campaign: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate results and apply the pre-registered rules of the protocol. The three verdicts
    (compatibility, quality, economy) stay separate; nothing is promoted."""
    attempts = [r for r in records if r.get("record_type") == "attempt"]
    stops = [r["reason"] for r in records if r.get("record_type") == "stop"]
    rules = campaign["rules"]
    out: dict[str, Any] = {"promotion": False, "stops": stops,
                           "screening": _report_screening(rules["screening"], attempts)}
    comparison = [r for r in attempts if r["task"]["set"] == "comparison"]
    out["comparison"] = _report_comparison(rules["comparison"], comparison, stops)
    return out


def _report_screening(rule: Mapping[str, Any], attempts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_candidate: dict[str, list[Mapping[str, Any]]] = {}
    for rec in attempts:
        if rec["path"] == "S":
            by_candidate.setdefault(rec["local"]["candidate"], []).append(rec)
    table = {}
    for cid, recs in sorted(by_candidate.items()):
        swaps = [r["machine"]["after"]["swap_used_mib"] for r in recs]
        table[cid] = {"tasks": len(recs), "accepted": sum(1 for r in recs if r["accepted"]),
                      "total_seconds": round(sum(r["wall_seconds"] for r in recs), 3),
                      "peak_swap_used_mib": None if any(s is None for s in swaps) else max(swaps),
                      "harness": sorted({r["local"]["harness"] for r in recs})}
    selected, reason = None, "no_screening_results"
    if table:
        best = max(v["accepted"] for v in table.values())
        tied = sorted(c for c, v in table.items() if v["accepted"] == best)
        if best < rule["min_accepted"]:
            reason = "no_candidate_reaches_min_accepted: keep_cloud"
        elif len(tied) == 1:
            selected, reason = tied[0], "most_accepted"
        else:
            fastest = min(table[c]["total_seconds"] for c in tied)
            winners = [c for c in tied if table[c]["total_seconds"] == fastest]
            selected, reason = (winners[0], "tie_broken_by_total_duration") if len(winners) == 1 \
                else (None, "tie_not_resolved")
    complete = bool(table) and all(v["tasks"] == rule["tasks"] for v in table.values())
    return {"candidates": table, "selected": selected, "reason": reason, "complete": complete}


def _arm_totals(recs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    tasks: dict[int, list[Mapping[str, Any]]] = {}
    for rec in recs:
        tasks.setdefault(rec["task"]["pr"], []).append(rec)
    per_task = {}
    for pr, items in sorted(tasks.items()):
        per_task[pr] = {
            "accepted": any(r["accepted"] is True for r in items),
            "review_rounds": sum(r["review"]["rounds"] for r in items),
            "wall_seconds": sum(r["wall_seconds"] for r in items),
            "cloud_executions": sum(r["cloud_executions"] for r in items),
            "premium_billing_tokens": _sum_known([r["premium"]["billing_total"] for r in items]),
            "local_attempt_ok": next((r["local_outcome"] == "accepted" for r in items
                                      if r["segment"] == "local"), None)}
    locals_ = [r for r in recs if r["segment"] == "local"]
    return {"tasks": per_task, "locals": locals_}


def _report_comparison(rule: Mapping[str, Any], attempts: Sequence[Mapping[str, Any]],
                       stops: Sequence[str]) -> dict[str, Any]:
    arms = {p: _arm_totals([r for r in attempts if r["path"] == p]) for p in PATHS
            if any(r["path"] == p for r in attempts)}
    out: dict[str, Any] = {"arms": {}, "decision": "inconclusive", "recommendation": None}
    reference = arms.get("A")
    if reference is None:
        out["reason"] = "no_reference_path_A"
        return out
    for path in ("B", "C"):
        if path not in arms:
            continue
        arm = arms[path]
        shared = sorted(set(arm["tasks"]) & set(reference["tasks"]))
        a_tasks = [reference["tasks"][t] for t in shared]
        x_tasks = [arm["tasks"][t] for t in shared]
        quality = _verdict(all(t["accepted"] for t in x_tasks)
                           and sum(t["review_rounds"] for t in x_tasks)
                           <= sum(t["review_rounds"] for t in a_tasks), bool(shared))
        a_prem = _sum_known([t["premium_billing_tokens"] for t in a_tasks])
        x_prem = _sum_known([t["premium_billing_tokens"] for t in x_tasks])
        premium_v = (None if a_prem is None or x_prem is None or not shared
                     else x_prem <= (1 - rule["premium_reduction_min"]) * a_prem)
        a_wall, x_wall = sum(t["wall_seconds"] for t in a_tasks), sum(t["wall_seconds"] for t in x_tasks)
        time_v = x_wall <= rule["time_ratio_max"] * a_wall if shared else None
        economy = ("unavailable" if premium_v is None else
                   "pass" if premium_v and time_v else "fail")
        compat = _compatibility(rule, arm["locals"]) if path == "C" else "pass"
        out["arms"][path] = {
            "tasks_compared": len(shared),
            "compatibility": compat, "quality": quality, "economy": economy,
            "economy_detail": {"premium_billing_tokens": x_prem, "reference_premium_billing_tokens": a_prem,
                               "premium_pass": premium_v, "wall_seconds": round(x_wall, 3),
                               "reference_wall_seconds": round(a_wall, 3), "time_pass": time_v},
            "local_successes": sum(1 for t in x_tasks if t["local_attempt_ok"]) if path == "C" else None}
    verdicts = {p: (a["compatibility"], a["quality"], a["economy"]) for p, a in out["arms"].items()}
    retained = [p for p, v in verdicts.items() if all(x == "pass" for x in v)]
    if retained:
        out["decision"], out["recommendation"] = "retained", ("B" if "B" in retained else retained[0])
    elif stops or any("fail" in v for v in verdicts.values()):
        out["decision"], out["recommendation"] = "keep_cloud", "A"
    expected = rule["tasks"]
    out["complete"] = all(a["tasks_compared"] == expected for a in out["arms"].values()) and bool(out["arms"])
    if out["decision"] == "retained" and not out["complete"]:
        out["decision"] = "inconclusive"
    return out


def _verdict(passed: bool, available: bool) -> str:
    return "unavailable" if not available else "pass" if passed else "fail"


def _compatibility(rule: Mapping[str, Any], locals_: Sequence[Mapping[str, Any]]) -> str:
    """Machine criterion: no local attempt ended by an external signal and extra swap under the
    limit; an unmeasured swap makes the verdict unavailable."""
    if not locals_:
        return "unavailable"
    if any(r["local"]["ended_by_external_signal"] for r in locals_):
        return "fail"
    deltas = []
    for r in locals_:
        before, after = r["machine"]["before"]["swap_used_mib"], r["machine"]["after"]["swap_used_mib"]
        if before is None or after is None:
            return "unavailable"
        deltas.append(after - before)
    return "pass" if max(deltas) < rule["extra_swap_gib_max"] * 1024 else "fail"


# ----------------------------------------------------------------------------------- CLI

def _tasks(manifest: Mapping[str, Any], snapshot: Mapping[str, Any], group: str) -> list[dict[str, Any]]:
    by_pr = {t["pr"]: t for t in snapshot["prs"]}
    return [by_pr[t["pr"]] for t in manifest[group]]


def main(argv: Sequence[str] | None = None, *, today: dt.date | None = None) -> int:
    parser = argparse.ArgumentParser(prog="foundry.local_first_runner", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("preflight", "screen", "compare"):
        p = sub.add_parser(name)
        p.add_argument("--campaign", required=True, help="the campaign config JSON")
        p.add_argument("--dry-run", action="store_true", help="fake drivers only, canned machine facts")
        p.add_argument("--candidate", nargs="+" if name == "screen" else None, required=True)
        if name != "preflight":
            p.add_argument("--envelope", help="authorization envelope (mandatory)")
            p.add_argument("--state-dir", required=True)
            p.add_argument("--work-root", required=True, help="disposable area outside the checkout")
            p.add_argument("--repo", default=".")
            p.add_argument("--snapshot", required=True)
            p.add_argument("--manifest", required=True)
            p.add_argument("--harness", default="local_harness",
                           choices=("local_harness", "neutral_harness"))
            p.add_argument("--sandbox", action="store_true", help="also sandbox a dry run")
        if name == "compare":
            p.add_argument("--paths", default="A,B,C")
    r = sub.add_parser("report")
    r.add_argument("--campaign", required=True)
    r.add_argument("--results", required=True)
    args = parser.parse_args(argv)
    campaign = load_campaign(Path(args.campaign))
    if args.cmd == "report":
        print(json.dumps(report(campaign, _read_jsonl(Path(args.results))), indent=2))
        return 0
    if args.cmd == "preflight":
        model = campaign["candidates"][args.candidate]["model"]
        run = dry_run_facts(campaign, model) if args.dry_run else default_run
        result = preflight(campaign, model, run, (lambda: 1e6) if args.dry_run else None)
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 2
    mode = args.cmd
    try:
        envelope = load_envelope(Path(args.envelope) if args.envelope else None, mode,
                                 today or dt.date.today())
        candidates = args.candidate if mode == "screen" else [args.candidate]
        for cid in candidates:
            if cid not in campaign["candidates"]:
                raise RunnerError(f"unknown candidate {cid}")
        paths = args.paths.split(",") if mode == "compare" else []
        if not set(paths) <= set(PATHS):
            raise RunnerError(f"unknown path in {args.paths}")
        manifest = json.loads(Path(args.manifest).read_text("utf-8"))
        snapshot = json.loads(Path(args.snapshot).read_text("utf-8"))
        runner = Runner(
            repo=Path(args.repo), campaign=campaign, envelope=envelope, state_dir=Path(args.state_dir),
            work_root=Path(args.work_root), mode=mode, dry_run=args.dry_run,
            sandbox=(not args.dry_run) or args.sandbox,
            run=(lambda _argv: None) if args.dry_run else default_run,
            preflight_run=(lambda cid: dry_run_facts(campaign, campaign["candidates"][cid]["model"]))
            if args.dry_run else None,
            disk_free_gib=(lambda: 1e6) if args.dry_run else None)
        if mode == "screen":
            runner.screen(_tasks(manifest, snapshot, "screening"), candidates, args.harness)
        else:
            runner.compare(_tasks(manifest, snapshot, "comparison"), candidates[0], paths, args.harness)
        if runner.stopped and runner.stopped.startswith("cap_reached"):
            print(f"stopped: {runner.stopped}", file=sys.stderr)
            return 3
    except PreflightRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (RunnerError, lfc.CorpusError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
