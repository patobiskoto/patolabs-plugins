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
STREAM_FORMATS = ("none", "omp-json", "claude-stream-json")
# Tool names that would let a cloud arm start another agent (a second, unread transcript).
SUBAGENT_TOOLS = ("Agent", "Task")
# A value of a driver ``env_set`` that looks like a secret name is accepted only when it is one of these
# documented placeholders for a local endpoint that needs no key.
_PLACEHOLDER_VALUES = frozenset({"local-endpoint-no-key"})


class RunnerError(RuntimeError):
    pass


class EnvelopeError(RunnerError):
    """No (valid) authorization envelope, or the action is outside it."""


class CapReached(RunnerError):
    """An envelope ceiling is reached: the campaign stops."""


class CandidateFault(RunnerError):
    """The arm itself left the bundle in a state the launcher refuses to run git on (git surface
    changed, tree still moving). Not a launcher failure: the attempt is refused, never void."""


class ToolsetRefused(RunnerError):
    """A cloud record refused on the ``init`` tool set of its stream; carries the contamination audit
    of the same stream, recorded alongside the refusal."""

    def __init__(self, reason: str, contamination: Sequence[str]):
        super().__init__(reason)
        self.contamination = list(contamination)


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
        _check_driver_pins(path, name, driver)
    deny = (data.get("isolation") or {}).get("deny_read_home", DEFAULT_HOME_DENY)
    if not isinstance(deny, dict) or set(deny) != {"local", "cloud"} or not all(
            isinstance(v, list) and all(isinstance(x, str) and x and not x.startswith("/")
                                        and ".." not in Path(x).parts for x in v)
            for v in deny.values()):
        raise RunnerError(f"{path}: isolation.deny_read_home needs relative lists for local and cloud")
    iso = data.get("isolation") or {}
    if type(iso.get("deny_home_by_default", True)) is not bool:
        raise RunnerError(f"{path}: isolation.deny_home_by_default must be a boolean")
    allow = iso.get("allow_read_home", [])
    if not isinstance(allow, list) or not all(
            isinstance(x, str) and x and not x.startswith("/") and ".." not in Path(x).parts for x in allow):
        raise RunnerError(f"{path}: isolation.allow_read_home needs a list of relative home entries")
    _check_cloud_bash_deny(path, data)
    for cid, cand in data["candidates"].items():
        if not _ID.match(cid) or not isinstance(cand.get("model"), str):
            raise RunnerError(f"{path}: candidate {cid} needs an id and a model")
        for key, kind in (("min_context", int), ("lm_studio_key", str), ("quantization", str)):
            if key in cand and (type(cand[key]) is not kind or not cand[key] or cand[key] is True):
                raise RunnerError(f"{path}: candidate {cid}: {key} must be a non-empty {kind.__name__}")
    return data


def _check_driver_pins(path: Path, name: str, driver: Mapping[str, Any]) -> None:
    """What a pinned driver must declare (PAT-111): a local driver is always sandboxed, an
    unsandboxed cloud driver says why, a verified real driver names its evidence, a verified Claude
    log layout comes with the stream that lets the launcher assert no sub-agent tool exists."""
    kind, fake = driver["kind"], bool(driver.get("fake"))
    if "sandbox" in driver:
        if type(driver["sandbox"]) is not bool:
            raise RunnerError(f"{path}: driver {name}: sandbox must be a boolean")
        if driver["sandbox"] is False:
            if kind in LOCAL_KINDS:
                raise RunnerError(f"{path}: local driver {name} cannot run unsandboxed")
            if not (isinstance(driver.get("sandbox_reason"), str) and driver["sandbox_reason"].strip()):
                raise RunnerError(f"{path}: unsandboxed driver {name} needs a sandbox_reason")
    if (driver.get("stream") or {}).get("format", "none") not in STREAM_FORMATS:
        raise RunnerError(f"{path}: driver {name}: unknown stream format")
    if driver.get("verified") is True and not fake and not (
            isinstance(driver.get("evidence"), str) and driver["evidence"].strip()):
        raise RunnerError(f"{path}: driver {name} is verified without evidence (the proof of its trial)")
    log = driver.get("session_log") or {}
    if (kind in ("cloud_implementer", "cloud_reviewer") and not fake and log.get("layout_verified") is True
            and (driver.get("stream") or {}).get("format") != "claude-stream-json"):
        raise RunnerError(f"{path}: driver {name} declares a verified log layout without the "
                          "claude-stream-json stream: the no-sub-agent assertion needs the init event")
    for key, value in (driver.get("env_set") or {}).items():
        if not isinstance(key, str) or not isinstance(value, str) or key.startswith("FOUNDRY_"):
            raise RunnerError(f"{path}: driver {name}: bad env_set entry {key}")
        if _SECRET_NAME.search(key) and value not in _PLACEHOLDER_VALUES:
            raise RunnerError(f"{path}: driver {name} may not set {key} to anything but a placeholder")
    spec = driver.get("executable")
    if spec is not None and not (isinstance(spec, dict) and all(
            isinstance(spec.get(k), str) and spec[k] for k in ("env", "placeholder", "package", "version"))):
        raise RunnerError(f"{path}: driver {name}: executable needs env, placeholder, package, version")
    allowed = driver.get("allowed_tools")
    if allowed is not None and not (isinstance(allowed, list) and all(isinstance(t, str) for t in allowed)):
        raise RunnerError(f"{path}: driver {name}: allowed_tools must be a list of tool names")
    if (kind in ("cloud_implementer", "cloud_reviewer") and not fake and driver.get("verified") is True
            and not allowed):
        raise RunnerError(f"{path}: verified cloud driver {name} needs allowed_tools (the init-event allowlist)")
    traj = driver.get("trajectory")
    if traj is not None and not (isinstance(traj, dict) and isinstance(traj.get("file"), str)
                                 and isinstance(traj.get("steps_path"), list)
                                 and all(isinstance(k, str) for k in traj["steps_path"])):
        raise RunnerError(f"{path}: driver {name}: trajectory needs a file and a steps_path list")
    dirs = driver.get("make_dirs")
    if dirs is not None and not (isinstance(dirs, list) and all(isinstance(d, str) for d in dirs)):
        raise RunnerError(f"{path}: driver {name}: make_dirs must be a list of paths")


def _check_cloud_bash_deny(path: Path, data: Mapping[str, Any]) -> None:
    """``cloud_bash_deny`` (best-effort Bash permission rules, kept as data) must be literally part of
    the ``--disallowedTools`` of every real Claude driver: the pinned argv is what runs."""
    rules = data.get("cloud_bash_deny")
    cloud = {name: d for name, d in data["drivers"].items()
             if d["kind"] in ("cloud_implementer", "cloud_reviewer") and not d.get("fake")
             and (d.get("stream") or {}).get("format") == "claude-stream-json"}
    if rules is None:
        if cloud:
            raise RunnerError(f"{path}: real cloud drivers need cloud_bash_deny (the Bash permission rules)")
        return
    if not isinstance(rules, list) or not rules or not all(
            isinstance(r, str) and re.fullmatch(r"Bash\(.+\)", r) for r in rules):
        raise RunnerError(f"{path}: cloud_bash_deny must be a non-empty list of Bash(...) rules")
    for name, driver in cloud.items():
        argv = driver["argv"]
        if "--disallowedTools" not in argv:
            raise RunnerError(f"{path}: driver {name} has no --disallowedTools")
        denied = argv[argv.index("--disallowedTools") + 1:]
        missing = [r for r in rules if r not in denied]
        if missing:
            raise RunnerError(f"{path}: driver {name}: cloud_bash_deny rule(s) missing from "
                              f"--disallowedTools: {', '.join(missing)}")


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


def _loaded_quantization(item: Mapping[str, Any]) -> str | None:
    quant = item.get("quantization")
    if isinstance(quant, Mapping):
        quant = quant.get("name")
    return quant.strip().lower() if isinstance(quant, str) and quant.strip() else None


def _loaded_context(item: Mapping[str, Any]) -> int | None:
    for key in ("contextLength", "context_length"):
        if type(item.get(key)) is int:
            return item[key]
    return None


def _check_loaded_instance(item: Mapping[str, Any], candidate: Mapping[str, Any],
                           refusals: list[str], facts: dict[str, Any]) -> None:
    """The loaded instance must be the declared candidate: model key, quantization and a context at
    least the frozen one (LM Studio ignores the requested ``-c`` for some builds: the value it
    really loaded is read, never assumed). An absent value refuses (unknown is not a pass)."""
    context, quant = _loaded_context(item), _loaded_quantization(item)
    facts["loaded_context_length"], facts["loaded_quantization"] = context, quant
    facts["loaded_model_key"] = item.get("modelKey")
    minimum = candidate.get("min_context")
    if minimum is not None:
        if context is None:
            refusals.append("loaded_context_unknown")
        elif context < minimum:
            refusals.append(f"loaded_context_below_minimum:{context}<{minimum}")
    wanted_quant = candidate.get("quantization")
    if wanted_quant is not None:
        if quant is None:
            refusals.append("loaded_quantization_unknown")
        elif quant != wanted_quant.strip().lower():
            refusals.append(f"loaded_quantization_differs:{quant}!={wanted_quant}")
    wanted_key = candidate.get("lm_studio_key")
    if wanted_key is not None:
        if item.get("modelKey") is None:
            refusals.append("loaded_model_key_unknown")
        elif item["modelKey"] != wanted_key:
            refusals.append(f"loaded_model_key_differs:{item['modelKey']}!={wanted_key}")


def preflight(campaign: Mapping[str, Any], expected_model: str,
              run: Callable[[Sequence[str]], str | None] = default_run,
              disk_free_gib: Callable[[], float] | None = None,
              candidate: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Machine facts against the frozen coordinates; refuses on any difference. Loads nothing.
    ``candidate`` (its entry of the campaign) adds the loaded-instance checks: context length,
    quantization and model key read from ``lms ps --json``."""
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
            by_id = {str(i.get("identifier") or i.get("modelKey")): i for i in items}
            ids = sorted(by_id)
        except (ValueError, AttributeError, TypeError):
            refusals.append("lms_ps_unparseable")
        else:
            facts["loaded_models"] = ids
            if expected_model not in ids:
                refusals.append("expected_model_not_loaded")
            elif candidate:
                _check_loaded_instance(by_id[expected_model], candidate, refusals, facts)
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
    declared = next((c for c in campaign["candidates"].values() if c["model"] == expected_model), {})
    loaded = {"identifier": expected_model,
              **({"modelKey": declared["lm_studio_key"]} if "lm_studio_key" in declared else {}),
              **({"contextLength": declared["min_context"]} if "min_context" in declared else {}),
              **({"quantization": declared["quantization"]} if "quantization" in declared else {})}
    answers = {
        ("sysctl", "-n", "machdep.cpu.brand_string"): f"Apple {frozen['chip_contains']}",
        ("sysctl", "-n", "hw.memsize"): str(frozen["memory_gib"] * 2**30),
        ("sw_vers", "-productVersion"): frozen["os_version"],
        ("lms", "version"): frozen["lm_studio_version_contains"],
        ("lms", "runtime", "ls"): frozen["mlx_runtime_contains"],
        ("lms", "ps", "--json"): json.dumps([loaded]),
        ("sysctl", "-n", "vm.swapusage"): "total = 1024.00M  used = 100.00M  free = 924.00M",
        ("memory_pressure",): "System-wide memory free percentage: 80%",
        ("ps", "-axo", "rss=,command="): ""}
    return lambda argv: answers.get(tuple(argv))


# ------------------------------------------------------------------------------ isolation

def _sb(path: str | Path) -> str:
    real = os.path.realpath(str(path)).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{real}"'


def _metadata_ancestors(paths: Sequence[Path], home: Path) -> list[Path]:
    """Every directory from ``home`` down to (and excluding) each path inside it: a process must be able
    to ``stat`` them to reach what it may read (only their metadata, never their content or listing)."""
    found: dict[str, Path] = {}
    for path in paths:
        real = Path(os.path.realpath(path))
        if real != home and home in real.parents:
            for parent in (home, *reversed([p for p in real.parents if home in p.parents])):
                found.setdefault(str(parent), parent)
    return list(found.values())


def sandbox_profile(*, writable: Sequence[Path], deny_read: Sequence[Path], network: str,
                    deny_home: Path | None = None, allow_read: Sequence[Path] = ()) -> str:
    """sandbox-exec profile: file writes only under ``writable``, no read of ``deny_read``, and
    (``network == "loopback"``) no network except loopback. With ``deny_home`` (the user's REAL home)
    every file read under it is denied first, then only ``writable`` and ``allow_read`` are allowed
    back (and the metadata of their ancestors up to the home); ``deny_read`` comes last, so it still
    wins: the explicit list is a second layer."""
    for root in writable:  # a denied read path around a writable root would break the arm silently
        for denied in deny_read:
            real_w, real_d = Path(os.path.realpath(root)), Path(os.path.realpath(denied))
            if real_w == real_d or real_d in real_w.parents:
                raise RunnerError(f"writable path {real_w} is inside the read-denied path {real_d}: "
                                  "keep the launcher inputs in a directory apart from the work root")
    lines = ["(version 1)", "(allow default)", "(deny file-write*)",
             "(allow file-write* " + " ".join(f"(subpath {_sb(p)})" for p in writable)
             + ' (literal "/dev/null") (literal "/dev/dtracehelper") (literal "/dev/tty"))']
    if deny_home is not None:
        home = Path(os.path.realpath(deny_home))
        keep = [*writable, *allow_read]
        lines.append(f"(deny file-read* (subpath {_sb(home)}))")
        lines.append("(allow file-read* " + " ".join(f"(subpath {_sb(p)})" for p in keep) + ")")
        ancestors = _metadata_ancestors(keep, home)
        if ancestors:
            lines.append("(allow file-read-metadata " + " ".join(f"(literal {_sb(p)})" for p in ancestors) + ")")
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
    SSH/GPG/AWS, keychains). For a local driver this explicit list is the SECOND layer, on top of the
    default denial of the whole real home (``Runner._home_policy``); for the audit it names what a cloud
    arm (no sandbox, no read denial) must not touch."""
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


def sandbox_text(driver: Mapping[str, Any], writable: Sequence[Path], deny_read: Sequence[Path],
                 deny_home: Path | None = None, allow_read: Sequence[Path] = ()) -> str:
    """The profile of one driver, or a ``RunnerError`` when isolation cannot be enforced. Pure: the
    runner calls it before any claim or reservation, so a configuration error costs nothing."""
    if not sandbox_available():
        raise RunnerError("sandbox-exec is unavailable: isolation cannot be enforced")
    return sandbox_profile(writable=list(writable), deny_read=list(deny_read),
                           network=driver.get("network", "loopback"), deny_home=deny_home,
                           allow_read=list(allow_read))


# ------------------------------------------------------------------------- stream parsing

class StreamStats:
    """Counters read from a harness event stream. ``format == "none"`` exposes nothing (unknown)."""

    def __init__(self, stream: Mapping[str, Any] | None):
        self.stream = stream or {"format": "none"}
        self.known = self.stream.get("format") == "omp-json"
        self.claude = self.stream.get("format") == "claude-stream-json"
        self.init_tools: list[str] | None = None  # tool names of the host ``system/init`` event
        self.steps = 0 if self.known else None
        self.tokens: dict[str, int] | None = {"input": 0, "output": 0, "reasoning": 0} if self.known else None
        self.speeds: dict[str, list[float]] = {"prefill": [], "generation": []}

    def feed(self, line: str) -> None:
        if not (self.known or self.claude):
            return
        try:
            event = json.loads(line)
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        if self.claude:
            tools = event.get("tools")
            if (event.get("type") == "system" and event.get("subtype") == "init"
                    and self.init_tools is None and isinstance(tools, list)):
                self.init_tools = [t for t in tools if isinstance(t, str)]
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

    def steps_from_file(self, path: str, keys: Sequence[str]) -> None:
        """Steps of a harness that writes a trajectory file instead of an event stream (read once the
        process has ended: the step bound cannot be imposed while it runs)."""
        try:
            value: Any = json.loads(Path(path).read_text(encoding="utf-8"))
            for key in keys:
                value = value[key]
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            return
        if type(value) is int and value >= 0:
            self.steps = value

    def summary(self) -> dict[str, Any]:
        unknown: dict[str, str] = {}
        out: dict[str, Any] = {"steps": self.steps, "stream_tokens": self.tokens}
        if self.steps is None:
            unknown["steps"] = "stream format exposes no events"
        if self.tokens is None:
            unknown["stream_tokens"] = "stream format exposes no events"
        if self.claude:
            out["init_tools"] = self.init_tools
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
                   host_env: Mapping[str, str] | None = None, deny_home: Path | None = None,
                   allow_read: Sequence[Path] = ()) -> dict[str, Any]:
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
    env.update({k: _substitute(v, values) for k, v in (driver.get("env_set") or {}).items()})
    for rel_dir in driver.get("make_dirs") or []:  # e.g. the harness's own configuration directory
        Path(_substitute(rel_dir, values)).mkdir(parents=True, exist_ok=True)
    argv = [_substitute(a, values) for a in driver["argv"]]
    profile_dir = None
    if sandbox:
        sandbox_text(driver, [workdir, scratch, *extra_write], deny_read, deny_home, allow_read)  # no file yet
        profile_dir = Path(os.path.realpath(tempfile.mkdtemp(prefix="foundry-sb-")))
        profile = profile_dir / "profile.sb"  # it names every denied path: the arm may not read it
        profile.write_text(sandbox_text(driver, [workdir, scratch, *extra_write],
                                        [*deny_read, profile_dir], deny_home, allow_read), encoding="utf-8")
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
    trajectory = driver.get("trajectory")
    if trajectory:
        stats.steps_from_file(_substitute(trajectory["file"], values), trajectory["steps_path"])
        if max_steps is not None and stats.steps is not None and stats.steps > max_steps:
            state["step_limit"] = True  # a posteriori: the harness's own bound did not hold the arm
    launcher_kill = state["timed_out"] or state["step_limit"]
    return {"exit_code": code if code is not None and code >= 0 else None,
            "signal": -code if code is not None and code < 0 else None,
            "timed_out": state["timed_out"], "step_limit_hit": state["step_limit"],
            "launcher_kill": launcher_kill, "start_error": None,
            "wall_seconds": round(time.monotonic() - started, 3), "stream": stats.summary()}


# ---------------------------------------------------- pinned harness and transcript checks (PAT-111)

def tool_allowlist_refusal(driver: Mapping[str, Any], stream_summary: Mapping[str, Any]) -> str | None:
    """Why a cloud arm's record must be refused: the host ``system/init`` event of the stream does not
    prove the arm's tool set. With ``allowed_tools`` declared (the pinned cloud drivers: Bash, Edit,
    Read, Write) the init tools must be a subset of it, so a new host version that adds a tool (web,
    sub-agent, workflow...) is refused rather than trusted; without it only a sub-agent tool
    (``Agent``/``Task``) is refused. A sub-agent transcript would be missed by the token reading (one
    file per session id). ``None`` when proven or when the driver is not a Claude stream driver."""
    if (driver.get("stream") or {}).get("format") != "claude-stream-json":
        return None
    tools = stream_summary.get("init_tools")
    if tools is None:
        return "no system/init event in the stream: cannot assert the arm's tool set"
    allowed = driver.get("allowed_tools")
    if allowed is not None:
        extra = sorted(set(tools) - set(allowed))
        return f"tool(s) outside the allowlist available to the arm: {', '.join(extra)}" if extra else None
    found = sorted(set(tools) & set(SUBAGENT_TOOLS))
    return f"sub-agent tool available to the arm: {', '.join(found)}" if found else None


def installed_version(executable: Path, package: str) -> str | None:
    """Version of ``package`` installed in the virtual environment of ``executable`` (read from its
    ``dist-info``; the harness is never run to ask). ``None`` when absent or ambiguous."""
    dist = re.sub(r"[-_.]+", "_", package).lower()
    found = {d.name[len(dist) + 1:-len(".dist-info")]
             for d in Path(executable).parent.parent.glob(f"lib/python*/site-packages/{dist}-*.dist-info")}
    return next(iter(found)) if len(found) == 1 else None


def resolve_executable(driver: Mapping[str, Any], host_env: Mapping[str, str]) -> dict[str, str] | None:
    """The harness executable of a driver that takes it from an operator variable (no absolute path
    is committed): it must be an absolute path to an executable file whose installed package has the
    pinned version. The path is returned for the argv; the record keeps only the variable name and the
    version."""
    spec = driver.get("executable")
    if not spec:
        return None
    value = host_env.get(spec["env"])
    if not value or not os.path.isabs(value):
        raise RunnerError(f"{spec['env']} must hold the absolute path of the harness executable")
    path = Path(value)
    if not (path.is_file() and os.access(path, os.X_OK)):
        raise RunnerError(f"{spec['env']} does not name an executable file")
    version = installed_version(path, spec["package"])
    if version != spec["version"]:
        raise RunnerError(f"{spec['package']} installed for {spec['env']} is {version or 'unknown'}, "
                          f"the pinned version is {spec['version']}")
    return {"placeholder": spec["placeholder"], "path": str(path), "env": spec["env"],
            "package": spec["package"], "version": version}


_PATH_KEYS = frozenset({"path", "file_path", "notebook_path", "cwd", "directory", "dir", "file",
                        "filename", "paths", "file_paths"})
# Tools whose ``pattern`` argument is a path glob (Claude ``Glob``, omp ``find``), not a text pattern.
_GLOB_TOOLS = frozenset({"glob", "find"})
_PATH_TOKEN = re.compile(r"(?:~|\$\{?HOME\}?|(?<![\w.~$}/])/|(?<![\w.~$}/])\.\.?/)[^\s'\"`;|&<>(),]*")


def _path_tokens(text: str) -> list[str]:
    """Path-like tokens of free text, minus a token made only of slashes (``/``, ``//``): that is the
    division / floor-division operator of code, not a path."""
    return [t for t in _PATH_TOKEN.findall(text) if t.strip("/")]


def _tool_calls(lines: Sequence[str]) -> list[tuple[str, Any]]:
    """Every tool call of a stream: Claude ``tool_use`` blocks of assistant messages and omp
    ``tool_execution_start`` events, as ``(tool name, arguments)``."""
    calls: list[tuple[str, Any]] = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "tool_execution_start":
            calls.append((str(event.get("toolName")), event.get("args")))
        elif event.get("type") == "assistant" and isinstance(event.get("message"), dict):
            for block in event["message"].get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    calls.append((str(block.get("name")), block.get("input")))
    return calls


def _strings(value: Any, key: str | None = None) -> list[tuple[str | None, str]]:
    if isinstance(value, str):
        return [(key, value)]
    if isinstance(value, Mapping):
        return [x for k, v in value.items() for x in _strings(v, str(k))]
    if isinstance(value, list):
        return [x for v in value for x in _strings(v, key)]
    return []


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


# ---- command audit (best effort: the stream shows the command line, not what a script does)
_COMMAND_KEYS = frozenset({"command", "cmd"})
_SEGMENT_SPLIT = re.compile(r"&&|\|\||[;|&\n`]|\$\(|\)")
_ASSIGNMENT = re.compile(r"^\w+=")
_WRAPPERS = frozenset({"env", "sudo", "command", "exec", "nohup", "time", "nice", "xargs", "timeout",
                       "builtin"})
_SHELLS = frozenset({"sh", "bash", "zsh", "dash"})
# Executables a cloud arm has no business running (tracker, merge, network, other agents, keychain).
FORBIDDEN_EXECUTABLES = frozenset({"gh", "curl", "wget", "claude", "codex", "omp", "ssh", "scp", "nc",
                                   "security", "open", "npm"})
FORBIDDEN_GIT = frozenset({"push", "remote", "clone", "fetch", "pull"})


def _segments(command: str) -> list[list[str]]:
    """Simple command segments of a shell command line (split on ``&& || ; | & `` and ``$(``, quotes
    dropped). Not a shell parser: a command built at run time or hidden in a script is not seen."""
    text = command.replace("'", " ").replace('"', " ")
    return [tokens for tokens in (part.split() for part in _SEGMENT_SPLIT.split(text)) if tokens]


def _strip_wrappers(tokens: Sequence[str]) -> list[str]:
    """Drop leading ``VAR=value``, ``env``, ``sudo``, ``command``, ``exec``, ``nohup``, ``time``,
    ``nice``, ``xargs``, ``timeout`` and their options."""
    rest = list(tokens)
    while rest:
        if _ASSIGNMENT.match(rest[0]):
            rest.pop(0)
        elif os.path.basename(rest[0].lstrip("\\({")) in _WRAPPERS:
            wrapper = os.path.basename(rest.pop(0).lstrip("\\({"))
            while rest and (rest[0].startswith("-") or _ASSIGNMENT.match(rest[0]) or (
                    wrapper == "timeout" and re.fullmatch(r"[\d.]+[smhd]?", rest[0]))):
                rest.pop(0)
        else:
            break
    return rest


def _forbidden_command(tokens: Sequence[str]) -> str | None:
    """The forbidden executable a segment starts with (after wrappers and ``sh -c``), or ``None``."""
    rest = _strip_wrappers(tokens)
    if not rest:
        return None
    exe, args = os.path.basename(rest[0].lstrip("\\({")), rest[1:]
    if exe in _SHELLS:
        for n, arg in enumerate(args):
            if arg.startswith("-") and "c" in arg[1:]:
                return _forbidden_command(args[n + 1:])
        return None
    if exe in FORBIDDEN_EXECUTABLES:
        return exe
    if exe == "git":
        skip = False
        for arg in args:
            if skip:
                skip = False
            elif arg in ("-C", "-c", "--git-dir", "--work-tree", "--namespace"):
                skip = True
            elif not arg.startswith("-"):
                return f"git {arg}" if arg in FORBIDDEN_GIT else None
        return None
    if re.fullmatch(r"python[\d.]*", exe) and "-m" in args[:-1]:
        module = args[args.index("-m") + 1]
        if module.startswith("http"):
            return "python -m http"
        if module == "pip" and "install" in args:
            return "pip install"
    if exe in ("pip", "pip3") and "install" in args:
        return "pip install"
    return None


def _pathlike(token: str) -> bool:
    if token and not token.strip("/"):
        return False  # ``/``, ``//``: an operator, not a path (unless a reader takes it, see below)
    return token.startswith(("~", "$HOME", "${HOME}", "/")) or token in (".", "..") or "/" in token


# Executables that, given a bare ``/``, walk or read the filesystem root (``find / -name x``).
_ROOT_READERS = frozenset({"find", "rg", "ls", "du", "tree", "cat"})


def _reads_root(tokens: Sequence[str]) -> bool:
    """Whether a bare ``/`` in this segment (wrappers stripped) is a path: an argument of ``find``,
    ``rg``, ``ls``, ``du``, ``tree``, ``cat`` or of a recursive ``grep`` (``-r``/``-R``)."""
    if not tokens:
        return False
    exe = os.path.basename(tokens[0].lstrip("\\({"))
    if exe in ("grep", "egrep", "fgrep"):
        return any(t in ("--recursive", "--dereference-recursive")
                   or (t.startswith("-") and not t.startswith("--") and ("r" in t or "R" in t))
                   for t in tokens[1:])
    return exe in _ROOT_READERS


def _unquoted_expansions(command: str) -> str:
    """The command with what the shell does NOT expand neutralised for the path audit: a ``~`` inside
    single or double quotes and a ``$`` inside single quotes become ``_`` (``grep -rn '~/.claude' .``
    searches the text ``~/.claude``, it reads nothing under the home). An unquoted ``~`` or ``$HOME``
    and a ``$HOME`` inside double quotes are kept (``cat ~/.config/x`` stays an access). Best effort:
    a quote inside a heredoc body is read like any other."""
    out, quote, escaped = [], None, False
    for ch in command:
        if escaped:
            escaped = False
        elif ch == "\\" and quote != "'":
            escaped = True
        elif quote is None and ch in "'\"":
            quote = ch
        elif ch == quote:
            quote = None
        elif quote is not None and (ch == "~" or (ch == "$" and quote == "'")):
            ch = "_"
        out.append(ch)
    return "".join(out)


def _expand(token: str, home: str) -> str | None:
    """``~``, ``$HOME`` and ``${HOME}`` as the home directory; ``~user`` is not expanded (``None``)."""
    if token.startswith("${HOME}"):
        return home + token[len("${HOME}"):]
    if token.startswith("$HOME"):
        return home + token[len("$HOME"):]
    if token == "~" or token.startswith("~/"):
        return home + token[1:]
    return None if token.startswith("~") else token


def _command_paths(command: str, bundle: Path, home: str) -> list[tuple[Path, bool]]:
    """Every path a command line names, resolved against the working directory the line has at that
    point (``cd`` is followed): ``find ~``, ``cd ~ && cat .claude/x``, ``src/../../..`` included; a bare
    ``/`` only as an argument of a filesystem reader (``find / -name x``, see ``_reads_root``). The
    flag says the path was RELATIVE to the bundle (an honest arm never climbs out of it). Expects the
    command after ``_unquoted_expansions``."""
    cwd, out = bundle, []

    def resolve(token: str) -> tuple[Path, bool] | None:
        expanded = _expand(token, home)
        if expanded is None:
            return None
        relative = not os.path.isabs(expanded) and cwd == bundle
        return Path(os.path.realpath(expanded if os.path.isabs(expanded) else cwd / expanded)), relative

    for segment in _segments(command):
        tokens = _strip_wrappers(segment)
        if tokens and os.path.basename(tokens[0]) == "cd":
            target = next((t for t in tokens[1:] if not t.startswith("-")), "~")
            found = resolve(target)
            if found is not None:
                out.append(found)
                cwd = found[0]
            continue
        root_reader = _reads_root(tokens)
        for token in tokens:
            token = token.lstrip("<>")
            if token.startswith("-"):
                token = token.partition("=")[2]
            if token and (_pathlike(token) or (root_reader and not token.strip("/"))) \
                    and (found := resolve(token)) is not None:
                out.append(found)
    return out


def _tool_results(lines: Sequence[str]) -> list[str]:
    """Text of every tool result a stream exposes (Claude ``tool_result`` blocks of user messages,
    omp ``tool_execution_end`` events)."""
    texts: list[str] = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "user" and isinstance(event.get("message"), dict):
            content = event["message"].get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    texts += [t for _, t in _strings(block.get("content"))]
        elif event.get("type") in ("tool_execution_end", "tool_execution_update"):
            texts += [t for _, t in _strings(event.get("result"))]
    return texts


def audit_transcript(stream_log: Path, *, bundle: Path, scratch: Path, sensitive: Sequence[Path],
                     home: str) -> list[str]:
    """What an arm's stream shows it did outside its bundle and attempt directory. Only the path
    arguments of its tool calls (``_PATH_KEYS``, the ``pattern`` of a Glob/find tool) and its shell
    commands are read: the text it writes or searches (Edit ``old_string``/``new_string``, Write
    ``content``, Grep ``pattern``...) is not an access. Paths (relative ones resolved against the bundle,
    or against the directory of a ``cd``) that fall among ``sensitive`` (plugin cache and the rest of the
    user's configuration, other checkouts of this repository, the launcher's own files); for a path key
    or a Bash command also any path that resolves to the real home or below it, to a directory that
    contains a sensitive path, or to an ancestor of the bundle (``find ~``, ``src/../../..``). In a
    command, ``~`` and ``$HOME`` are expanded only where the shell expands them (``grep -rn '~/.claude'
    .`` is clean, ``cat ~/.config/x`` is not). ``tool_result:<path>`` for a LITERAL absolute path under
    a sensitive root that a tool RESULT shows (``~``/``$HOME`` in result text are not expanded: a file
    of the bundle that mentions ``~/.claude`` is text, not an access); ``command:<label>`` for a Bash
    command that runs a forbidden executable (see ``FORBIDDEN_EXECUTABLES``, ``git push|remote|clone|
    fetch|pull``, ``python -m http``, ``pip install``). Best effort on a command line (a path or a
    command built at run time, or run by a script, is not seen). A non-empty result makes the attempt
    ``contaminated``. ``~`` shows as the home directory in the result. Known limit: a token made only of
    slashes (``/``, ``//``) is the division operator of code, not a path, EXCEPT as an argument of a
    filesystem reader (``find``, ``grep -r``, ``rg``, ``ls``, ``du``, ``tree``, ``cat``): ``find / -name x
    -exec cat {} +`` is flagged, while a bare ``/`` reaching the shell any other way (``echo / | xargs
    ls``, a script) is not seen."""
    try:
        lines = Path(stream_log).read_text("utf-8", "replace").splitlines()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise RunnerError(f"transcript unreadable, it cannot be audited: {exc}") from None
    bundle_real, home_real = Path(os.path.realpath(bundle)), Path(os.path.realpath(home))
    allowed = [bundle_real, Path(os.path.realpath(scratch))]
    roots = [Path(os.path.realpath(p)) for p in sensitive]

    def shown(path: Path) -> str:
        return str(path).replace(str(home_real), "~", 1)

    hits: dict[str, None] = {}
    commands: dict[str, None] = {}

    def consider(path: Path, broad: bool, relative: bool = False) -> None:
        if any(_within(path, a) for a in allowed):
            return
        if any(_within(path, r) for r in roots) or relative or (broad and (
                _within(path, home_real) or _within(bundle_real, path)
                or any(_within(r, path) for r in roots))):
            hits[shown(path)] = None

    for name, args in _tool_calls(lines):
        path_keys = _PATH_KEYS | ({"pattern"} if name.lower() in _GLOB_TOOLS else frozenset())
        for key, text in _strings(args):
            if key in _COMMAND_KEYS:
                literal = _unquoted_expansions(text)
                tokens = _path_tokens(literal)
            elif key in path_keys:
                tokens = _path_tokens(text) + [text.strip()]
            else:  # text the arm writes or searches (Edit, Write, Grep pattern...): not an access
                continue
            for token in tokens:
                token = _expand(token, str(home_real))
                if token is not None:
                    consider(Path(os.path.realpath(token if os.path.isabs(token) else bundle / token)),
                             key in path_keys, key in path_keys and not os.path.isabs(token))
            if key in _COMMAND_KEYS:
                for path, relative in _command_paths(literal, bundle_real, str(home_real)):
                    consider(path, True, relative)
                for segment in _segments(text):
                    if (found := _forbidden_command(segment)) is not None:
                        commands[f"command:{found}"] = None
    seen: dict[str, None] = {}
    for text in _tool_results(lines):
        for token in _path_tokens(text):
            token = re.sub(r":\d+(?::\d+)?:?$", "", token).rstrip(":.")  # ``file.py:12:`` locations
            if os.path.isabs(token):  # a literal path only: ``~``/``$HOME`` in text are not expanded
                path = Path(os.path.realpath(token))
                if any(_within(path, r) for r in roots) and not any(_within(path, a) for a in allowed):
                    seen[f"tool_result:{shown(path)}"] = None
    return [*sorted(hits), *sorted(seen), *sorted(commands)]


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
                 provenance: Mapping[str, Any] | None = None, require_screening: bool = True,
                 screening_campaign: str | None = None):
        if mode not in MODES:
            raise RunnerError(f"unknown mode {mode!r}")
        self.repo, self.campaign, self.envelope = Path(repo).resolve(), campaign, envelope
        self.state_dir, self.work_root = Path(state_dir).resolve(), Path(work_root).resolve()
        self.mode, self.dry_run, self.sandbox = mode, dry_run, sandbox
        self.run, self.disk_free_gib, self.host_env = run, disk_free_gib, host_env
        self.today, self.input_paths = today, [Path(p) for p in input_paths]
        self.require_screening, self.screening_campaign = require_screening, screening_campaign
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
                if (mode == "compare" or driver["kind"] in LOCAL_KINDS) and driver.get("sandbox", True):
                    sandbox_text(driver, [self.work_root, *_extra_write(driver)], self._deny_read(driver),
                                 *self._home_policy(driver))
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
        if any(r["outcome"] in ("accepted", "review_unreadable", "contaminated") or
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

    def _home_policy(self, driver: Mapping[str, Any], exe: Mapping[str, str] | None = None
                     ) -> tuple[Path | None, list[Path]]:
        """``(deny_home, allow_read)`` of a LOCAL driver: every read under the user's real home is denied
        (``isolation.deny_home_by_default``, default true) except the entries of
        ``isolation.allow_read_home`` (empty by default) and the root of the harness executable's
        environment when it lives in the home. A cloud driver never gets it (AGENTS.md R6)."""
        iso = self.campaign.get("isolation") or {}
        if driver["kind"] not in LOCAL_KINDS or not iso.get("deny_home_by_default", True):
            return None, []
        env = os.environ if self.host_env is None else self.host_env
        home = Path(env.get("HOME") or Path.home())
        allow = [home / rel for rel in iso.get("allow_read_home", [])]
        if exe:
            binary = Path(exe["path"])
            allow += [binary.parent.parent, Path(os.path.realpath(binary)).parent.parent]
        return home, allow

    def _sandboxed(self, driver: Mapping[str, Any]) -> bool:
        """A local driver is always sandboxed (refused at load otherwise); a cloud driver may declare
        ``sandbox: false`` with its reason (Claude Code cannot authenticate under sandbox-exec)."""
        return self.sandbox and driver.get("sandbox", True)

    def _executable(self, driver: Mapping[str, Any]) -> dict[str, str] | None:
        env = os.environ if self.host_env is None else self.host_env
        return resolve_executable(driver, env)

    def _check_harness(self, driver_id: str) -> None:
        """A harness executable that cannot be resolved is refused before any claim or spend."""
        self._executable(self._driver(driver_id))

    def _audit(self, stream_log: Path, bundle: Path, scratch: Path) -> list[str]:
        """Contamination audit of one arm's tool calls, on the stream the launcher kept. Always against
        the strictest list (the one of a local arm): a cloud arm keeps ``~/.claude`` for its identity,
        but reading anything there (the plugin cache holds the merged tests) is a contamination."""
        env = os.environ if self.host_env is None else self.host_env
        home = str(env.get("HOME") or Path.home())
        sensitive = read_deny_list(
            repo=self.repo, home=home, state_dir=self.state_dir, input_paths=self.input_paths,
            kind="local_harness", isolation=(self.campaign.get("isolation") or {}).get("deny_read_home"))
        return audit_transcript(stream_log, bundle=bundle, scratch=scratch, sensitive=sensitive, home=home)

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
        did not touch the git configuration or attributes that git would honour. Called only AFTER the
        arm ran: any git failure or ``OSError`` here (a stale ``.git/index.lock`` left by the killed arm,
        an empty nested repository, an unreadable file) is the arm's doing, a ``CandidateFault``."""
        root, surface = self.guards[bundle]
        _wait_quiescent(bundle, self.quiescent_wait)
        try:
            changed = _git_surface(bundle) != surface
        except (RunnerError, OSError) as exc:  # the arm replaced ``.git`` or made a file unreadable
            raise CandidateFault(str(exc)) from None
        if changed:
            raise CandidateFault("bundle git configuration or attributes changed: refusing to run git on it")
        try:
            return _capture_patch(bundle, root)
        except (RunnerError, OSError) as exc:
            raise CandidateFault(f"bundle unreadable by git: {exc}") from None

    def _judge(self, task: Mapping[str, Any], bundle: Path) -> dict[str, Any]:
        """``lfc.judge``, where an ``OSError`` on the bundle's own content (a file the arm made
        unreadable, a bytecode file it made undeletable) is the arm's doing: a ``REFUSED`` verdict
        (``candidate_fault``), never a void attempt. Any other ``OSError`` (the launcher's environment)
        propagates and leaves the attempt void."""
        try:
            return lfc.judge(self.repo, task, bundle)
        except OSError as exc:
            real = Path(os.path.realpath(bundle))
            name = Path(os.path.realpath(exc.filename)) if isinstance(exc.filename, str) else None
            if name is None or not _within(name, real):
                raise
            rel = name.relative_to(real).as_posix()
            return _fault_verdict(CandidateFault(
                f"the judge cannot handle the bundle content: {type(exc).__name__} on {rel}: {exc.strerror}"))

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
                    replay_of: Mapping[str, Any] | None = None, judge: Mapping[str, Any] | None = None,
                    review: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        """A tool failure or an interruption (Ctrl-C, SIGTERM, any other exception) is recorded as
        such, never as a new verdict, before the campaign stops. An interrupted attempt has an unknown
        cost (``billing_total`` null), like any record whose cloud executions were not all read. A cut
        that comes AFTER a verdict (``judge``) or a review keeps them on the record: the attempt is then
        decided and a relaunch never replays it (no second chance after a verdict)."""
        status = _cut(exc)
        reason = (str(exc) or type(exc).__name__)[:200] if status == "tool_error" \
            else f"{type(exc).__name__}: {exc}"[:200]
        by_role = dict(by_role or {})
        record = {
            "record_type": "attempt", "task": _task_ref(task, task_set), "path": path,
            "segment": segment, "attempt": attempt, "outcome": status, "status": status,
            "reason": reason, "judge": dict(judge) if judge else None, "accepted": None,
            "local_outcome": status if segment == "local" else None,
            "review": dict(review) if review else {"rounds": 0, "verdicts": []},
            "wall_seconds": round(wall, 3),
            "cloud_executions": len(sessions), "cloud_sessions": list(sessions),
            "premium": {"by_role": {r: _classes(t) for r, t in by_role.items()},
                        "by_model": dict(by_model or {}),
                        "billing_total": None if status == "interrupted"
                        else _record_total(by_role, sessions)},
            "local": ({"ended_by_external_signal": False, **local} if local else None),
            "machine": None, "unknown": {status: reason},
            **({"replay_of": dict(replay_of)} if replay_of else {})}
        if getattr(exc, "contamination", None):  # refused AND contaminated: never replayed either
            record.update(contaminated=True, outcome="contaminated",
                          contamination=_contamination(exc.contamination))
            record["unknown"]["contaminated"] = _CONTAMINATED
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
        exe = self._executable(driver)  # refused before any claim, reservation or file
        self.ledger.check(cloud=False)
        self._claim(path, task, task_set, candidate_id, "local", attempt, 1 if replay_of else 0)
        bounds = self.campaign["bounds"]
        model = self.campaign["candidates"][candidate_id]["model"]
        execution, started, verdict = None, None, None
        name = self._attempt_name(task, f"{path}-local-{candidate_id}")
        self.ledger.append("attempt_started", attempt_dir=name, path=path, pr=task["pr"],
                           set=task_set, candidate=candidate_id, segment="local", attempt=attempt)
        try:
            bundle, attempt_dir = self._bundle(task, "", name=name)
            try:
                values = self._values(bundle, attempt_dir, model=model,
                                      **({exe["placeholder"]: exe["path"]} if exe else {}))
                values["prompt"] = self._prompt("implement", values)
                before = machine_snapshot(self.run, self.campaign.get("server_process_pattern"))
                budget = min(bounds["local_max_seconds"], max(self.ledger.remaining_seconds(), 0))
                deny_home, allow_read = self._home_policy(driver, exe)
                started = time.monotonic()
                stream_log = (self.state_dir / "streams"
                              / f"{self.envelope['campaign_id']}-{attempt_dir.name}.jsonl")
                execution = execute_driver(
                    driver, values, workdir=bundle, scratch=attempt_dir / "scratch",
                    stream_log=stream_log,
                    max_seconds=budget, max_steps=bounds["local_max_steps"],
                    sandbox=self._sandboxed(driver),
                    deny_read=self._deny_read(driver), host_env=self.host_env,
                    deny_home=deny_home, allow_read=allow_read)
                after = machine_snapshot(self.run, self.campaign.get("server_process_pattern"))
                contamination = self._audit(stream_log, bundle, attempt_dir / "scratch")
                try:
                    patch = self._patch_of(bundle)
                except CandidateFault as fault:  # the arm's doing: refused, never void and replayed
                    patch, verdict = b"", _fault_verdict(fault)
                else:
                    verdict = self._judge(task, bundle)
            finally:
                self._discard(bundle, attempt_dir)
        except BaseException as exc:  # tool failure or interruption: time is counted, the attempt
            with self._critical():    # is recorded; a verdict already received is kept on it, so
                                      # the attempt is decided and never replayed (no second chance)
                wall = 0.0
                if started is not None:
                    wall = execution["wall_seconds"] if execution else time.monotonic() - started
                self.ledger.settle(cloud=False, seconds=wall, premium_tokens=None, attempt_dir=name,
                                   **({"interrupted": True} if _cut(exc) == "interrupted" else {}))
                self._tool_error(task, path, "local", attempt, task_set, exc, wall=wall,
                                 local={"harness": driver_id, "harness_kind": driver["kind"],
                                        "candidate": candidate_id}, replay_of=replay_of,
                                 judge=_judge_summary(verdict) if verdict else None)
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
                     and not execution["launcher_kill"], "start_error": execution["start_error"],
                     **({"harness_executable": {k: exe[k] for k in ("env", "package", "version")}}
                        if exe else {})}
            if contamination:  # undecided for this path, never accepted; the verdict stays on record
                unknown["contaminated"] = _CONTAMINATED
            if execution["step_limit_hit"]:  # over the 40-step bound: never accepted
                unknown["local.step_limit"] = "the step bound was exceeded: the attempt is refused"
            record = {"record_type": "attempt", "task": _task_ref(task, task_set), "path": path,
                      "segment": "local", "attempt": attempt, "judge": _judge_summary(verdict),
                      "local_outcome": ("contaminated" if contamination else
                                        "accepted" if verdict["verdict"] == "ACCEPTED"
                                        and not execution["step_limit_hit"] else "refused"),
                      **({"contaminated": True, "contamination": _contamination(contamination),
                          "outcome": "contaminated"} if contamination else {}),
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
        if self._sandboxed(driver):  # a profile that cannot be generated must not burn a reservation
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
            stream_log = self.state_dir / "streams" / f"{self.envelope['campaign_id']}-{session_id}.jsonl"
            execution = execute_driver(
                driver, values, workdir=bundle, scratch=scratch, stream_log=stream_log,
                max_seconds=budget, max_steps=None, sandbox=self._sandboxed(driver), deny_read=deny,
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
        refusal = tool_allowlist_refusal(driver, execution["stream"])
        execution["contamination"] = self._audit(stream_log, bundle, scratch)  # refused or not
        if refusal:  # money is settled above; the record is refused (a tool error, never a verdict)
            raise ToolsetRefused(refusal, execution["contamination"])
        return execution, tokens, reason

    def _review(self, task: Mapping[str, Any], patch: bytes, label: str
                ) -> tuple[str | None, str, dict[str, Any], dict[str, int | None] | None, str | None]:
        """Independent review of ``patch`` on a fresh bundle: ``(verdict, findings, ...)``. An
        unreadable review is ``None`` (unknown), never a failed one. The patch's ``.claude/`` files are
        not applied to the reviewer's bundle (the reviewer's host would load them as its project
        settings); ``execution["review_excluded"]`` names them."""
        patch, excluded = _without_claude_dirs(patch)
        bundle, attempt_dir = self._bundle(task, f"{label}-review", patch)
        try:
            execution, tokens, reason = self.cloud_execution(
                "reviewer", REVIEWER_DRIVER, bundle, attempt_dir, "review")
            execution["review_excluded"] = excluded
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
                    **({"contaminated": True, "contamination": _contamination(state["contamination"])}
                       if state.get("contamination") else {}),
                    **({"review_excluded": state["review_excluded"]} if state.get("review_excluded")
                       else {}),
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
                    fault: CandidateFault | None = None
                    try:
                        patch = self._patch_of(bundle)
                    except CandidateFault as exc:  # the arm's doing: refused, the round is judged
                        fault = exc
                    if execution["contamination"]:  # undecided for this path: no verdict, no review
                        state["contamination"] = execution["contamination"]
                        state["outcome"] = "contaminated"
                        unknown["contaminated"] = _CONTAMINATED
                    elif fault is not None:
                        verdict = _fault_verdict(fault)
                    else:
                        verdict = self._judge(task, bundle)
                finally:
                    self._discard(bundle, attempt_dir)
                if verdict and verdict["verdict"] == "ACCEPTED":
                    review, findings, rev_exec, rev_tokens, rev_reason = self._review(
                        task, patch, f"{path}-{index}")
                    state["seconds"] += rev_exec["wall_seconds"]
                    state["review_excluded"] = rev_exec.get("review_excluded")
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
                    if rev_exec["contamination"]:  # the review itself touched sensitive paths
                        state.update(contamination=rev_exec["contamination"], outcome="contaminated",
                                     accepted=None)
                        unknown["contaminated"] = "the reviewer's " + _CONTAMINATED[len("the arm's "):]
                emit()  # inside the try: a cut before or after the write never loses the record
            except CapReached:  # the cut round is recorded even when nothing was spent in it: the
                state["outcome"] = "stopped_by_cap"  # task is not decided (never a refusal)
                emit()
                raise
            except BaseException as exc:  # tool failure, Ctrl-C, SIGTERM, anything: never silent
                self._tool_error(  # a verdict or a review already received stays on the record
                    task, path, segment, first_attempt + index, task_set, exc, wall=state["seconds"],
                    sessions=self.sessions[mark:], by_role=by_role, by_model=models, replay_of=again,
                    judge=_judge_summary(verdict) if verdict else None,
                    review={"rounds": len(state["rounds"]), "verdicts": state["rounds"]}
                    if state["rounds"] else None)
                raise
            outcome = state["outcome"]
            if outcome in ("accepted", "review_unreadable", "contaminated"):
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
            # a takeover is owed after a refused local attempt, a blocked review or a contaminated LOCAL
            # attempt; a contaminated REVIEW leaves the task undecided with no takeover (as at first launch)
            if state == "decided" and (info.get("outcome") in ("local_refused", "review_block") or (
                    info.get("outcome") == "contaminated" and info.get("local_outcome") == "contaminated")):
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
                if rev_exec.get("review_excluded"):
                    local["review_excluded"] = rev_exec.get("review_excluded")
                local["premium"] = {"by_role": {"reviewer": _classes(rev_tokens)},
                                    "by_model": rev_exec["by_model"], "billing_total": total}
                if rev_tokens is None:
                    local["unknown"]["premium.reviewer"] = rev_reason or "unknown"
                local["accepted"] = {"PASS": True, "BLOCK": False, None: None}[review]
                local["outcome"] = {"PASS": "accepted", "BLOCK": "review_block",
                                    None: "review_unreadable"}[review]
                if review is None:
                    local["unknown"]["review"] = "review_unreadable"
                if rev_exec["contamination"]:  # the review touched sensitive paths: undecided
                    local.update(contaminated=True, accepted=None, outcome="contaminated",
                                 contamination=_contamination(rev_exec["contamination"]))
                    local["unknown"]["contaminated"] = "the reviewer's " + _CONTAMINATED[len("the arm's "):]
                takeover = review == "BLOCK" and not rev_exec["contamination"]
            except BaseException as exc:  # cap, tool failure or interruption during the review
                cut, spent = _cut(exc), self.sessions[mark:]
                local.update(outcome=cut, cloud_sessions=spent, cloud_executions=len(spent))
                if cut != "stopped_by_cap":
                    local["status"], local["reason"] = cut, (str(exc) or type(exc).__name__)[:200]
                    local["unknown"][cut] = local["reason"]
                if spent or cut == "interrupted":  # a started review of unread cost: never 0
                    local["premium"]["billing_total"] = None
                if getattr(exc, "contamination", None):  # a refused review that also touched paths
                    local.update(contaminated=True, accepted=None, outcome="contaminated",
                                 contamination=_contamination(exc.contamination))
                    local["unknown"]["contaminated"] = "the reviewer's " + _CONTAMINATED[len("the arm's "):]
                self._emit(local)
                raise
        else:
            local["outcome"] = "contaminated" if local.get("contaminated") else "local_refused"
        self._emit(local)
        if takeover:  # a failed local attempt is neither a failure nor an escalation (ADR-0015)
            records += self.cloud_path(task, "C", task_set, segment="takeover", first_attempt=1)
        return records

    # ---- orchestration
    def preflight(self, candidate_id: str) -> dict[str, Any]:
        model = self.campaign["candidates"][candidate_id]["model"]
        result = preflight(self.campaign, model, self.preflight_run(candidate_id), self.disk_free_gib,
                           self.campaign["candidates"][candidate_id])
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
        self._check_harness(harness_id)  # an unresolvable harness executable costs nothing
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
                        record["accepted"] = (None if record.get("contaminated")
                                              else record["local_outcome"] == "accepted")
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
            for harness in dict.fromkeys(([harness_id] if "C" in paths else [])
                                         + (["neutral_harness"] if "N" in paths else [])):
                self._check_harness(harness)
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
                            rec["accepted"] = (None if rec.get("contaminated")
                                               else rec["local_outcome"] == "accepted")
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
        """The compared candidate must be the one the pre-registered screening rule selected. The
        screening results are those of THIS campaign id (same state directory and file). When there are
        none, a comparison of a local candidate (C or N) is refused, unless ``screening_campaign`` names
        another campaign id whose results (read-only, same campaign config, same dry-run nature) hold a
        completed screening that selected this candidate."""
        attempts = [r for r in self.prior_records if r.get("record_type") == "attempt"]
        if not any(r["path"] == "S" for r in attempts):
            if not self.require_screening:
                return
            attempts = self._foreign_screening()
        screening = _report_screening(self.campaign["rules"]["screening"], attempts)
        if screening["selected"] != candidate_id:
            raise RunnerError(f"candidate {candidate_id} is not the one the screening selected "
                              f"({screening['selected']}: {screening['reason']})")

    def _foreign_screening(self) -> list[dict[str, Any]]:
        """The attempts of the screening named by ``--screening-campaign`` (read, never written)."""
        other = self.screening_campaign
        if not other:
            raise RunnerError(
                f"no screening results under campaign id {self.envelope['campaign_id']}: a comparison of a "
                "local candidate needs them, or --screening-campaign <id> naming a completed screening")
        if not _ID.match(other) or other == self.envelope["campaign_id"]:
            raise RunnerError(f"--screening-campaign {other!r} must be another plain campaign id")
        path = self.state_dir / f"results-{other}.jsonl"
        if not path.is_file():
            raise RunnerError(f"no screening results at {path.name} in the state directory")
        records = _read_jsonl(path)
        attempts = [r for r in records if r.get("record_type") == "attempt"]
        if not any(r["path"] == "S" for r in attempts):
            raise RunnerError(f"{path.name} holds no screening attempt")
        for rec in records:
            if bool(rec.get("dry_run", False)) != self.dry_run or rec.get("campaign_id") != other or (
                    rec.get("campaign_sha256") != self.provenance["campaign_sha256"]):
                raise RunnerError(f"{path.name} was not produced under this campaign config and nature "
                                  "(dry-run or real): its screening cannot select a candidate here")
        return attempts

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
        stderr = proc.stderr.decode("utf-8", "replace")
        for prefix in (os.path.realpath(bundle), str(bundle)):  # an error names no work path
            stderr = stderr.replace(prefix, "<bundle>")
        raise RunnerError(f"git {args[0]} failed: {stderr[:200]}")
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
        raise CandidateFault("bundle is not quiescent: a process of the arm may have outlived it")


def _capture_patch(bundle: Path, root: str) -> bytes:
    """The candidate's change as a patch against the ROOT commit ``root`` (recorded when the bundle
    was built, so an arm that commits its work still yields it), taken BEFORE judging (the judge
    writes the protected tests into the bundle, which is then never reused). New files included."""
    _git_in(bundle, "add", "-A", "-f", "--", ".", *_PATCH_EXCLUDES)
    return _git_in(bundle, "diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv", root,
                   "--", ".", *_PATCH_EXCLUDES)


_DIFF_HEADER = re.compile(rb"^diff --git (.*)$", re.M)


def _without_claude_dirs(patch: bytes) -> tuple[bytes, list[str]]:
    """``patch`` minus every file section under a ``.claude/`` directory (at any depth, either side of
    a rename), and the (post-image) paths of the dropped sections."""
    starts = [m.start() for m in _DIFF_HEADER.finditer(patch)] if patch else []
    if not starts:
        return patch, []
    kept, dropped = [patch[:starts[0]]], []
    for begin, end in zip(starts, [*starts[1:], len(patch)]):
        section = patch[begin:end]
        header = _DIFF_HEADER.match(section).group(1)
        if b"/.claude/" in header:
            path = header.replace(b'"', b"").rsplit(b" b/", 1)[-1]
            dropped.append(path.decode("utf-8", "replace"))
        else:
            kept.append(section)
    return b"".join(kept), dropped


def _apply_patch(bundle: Path, patch: bytes) -> None:
    """Apply onto the index and the tree: the reviewer's ``git diff HEAD`` shows every change."""
    if patch:
        _git_in(bundle, "apply", "--index", "--whitespace=nowarn", "-", stdin=patch)


def _classes(tokens: Mapping[str, int | None] | None) -> dict[str, int | None] | None:
    return None if tokens is None else dict(tokens)


def _task_ref(task: Mapping[str, Any], task_set: str) -> dict[str, Any]:
    return {"pr": task["pr"], "issue": task.get("issue"), "set": task_set}


_CONTAMINATED = "the arm's tool calls touched sensitive paths or ran a forbidden command"


def _contamination(items: Sequence[str]) -> dict[str, list[str]]:
    """``audit_transcript`` hits as the record's ``paths`` and ``commands``."""
    return {"paths": [i for i in items if not i.startswith("command:")],
            "commands": [i[len("command:"):] for i in items if i.startswith("command:")]}


def _fault_verdict(fault: BaseException) -> dict[str, Any]:
    """The mechanical refusal of an attempt whose bundle the arm left unsafe to judge: the judge did
    not run, and the attempt counts as refused (decided), never as a launcher failure."""
    return {"verdict": "REFUSED", "passed": 0, "failed": 0, "errors": 0, "skipped": 0,
            "note": f"candidate_fault: {fault}"[:200]}


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
UNKNOWN_OUTCOMES = ("review_unreadable", "tool_error", "stopped_by_cap", "interrupted", "contaminated")
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
                           "void_attempts": _void_attempts(attempts, holes, ledger),
                           "contaminated": [{"path": r["path"], "task": r["task"],
                                             "candidate": (r.get("local") or {}).get("candidate"),
                                             "segment": r.get("segment"), "attempt": r.get("attempt"),
                                             "paths": (r.get("contamination") or {}).get("paths", []),
                                             "commands": (r.get("contamination") or {}).get("commands", [])}
                                            for r in attempts if r.get("contaminated")],
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


def _void_attempts(attempts: Sequence[Mapping[str, Any]], holes: Sequence[Mapping[str, Any]],
                   ledger: Sequence[Mapping[str, Any]] = ()) -> list[dict[str, Any]]:
    """Every void attempt: the records cut before a verdict, the starts a hard kill left with no
    record, and the cloud executions the ledger holds that no record names (a round killed after its
    settlement, before its record: it is replayed without ``replay_of``, so ``replayed`` is unknown),
    each with whether a replay record names it."""
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
    named = {sid for r in attempts for sid in r.get("cloud_sessions", [])}
    out += [{"path": None, "task": None, "candidate": None, "segment": None, "attempt": None,
             "outcome": "hard_kill", "reason": "cloud_started without a result record",
             "role": e.get("role"), "cloud_sessions": [e.get("session_id")], "replayed": None}
            for e in ledger if e.get("kind") == "cloud_started" and e.get("session_id") not in named]
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
        elif rec["path"] == "S" and rec.get("contaminated"):  # touched sensitive paths: undecided
            undecided[_logical_key(rec)] = {
                "candidate": rec["local"]["candidate"], "pr": rec["task"]["pr"],
                "outcome": "contaminated", "replayed": False}
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
    if (table or undecided_tasks) and not complete:  # never select before every task has a record
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
            "contaminated": not accepted and any(r.get("contaminated") for r in items),
            "review_rounds": sum(r["review"]["rounds"] for r in items),
            "wall_seconds": sum(r["wall_seconds"] for r in items),
            "cloud_executions": sum(r["cloud_executions"] for r in items),
            "premium_billing_tokens": _sum_known([r["premium"]["billing_total"] for r in items]),
            "local_attempt_ok": next((r["local_outcome"] == "accepted" for r in items
                                      if r["segment"] == "local" and not r.get("contaminated")
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
        reference_contaminated = any(t["contaminated"] for t in a_tasks)  # no clean reference to compare to
        quality = ("unavailable" if not shared else "fail" if failed else "unavailable" if undecided
                   or reference_contaminated else _verdict(sum(t["review_rounds"] for t in x_tasks)
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
            p.add_argument("--screening-campaign", default=None,
                           help="campaign id of a completed screening (results read-only in --state-dir) "
                                "when this campaign id holds none; C and N need one or the other")
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
        result = preflight(campaign, model, run, (lambda: 1e6) if args.dry_run else None,
                           campaign["candidates"][args.candidate])
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
            screening_campaign=getattr(args, "screening_campaign", None),
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
