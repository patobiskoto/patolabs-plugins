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
import tempfile
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
# Home entries a candidate may not read (campaign config ``isolation.deny_read_home`` overrides this
# list, which is a launcher choice and not a protocol coordinate). A local arm may read nothing of the
# user's configuration; a cloud arm keeps ``~/.claude`` (OAuth identity, AGENTS.md R6) and ``~/.config``.
_HOME_SECRETS = [".ssh", ".gnupg", ".aws", ".netrc", "Library/Keychains", ".git-credentials", ".npmrc",
                 ".pypirc", ".docker", ".kube", ".zsh_history", ".bash_history", ".python_history"]
DEFAULT_HOME_DENY = {  # a cloud arm also keeps ``~/.claude.json`` (Claude Code reads and writes it)
    "local": [".claude", ".claude.json", ".codex", ".config", *_HOME_SECRETS],
    "cloud": [".codex", ".config/foundry", *_HOME_SECRETS]}
RULE_STOPS = ("fewer_than_min_local_successes", "premium_c_not_below_a")  # pre-registered early stops
PROVENANCE_KEYS = ("campaign_sha256", "manifest_sha256", "envelope_sha256")
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
    deny = (data.get("isolation") or {}).get("deny_read_home", DEFAULT_HOME_DENY)
    if not isinstance(deny, dict) or set(deny) != {"local", "cloud"} or not all(
            isinstance(v, list) and all(isinstance(x, str) and x and not x.startswith("/")
                                        and ".." not in Path(x).parts for x in v)
            for v in deny.values()):
        raise RunnerError(f"{path}: isolation.deny_read_home needs relative lists for local and cloud")
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
    """Append-only consumption ledger. A cloud execution is recorded BEFORE it starts and its
    ``settled`` line carries the same session id: a ``cloud_started`` without a paired ``settled``
    (crash, kill, interruption) has unknown tokens and stops every later cloud execution."""

    def __init__(self, state_dir: Path, envelope: Mapping[str, Any], mode: str,
                 now: Callable[[], float] = time.time, *, dry_run: bool = False,
                 provenance: Mapping[str, Any] | None = None):
        self.mode, self.caps, self.now, self.dry_run = mode, envelope["caps"], now, dry_run
        self.path = Path(state_dir) / f"ledger-{envelope['campaign_id']}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        expected = {"envelope_sha256": envelope["sha256"], **(provenance or {})}
        self.launch = 1  # this launcher's rank in the campaign: keeps attempt names unique across launches
        if self.path.exists():  # a dry run and a real run never share a ledger (nor a cap)
            for entry in _read_jsonl(self.path):
                if bool(entry.get("dry_run", False)) != dry_run:
                    raise RunnerError(f"{self.path} holds {'real' if dry_run else 'dry-run'} entries: "
                                      "refusing to mix dry-run and real records")
                if entry.get("kind") == "session_started":
                    self.launch += 1
                    for key, value in expected.items():  # before any claim or reservation
                        if entry.get(key) != value:
                            raise RunnerError(f"{self.path} was written under another {key}: the "
                                              "campaign, manifest and envelope cannot change after the "
                                              "first launch (new campaign id and envelope needed)")
        self.append("session_started", mode=mode, **expected)

    def append(self, kind: str, **fields: Any) -> None:
        line = json.dumps({"kind": kind, "at": self.now(), "dry_run": self.dry_run, **fields},
                          sort_keys=True) + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def totals(self) -> dict[str, Any]:
        out: dict[str, Any] = {"cloud_started": 0, "premium_tokens": 0, "tokens_unmeasurable": False,
                               "seconds": 0.0, "unsettled_sessions": []}
        pending: dict[str, None] = {}
        for entry in _read_jsonl(self.path):
            if bool(entry.get("dry_run", False)) != self.dry_run:
                continue
            if entry["kind"] == "cloud_started":
                out["cloud_started"] += 1
                pending[entry.get("session_id")] = None
            elif entry["kind"] == "settled":
                out["seconds"] += entry.get("seconds") or 0
                if entry.get("cloud"):
                    pending.pop(entry.get("session_id"), None)
                    if entry.get("premium_tokens") is None:
                        out["tokens_unmeasurable"] = True
                    else:
                        out["premium_tokens"] += entry["premium_tokens"]
        out["unsettled_sessions"] = list(pending)
        if pending:  # a started execution with no settlement: its tokens are unknown, never 0
            out["tokens_unmeasurable"] = True
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

    def settle(self, *, cloud: bool, seconds: float, premium_tokens: int | None,
               session_id: str | None = None, **extra: Any) -> None:
        self.append("settled", cloud=cloud, seconds=round(seconds, 3), premium_tokens=premium_tokens,
                    session_id=session_id, **extra)


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
    for root in writable:  # a denied read path around a writable root would break the arm silently
        for denied in deny_read:
            real_w, real_d = Path(os.path.realpath(root)), Path(os.path.realpath(denied))
            if real_w == real_d or real_d in real_w.parents:
                raise RunnerError(f"writable path {real_w} is inside the read-denied path {real_d}: "
                                  "keep the launcher inputs in a directory apart from the work root")
    lines = ["(version 1)", "(allow default)", "(deny file-write*)",
             "(allow file-write* " + " ".join(f"(subpath {_sb(p)})" for p in writable)
             + ' (literal "/dev/null") (literal "/dev/dtracehelper") (literal "/dev/tty"))']
    if deny_read:
        lines.append("(deny file-read* " + " ".join(f"(subpath {_sb(p)})" for p in deny_read) + ")")
    if network == "loopback":
        lines += ["(deny network*)", '(allow network* (remote ip "localhost:*"))']
    return "\n".join(lines) + "\n"


def _worktrees(repo: Path) -> list[Path]:
    """Every checkout/worktree of the repository git can enumerate (local git, read-only)."""
    proc = subprocess.run(["git", "-C", str(repo), "worktree", "list", "--porcelain"],
                          capture_output=True, text=True, check=False)
    return [Path(line[len("worktree "):]) for line in proc.stdout.splitlines()
            if proc.returncode == 0 and line.startswith("worktree ")]


def read_deny_list(*, repo: Path, home: str | Path, state_dir: Path, input_paths: Sequence[Path],
                   kind: str, isolation: Mapping[str, Any] | None = None) -> list[Path]:
    """Paths a candidate may not read: every checkout and worktree of this repository, the state
    directory, the parent directory of each launcher input (envelope, campaign, snapshot,
    manifest), and the sensitive entries of the user's real home (configuration, plugin cache,
    SSH/GPG/AWS, keychains). Explicit deny list on top of ``(allow default)``: not a default-deny of
    the home (not verifiable without running the harness; PAT-109)."""
    names = (isolation or DEFAULT_HOME_DENY)["local" if kind in LOCAL_KINDS else "cloud"]
    paths = [*lfc._checkout_roots(repo), *_worktrees(repo), Path(state_dir),
             *(Path(p).resolve().parent for p in input_paths), *(Path(home) / n for n in names)]
    unique: dict[str, Path] = {}
    for path in paths:
        unique.setdefault(os.path.realpath(path), path)
    return list(unique.values())


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


def sandbox_text(driver: Mapping[str, Any], writable: Sequence[Path], deny_read: Sequence[Path]) -> str:
    """The profile of one driver, or a ``RunnerError`` when isolation cannot be enforced. Pure: the
    runner calls it before any claim or reservation, so a configuration error costs nothing."""
    if not sandbox_available():
        raise RunnerError("sandbox-exec is unavailable: isolation cannot be enforced")
    return sandbox_profile(writable=list(writable), deny_read=list(deny_read),
                           network=driver.get("network", "loopback"))


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


def _deliver(gate: dict[str, Any]) -> None:
    """Stop deferring and raise the signal received meanwhile, if any."""
    gate["defer"] = False
    signum, gate["pending"] = gate["pending"], None
    if signum is not None:
        raise KeyboardInterrupt if signum == signal.SIGINT else SystemExit(128 + signum)


def _install_term_handlers(gate: dict[str, Any]) -> dict[int, Any]:
    """SIGTERM/SIGHUP end the call like an interruption (``SystemExit``) so the ``finally`` that
    kills the arm's process group runs. While ``gate["defer"]`` is set (the arm is being started
    and its pid is not known yet) a signal, Ctrl-C included, is only noted in ``gate["pending"]``
    and raised by ``_deliver`` once the process can be killed. Only the main thread can install
    handlers; an ignored SIGINT stays ignored."""
    if threading.current_thread() is not threading.main_thread():
        return {}

    def handler(signum: int, _frame: Any) -> None:
        gate["pending"] = signum
        if not gate["defer"]:
            _deliver(gate)

    return {sig: signal.signal(sig, handler) for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
            if not (sig == signal.SIGINT and signal.getsignal(sig) is signal.SIG_IGN)}


def _restore_handlers(previous: Mapping[int, Any]) -> None:
    for sig, old in previous.items():  # None: the previous handler was not set from Python
        signal.signal(sig, signal.SIG_DFL if old is None else old)


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
        sandbox_text(driver, [workdir, scratch, *extra_write], deny_read)  # refuse before any file
        profile_dir = Path(os.path.realpath(tempfile.mkdtemp(prefix="foundry-sb-")))
        profile = profile_dir / "profile.sb"  # it names every denied path: the arm may not read it
        profile.write_text(sandbox_text(driver, [workdir, scratch, *extra_write],
                                        [*deny_read, profile_dir]), encoding="utf-8")
        argv = ["sandbox-exec", "-f", str(profile), *argv]
    stats = StreamStats(driver.get("stream"))
    state = {"step_limit": False, "timed_out": False}
    stream_log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    proc: subprocess.Popen | None = None
    gate: dict[str, Any] = {"defer": True, "pending": None}  # no signal can cut ``Popen`` itself
    previous = _install_term_handlers(gate)
    try:
        try:
            with stream_log.open("wb") as log, (scratch / "stderr.log").open("wb") as err:
                proc = subprocess.Popen(argv, cwd=workdir, env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=err, start_new_session=True)
                _deliver(gate)  # ``proc`` is known: an interruption now kills its group (finally)

                def pump(proc: subprocess.Popen = proc) -> None:
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
                    try:
                        proc.wait(timeout=max(max_seconds, 0.001))
                    except subprocess.TimeoutExpired:
                        state["timed_out"] = True
                finally:  # also on Ctrl-C, SIGTERM or a launcher error: nothing outlives the call
                    _kill_group(proc)
                    proc.wait()
                    reader.join(timeout=10)
        except OSError as exc:
            _deliver(gate)
            return {"exit_code": None, "signal": None, "timed_out": False, "step_limit_hit": False,
                    "wall_seconds": round(time.monotonic() - started, 3), "start_error": str(exc)[:200],
                    "stream": stats.summary(), "launcher_kill": False}
    finally:
        gate["defer"] = True  # a signal during the cleanup is noted and raised once it is complete
        try:
            if proc is not None:  # whatever was started is killed and reaped, however the call ends
                _kill_group(proc)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=10)
        finally:
            try:
                if profile_dir is not None:
                    shutil.rmtree(profile_dir, ignore_errors=True)
            finally:
                _restore_handlers(previous)
                _deliver(gate)  # never masked: raised after the handlers are back
    code = proc.returncode
    launcher_kill = state["timed_out"] or state["step_limit"]
    return {"exit_code": code if code is not None and code >= 0 else None,
            "signal": -code if code is not None and code < 0 else None,
            "timed_out": state["timed_out"], "step_limit_hit": state["step_limit"],
            "launcher_kill": launcher_kill, "start_error": None,
            "wall_seconds": round(time.monotonic() - started, 3), "stream": stats.summary()}


# --------------------------------------------------------------------- premium tokens

def premium_tokens(session_id: str, session_log: Mapping[str, Any],
                   by_model: dict[str, dict[str, int | None]] | None = None
                   ) -> tuple[dict[str, int | None] | None, str | None]:
    """Token counters of ONE cloud execution, read from the host session log named by the session
    id the launcher gave the driver (never by time window). ``(None, reason)`` when unreadable;
    a class the host does not report (reasoning) is None, never 0. The log layout is read only from
    ``<projects_dir>/*/<session>.jsonl``: a host that keeps subagent transcripts elsewhere would
    make this total look complete while missing work, so the counters are unknown unless the driver
    declares ``session_log.layout_verified: true`` (PAT-109 precondition). ``by_model`` (optional)
    receives the same counters per model."""
    if session_log.get("layout_verified") is not True:
        return None, "log_layout_unverified"
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
        model_total = None if by_model is None else by_model.setdefault(
            str(row.get("model") or "unknown"), dict.fromkeys(TOKEN_CLASSES, 0))
        for name in TOKEN_CLASSES:
            value = row["tokens"][name]
            total[name] = None if (total[name] is None or not isinstance(value, int)) \
                else total[name] + value
            if model_total is not None:
                model_total[name] = None if (model_total[name] is None or not isinstance(value, int)) \
                    else model_total[name] + value
    return total, None


def billing_total(tokens: Mapping[str, int | None] | None) -> int | None:
    if tokens is None or any(tokens.get(k) is None for k in ca.BILLING_TOKEN_KEYS):
        return None
    return sum(tokens[k] for k in ca.BILLING_TOKEN_KEYS)


# ------------------------------------------------------------------------------ runner

def config_digest(data: Any) -> str:
    """sha256 of the canonical JSON of a loaded configuration (campaign, manifest)."""
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()


def _attempt_key(path: str, pr: Any, task_set: Any, candidate: Any, segment: str, attempt: int,
                 replay: int = 0) -> tuple:
    """The key of one RECORD: the logical attempt plus 1 when the record is the replay of a void one."""
    return (path, pr, task_set, candidate, segment, attempt, replay)


def _merge_models(into: dict[str, dict[str, int | None]], more: Mapping[str, Mapping[str, Any]]) -> None:
    for model, classes in more.items():
        current = into.setdefault(model, dict.fromkeys(TOKEN_CLASSES, 0))
        for name in TOKEN_CLASSES:
            value = classes.get(name)
            current[name] = None if (current[name] is None or value is None) else current[name] + value


class Runner:
    """Plays tasks through the paths. Every argument is injected so a test (or a dry run) never
    needs a real machine, model or cloud."""

    quiescent_wait = 0.2

    def __init__(self, *, repo: Path, campaign: Mapping[str, Any], envelope: Mapping[str, Any],
                 state_dir: Path, work_root: Path, mode: str, dry_run: bool, sandbox: bool,
                 run: Callable[[Sequence[str]], str | None] = default_run,
                 preflight_run: Callable[[str], Callable[[Sequence[str]], str | None]] | None = None,
                 disk_free_gib: Callable[[], float] | None = None,
                 host_env: Mapping[str, str] | None = None, now: Callable[[], float] = time.time,
                 today: Callable[[], dt.date] = dt.date.today, input_paths: Sequence[Path] = (),
                 provenance: Mapping[str, Any] | None = None):
        if mode not in MODES:
            raise RunnerError(f"unknown mode {mode!r}")
        self.repo, self.campaign, self.envelope = Path(repo).resolve(), campaign, envelope
        self.state_dir, self.work_root = Path(state_dir).resolve(), Path(work_root).resolve()
        self.mode, self.dry_run, self.sandbox = mode, dry_run, sandbox
        self.run, self.disk_free_gib, self.host_env = run, disk_free_gib, host_env
        self.today, self.input_paths = today, [Path(p) for p in input_paths]
        self.preflight_run = preflight_run or (lambda _candidate: run)
        if lfc.inside_developer_checkout(self.repo, self.work_root):
            raise RunnerError("work root is inside the developer checkout")
        if not dry_run and not sandbox:
            raise RunnerError("a real run needs the sandbox")
        given = provenance or {}
        self.provenance = {"campaign_sha256": given.get("campaign_sha256") or config_digest(campaign),
                           "manifest_sha256": given.get("manifest_sha256"),
                           "envelope_sha256": envelope["sha256"]}
        self.results_path = self.state_dir / f"results-{envelope['campaign_id']}.jsonl"
        self.seen = self._scan_results()  # refuses a mixed or foreign state before anything starts
        self.prior_ledger: list[dict[str, Any]] = []
        if sandbox:  # a configuration error must cost nothing: checked before any claim or reservation
            for driver in campaign["drivers"].values():
                if mode == "compare" or driver["kind"] in LOCAL_KINDS:
                    sandbox_text(driver, [self.work_root, *_extra_write(driver)], self._deny_read(driver))
        self.sessions: list[str] = []  # session id of every cloud execution reserved in the ledger
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.ledger = Ledger(self.state_dir, envelope, mode, now, dry_run=dry_run,
                             provenance=self.provenance)
        self.prior_ledger = _read_jsonl(self.ledger.path)  # what the earlier launches left (resume)
        self.counter = 0
        self.launch = self.ledger.launch
        self.gate: dict[str, Any] = {"defer": False, "pending": None}  # see ``_signals``
        self.unrecorded: dict[str, Any] | None = None  # a settled local attempt not yet in the results
        self.emitted: set[tuple] = set()
        self.stopped: str | None = None
        self.handed: set[Path] = set()
        self.guards: dict[Path, tuple[str, dict[str, str | None]]] = {}

    # ---- bookkeeping
    def _scan_results(self) -> set[tuple]:
        """Attempt keys already recorded (see ``_local_state`` and ``_cloud_resume`` for what a relaunch
        resumes: a recorded key is never claimed again). Results written in dry-run mode, under
        another campaign config, manifest or envelope never share a file with this run."""
        seen: set[tuple] = set()
        self.prior_records: list[dict[str, Any]] = []
        if not self.results_path.exists():
            return seen
        self.prior_records = _read_jsonl(self.results_path)
        for rec in self.prior_records:
            if bool(rec.get("dry_run", False)) != self.dry_run:
                raise RunnerError(f"{self.results_path} holds {'real' if self.dry_run else 'dry-run'} "
                                  "records: refusing to mix dry-run and real records")
            for key in PROVENANCE_KEYS:
                if rec.get(key) != self.provenance[key]:
                    raise RunnerError(f"{self.results_path} was written under another {key}: "
                                      "the rules cannot change after the fact")
            if rec.get("record_type") == "attempt":
                seen.add(_record_key(rec))
        return seen

    def _claim(self, path: str, task: Mapping[str, Any], task_set: str, candidate: str | None,
               segment: str, attempt: int, replay: int = 0) -> None:
        key = _attempt_key(path, task["pr"], task_set, candidate, segment, attempt, replay)
        if key in self.seen:
            raise RunnerError(f"attempt already recorded (no second chance, no replay): {key}")
        self.seen.add(key)

    def _local_state(self, path: str, task: Mapping[str, Any], task_set: str, candidate: str,
                     segment: str, attempt: int) -> tuple[str, Any]:
        """What a relaunch does with a local attempt: ``decided`` (a record carries a judge verdict:
        never replayed, whatever happened afterwards), ``replay`` (cut before any verdict, tried once:
        a void record, or an ``attempt_started`` with no record and no settlement; the second element
        is the ``replay_of`` to record), ``undecided`` (cut twice, or settled with no record, so a
        verdict may have been received: never replayed) or ``fresh``."""
        base = (path, task["pr"], task_set, candidate, segment, attempt)
        recs = [r for r in self.prior_records
                if r.get("record_type") == "attempt" and _logical_key(r) == base]
        for rec in recs:
            if rec.get("judge"):
                return "decided", rec
        starts = [e for e in self.prior_ledger if e.get("kind") == "attempt_started" and
                  (e.get("path"), e.get("pr"), e.get("set"), e.get("candidate"), e.get("segment"),
                   e.get("attempt")) == base]
        if not recs and not starts:
            return "fresh", None
        if max(len(recs), len(starts)) > 1 or (recs and recs[0].get("outcome") not in LOST_OUTCOMES):
            return "undecided", None
        name = starts[0].get("attempt_dir") if starts else None
        if recs:
            outcome, reason = recs[0]["outcome"], recs[0].get("reason")
        elif any(e.get("kind") == "settled" and not e.get("cloud") and e.get("attempt_dir") == name
                 for e in self.prior_ledger):
            return "undecided", None
        else:
            outcome, reason = "hard_kill", "attempt_started without settlement"
        return "replay", {"attempt": attempt, "attempt_dir": name, "outcome": outcome, "reason": reason}

    def _cloud_resume(self, prior: Sequence[Mapping[str, Any]], first_attempt: int
                      ) -> dict[str, Any] | None:
        """Cloud path already started by an earlier launch: ``None`` (nothing to play: it ended, or
        cannot be continued: the patch and the findings of a round are not kept, so only a first
        round cut before any verdict is replayed, once), else the ``replay_of`` of that round. A void
        round keeps its cost; a cap still applies to the replay (an unmeasured spend stops it)."""
        last = first_attempt + self.campaign["bounds"]["max_correction_rounds"]
        if any(r["outcome"] in ("accepted", "review_unreadable") or
               (r["attempt"] == last and r["outcome"] in ("review_block", "judge_refused"))
               for r in prior):
            return None
        first = prior[0]
        if len(prior) != 1 or first["attempt"] != first_attempt or first.get("judge") \
                or first["outcome"] not in LOST_OUTCOMES:
            return None
        self.ledger.check(cloud=True)
        return {"attempt": first_attempt, "attempt_dir": None, "outcome": first["outcome"],
                "reason": first.get("reason"), "cloud_sessions": list(first["cloud_sessions"])}

    def _emit(self, record: dict[str, Any]) -> dict[str, Any]:
        record = {"schema": RESULT_SCHEMA, "campaign_id": self.envelope["campaign_id"],
                  "mode": self.mode, "dry_run": self.dry_run, **self.provenance, **record}
        self.results_path.parent.mkdir(parents=True, exist_ok=True)
        with self._critical():  # a signal never cuts the write: it is raised once the record is on disk
            with self.results_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())  # the record is the anti-replay guard: it must survive a crash
            if record.get("record_type") == "attempt":
                self.emitted.add(_record_key(record))
                if self.unrecorded is not None and _record_key(self.unrecorded) == _record_key(record):
                    self.unrecorded = None
        return record

    # ---- signals
    @contextlib.contextmanager
    def _signals(self):
        """SIGTERM, SIGHUP and Ctrl-C become an exception (``SystemExit(128 + n)`` /
        ``KeyboardInterrupt``) for the WHOLE call, not only while a driver runs, so the paths that
        already record an interruption (``interrupted`` record, ``stop``) cover the bundle build, the
        judge, the log reading and the record writing too. The exact previous handlers are put back."""
        previous = _install_term_handlers(self.gate)
        try:
            yield
        finally:
            self.gate["defer"] = True
            try:
                _restore_handlers(previous)
            finally:
                _deliver(self.gate)

    @contextlib.contextmanager
    def _critical(self):
        """A signal received inside is noted and raised when the block ends (never dropped, never
        raised in the middle of a ledger or results write)."""
        outer = self.gate["defer"]
        self.gate["defer"] = True
        try:
            yield
        finally:
            self.gate["defer"] = outer
            if not outer:
                _deliver(self.gate)

    def _driver(self, driver_id: str) -> Mapping[str, Any]:
        driver = self.campaign["drivers"].get(driver_id)
        if driver is None:
            raise RunnerError(f"campaign declares no driver {driver_id}")
        _check_driver_usable(driver_id, driver, self.dry_run)
        return driver

    def _deny_read(self, driver: Mapping[str, Any]) -> list[Path]:
        env = os.environ if self.host_env is None else self.host_env
        return read_deny_list(
            repo=self.repo, home=env.get("HOME") or Path.home(), state_dir=self.state_dir,
            input_paths=self.input_paths, kind=driver["kind"],
            isolation=(self.campaign.get("isolation") or {}).get("deny_read_home"))

    def _attempt_name(self, task: Mapping[str, Any], label: str) -> str:
        """Unique across the launches of one campaign (the launch rank comes from the ledger): a
        relaunch never overwrites the stream log of an attempt a previous launcher left."""
        self.counter += 1
        return f"attempt-l{self.launch:02d}-{self.counter:04d}-pr{task['pr']}-{label}"

    def _bundle(self, task: Mapping[str, Any], label: str, patch: bytes | None = None,
                name: str | None = None) -> tuple[Path, Path]:
        """A FRESH bundle for one attempt (a judged bundle is never handed back to a candidate)
        and its attempt directory (scratch). The statement carries the launcher footer, identical
        for every arm. The patch (a previous attempt) is applied to the index so that
        ``git diff HEAD`` shows every change, new files included."""
        attempt_dir = self.work_root / (name or self._attempt_name(task, label))
        bundle = lfc.build_bundle(self.repo, task, attempt_dir / "bundle")
        try:
            statement = bundle / "TASK.md"
            statement.write_text(statement.read_text(encoding="utf-8")
                                 + self.campaign["statement_footer"], encoding="utf-8")
            _git_in(bundle, "add", "TASK.md")  # the footer is part of the base: a diff shows only the arm
            _git_in(bundle, "commit", "-q", "--amend", "--no-edit", "--no-verify")
            root = _git_in(bundle, "rev-parse", "HEAD").decode().strip()
            self.guards[bundle] = (root, _git_surface(bundle))
            if patch:
                _apply_patch(bundle, patch)
            if lfc.is_judged(bundle) or bundle in self.handed:
                raise RunnerError("refusing to hand a judged or reused bundle to a candidate")
        except BaseException:  # nothing is left on disk when the bundle cannot be prepared
            self.guards.pop(bundle, None)
            lfc.remove_bundle(bundle)
            shutil.rmtree(attempt_dir, ignore_errors=True)
            raise
        self.handed.add(bundle)
        return bundle, attempt_dir

    def _discard(self, bundle: Path, attempt_dir: Path) -> None:
        self.guards.pop(bundle, None)
        lfc.remove_bundle(bundle)
        shutil.rmtree(attempt_dir, ignore_errors=True)

    def _patch_of(self, bundle: Path) -> bytes:
        """The candidate's change against the ROOT commit recorded at construction (a candidate
        that commits still yields its patch), after checking that the arm left nothing running and
        did not touch the git configuration or attributes that git would honour."""
        root, surface = self.guards[bundle]
        _wait_quiescent(bundle, self.quiescent_wait)
        if _git_surface(bundle) != surface:
            raise RunnerError("bundle git configuration or attributes changed: refusing to run git on it")
        return _capture_patch(bundle, root)

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

    def _tool_error(self, task: Mapping[str, Any], path: str, segment: str, attempt: int,
                    task_set: str, exc: BaseException, *, wall: float = 0.0, sessions: Sequence[str] = (),
                    by_role: Mapping[str, Any] | None = None, by_model: Mapping[str, Any] | None = None,
                    local: Mapping[str, Any] | None = None,
                    replay_of: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        """A tool failure or an interruption (Ctrl-C, SIGTERM, any other exception) is recorded as
        such, never as a verdict, before the campaign stops. An interrupted attempt has an unknown
        cost (``billing_total`` null), like any record whose cloud executions were not all read."""
        status = _cut(exc)
        reason = (str(exc) or type(exc).__name__)[:200] if status == "tool_error" \
            else f"{type(exc).__name__}: {exc}"[:200]
        by_role = dict(by_role or {})
        record = {
            "record_type": "attempt", "task": _task_ref(task, task_set), "path": path,
            "segment": segment, "attempt": attempt, "outcome": status, "status": status,
            "reason": reason, "judge": None, "accepted": None,
            "local_outcome": status if segment == "local" else None,
            "review": {"rounds": 0, "verdicts": []}, "wall_seconds": round(wall, 3),
            "cloud_executions": len(sessions), "cloud_sessions": list(sessions),
            "premium": {"by_role": {r: _classes(t) for r, t in by_role.items()},
                        "by_model": dict(by_model or {}),
                        "billing_total": None if status == "interrupted"
                        else _record_total(by_role, sessions)},
            "local": ({"ended_by_external_signal": False, **local} if local else None),
            "machine": None, "unknown": {status: reason},
            **({"replay_of": dict(replay_of)} if replay_of else {})}
        if _record_key(record) in self.emitted:  # the attempt reached the results just before the cut
            return None
        return self._emit(record)

    # ---- one local attempt (screen, path N, first step of path C)
    def local_attempt(self, task: Mapping[str, Any], candidate_id: str, driver_id: str,
                      path: str, attempt: int = 0, task_set: str = "",
                      replay_of: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], bytes]:
        driver = self._driver(driver_id)
        if driver["kind"] not in LOCAL_KINDS:
            raise RunnerError(f"{driver_id} is not a local harness")
        self.ledger.check(cloud=False)
        self._claim(path, task, task_set, candidate_id, "local", attempt, 1 if replay_of else 0)
        bounds = self.campaign["bounds"]
        model = self.campaign["candidates"][candidate_id]["model"]
        execution, started = None, None
        name = self._attempt_name(task, f"{path}-local-{candidate_id}")
        self.ledger.append("attempt_started", attempt_dir=name, path=path, pr=task["pr"],
                           set=task_set, candidate=candidate_id, segment="local", attempt=attempt)
        try:
            bundle, attempt_dir = self._bundle(task, "", name=name)
            try:
                values = self._values(bundle, attempt_dir, model=model)
                values["prompt"] = self._prompt("implement", values)
                before = machine_snapshot(self.run, self.campaign.get("server_process_pattern"))
                budget = min(bounds["local_max_seconds"], max(self.ledger.remaining_seconds(), 0))
                started = time.monotonic()
                execution = execute_driver(
                    driver, values, workdir=bundle, scratch=attempt_dir / "scratch",
                    stream_log=self.state_dir / "streams" / f"{self.envelope['campaign_id']}-"
                                                             f"{attempt_dir.name}.jsonl",
                    max_seconds=budget, max_steps=bounds["local_max_steps"], sandbox=self.sandbox,
                    deny_read=self._deny_read(driver), host_env=self.host_env)
                after = machine_snapshot(self.run, self.campaign.get("server_process_pattern"))
                patch = self._patch_of(bundle)
                verdict = lfc.judge(self.repo, task, bundle)
            finally:
                self._discard(bundle, attempt_dir)
        except BaseException as exc:  # tool failure or interruption: time is counted, the attempt
            with self._critical():    # is recorded and can never be replayed (no second chance)
                wall = 0.0
                if started is not None:
                    wall = execution["wall_seconds"] if execution else time.monotonic() - started
                self.ledger.settle(cloud=False, seconds=wall, premium_tokens=None, attempt_dir=name,
                                   **({"interrupted": True} if _cut(exc) == "interrupted" else {}))
                self._tool_error(task, path, "local", attempt, task_set, exc, wall=wall,
                                 local={"harness": driver_id, "harness_kind": driver["kind"],
                                        "candidate": candidate_id}, replay_of=replay_of)
            raise
        with self._critical():  # settled and registered as unrecorded together (see ``_stop``)
            self.ledger.settle(cloud=False, seconds=execution["wall_seconds"], premium_tokens=None,
                               attempt_dir=name)
            stream = execution["stream"]
            unknown = {f"local.{k}": v for k, v in stream["unknown"].items()}
            unknown.update({f"machine.{k}": v
                            for k, v in {**before["unknown"], **after["unknown"]}.items()})
            local = {"harness": driver_id, "harness_kind": driver["kind"], "candidate": candidate_id,
                     "max_seconds": round(budget, 3), "nominal_max_seconds": bounds["local_max_seconds"],
                     "max_steps": bounds["local_max_steps"],
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
                      "cloud_sessions": [],
                      "premium": {"by_role": {}, "by_model": {}, "billing_total": 0}, "local": local,
                      "machine": {"before": _strip(before), "after": _strip(after)}, "unknown": unknown,
                      **({"replay_of": dict(replay_of)} if replay_of else {})}
            self.unrecorded = record  # until ``_emit`` writes it, an interruption writes it as interrupted
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
        if self.today() > dt.date.fromisoformat(str(self.envelope["expires_on"])):
            raise EnvelopeError(f"envelope expired on {self.envelope['expires_on']}")  # checked EACH time
        session_id = str(uuid.uuid4())
        scratch = attempt_dir / "scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        if feedback is not None:
            (scratch / "feedback.md").write_text(feedback, encoding="utf-8")
        values = self._values(bundle, attempt_dir, session_id=session_id,
                              model=driver.get("model", ""))
        values["prompt"] = self._prompt(prompt_key, values)
        extra = _extra_write(driver)
        deny = self._deny_read(driver)
        if self.sandbox:  # a profile that cannot be generated must not burn a reservation
            sandbox_text(driver, [bundle, scratch, *extra], deny)
        budget = min(self.campaign["bounds"]["cloud_max_seconds"],
                     max(self.ledger.remaining_seconds(), 0))
        reserved = False
        started = time.monotonic()
        try:
            with self._critical():  # a signal here is raised at the end of the block: inside the try
                self.ledger.reserve_cloud(role, session_id)  # cap checked, then recorded, then run
                reserved = True
                self.sessions.append(session_id)  # every reserved execution is named by a record
            execution = execute_driver(
                driver, values, workdir=bundle, scratch=scratch,
                stream_log=self.state_dir / "streams" / f"{self.envelope['campaign_id']}-"
                                                         f"{session_id}.jsonl",
                max_seconds=budget, max_steps=None, sandbox=self.sandbox, deny_read=deny,
                extra_write=extra, host_env=self.host_env)
        except BaseException:  # interrupted: the arm is killed, its tokens are unknown (never 0)
            if reserved:
                with self._critical():
                    self.ledger.settle(cloud=True, seconds=time.monotonic() - started,
                                       premium_tokens=None, session_id=session_id, interrupted=True)
            raise
        by_model: dict[str, dict[str, int | None]] = {}
        tokens, reason = premium_tokens(session_id, driver.get("session_log") or {}, by_model)
        self.ledger.settle(cloud=True, seconds=execution["wall_seconds"],
                           premium_tokens=billing_total(tokens), session_id=session_id)
        execution["session_id"] = session_id
        execution["by_model"] = by_model if tokens is not None else {}
        return execution, tokens, reason

    def _review(self, task: Mapping[str, Any], patch: bytes, label: str
                ) -> tuple[str | None, str, dict[str, Any], dict[str, int | None] | None, str | None]:
        """Independent review of ``patch`` on a fresh bundle: ``(verdict, findings, ...)``. An
        unreadable review is ``None`` (unknown), never a failed one."""
        bundle, attempt_dir = self._bundle(task, f"{label}-review", patch)
        try:
            execution, tokens, reason = self.cloud_execution(
                "reviewer", REVIEWER_DRIVER, bundle, attempt_dir, "review")
            verdict, findings = None, ""
            with contextlib.suppress(OSError, ValueError, AttributeError):
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
        prior = [r for r in self.prior_records if r.get("record_type") == "attempt"
                 and (r["path"], r["task"]["pr"], r["task"].get("set"), r.get("segment")) ==
                 (path, task["pr"], task_set, segment)]
        replay = self._cloud_resume(prior, first_attempt) if prior else None
        if prior and replay is None:
            return records
        for index in range(self.campaign["bounds"]["max_correction_rounds"] + 1):
            role = "implementer" if index == 0 else "corrector"
            again = replay if index == 0 else None
            self._claim(path, task, task_set, None, segment, first_attempt + index, 1 if again else 0)
            by_role: dict[str, Any] = {}
            models: dict[str, dict[str, int | None]] = {}
            unknown: dict[str, str] = {}
            verdict: Mapping[str, Any] | None = None
            state = {"seconds": 0.0, "rounds": [], "accepted": None, "outcome": "judge_refused"}
            findings = ""
            mark = len(self.sessions)

            def emit() -> None:
                spent = self.sessions[mark:]
                records.append(self._emit({
                    "record_type": "attempt", "task": _task_ref(task, task_set), "path": path,
                    "segment": segment, "attempt": first_attempt + index, "outcome": state["outcome"],
                    "judge": _judge_summary(verdict) if verdict else None,
                    "accepted": state["accepted"],
                    "review": {"rounds": len(state["rounds"]), "verdicts": state["rounds"]},
                    "wall_seconds": round(state["seconds"], 3), "cloud_executions": len(spent),
                    "cloud_sessions": spent,
                    "premium": {"by_role": {r: _classes(t) for r, t in by_role.items()},
                                "by_model": models, "billing_total": _record_total(by_role, spent)},
                    "local": None, "machine": None, "unknown": unknown,
                    **({"replay_of": again} if again else {})}))

            try:
                bundle, attempt_dir = self._bundle(task, f"{path}-{role}{index}", patch)
                try:
                    execution, tokens, reason = self.cloud_execution(
                        role, implementer, bundle, attempt_dir,
                        "implement" if index == 0 else "correct", feedback)
                    state["seconds"] = execution["wall_seconds"]
                    by_role[role] = tokens
                    _merge_models(models, execution["by_model"])
                    if tokens is None:
                        unknown[f"premium.{role}"] = reason or "unknown"
                    patch = self._patch_of(bundle)
                    verdict = lfc.judge(self.repo, task, bundle)
                finally:
                    self._discard(bundle, attempt_dir)
                if verdict["verdict"] == "ACCEPTED":
                    review, findings, rev_exec, rev_tokens, rev_reason = self._review(
                        task, patch, f"{path}-{index}")
                    state["seconds"] += rev_exec["wall_seconds"]
                    by_role["reviewer"] = rev_tokens
                    _merge_models(models, rev_exec["by_model"])
                    if rev_tokens is None:
                        unknown["premium.reviewer"] = rev_reason or "unknown"
                    state["rounds"] = [review]
                    state["outcome"] = {"PASS": "accepted", "BLOCK": "review_block",
                                        None: "review_unreadable"}[review]
                    state["accepted"] = {"PASS": True, "BLOCK": False, None: None}[review]
                    if review is None:
                        unknown["review"] = "review_unreadable"
                emit()  # inside the try: a cut before or after the write never loses the record
            except CapReached:  # the cut round is recorded even when nothing was spent in it: the
                state["outcome"] = "stopped_by_cap"  # task is not decided (never a refusal)
                emit()
                raise
            except BaseException as exc:  # tool failure, Ctrl-C, SIGTERM, anything: never silent
                self._tool_error(
                    task, path, segment, first_attempt + index, task_set, exc, wall=state["seconds"],
                    sessions=self.sessions[mark:], by_role=by_role, by_model=models, replay_of=again)
                raise
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
        state, info = self._local_state("C", task, task_set, candidate_id, "local", 0)
        if state in ("decided", "undecided"):  # the local attempt is never replayed; only a takeover left
            if state == "decided" and info.get("outcome") in ("local_refused", "review_block"):
                return self.cloud_path(task, "C", task_set, segment="takeover", first_attempt=1)
            return []
        local, patch = self.local_attempt(task, candidate_id, harness_id, "C", 0, task_set, info)
        records = [local]
        takeover = local["local_outcome"] != "accepted"
        if not takeover:
            mark = len(self.sessions)
            try:
                review, _, rev_exec, rev_tokens, rev_reason = self._review(task, patch, "C-0")
                total = billing_total(rev_tokens)
                local["review"] = {"rounds": 1, "verdicts": [review]}
                local["cloud_executions"], local["cloud_sessions"] = 1, self.sessions[mark:]
                local["wall_seconds"] = round(local["wall_seconds"] + rev_exec["wall_seconds"], 3)
                local["premium"] = {"by_role": {"reviewer": _classes(rev_tokens)},
                                    "by_model": rev_exec["by_model"], "billing_total": total}
                if rev_tokens is None:
                    local["unknown"]["premium.reviewer"] = rev_reason or "unknown"
                local["accepted"] = {"PASS": True, "BLOCK": False, None: None}[review]
                local["outcome"] = {"PASS": "accepted", "BLOCK": "review_block",
                                    None: "review_unreadable"}[review]
                if review is None:
                    local["unknown"]["review"] = "review_unreadable"
                takeover = review == "BLOCK"
            except BaseException as exc:  # cap, tool failure or interruption during the review
                cut, spent = _cut(exc), self.sessions[mark:]
                local.update(outcome=cut, cloud_sessions=spent, cloud_executions=len(spent))
                if cut != "stopped_by_cap":
                    local["status"], local["reason"] = cut, (str(exc) or type(exc).__name__)[:200]
                    local["unknown"][cut] = local["reason"]
                if spent or cut == "interrupted":  # a started review of unread cost: never 0
                    local["premium"]["billing_total"] = None
                self._emit(local)
                raise
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
        """Local attempts only (candidate x task) and the judge: no cloud, ever. The machine
        preflight is re-run before every task."""
        out = []
        with self._signals():  # the WHOLE call: an interruption anywhere leaves a record and a stop
            try:
                for candidate_id in candidate_ids:
                    for task in tasks:
                        state, info = self._local_state("S", task, "screening", candidate_id, "local", 0)
                        if state in ("decided", "undecided"):
                            continue  # resume: a decided task is never replayed, an undecided one stays so
                        self.preflight(candidate_id)
                        record, _ = self.local_attempt(task, candidate_id, harness_id, "S", 0, "screening",
                                                       info)
                        record["accepted"] = record["local_outcome"] == "accepted"
                        out.append(self._emit(record))
            except PreflightRefused:
                raise
            except CapReached as exc:
                self._stop(f"cap_reached:{exc}", exc)
            except (RunnerError, lfc.CorpusError) as exc:
                self._stop(f"tool_error:{str(exc)[:160]}", exc)
                raise
            except BaseException as exc:  # Ctrl-C, SIGTERM/SIGHUP (SystemExit) or an unexpected error
                self._stop(f"interrupted:{type(exc).__name__}", exc)
                raise
        return out

    def compare(self, tasks: Sequence[Mapping[str, Any]], candidate_id: str,
                paths: Sequence[str] = ("A", "B", "C"), harness_id: str = "local_harness"
                ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        local_ok = local_fail = 0
        premium = {"A": 0, "C": 0}
        premium_known = {"A": True, "C": True}
        for rec in self.prior_records:  # a resume keeps the counts the early-stop rules rely on
            if rec.get("record_type") != "attempt" or rec["task"].get("set") != "comparison" \
                    or rec["task"]["pr"] not in {t["pr"] for t in tasks}:
                continue
            if rec["path"] == "C" and rec["segment"] == "local" and rec.get("outcome") not in LOST_OUTCOMES:
                local_ok += rec["local_outcome"] == "accepted"
                local_fail += rec["local_outcome"] != "accepted"
            if rec["path"] in premium:
                total = rec["premium"]["billing_total"]
                premium_known[rec["path"]] &= total is not None
                premium[rec["path"]] += total or 0
        needs_local = bool({"C", "N"} & set(paths))  # A and B alone never need the local model
        if needs_local:  # refused before any claim or reservation
            self._check_selected(candidate_id)
        with self._signals():  # the WHOLE call: an interruption anywhere leaves a record and a stop
            try:
                for done, task in enumerate(tasks, 1):
                    if needs_local:
                        self.preflight(candidate_id)
                    for path in paths:
                        if path == "A" or path == "B":
                            recs = self.cloud_path(task, path, "comparison")
                        elif path == "C":
                            recs = self.hybrid_path(task, candidate_id, harness_id, "comparison")
                            first = recs[0] if recs else None
                            if first and first["segment"] == "local":
                                local_ok += first["local_outcome"] == "accepted"
                                local_fail += first["local_outcome"] != "accepted"
                        else:  # N: the same local attempt under the neutral harness, no cloud
                            state, info = self._local_state("N", task, "comparison", candidate_id,
                                                            "local", 0)
                            if state in ("decided", "undecided"):
                                continue
                            rec, _ = self.local_attempt(task, candidate_id, "neutral_harness", "N", 0,
                                                        "comparison", info)
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
            except PreflightRefused:
                raise
            except CapReached as exc:
                self._stop(f"cap_reached:{exc}", exc)
            except (RunnerError, lfc.CorpusError) as exc:
                self._stop(f"tool_error:{str(exc)[:160]}", exc)
                raise
            except BaseException as exc:  # Ctrl-C, SIGTERM/SIGHUP (SystemExit) or an unexpected error
                self._stop(f"interrupted:{type(exc).__name__}", exc)
                raise
        return out

    def _check_selected(self, candidate_id: str) -> None:
        """When the screening results sit in this state directory, the compared candidate must be the
        one the pre-registered rule selected (no screening results here: ``report`` says so)."""
        attempts = [r for r in self.prior_records if r.get("record_type") == "attempt"]
        if not any(r["path"] == "S" for r in attempts):
            return
        screening = _report_screening(self.campaign["rules"]["screening"], attempts)
        if screening["selected"] != candidate_id:
            raise RunnerError(f"candidate {candidate_id} is not the one the screening selected "
                              f"({screening['selected']}: {screening['reason']})")

    def _stopped(self, out: list[dict[str, Any]], reason: str) -> list[dict[str, Any]]:
        self._stop(reason)
        return out

    def _stop(self, reason: str, exc: BaseException | None = None) -> None:
        """Record the stop. A settled local attempt that no record names yet is written first, as
        ``interrupted`` (its result is not trusted once the call was cut). A second signal received
        meanwhile is raised after both writes, never swallowed."""
        with self._critical():
            if exc is not None:
                self._flush_unrecorded(exc)
            self.stopped = reason
            self.ledger.append("stopped", reason=reason)
            self._emit({"record_type": "stop", "reason": reason})

    def _flush_unrecorded(self, exc: BaseException) -> None:
        record, self.unrecorded = self.unrecorded, None
        if record is None:
            return
        status = _cut(exc)
        reason = (str(exc) or type(exc).__name__)[:200] if status == "tool_error" \
            else f"{type(exc).__name__}: {exc}"[:200]
        record.update(outcome=status, status=status, reason=reason, accepted=None)
        record["unknown"][status] = reason
        if status == "interrupted":
            record["premium"] = {**record["premium"], "billing_total": None}
        self._emit(record)


# ----------------------------------------------------------------------------- helpers

_PATCH_EXCLUDES = (":(exclude)TASK.md", ":(exclude,glob)**/__pycache__/**", ":(exclude,glob)**/*.pyc",
                   ":(exclude,glob)**/node_modules/**", ":(exclude,glob)**/.venv/**",
                   ":(exclude,glob)**/venv/**")
# git never reads a user/system configuration, hooks, a filesystem monitor or file protocol helper on
# a bundle the candidate controlled.
_GIT_NEUTRAL = ("-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}",
                "-c", "protocol.file.allow=never", "-c", f"core.attributesFile={os.devnull}",
                "-c", "commit.gpgsign=false")


def _cut(exc: BaseException) -> str:
    """How an attempt was cut short: a cap, a tool failure, or an interruption (everything else)."""
    if isinstance(exc, CapReached):
        return "stopped_by_cap"
    return "tool_error" if isinstance(exc, (RunnerError, lfc.CorpusError)) else "interrupted"


def _extra_write(driver: Mapping[str, Any]) -> list[Path]:
    return [Path(os.path.expanduser(p)) for p in driver.get("extra_write", [])]


def _record_total(by_role: Mapping[str, Any], sessions: Sequence[str]) -> int | None:
    """Premium work of one record: known only when the counters of EVERY cloud execution it started
    were read (an execution reserved in the ledger and not read back is unknown, never 0)."""
    totals = [billing_total(t) for t in by_role.values()]
    return None if len(totals) != len(sessions) or any(t is None for t in totals) else sum(totals)


def _record_key(rec: Mapping[str, Any]) -> tuple:
    return (*_logical_key(rec), 1 if rec.get("replay_of") else 0)


def _logical_key(rec: Mapping[str, Any]) -> tuple:
    """The attempt a record is about: a void record and its replay share it."""
    return _attempt_key(rec["path"], rec["task"]["pr"], rec["task"].get("set"),
                        (rec.get("local") or {}).get("candidate"), rec.get("segment", ""),
                        rec.get("attempt", 0))[:6]


def _git_in(bundle: Path, *args: str, stdin: bytes | None = None) -> bytes:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", **lfc.BUNDLE_AUTHOR}
    proc = subprocess.run(["git", "-C", str(bundle), *_GIT_NEUTRAL, *args], input=stdin,
                          capture_output=True, env=env, check=False)
    if proc.returncode != 0:
        raise RunnerError(f"git {args[0]} failed: {proc.stderr.decode('utf-8', 'replace')[:200]}")
    return proc.stdout


def _git_surface(bundle: Path) -> dict[str, str | None]:
    """What git would honour inside a bundle: ``.git`` itself, its config and attribute files and
    every ``.gitattributes``. Compared before and after the candidate ran."""
    git = bundle / ".git"
    if git.is_symlink() or not git.is_dir():
        raise RunnerError("bundle .git is not a plain directory")

    def digest(path: Path) -> str | None:
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() \
            and not path.is_symlink() else ("symlink" if path.is_symlink() else None)

    surface = {".git/config": digest(git / "config"),
               ".git/info/attributes": digest(git / "info" / "attributes")}
    for root, dirs, files in os.walk(bundle):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in files:
            if name == ".gitattributes":
                full = Path(root) / name
                surface[full.relative_to(bundle).as_posix()] = digest(full)
    return surface


def _tree_signature(bundle: Path) -> frozenset:
    entries = set()
    for root, dirs, files in os.walk(bundle):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in files:
            full = Path(root) / name
            with contextlib.suppress(OSError):
                info = full.lstat()
                entries.add((full.relative_to(bundle).as_posix(), info.st_size, info.st_mtime_ns))
    return frozenset(entries)


def _wait_quiescent(bundle: Path, wait: float) -> None:
    """Refuse to judge a bundle that is still changing (a child that escaped the group kill, e.g.
    through ``setsid``, keeps writing). Best effort: a quiet escaped child is not detected."""
    before = _tree_signature(bundle)
    time.sleep(wait)
    if _tree_signature(bundle) != before:
        raise RunnerError("bundle is not quiescent: a process of the arm may have outlived it")


def _capture_patch(bundle: Path, root: str) -> bytes:
    """The candidate's change as a patch against the ROOT commit ``root`` (recorded when the bundle
    was built, so an arm that commits its work still yields it), taken BEFORE judging (the judge
    writes the protected tests into the bundle, which is then never reused). New files included."""
    _git_in(bundle, "add", "-A", "-f", "--", ".", *_PATCH_EXCLUDES)
    return _git_in(bundle, "diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv", root,
                   "--", ".", *_PATCH_EXCLUDES)


def _apply_patch(bundle: Path, patch: bytes) -> None:
    """Apply onto the index and the tree: the reviewer's ``git diff HEAD`` shows every change."""
    if patch:
        _git_in(bundle, "apply", "--index", "--whitespace=nowarn", "-", stdin=patch)


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
    """Every record of a ledger or results file; a truncated or foreign line is a clean refusal."""
    out = []
    try:
        lines = Path(path).read_text("utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise RunnerError(f"{path}: unreadable ({exc})") from None
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            entry = None
        if not isinstance(entry, dict):
            raise RunnerError(f"{path}: line {number} is not a JSON record (truncated or damaged "
                              "file): this state cannot be trusted, start a new campaign")
        out.append(entry)
    return out


def _sum_known(values: Sequence[float | int | None]) -> float | int | None:
    return None if any(v is None for v in values) else sum(values)


# Outcomes where the verdict of a task is not known (tool failure, unreadable review, cap stop,
# interruption), and those where an attempt itself was lost.
UNKNOWN_OUTCOMES = ("review_unreadable", "tool_error", "stopped_by_cap", "interrupted")
LOST_OUTCOMES = ("tool_error", "interrupted")


def _check_records(records: Sequence[Mapping[str, Any]], campaign_sha256: str | None) -> dict[str, Any]:
    """Refuse results that mix dry-run and real records, were produced under different rules, or
    hold an attempt twice (a replayed attempt must never count)."""
    if len({bool(r.get("dry_run", False)) for r in records}) > 1:
        raise RunnerError("results mix dry-run and real records")
    shas: dict[str, Any] = {}
    for key in PROVENANCE_KEYS:
        values = {r.get(key) for r in records}
        if len(values) > 1:
            raise RunnerError(f"results were produced under several {key}")
        shas[key] = next(iter(values), None)
    if campaign_sha256 is not None and records and shas["campaign_sha256"] != campaign_sha256:
        raise RunnerError("the campaign config differs from the one these results were produced "
                          "under (campaign_sha256 mismatch): the rules cannot change after the fact")
    keys = [_record_key(r) for r in records if r.get("record_type") == "attempt"]
    duplicates = sorted({str(k) for k in keys if keys.count(k) > 1})
    decided = [_logical_key(r) for r in records if r.get("record_type") == "attempt" and r.get("judge")]
    duplicates += sorted({f"{k} (decided twice)" for k in decided if decided.count(k) > 1})
    if duplicates:
        raise RunnerError(f"results hold duplicated attempts: {', '.join(duplicates)}")
    return shas


def _check_ledger(records: Sequence[Mapping[str, Any]], ledger: Sequence[Mapping[str, Any]],
                  shas: Mapping[str, Any]) -> list[str]:
    """Cross-check the results against the consumption ledger of the same campaign. The invariant:
    every ``cloud_started`` session id is named by exactly one result record (``cloud_sessions``) and
    settled with known tokens, and every cloud execution of the results has its ledger line.

    Raises when the two files cannot describe the same campaign (no ledger, other digests, a cloud
    execution the ledger does not know, one named twice, a premium total the ledger does not back).
    Returns the reasons why some SPENT work is unknown (never an empty list for a campaign whose
    ledger knows more than its results): the caller then gives no economy verdict."""
    if not ledger:
        raise RunnerError("no ledger entry: a report needs the ledger written with these results")
    attempts = [r for r in records if r.get("record_type") == "attempt"]
    if records and {bool(e.get("dry_run", False)) for e in ledger} != {
            bool(records[0].get("dry_run", False))}:
        raise RunnerError("the ledger and the results mix dry-run and real records")
    for entry in ledger:
        if records and entry.get("kind") == "session_started":
            for key in PROVENANCE_KEYS:
                if entry.get(key) != shas[key]:
                    raise RunnerError(f"the ledger was written under another {key} than the results")
    started: dict[Any, None] = {}
    settled: dict[Any, Mapping[str, Any]] = {}
    for entry in ledger:
        if entry.get("kind") == "cloud_started":
            if entry.get("session_id") in started:
                raise RunnerError(f"the ledger holds the cloud execution {entry.get('session_id')} twice")
            started[entry.get("session_id")] = None
        elif entry.get("kind") == "settled" and entry.get("cloud"):
            settled[entry.get("session_id")] = entry
    named: set[Any] = set()
    for rec in attempts:
        sessions = rec.get("cloud_sessions")
        if not isinstance(sessions, list) or len(sessions) != rec.get("cloud_executions"):
            raise RunnerError(f"record {_record_key(rec)} does not name its cloud executions")
        for sid in sessions:
            if sid in named:
                raise RunnerError(f"the cloud execution {sid} is named by two result records")
            if sid not in started:
                raise RunnerError(f"the results hold the cloud execution {sid}, unknown to the ledger")
            named.add(sid)
        total = rec["premium"]["billing_total"]
        backed = _sum_known([settled.get(sid, {}).get("premium_tokens") for sid in sessions])
        if total is not None and total != backed:
            raise RunnerError(f"record {_record_key(rec)} counts {total} premium tokens, "
                              f"the ledger {backed}")
    unknown = []
    for sid in started:
        if sid not in settled:
            unknown.append(f"cloud_started_without_settled:{sid}")
        elif settled[sid].get("interrupted"):
            unknown.append(f"interrupted:{sid}")
        elif settled[sid].get("premium_tokens") is None:
            unknown.append(f"tokens_unknown:{sid}")
        if sid not in named:
            unknown.append(f"no_result_record:{sid}")
    return unknown


def report(campaign: Mapping[str, Any], records: Sequence[Mapping[str, Any]],
           ledger: Sequence[Mapping[str, Any]], *, campaign_sha256: str | None = None) -> dict[str, Any]:
    """Aggregate results and apply the pre-registered rules of the protocol. The three verdicts
    (compatibility, quality, economy) stay separate; nothing is promoted. The rules applied are
    printed with the digests the results were produced under. ``ledger`` (the entries of the
    campaign's ledger) is mandatory: whenever it knows of spent work the results cannot measure,
    no economy verdict is given and the decision is ``inconclusive``."""
    shas = _check_records(records, campaign_sha256)
    unknown_work = _check_ledger(records, ledger, shas)
    attempts = [r for r in records if r.get("record_type") == "attempt"]
    stops = [r["reason"] for r in records if r.get("record_type") == "stop"]
    rules = campaign["rules"]
    holes = _unsettled_starts(ledger)
    replayed = {(r["replay_of"] or {}).get("attempt_dir") for r in attempts if r.get("replay_of")}
    for hole in holes:  # a killed attempt a later launch replayed is no longer an open hole
        hole["replayed"] = hole.get("attempt_dir") in replayed
    open_holes = [h for h in holes if not h["replayed"]]
    counted = {_logical_key(r) for r in attempts if r.get("outcome") not in LOST_OUTCOMES}
    out: dict[str, Any] = {"promotion": False, "stops": stops, "rules_applied": rules,
                           "provenance": shas,
                           "ledger": {"unknown_spent_work": unknown_work, "unsettled_starts": holes},
                           "dry_run": any(r.get("dry_run", False) for r in records),
                           "void_attempts": _void_attempts(attempts, holes),
                           "replays": [{"path": r["path"], "task": r["task"],
                                        "candidate": (r.get("local") or {}).get("candidate"),
                                        "segment": r.get("segment"), "attempt": r.get("attempt"),
                                        "outcome": r.get("outcome"), "replay_of": r["replay_of"]}
                                       for r in attempts if r.get("replay_of")],
                           "screening": _report_screening(rules["screening"], attempts, len({
                               (h["path"], h["pr"], h["candidate"], h["segment"], h["attempt"])
                               for h in open_holes if h["kind"] == "attempt_started" and h["path"] == "S"
                               and (h["path"], h["pr"], h["set"], h["candidate"], h["segment"],
                                    h["attempt"]) not in counted}))}
    out["screening"]["warnings"] = [h["warning"] for h in open_holes if h["mode"] == "screen"]
    comparison = [r for r in attempts if r["task"]["set"] == "comparison"]
    out["comparison"] = _report_comparison(rules["comparison"], comparison, stops, bool(unknown_work),
                                           [h["warning"] for h in open_holes if h["mode"] == "compare"])
    out["comparison"]["screening_selected"] = _check_compared_candidate(
        out["screening"], attempts, comparison)
    return out


def _is_void(rec: Mapping[str, Any]) -> bool:
    """Cut before any judge verdict (the only attempts a launcher may replay, once)."""
    return not rec.get("judge") and rec.get("outcome") in LOST_OUTCOMES


def _superseded(attempts: Sequence[Mapping[str, Any]]) -> set[int]:
    """Void records whose attempt was later settled by another record (the replay): their cost
    stays counted, but they no longer leave the task undecided."""
    counted = {_logical_key(r) for r in attempts if r.get("outcome") not in LOST_OUTCOMES}
    return {id(r) for r in attempts if _is_void(r) and _logical_key(r) in counted}


def _void_attempts(attempts: Sequence[Mapping[str, Any]], holes: Sequence[Mapping[str, Any]]
                   ) -> list[dict[str, Any]]:
    """Every void attempt: the records cut before a verdict and the starts a hard kill left with no
    record, each with whether a replay record names it."""
    replays = [r["replay_of"] for r in attempts if r.get("replay_of")]
    out = [{"path": r["path"], "task": r["task"], "candidate": (r.get("local") or {}).get("candidate"),
            "segment": r.get("segment"), "attempt": r.get("attempt"), "outcome": r["outcome"],
            "reason": r.get("reason"), "cloud_sessions": r.get("cloud_sessions", []),
            "replayed": any(x.get("attempt") == r.get("attempt") and x.get("outcome") == r["outcome"]
                            and r.get("reason") == x.get("reason") for x in replays)}
           for r in attempts if _is_void(r) and not r.get("replay_of")]
    out += [{"path": h.get("path"), "task": {"pr": h.get("pr"), "set": h.get("set")},
             "candidate": h.get("candidate"), "segment": h.get("segment"), "attempt": h.get("attempt"),
             "outcome": "hard_kill", "reason": "attempt_started without settlement",
             "attempt_dir": h["attempt_dir"], "cloud_sessions": [], "replayed": h["replayed"]}
            for h in holes if h["kind"] == "attempt_started"]
    return out


def _unsettled_starts(ledger: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every local attempt start of the ledger without a ``settled`` of the same ``attempt_dir`` and
    every passed machine preflight followed by no attempt start (the launcher was killed between
    them) before the next preflight or session: a killed attempt writes neither record nor
    settlement, and a relaunch may have replayed it. A preflight followed by a clean ``stopped`` is
    not one. ``mode`` is the mode of the session that wrote the entry."""
    settled = {e.get("attempt_dir") for e in ledger if e.get("kind") == "settled" and not e.get("cloud")}
    out: list[dict[str, Any]] = []
    mode: Any = None
    window: dict[str, Any] | None = None

    def close() -> None:
        if window is not None and not window["attempts"]:
            out.append({"kind": "preflight", "mode": window["mode"], "ledger_line": window["line"],
                        "candidate": window["candidate"],
                        "warning": f"preflight_without_attempt:{window['candidate']}"
                                   f"@ledger_line_{window['line']}"})

    for line, entry in enumerate(ledger, 1):
        kind = entry.get("kind")
        if kind in ("session_started", "preflight"):
            close()
            window = None
        elif kind == "stopped":
            window = None
        if kind == "session_started":
            mode = entry.get("mode")
        elif kind == "preflight" and entry.get("ok") is True:
            window = {"mode": mode, "line": line, "candidate": entry.get("candidate"), "attempts": False}
        elif kind == "attempt_started":
            if window is not None:
                window["attempts"] = True
            if entry.get("attempt_dir") not in settled:
                out.append({"kind": "attempt_started", "mode": mode, "ledger_line": line,
                            "attempt_dir": entry.get("attempt_dir"), "path": entry.get("path"),
                            "candidate": entry.get("candidate"), "pr": entry.get("pr"),
                            "set": entry.get("set"), "segment": entry.get("segment"),
                            "attempt": entry.get("attempt"),
                            "warning": f"attempt_started_without_settled:{entry.get('attempt_dir')}"})
    close()
    return out


def _check_compared_candidate(screening: Mapping[str, Any], attempts: Sequence[Mapping[str, Any]],
                              comparison: Sequence[Mapping[str, Any]]) -> str:
    """The local candidate of the comparison must be the one the screening selected under the
    pre-registered rule: otherwise the comparison is refused. Without screening results in these
    files the check cannot be done and the report says so (it never assumes)."""
    compared = {(r.get("local") or {}).get("candidate") for r in comparison if r["path"] in ("C", "N")}
    compared.discard(None)
    if not any(r["path"] == "S" for r in attempts):
        return "screening_results_not_available"
    if compared and compared != {screening["selected"]}:
        raise RunnerError(f"the compared candidate(s) {sorted(compared)} are not the one the screening "
                          f"selected ({screening['selected']}: {screening['reason']})")
    return screening["selected"] or "none_selected"


def _report_screening(rule: Mapping[str, Any], attempts: Sequence[Mapping[str, Any]],
                      killed: int = 0) -> dict[str, Any]:
    by_candidate: dict[str, list[Mapping[str, Any]]] = {}
    undecided: dict[tuple, dict[str, Any]] = {}  # tasks with no decided record (cut, replay included)
    gone = _superseded(attempts)
    for rec in attempts:
        if rec["path"] == "S" and rec.get("outcome") in LOST_OUTCOMES:
            if id(rec) not in gone:  # a tool failure or an interruption is not a refusal: unknown
                undecided[_logical_key(rec)] = {
                    "candidate": rec["local"]["candidate"], "pr": rec["task"]["pr"],
                    "outcome": rec["outcome"], "replayed": bool(rec.get("replay_of"))}
        elif rec["path"] == "S":
            by_candidate.setdefault(rec["local"]["candidate"], []).append(rec)
    table = {}
    for cid, recs in sorted(by_candidate.items()):
        swaps = [r["machine"]["after"]["swap_used_mib"] for r in recs]
        table[cid] = {"tasks": len(recs), "accepted": sum(1 for r in recs if r["accepted"]),
                      "total_seconds": round(sum(r["wall_seconds"] for r in recs), 3),
                      "peak_swap_used_mib": None if any(s is None for s in swaps) else max(swaps),
                      "harness": sorted({r["local"]["harness"] for r in recs})}
    undecided_tasks = list(undecided.values())
    lost = len(undecided_tasks) + killed
    complete = bool(table) and not lost and all(v["tasks"] == rule["tasks"] for v in table.values())
    selected, reason = None, "no_screening_results"
    if table and not complete:  # never select before every candidate has a record for every task
        reason = "incomplete_screening"
    elif table:
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
    return {"candidates": table, "selected": selected, "reason": reason, "complete": complete,
            "undecided_tasks": undecided_tasks, "killed_not_replayed": killed}


def _arm_totals(recs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    tasks: dict[int, list[Mapping[str, Any]]] = {}
    for rec in recs:
        tasks.setdefault(rec["task"]["pr"], []).append(rec)
    per_task = {}
    gone = _superseded(recs)
    for pr, items in sorted(tasks.items()):
        accepted = any(r["accepted"] is True for r in items)
        models: dict[str, dict[str, int | None]] = {}
        for r in items:
            _merge_models(models, r["premium"].get("by_model") or {})
        per_task[pr] = {
            "accepted": accepted,
            "unknown": not accepted and any(r.get("outcome") in UNKNOWN_OUTCOMES for r in items
                                            if id(r) not in gone),
            "premium_by_model": models,
            "review_rounds": sum(r["review"]["rounds"] for r in items),
            "wall_seconds": sum(r["wall_seconds"] for r in items),
            "cloud_executions": sum(r["cloud_executions"] for r in items),
            "premium_billing_tokens": _sum_known([r["premium"]["billing_total"] for r in items]),
            "local_attempt_ok": next((r["local_outcome"] == "accepted" for r in items
                                      if r["segment"] == "local"
                                      and r.get("outcome") not in LOST_OUTCOMES), None)}
    locals_ = [r for r in recs if r["segment"] == "local" and r.get("outcome") not in LOST_OUTCOMES]
    lost = any(r["segment"] == "local" and r.get("outcome") in LOST_OUTCOMES and id(r) not in gone
               for r in recs)
    return {"tasks": per_task, "locals": locals_, "local_tool_error": lost}


def _report_comparison(rule: Mapping[str, Any], attempts: Sequence[Mapping[str, Any]],
                       stops: Sequence[str], unknown_work: bool = False,
                       warnings: Sequence[str] = ()) -> dict[str, Any]:
    arms = {p: _arm_totals([r for r in attempts if r["path"] == p]) for p in PATHS
            if any(r["path"] == p for r in attempts)}
    out: dict[str, Any] = {"arms": {}, "decision": "inconclusive", "recommendation": None,
                           "warnings": list(warnings)}
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
        failed = [t for t in x_tasks if not t["accepted"] and not t["unknown"]]
        undecided = [t for t in x_tasks if not t["accepted"] and t["unknown"]]
        quality = ("unavailable" if not shared else "fail" if failed else "unavailable" if undecided
                   else _verdict(sum(t["review_rounds"] for t in x_tasks)
                                 <= sum(t["review_rounds"] for t in a_tasks), True))
        a_prem = _sum_known([t["premium_billing_tokens"] for t in a_tasks])
        x_prem = _sum_known([t["premium_billing_tokens"] for t in x_tasks])
        # A path cut before its task was decided (cap, tool failure, interruption, unreadable
        # review) has no complete cost, and spent work the ledger cannot match is unknown: in both
        # cases a premium comparison would look better than what was really spent.
        cut = unknown_work or any(t["unknown"] for t in [*a_tasks, *x_tasks])
        premium_v = (None if a_prem is None or x_prem is None or not shared or cut
                     else x_prem <= (1 - rule["premium_reduction_min"]) * a_prem)
        a_wall, x_wall = sum(t["wall_seconds"] for t in a_tasks), sum(t["wall_seconds"] for t in x_tasks)
        time_v = x_wall <= rule["time_ratio_max"] * a_wall if shared else None
        economy = ("unavailable" if premium_v is None else
                   "pass" if premium_v and time_v else "fail")
        compat = (_compatibility(rule, arm["locals"], arm["local_tool_error"])
                  if path == "C" else "pass")
        models_x: dict[str, dict[str, int | None]] = {}
        models_a: dict[str, dict[str, int | None]] = {}
        for t in x_tasks:
            _merge_models(models_x, t["premium_by_model"])
        for t in a_tasks:
            _merge_models(models_a, t["premium_by_model"])
        out["arms"][path] = {
            "tasks_compared": len(shared),
            "compatibility": compat, "quality": quality, "economy": economy,
            "economy_detail": {"premium_billing_tokens": x_prem, "reference_premium_billing_tokens": a_prem,
                               "premium_by_model": models_x, "reference_premium_by_model": models_a,
                               "premium_definition": "unweighted sum of the four billing token "
                               "classes over all models (a limit: a cheaper model weighs the same)",
                               "premium_pass": premium_v, "wall_seconds": round(x_wall, 3),
                               "reference_wall_seconds": round(a_wall, 3), "time_pass": time_v},
            "local_successes": sum(1 for t in x_tasks if t["local_attempt_ok"]) if path == "C" else None}
    verdicts = {p: (a["compatibility"], a["quality"], a["economy"]) for p, a in out["arms"].items()}
    retained = [p for p, v in verdicts.items() if all(x == "pass" for x in v)]
    if retained:
        out["decision"], out["recommendation"] = "retained", ("B" if "B" in retained else retained[0])
    elif any(s in RULE_STOPS for s in stops) or any("fail" in v for v in verdicts.values()):
        out["decision"], out["recommendation"] = "keep_cloud", "A"
    # a stop on a cap or a tool failure leaves an incomplete campaign: inconclusive, not keep_cloud
    expected = rule["tasks"]
    out["complete"] = all(a["tasks_compared"] == expected for a in out["arms"].values()) and bool(out["arms"])
    if out["decision"] == "retained" and not out["complete"]:
        out["decision"] = "inconclusive"
    gone = _superseded(attempts)
    if unknown_work or warnings or any(r.get("outcome") == "interrupted" and id(r) not in gone
                                       for r in attempts):
        # spent work is unknown, or a local attempt started and never settled (killed, possibly replayed)
        out["decision"], out["recommendation"] = "inconclusive", None
    return out


def _verdict(passed: bool, available: bool) -> str:
    return "unavailable" if not available else "pass" if passed else "fail"


def _compatibility(rule: Mapping[str, Any], locals_: Sequence[Mapping[str, Any]],
                   lost: bool = False) -> str:
    """Machine criterion: no local attempt ended by an external signal and extra swap under the
    limit; an unmeasured swap, or a local attempt lost to a tool failure, makes the verdict
    unavailable."""
    if not locals_:
        return "unavailable"
    if any(r["local"]["ended_by_external_signal"] for r in locals_):
        return "fail"
    if lost:
        return "unavailable"
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
    r.add_argument("--results", required=True,
                   help="results-<campaign>.jsonl; ledger-<campaign>.jsonl is read beside it (mandatory)")
    args = parser.parse_args(argv)
    campaign = load_campaign(Path(args.campaign))
    if args.cmd == "report":
        try:
            results = Path(args.results)
            name = re.fullmatch(r"results-(.+)\.jsonl", results.name)
            ledger = results.with_name(f"ledger-{name.group(1)}.jsonl") if name else None
            if ledger is None or not ledger.is_file():
                raise RunnerError(f"no ledger beside {results} (expected ledger-<campaign>.jsonl in "
                                  "the same state directory): a report is refused without it")
            records = _read_jsonl(results)
            if any(r.get("campaign_id") != name.group(1) for r in records):
                raise RunnerError(f"{results} holds records of another campaign id")
            result = report(campaign, records, _read_jsonl(ledger),
                            campaign_sha256=hashlib.sha256(Path(args.campaign).read_bytes()).hexdigest())
        except RunnerError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2))
        return 0
    if args.cmd == "preflight":
        if args.candidate not in campaign["candidates"]:
            print(f"refused: unknown candidate {args.candidate}", file=sys.stderr)
            return 2
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
            today=lambda: today or dt.date.today(),
            input_paths=[Path(args.envelope), Path(args.campaign), Path(args.snapshot),
                         Path(args.manifest)],
            provenance={"campaign_sha256": hashlib.sha256(Path(args.campaign).read_bytes()).hexdigest(),
                        "manifest_sha256": hashlib.sha256(Path(args.manifest).read_bytes()).hexdigest()},
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
