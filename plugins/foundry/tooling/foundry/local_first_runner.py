"""PAT-108: comparison launcher of the PAT-19 local-first qualification (protocol v1).

A campaign tool, not a product role (FOUNDRY-ADR-0007): it never touches routing, mappings, roles
or defaults and promotes nothing. It plays one task per path (A current cloud, B economy cloud,
C hybrid = one bounded local attempt then cloud validation, with the A path taking over) from the
PAT-107 bundles, judged by ``local_first_corpus``; premium tokens are read from the host session
logs (``cost_attribution``, FOUNDRY-ADR-0015) and attached to an execution by the session id the
launcher itself hands to the driver.

Protocol v2 (PAT-114, ``screen-exploration`` and ``compare-exploration``, config schema v2) reuses the same
machinery for a read-only explorer judged on localization (``local_first_exploration``) and then on its
downstream effect; see ``docs/qualification/pat-19-protocol-v2.md``.

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
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Collection, Mapping, NamedTuple, Sequence

from foundry import cost_attribution as ca
from foundry import local_first_corpus as lfc
from foundry import local_first_exploration as lfe

CAMPAIGN_SCHEMA = "foundry.local-first-campaign.v1"
CAMPAIGN_SCHEMA_V2 = "foundry.local-first-campaign.v2"  # protocol v2 (PAT-114): read-only exploration
PROTOCOL_V3 = "pat-19-protocol-v3"  # protocol v3 (PAT-116): a v2-schema campaign with a wider budget, 2 candidates
V3_CANDIDATES = ("qwen3.6-35b-a3b-mlx-4bit", "qwen3-coder-30b-a3b-mlx-4bit")
V3_BOUNDS = {"explorer_max_steps": 60, "explorer_max_seconds": 900}
PROTOCOL_V4 = "pat-19-protocol-v4"  # protocol v4 (PAT-121): v3 + feedback and private root, fixed candidate, A and L
V4_CANDIDATES = ("qwen3.6-35b-a3b-mlx-4bit",)
V4_FEEDBACK = {"hidden_test_failures": True, "max_failures": 20, "max_message_chars": 300, "max_name_chars": 200}
V4_ARMS = ("A", "L")  # no Haiku arm E in protocol v4
V4_KEYS = "correction_feedback, isolation.private_attempt_root, exploration.fixed_candidate and " \
          "exploration.comparison_task_group"
# PAT-126: protocol v5 (frozen 2026-10-08, before any trial of its campaign, after four pilots) and its pilot. The pilot is the same instrument on one task, under a
# protocol name and a campaign id of its own, so that its records can never be read as, or mixed with, the campaign's.
PROTOCOL_V5 = "pat-19-protocol-v5"
PROTOCOL_V5_PILOT = "pat-19-protocol-v5-pilot"
V5_PROTOCOLS = (PROTOCOL_V5, PROTOCOL_V5_PILOT)
FROZEN_PROTOCOLS = ("pat-19-protocol-v1", "pat-19-protocol-v2", "pat-19-protocol-v3", PROTOCOL_V4, PROTOCOL_V5)
V5_TASKS = (26, 38, 25, 42, 33, 37, 30, 83, 27, 24, 48, 19)  # the six v3 comparison tasks, then the six v3 screening ones
V5_PILOT_TASKS = (27,)
V5_TASK_SET = {PROTOCOL_V5: "pat-19-v5", PROTOCOL_V5_PILOT: "pat-19-v5-pilot"}  # label of ``task.set`` in the records
V5_CLAUDE_CODE = "2.1.285"  # the only Claude Code version observed for the audit's assumptions and the sandbox trials
V5_PAIRED_DECIDED_MIN = 9  # rules.exploration_comparison.paired_decided_min: tasks decided in both arms (of 12)
V5_ALLOW_READ_HOME = (".config/git/ignore",)  # the one home file a v5 cloud shell may read (git's default excludes)
COMPARISON_SET = "comparison"  # ``task.set`` of the comparison records of v1 to v4
# PAT-126 (pilot 2): ``isolation.audit_policy``, a protocol v5+ key. Where the native sandbox is observed to be in force
# (``BARRIER_OBSERVED``) the operating system is the barrier and the lexical audit a journal: a call the host refused did
# not run (it counts for nothing), and a SHELL finding under a root the OS denies to that execution, or at a directory the
# audit cannot name, is journaled (``audit.journal``), not a contamination.
AUDIT_POLICY_KEY = "audit_policy"
AUDIT_POLICY = "journal_under_observed_barrier"
# Whole-call refusals of the host, the shapes the trial tool knows, each anchored as the WHOLE error of the call: the
# permission-mode denial for any tool; the two permission-rule messages for the FILE tools only (a Bash command that ran,
# failed and printed such a sentence was not refused).
_DONT_ASK = re.compile(r"\APermission to use \w+ has been denied because Claude Code is running in don't ask mode", re.I)
_RULE_DENIED = re.compile(r"\A(?:File is in a directory that is denied by your permission settings\.?"
                          r"|[^\n]{0,500}setting blocks reads outside the working directories\.?)\s*\Z")
_FILE_TOOLS = frozenset({"Read", "Edit", "Write", "Glob", "Grep"})


def _refused_call(tool: str, text: str) -> bool:
    return bool(_DONT_ASK.search(text) or (tool in _FILE_TOOLS and _RULE_DENIED.match(text)))
# PAT-123: ``isolation.audit_revision`` 2 (a protocol after v4 only) follows the working directory of a command
# line, attributes a flag of the reviewer's session to the review, and records a path that does not exist apart
AUDIT_REVISION = 2
# PAT-128: ``isolation.audit_absent_path_forms``, accepted only under a protocol after v5 (never v1 to v5, whose verdicts
# stay as they were counted). With it the audit also recognises the answer ``Path '<given path>' not found`` (the form the
# omp ``read`` tool uses; v5 observed it on two explorations) as an absent path, apart from the hits, like ``Path not found:``.
ABSENT_FORMS_KEY = "audit_absent_path_forms"
ABSENT_FORMS = "quoted_path"
# PAT-123/PAT-124: what a record of audit revision 2 that ran to completion says of the barrier (Claude Code's
# native Bash sandbox). The launcher never observes the operating system enforce it: the value says only what it
# verified, from weakest to strongest; no value reads "confined" (a record cut by an error carries no ``audit``).
AUDIT_BARRIER = "not_verified"  # no native sandbox configured, or nothing observed (also a local arm)
BARRIER_SETTINGS = "settings_transmitted"  # ``--settings`` was passed to a process that started
# ... and the stream names ONE Claude Code version in its ``init`` event(s), the permission mode asked for when it
# names one, and ends on a ``result`` event that is not an error (the session ran to its end)
BARRIER_OBSERVED = "settings_transmitted_version_observed"
BARRIERS = (AUDIT_BARRIER, BARRIER_SETTINGS, BARRIER_OBSERVED)
# PAT-124 (a protocol after v4 only): ``isolation.cloud_native_sandbox`` runs every cloud driver with Claude Code's
# native Bash sandbox (see ``native_sandbox_settings``). The permission mode is then ``dontAsk``: an edit outside the
# attempt directory would prompt, so it is denied (under ``bypassPermissions`` the Write tool created a file next door:
# seen by the coordinator's manual trial T1 of 2026-10-08 only, evidence not committed, not the launcher's settings).
NATIVE_SANDBOX_KEY = "cloud_native_sandbox"
NATIVE_PERMISSION_MODE = "dontAsk"
NATIVE_TRIAL_MARKER = ".pat19-native-sandbox-trial"  # in the state directory of a trial, never of a campaign
NATIVE_GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}  # added to the child's environment
# Flags a cloud driver may not carry under the key, bare or as ``--flag=value``: they set the mode or the settings
# themselves, or widen what the session may read, write or run beyond the settings the launcher passes. Exact names:
# ``--setting-sources``, ``--strict-mcp-config`` and ``--disallowedTools`` are other flags and stay accepted.
NATIVE_REFUSED_FLAGS = ("--settings", "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions",
                        "--add-dir", "--allowedTools", "--allowed-tools", "--mcp-config", "--plugin-dir")
NATIVE_TRIAL_KEY = "_native_trial"  # set in memory by the trial verb only; refused in a config file
_PROTOCOL_AFTER_V4 = re.compile(r"pat-19-protocol-v(?:[5-9]|[1-9]\d+)|pat-19-protocol-v5-pilot")
WORK_REMAINS_LINE = "pat19-v3: work_remains={}"  # printed on stdout by a one-task-per-launch launch
ENVELOPE_SCHEMA = "foundry.local-first-envelope.v1"
RESULT_SCHEMA = "foundry.local-first-result.v1"
# ``screen`` and ``compare`` are the protocol v1 modes; the two ``*_exploration`` modes are protocol v2.
EXPLORE_MODES = ("screen_exploration", "compare_exploration")
MODES = ("screen", "compare", *EXPLORE_MODES)
CLOUD_MODES = ("compare", "compare_exploration")  # the only modes that may start a cloud execution
DRIVER_KINDS = ("local_harness", "neutral_harness", "cloud_implementer", "cloud_reviewer",
                "local_explorer", "cloud_explorer")
LOCAL_KINDS = ("local_harness", "neutral_harness", "local_explorer")
CLOUD_KINDS = ("cloud_implementer", "cloud_reviewer", "cloud_explorer")
PATHS = ("A", "B", "C", "N")
# Protocol v2 paths: ``XS`` = exploration screening attempt; ``A`` (no report), ``L`` (report of the
# retained local explorer) and ``E`` (report of the cloud economy explorer) in the comparison.
EXPLORE_ARMS = ("A", "L", "E")
IMPLEMENTER_DRIVER = {"A": "cloud_implementer_current", "B": "cloud_implementer_economy",
                      "L": "cloud_implementer_current", "E": "cloud_implementer_current"}
REVIEWER_DRIVER = "cloud_reviewer"
LOCAL_EXPLORER_DRIVER = "local_explorer"
CLOUD_EXPLORER_DRIVER = "cloud_explorer_economy"
BOUND_HIT = "the exploration was cut by a bound (time or steps): refused, whatever a draft contains"
BUNDLE_MODIFIED = "the explorer modified the bundle (it is a read-only role): exploration refused"
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
# `lms version` prints only the CLI commit; the app version is in the app bundle's plist.
LM_STUDIO_VERSION_COMMAND = ("plutil", "-extract", "CFBundleShortVersionString", "raw",
                             "/Applications/LM Studio.app/Contents/Info.plist")
# The only external commands the preflight and the machine probes may run (read-only).
READ_ONLY_COMMANDS = frozenset({
    ("sysctl", "-n", "machdep.cpu.brand_string"), ("sysctl", "-n", "hw.memsize"),
    ("sysctl", "-n", "vm.swapusage"), ("sw_vers", "-productVersion"), ("memory_pressure",),
    LM_STUDIO_VERSION_COMMAND, ("lms", "runtime", "ls"), ("lms", "ps", "--json"),
    ("ps", "-axo", "rss=,command=")})  # the v2 dedicated-machine check uses only these same two probes
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


class FoundryStateChanged(RunnerError):
    """The real Foundry registry changed during a cloud execution (PAT-120). The attempt is not accepted
    (recorded undecided, never replayed; the ledger settles the execution, the record's billing total is
    null) and the campaign stops; carries the fingerprints (never the content) and the contamination
    audit of the same stream."""

    def __init__(self, before: Mapping[str, str], after: Mapping[str, str],
                 contamination: Sequence[str] = ()):
        changed = sorted(k for k in after if after[k] != before.get(k))
        super().__init__("the real Foundry registry changed during a cloud execution "
                         f"({', '.join(changed)}: " + "; ".join(
                             f"{k} {before.get(k)} -> {after[k]}" for k in changed) + ")")
        self.fingerprints = {"before": dict(before), "after": dict(after)}
        self.contamination = list(contamination)


STATE_CHANGED_STOP = "foundry_state_changed"  # stop reason; exit code 4 of the launcher


def _file_fingerprint(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return "absent"
    except OSError as exc:  # unreadable is a distinct, comparable state, never a silent "unchanged"
        return f"unreadable:{type(exc).__name__}"


def registry_fingerprints(host_env: Mapping[str, str]) -> dict[str, str]:
    """sha256 of the bytes of the REAL Foundry ``registry.json`` (or ``absent``), read-only, at BOTH
    places a Foundry process of the host could use: the host's ``FOUNDRY_DATA`` (when it exports one)
    and ``<HOME>/.config/foundry`` (``registry.data_dir`` falls back to it when the variable is unset)."""
    home = host_env.get("HOME") or os.path.expanduser("~")
    places = {"home_config": Path(home) / ".config" / "foundry" / "registry.json"}
    if host_env.get("FOUNDRY_DATA"):
        places["host_foundry_data"] = Path(host_env["FOUNDRY_DATA"]) / "registry.json"
    return {name: _file_fingerprint(path) for name, path in places.items()}


class PreflightRefused(RunnerError):
    def __init__(self, refusals: Sequence[str]):
        super().__init__("preflight refused: " + ", ".join(refusals))
        self.refusals = list(refusals)


# ------------------------------------------------------------------------ campaign config

def _check_native_sandbox(path: Any, data: Mapping[str, Any], later: bool) -> None:
    """PAT-124: ``isolation.cloud_native_sandbox`` is a boolean accepted only under a protocol after v4; audit
    revision 2 is accepted only together with it (the audit is a journal, the native sandbox the barrier); with it
    every cloud driver reads a Claude Code stream (the barrier is read from it) and is not wrapped in
    ``sandbox-exec`` (Claude Code cannot authenticate under it: AGENTS.md R6)."""
    iso = data.get("isolation") or {}
    if NATIVE_SANDBOX_KEY in iso:
        if type(iso[NATIVE_SANDBOX_KEY]) is not bool:
            raise RunnerError(f"{path}: isolation.{NATIVE_SANDBOX_KEY} must be a boolean")
        if not later:
            raise RunnerError(f"{path}: isolation.{NATIVE_SANDBOX_KEY} is accepted only under a protocol after v4 "
                              f"(pat-19-protocol-v5 or later), not under {data.get('protocol')!r}")
    native = iso.get(NATIVE_SANDBOX_KEY) is True
    if iso.get("audit_revision", 1) == AUDIT_REVISION and not native:
        raise RunnerError(f"{path}: isolation.audit_revision {AUDIT_REVISION} is accepted only together with "
                          f"isolation.{NATIVE_SANDBOX_KEY} true (the lexical audit is a journal, not a barrier)")
    if native:
        for name, driver in data["drivers"].items():
            cloud = driver["kind"] in CLOUD_KINDS
            clash = [a for a in driver["argv"] if cloud and (
                a.startswith("--permission-mode=") or a.split("=", 1)[0] in NATIVE_REFUSED_FLAGS)]
            if cloud and driver["argv"].count("--permission-mode") > 1:  # only ONE separated pair is replaced
                clash.append("--permission-mode more than once")
            if clash:
                raise RunnerError(f"{path}: isolation.{NATIVE_SANDBOX_KEY}: cloud driver {name} may not carry "
                                  f"{clash[0].split('=')[0]} (the launcher sets the mode and the settings itself)")
            if driver["kind"] in CLOUD_KINDS and (driver.get("sandbox", True) is not False
                                                  or (driver.get("stream") or {}).get("format")
                                                  != "claude-stream-json"):
                raise RunnerError(f"{path}: isolation.{NATIVE_SANDBOX_KEY} needs cloud driver {name} to declare "
                                  "sandbox false and a claude-stream-json stream")


def load_campaign(path: Path) -> dict[str, Any]:
    """The frozen campaign config. Fails closed on any missing piece."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") not in (CAMPAIGN_SCHEMA, CAMPAIGN_SCHEMA_V2):
        raise RunnerError(f"{path}: unexpected schema")
    if NATIVE_TRIAL_KEY in data:  # PAT-124: the trial verb sets it in memory, after this load
        raise RunnerError(f"{path}: {NATIVE_TRIAL_KEY} is not a configuration key (the trial verb sets it in memory)")
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
    if data.get("protocol") == PROTOCOL_V4 and data["schema"] != CAMPAIGN_SCHEMA_V2:
        raise RunnerError(f"{path}: protocol v4 requires the exploration schema {CAMPAIGN_SCHEMA_V2}")
    if data.get("protocol") in V5_PROTOCOLS and data["schema"] != CAMPAIGN_SCHEMA_V2:
        raise RunnerError(f"{path}: protocol v5 requires the exploration schema {CAMPAIGN_SCHEMA_V2}")
    later = bool(_PROTOCOL_AFTER_V4.fullmatch(str(data.get("protocol"))))
    # PAT-126: the v4 keys are also accepted by a protocol after v4 (v5); the two comparison-list keys only by it
    if not later and data.get("protocol") != PROTOCOL_V4 and (
            "correction_feedback" in data or "private_attempt_root" in iso
            or {"fixed_candidate", "comparison_task_group"} & set(data.get("exploration") or {})):
        raise RunnerError(f"{path}: {V4_KEYS} are accepted only under protocol {PROTOCOL_V4} "
                          "(isolation.private_attempt_root also under pat-19-protocol-v5 or later)")
    if not later and "paired_decided_min" in ((data.get("rules") or {}).get("exploration_comparison") or {}):
        raise RunnerError(f"{path}: rules.exploration_comparison.paired_decided_min is accepted only under a protocol "
                          f"after v4 (pat-19-protocol-v5 or later), not under {data.get('protocol')!r}")
    if not later and {"comparison_tasks", "comparison_task_set"} & set(data.get("exploration") or {}):
        raise RunnerError(f"{path}: exploration.comparison_tasks and exploration.comparison_task_set are accepted "
                          f"only under a protocol after v4 (pat-19-protocol-v5 or later), not under "
                          f"{data.get('protocol')!r}")
    if type(iso.get("private_attempt_root", False)) is not bool:
        raise RunnerError(f"{path}: isolation.private_attempt_root must be a boolean")
    if iso.get("audit_revision", 1) not in (1, AUDIT_REVISION) or type(iso.get("audit_revision", 1)) is not int:
        raise RunnerError(f"{path}: isolation.audit_revision must be 1 or {AUDIT_REVISION}")
    if iso.get("audit_revision", 1) != 1 and not later:
        raise RunnerError(f"{path}: isolation.audit_revision {AUDIT_REVISION} is accepted only under a protocol "
                          f"after v4 (pat-19-protocol-v5 or later), not under the frozen protocol "
                          f"{data.get('protocol')!r}")
    _check_native_sandbox(path, data, later)
    if AUDIT_POLICY_KEY in iso:  # PAT-126: after v4 only, one value, and only with the barrier it relies on
        if not later:
            raise RunnerError(f"{path}: isolation.{AUDIT_POLICY_KEY} is accepted only under a protocol after v4 "
                              f"(pat-19-protocol-v5 or later), not under {data.get('protocol')!r}")
        if iso[AUDIT_POLICY_KEY] != AUDIT_POLICY:
            raise RunnerError(f"{path}: isolation.{AUDIT_POLICY_KEY} is {AUDIT_POLICY!r} or absent")
        if iso.get(NATIVE_SANDBOX_KEY) is not True or iso.get("audit_revision") != AUDIT_REVISION:
            raise RunnerError(f"{path}: isolation.{AUDIT_POLICY_KEY} needs isolation.{NATIVE_SANDBOX_KEY} true and "
                              f"isolation.audit_revision {AUDIT_REVISION} (the operating system is the barrier it relies on)")
    if ABSENT_FORMS_KEY in iso:  # PAT-128: after v5 only (v5 and its pilot are pinned without it), one value
        if not later or data.get("protocol") in V5_PROTOCOLS:
            raise RunnerError(f"{path}: isolation.{ABSENT_FORMS_KEY} is accepted only under a protocol after v5 "
                              f"(pat-19-protocol-v6 or later), not under {data.get('protocol')!r}")
        if iso[ABSENT_FORMS_KEY] != ABSENT_FORMS:
            raise RunnerError(f"{path}: isolation.{ABSENT_FORMS_KEY} is {ABSENT_FORMS!r} or absent")
        if iso.get("audit_revision") != AUDIT_REVISION:
            raise RunnerError(f"{path}: isolation.{ABSENT_FORMS_KEY} needs isolation.audit_revision {AUDIT_REVISION}")
    _check_correction_feedback(path, data)
    _check_cloud_bash_deny(path, data)
    for cid, cand in data["candidates"].items():
        if not _ID.match(cid) or not isinstance(cand.get("model"), str):
            raise RunnerError(f"{path}: candidate {cid} needs an id and a model")
        for key, kind in (("min_context", int), ("lm_studio_key", str), ("quantization", str)):
            if key in cand and (type(cand[key]) is not kind or not cand[key] or cand[key] is True):
                raise RunnerError(f"{path}: candidate {cid}: {key} must be a non-empty {kind.__name__}")
    if data["schema"] == CAMPAIGN_SCHEMA_V2:
        _check_exploration_config(path, data)
    return data


def _check_correction_feedback(path: Path, data: Mapping[str, Any]) -> None:
    """``correction_feedback`` (PAT-121, absent = off): what a corrector learns after a judge refusal."""
    spec = data.get("correction_feedback")
    if spec is None:
        return
    if not isinstance(spec, dict) or set(spec) - {"hidden_test_failures", "max_failures", "max_message_chars",
                                                   "max_name_chars", "note"} or type(spec.get("hidden_test_failures")) is not bool:
        raise RunnerError(f"{path}: correction_feedback needs a boolean hidden_test_failures (and "
                          "max_failures, max_message_chars, max_name_chars)")
    if spec["hidden_test_failures"] and not all(
            type(spec.get(k)) is int and spec[k] > 0 for k in ("max_failures", "max_message_chars", "max_name_chars")):
        raise RunnerError(f"{path}: correction_feedback.max_failures, max_message_chars and max_name_chars "
                          "must be positive integers")


def _check_exploration_config(path: Path, data: Mapping[str, Any]) -> None:
    """What a protocol v2 campaign must declare (PAT-114): the explorer bounds, the explore prompt, the
    pre-registered rules, the dedicated-machine thresholds and an explorer driver of each kind whose
    argv cannot silently lose its read-only tool set."""
    for key in ("explorer_max_seconds", "explorer_max_steps"):
        if type(data["bounds"].get(key)) is not int or data["bounds"][key] <= 0:
            raise RunnerError(f"{path}: bounds.{key} must be a positive integer")
    if not isinstance(data["prompts"].get("explore"), str):
        raise RunnerError(f"{path}: prompts.explore missing")
    rules = data["rules"]
    shape = (("exploration_screening", ("tasks", "file_precision_min", "retained_function_recall_min")),
             ("exploration_comparison", ("tasks", "premium_per_accepted_ratio_max", "extra_swap_gib_max")))
    for name, keys in shape:
        if not isinstance(rules.get(name), dict) or not all(
                isinstance(rules[name].get(k), (int, float)) and not isinstance(rules[name][k], bool)
                for k in keys):
            raise RunnerError(f"{path}: rules.{name} needs numeric {', '.join(keys)}")
    machine = data.get("dedicated_machine")
    if not isinstance(machine, dict) or not all(
            isinstance(machine.get(k), (int, float)) and not isinstance(machine.get(k), bool)
            for k in ("max_other_process_rss_gib", "min_free_percent")):
        raise RunnerError(f"{path}: dedicated_machine needs max_other_process_rss_gib and min_free_percent")
    patterns = machine.get("allowed_command_patterns")
    if not isinstance(patterns, dict) or not patterns or not all(
            isinstance(v, list) and all(isinstance(p, str) for p in v) for v in patterns.values()):
        raise RunnerError(f"{path}: dedicated_machine.allowed_command_patterns needs lists of patterns")
    for pats in patterns.values():
        for pattern in pats:
            try:
                re.compile(pattern)
            except re.error:
                raise RunnerError(f"{path}: dedicated_machine pattern {pattern!r} is not a regex") from None
    limits = (data.get("exploration") or {}).get("report_render_limits")
    if not isinstance(limits, dict) or not all(
            type(limits.get(k)) is int and limits[k] > 0 for k in ("max_files", "max_functions",
                                                                    "max_rationale_chars")):
        raise RunnerError(f"{path}: exploration.report_render_limits needs positive integers")
    if limits["max_functions"] != lfe.MAX_FUNCTIONS:
        raise RunnerError(f"{path}: report_render_limits.max_functions must be {lfe.MAX_FUNCTIONS} "
                          "(the function cap of the protocol)")
    frozen = rules["exploration_screening"].get("candidates")
    if not isinstance(frozen, list) or not frozen or len(set(frozen)) != len(frozen) or not set(frozen) <= set(
            data["candidates"]):
        raise RunnerError(f"{path}: rules.exploration_screening.candidates must list the frozen candidates "
                          "(declared candidates, once each)")
    if type((data.get("exploration") or {}).get("one_task_per_launch", False)) is not bool:
        raise RunnerError(f"{path}: exploration.one_task_per_launch must be a boolean")
    if data.get("protocol") == PROTOCOL_V3:  # the v3 coordinates are pinned (a drift would be a v4)
        if tuple(frozen) != V3_CANDIDATES or tuple(data["candidates"]) != V3_CANDIDATES:
            raise RunnerError(f"{path}: protocol v3 freezes exactly the candidates {list(V3_CANDIDATES)}")
        if any(data["bounds"][k] != v for k, v in V3_BOUNDS.items()):
            raise RunnerError(f"{path}: protocol v3 freezes the explorer bounds at {V3_BOUNDS}")
        if data["exploration"].get("one_task_per_launch") is not True:
            raise RunnerError(f"{path}: protocol v3 requires exploration.one_task_per_launch true "
                              "(the model is reloaded before each task)")
    group, fixed = ((data.get("exploration") or {}).get(k) for k in ("comparison_task_group", "fixed_candidate"))
    if group not in (None, "screening", "comparison") or (fixed is not None and fixed not in data["candidates"]):
        raise RunnerError(f"{path}: exploration.comparison_task_group is screening or comparison and "
                          "exploration.fixed_candidate a declared candidate")
    listed, label = ((data.get("exploration") or {}).get(k) for k in ("comparison_tasks", "comparison_task_set"))
    if (listed is None) != (label is None):
        raise RunnerError(f"{path}: exploration.comparison_tasks and exploration.comparison_task_set go together")
    if listed is not None:  # PAT-126: an explicit, ordered list of corpus PRs and the label of its records
        if group is not None or not isinstance(listed, list) or not listed or len(set(listed)) != len(listed) \
                or not all(type(n) is int and n > 0 for n in listed):
            raise RunnerError(f"{path}: exploration.comparison_tasks is a non-empty list of distinct PR numbers, "
                              "and replaces exploration.comparison_task_group")
        if not isinstance(label, str) or not _ID.match(label) or label in (COMPARISON_SET, "screening"):
            raise RunnerError(f"{path}: exploration.comparison_task_set is a plain identifier of its own "
                              "(not 'comparison' nor 'screening': the labels of the earlier protocols)")
    if data.get("protocol") in V5_PROTOCOLS:  # the v5 coordinates are pinned (a drift would be a v6)
        _check_v5_pins(path, data, frozen, fixed, listed, label)
    if data.get("protocol") == PROTOCOL_V4:  # the v4 coordinates are pinned (a drift would be a v5)
        if tuple(frozen) != V4_CANDIDATES or tuple(data["candidates"]) != V4_CANDIDATES or fixed != V4_CANDIDATES[0]:
            raise RunnerError(f"{path}: protocol v4 freezes exactly the candidate {list(V4_CANDIDATES)}")
        if any(data["bounds"][k] != v for k, v in V3_BOUNDS.items()) or data["bounds"]["max_correction_rounds"] != 2:
            raise RunnerError(f"{path}: protocol v4 freezes the explorer bounds at {V3_BOUNDS} and 2 corrections")
        if (data.get("exploration") or {}).get("one_task_per_launch") is not True or group != "screening":
            raise RunnerError(f"{path}: protocol v4 requires one_task_per_launch true and the six tasks of the "
                              "v3 screening as the comparison set (comparison_task_group screening)")
        feedback = data.get("correction_feedback") or {}
        if {k: feedback.get(k) for k in V4_FEEDBACK} != V4_FEEDBACK or not (
                data.get("isolation") or {}).get("private_attempt_root"):
            raise RunnerError(f"{path}: protocol v4 requires correction_feedback {V4_FEEDBACK} and "
                              "isolation.private_attempt_root true")
    spec = (data.get("exploration") or {}).get("ground_truth")
    if not isinstance(spec, dict) or not all(isinstance(spec.get(k), str) and spec[k] for k in ("file", "sha256")):
        raise RunnerError(f"{path}: exploration.ground_truth needs file and sha256")
    try:
        lfe.load_truth_set(Path(path).parent, spec)  # the frozen truth cannot drift: sha256 verified at load
    except lfe.ExplorationError as exc:
        raise RunnerError(f"{path}: {exc}") from None
    for name, driver in data["drivers"].items():
        argv = driver["argv"]
        if driver["kind"] == "local_explorer":
            flags = [a for a in argv if a == "--tools" or a.startswith("--tools")]
            tools = [a for a in flags if a.startswith("--tools=")]
            if len(flags) != 1 or len(tools) != 1 or not set(tools[0][len("--tools="):].split(",")) <= {
                    "read", "grep", "glob"}:
                raise RunnerError(f"{path}: driver {name}: a local explorer needs exactly one --tools= "
                                  "flag (one argument, no second tools flag) naming only read, grep, glob")
        elif driver["kind"] == "cloud_explorer":
            denied = argv[argv.index("--disallowedTools") + 1:] if "--disallowedTools" in argv else []
            if not {"Edit", "Write"} <= set(denied):
                raise RunnerError(f"{path}: driver {name}: a cloud explorer must deny Edit and Write")


def _check_v5_pins(path: Path, data: Mapping[str, Any], frozen: Sequence[str], fixed: Any,
                   tasks: Any, label: Any) -> None:
    """The coordinates protocol v5 (and its one-task pilot) pins at load (PAT-126): everything v4 pinned, the
    instrument keys of PAT-121/123/124, the explicit task list, the version of Claude Code and the one home file the
    cloud shell may read. The pilot differs from the campaign by its protocol name, its task list and its label."""
    protocol, expl, iso = data["protocol"], data.get("exploration") or {}, data.get("isolation") or {}
    wanted = V5_TASKS if protocol == PROTOCOL_V5 else V5_PILOT_TASKS
    if tuple(frozen) != V4_CANDIDATES or tuple(data["candidates"]) != V4_CANDIDATES or fixed != V4_CANDIDATES[0]:
        raise RunnerError(f"{path}: protocol v5 freezes exactly the candidate {list(V4_CANDIDATES)}")
    if any(data["bounds"][k] != v for k, v in V3_BOUNDS.items()) or data["bounds"]["max_correction_rounds"] != 2:
        raise RunnerError(f"{path}: protocol v5 freezes the explorer bounds at {V3_BOUNDS} and 2 corrections")
    if expl.get("one_task_per_launch") is not True:
        raise RunnerError(f"{path}: protocol v5 requires exploration.one_task_per_launch true")
    if tasks is None or tuple(tasks) != wanted or label != V5_TASK_SET[protocol]:
        raise RunnerError(f"{path}: protocol {protocol} requires exploration.comparison_tasks {list(wanted)} (in "
                          f"this order) and exploration.comparison_task_set {V5_TASK_SET[protocol]!r}")
    feedback = data.get("correction_feedback") or {}
    if {k: feedback.get(k) for k in V4_FEEDBACK} != V4_FEEDBACK:
        raise RunnerError(f"{path}: protocol v5 requires correction_feedback {V4_FEEDBACK}")
    if iso.get(AUDIT_POLICY_KEY) != AUDIT_POLICY:
        raise RunnerError(f"{path}: protocol v5 requires isolation.{AUDIT_POLICY_KEY} {AUDIT_POLICY!r}")
    if iso.get("private_attempt_root") is not True or iso.get(NATIVE_SANDBOX_KEY) is not True \
            or iso.get("audit_revision") != AUDIT_REVISION:
        raise RunnerError(f"{path}: protocol v5 requires isolation.private_attempt_root true, "
                          f"isolation.{NATIVE_SANDBOX_KEY} true and isolation.audit_revision {AUDIT_REVISION}")
    if not set(iso.get("allow_read_home", [])) <= set(V5_ALLOW_READ_HOME):
        raise RunnerError(f"{path}: protocol v5 allows reading nothing of the home but {list(V5_ALLOW_READ_HOME)} "
                          "(isolation.allow_read_home)")
    rule = data["rules"]["exploration_comparison"]
    if rule.get("paired_decided_min") != V5_PAIRED_DECIDED_MIN:
        raise RunnerError(f"{path}: protocol v5 requires rules.exploration_comparison.paired_decided_min "
                          f"{V5_PAIRED_DECIDED_MIN}")
    if rule["tasks"] != len(wanted) or rule["premium_per_accepted_ratio_max"] != 0.85:
        raise RunnerError(f"{path}: protocol v5 requires rules.exploration_comparison.tasks {len(wanted)} and "
                          "premium_per_accepted_ratio_max 0.85")
    for name, driver in data["drivers"].items():
        if driver["kind"] == "cloud_explorer":
            raise RunnerError(f"{path}: protocol v5 has no cloud explorer (no arm E): driver {name}")
        pin = driver.get("binary_version") or {}
        if driver["kind"] in CLOUD_KINDS and (pin.get("version") != V5_CLAUDE_CODE
                                              or list(pin.get("command") or ()) != ["claude", "--version"]):
            raise RunnerError(f"{path}: protocol v5 pins Claude Code {V5_CLAUDE_CODE} on every cloud driver "
                              f"(binary_version, command ['claude', '--version']): driver {name}")


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
    if (kind in CLOUD_KINDS and not fake and log.get("layout_verified") is True
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
    binary = driver.get("binary_version")
    if binary is not None:
        ok = (isinstance(binary, dict) and isinstance(binary.get("version"), str) and binary["version"]
              and isinstance(binary.get("command"), list) and binary["command"]
              and all(isinstance(a, str) and a for a in binary["command"])
              and isinstance(binary.get("pattern"), str))
        try:
            ok = ok and re.compile(binary["pattern"]).groups == 1
        except re.error:
            ok = False
        if not ok:
            raise RunnerError(f"{path}: driver {name}: binary_version needs command, pattern (one group), version")
    allowed = driver.get("allowed_tools")
    if allowed is not None and not (isinstance(allowed, list) and all(isinstance(t, str) for t in allowed)):
        raise RunnerError(f"{path}: driver {name}: allowed_tools must be a list of tool names")
    if (kind in CLOUD_KINDS and not fake and driver.get("verified") is True
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
             if d["kind"] in CLOUD_KINDS and not d.get("fake")
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
        self.appended = 0  # lines this launcher wrote (tells whether anything happened since a given line)
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
        self.appended += 1

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
        if self.mode not in CLOUD_MODES:
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
              candidate: Mapping[str, Any] | None = None, dedicated: bool = False) -> dict[str, Any]:
    """Machine facts against the frozen coordinates; refuses on any difference. Loads nothing.
    ``candidate`` (its entry of the campaign) adds the loaded-instance checks: context length,
    quantization and model key read from ``lms ps --json``. ``dedicated`` (protocol v2) adds the
    dedicated-machine admission (``lfe.check_dedicated_machine``): the observed values are recorded in
    ``facts["dedicated_machine"]`` and a refusal is named ``dedicated_machine_*``."""
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
    lms_version = fact("lm_studio_version", list(LM_STUDIO_VERSION_COMMAND))
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
    if dedicated:
        admission = lfe.check_dedicated_machine(campaign["dedicated_machine"],
                                                run(["ps", "-axo", "rss=,command="]), run(["memory_pressure"]))
        facts["dedicated_machine"] = admission["observed"]
        refusals.extend(admission["refusals"])
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
        LM_STUDIO_VERSION_COMMAND: frozen["lm_studio_version_contains"],
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
    """Whitelisted environment: no token, no agent socket; isolated HOME/XDG for a local arm (a cloud arm
    keeps the real HOME, the Claude Code OAuth identity: AGENTS.md R6). No ``FOUNDRY_*`` from the host: the
    campaign loader refuses them in ``env_allow``/``env_set``, and any that slips through is dropped here;
    the launcher alone sets ``FOUNDRY_DATA``, for the cloud drivers (``execute_driver``)."""
    names = ("PATH", "LANG", "LC_ALL") if driver.get("home", "isolated") == "isolated" else (
        "PATH", "LANG", "LC_ALL", "HOME", "LOGNAME", "USER", "TMPDIR")
    env = {n: host_env[n] for n in names if n in host_env}
    env.update({n: host_env[n] for n in driver.get("env_allow", [])
                if n in host_env and not n.startswith("FOUNDRY_")})
    if driver.get("home", "isolated") == "isolated":
        home = scratch / "home"
        env.update({"HOME": str(home), "TMPDIR": str(scratch / "tmp"), "TERM": "dumb",
                    "XDG_CONFIG_HOME": str(home / ".config"), "XDG_CACHE_HOME": str(home / ".cache"),
                    "XDG_DATA_HOME": str(home / ".local" / "share"),
                    "XDG_STATE_HOME": str(home / ".local" / "state")})
    return env


def native_sandbox_settings(*, attempt_dir: Path, work_root: Path, home: str | Path,
                            deny_read: Sequence[Path] = (), allow_read: Sequence[Path] = (),
                            allow_write: Sequence[Path] = (), protect: Sequence[Path] = ()) -> dict[str, Any]:
    """The ``--settings`` object of one cloud execution under ``isolation.cloud_native_sandbox`` (PAT-124). Pure.
    Every path is absolute and resolved (``"."`` does not designate the working directory in ``--settings``).

    The Bash sandbox (the operating system) denies the shell reads of the home and of the work root and re-allows
    the attempt directory (bundle and scratch; narrower wins), plus ``allow_read``; it writes only in the attempt
    directory, ``allow_write`` and the per-user temp directory (Claude Code's default). The file tools (Read, Edit,
    Write, Glob, Grep) are OUTSIDE that sandbox: they rely on permissions: mode ``dontAsk`` (set by
    ``execute_driver``) denies whatever would prompt, ``blockReadsOutsideWorkingDirectories`` makes a read outside the
    working directories prompt, ``additionalDirectories`` makes the attempt directory a working directory (the
    scratch is a sibling of the bundle), ``Edit``/``Read`` allow rules cover the attempt directory only (an ``Edit``
    rule governs Write too; a ``Read`` rule governs Glob and Grep), and ``Read``/``Edit`` deny rules cover the
    sensitive paths that do not contain the attempt directory. ``allowUnsandboxedCommands`` false and
    ``failIfUnavailable`` true: no command runs outside the sandbox, and none if it cannot start. ``protect`` (the
    bundle's ``.claude`` directory): denied to shell writes (``denyWrite``) and to the Edit/Write tools, so the arm
    cannot widen its own settings through the project scope that the drivers still load (``--setting-sources
    project,local``; arrays merge across scopes). A deny rule on a FILE entry (``.netrc``, ``*_history``) is emitted
    both bare and with ``/**`` (in gitignore semantics ``p/**`` covers only the content of a directory)."""
    real = os.path.realpath
    attempt, work, hm = real(attempt_dir), real(work_root), real(home)

    def covers(outer: str, inner: str) -> bool:
        return inner == outer or inner.startswith(outer.rstrip("/") + "/")

    if not os.path.isabs(str(home)) or hm == "/" or not covers(work, attempt) or attempt == work:
        raise RunnerError("the native sandbox needs an absolute home and an attempt directory inside the work root")
    extra = [real(p) for p in deny_read if not covers(hm, real(p)) and not covers(work, real(p))
             and not covers(real(p), attempt)]
    deny_sandbox = list(dict.fromkeys([hm, work, *extra]))
    deny_rules = [p for p in dict.fromkeys([hm, *(real(p) for p in deny_read), *extra]) if not covers(p, attempt)
                  and p != "/"]
    guarded = [real(p) for p in protect]

    def rule(tool: str, path: str) -> str:
        return f"{tool}(//{path.lstrip('/')}/**)"

    def rules(tool: str, path: str) -> list[str]:
        return [f"{tool}(//{path.lstrip('/')})", rule(tool, path)]

    return {
        "permissions": {
            "blockReadsOutsideWorkingDirectories": True,
            "additionalDirectories": [attempt],
            "allow": [rule("Read", attempt), rule("Edit", attempt)],
            "deny": [r for p in deny_rules for tool in ("Read", "Edit") for r in rules(tool, p)]
            + [r for p in guarded for r in rules("Edit", p)]},
        "sandbox": {
            "enabled": True, "allowUnsandboxedCommands": False, "failIfUnavailable": True,
            "autoAllowBashIfSandboxed": True,
            "filesystem": {"denyRead": deny_sandbox,
                           "allowRead": list(dict.fromkeys([attempt, *(real(p) for p in allow_read)])),
                           "allowWrite": list(dict.fromkeys([attempt, *(real(p) for p in allow_write)])),
                           **({"denyWrite": guarded} if guarded else {})}}}


def with_native_sandbox(argv: Sequence[str], settings: Mapping[str, Any]) -> list[str]:
    """``argv`` of a cloud driver with ``--permission-mode dontAsk`` (replacing the driver's own pair, if any) and
    ``--settings <json>`` appended. Appended, not inserted after the executable: a flag that follows another flag
    ends a variadic option such as ``--disallowedTools``, so nothing of the driver's own argv is swallowed or moved."""
    rest = list(argv)
    if "--permission-mode" in rest:
        at = rest.index("--permission-mode")
        del rest[at:at + 2]
    return [*rest, "--permission-mode", NATIVE_PERMISSION_MODE,
            "--settings", json.dumps(settings, sort_keys=True, separators=(",", ":"))]


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
                   allow_read: Sequence[Path] = (), workdir_writable: bool = True,
                   native_settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Run one arm command: process group killed at ``max_seconds`` (and at ``max_steps`` when
    the event stream exposes steps), stdin closed, whitelisted environment, optional sandbox.
    ``workdir_writable=False`` (protocol v2 explorers) makes the bundle READ-ONLY in the sandbox
    profile: only the attempt scratch (and ``extra_write``) is writable, the bundle stays readable.
    ``native_settings`` (PAT-124, cloud drivers under ``isolation.cloud_native_sandbox``): the argv gets
    ``--permission-mode dontAsk`` and ``--settings`` (``with_native_sandbox``) and the environment gets
    ``NATIVE_GIT_ENV`` after ``env_set``; the allow-listed variables (R6) are untouched, and the ``claude`` process
    itself is never wrapped: only its shell is sandboxed, by Claude Code."""
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
    # PAT-120: the launcher alone decides where Foundry's own state goes (never the config or the host):
    # a fresh empty directory in the attempt scratch, for the cloud drivers (a local arm has an isolated HOME).
    if driver.get("kind") in CLOUD_KINDS:  # by driver kind, not by HOME: a local arm keeps its environment
        foundry_data = scratch / "foundry-data"
        foundry_data.mkdir(parents=True, exist_ok=True)
        if any(foundry_data.iterdir()):
            raise RunnerError(f"{foundry_data} is not empty: a Foundry data dir is fresh for each execution")
        env["FOUNDRY_DATA"] = str(foundry_data)
    argv = [_substitute(a, values) for a in driver["argv"]]
    if native_settings is not None:
        argv = with_native_sandbox(argv, native_settings)
        env.update(NATIVE_GIT_ENV)
    profile_dir = None
    if sandbox:
        writable = [workdir, scratch, *extra_write] if workdir_writable else [scratch, *extra_write]
        readable = list(allow_read) if workdir_writable else [*allow_read, workdir]
        sandbox_text(driver, writable, deny_read, deny_home, readable)  # no file yet
        profile_dir = Path(os.path.realpath(tempfile.mkdtemp(prefix="foundry-sb-")))
        profile = profile_dir / "profile.sb"  # it names every denied path: the arm may not read it
        profile.write_text(sandbox_text(driver, writable, [*deny_read, profile_dir], deny_home, readable),
                           encoding="utf-8")
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


def check_binary_version(driver: Mapping[str, Any], host_env: Mapping[str, str]) -> None:
    """A harness that is not an operator-variable path (``omp``, taken from ``PATH``) is run with its
    read-only version command (``binary_version``: ``command``, ``pattern`` with one group, pinned
    ``version``); a missing executable, a failing command, an unparsable output or another version is
    refused. No-op when the driver declares nothing."""
    spec = driver.get("binary_version")
    if not spec:
        return
    command = list(spec["command"])
    exe = shutil.which(command[0], path=host_env.get("PATH") or os.defpath)
    if exe is None:
        raise RunnerError(f"{command[0]} not found on PATH: the pinned version is {spec['version']}")
    try:
        done = subprocess.run([exe, *command[1:]], capture_output=True, text=True, timeout=30, check=False,
                              env=dict(host_env), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RunnerError(f"{command[0]} version cannot be read: {exc}") from None
    match = re.search(spec["pattern"], done.stdout.strip(), re.MULTILINE) if done.returncode == 0 else None
    if match is None:
        raise RunnerError(f"{command[0]} version unreadable or unparsable, the pinned version is {spec['version']}")
    if match.group(1) != spec["version"]:
        raise RunnerError(f"{command[0]} on PATH is {match.group(1)}, the pinned version is {spec['version']}")


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
    return [(name, args) for _, name, args, _ in _tool_events(lines)]


def _tool_events(lines: Sequence[str]) -> list[tuple[str | None, str, Any, str]]:
    """``_tool_calls`` with the id of the call and the host that ran it (``claude`` or ``omp``)."""
    calls: list[tuple[str | None, str, Any, str]] = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "tool_execution_start":
            calls.append((event.get("toolCallId"), str(event.get("toolName")), event.get("args"), "omp"))
        elif event.get("type") == "assistant" and isinstance(event.get("message"), dict):
            for block in event["message"].get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    calls.append((block.get("id"), str(block.get("name")), block.get("input"), "claude"))
    return calls


_NOT_FOUND = "Path not found: "


def _missing_paths(lines: Sequence[str], quoted: bool = False) -> dict[str, str]:
    """omp tool calls that failed with ``Path not found: <path>`` (an error result of that call, naming the
    path it was given): ``{call id: path}``. Nothing was read. Another failure text, a result without an id,
    or a path the result does not name exactly as the call gave it, is not recognised (the audit keeps it).
    ``quoted`` (PAT-128, ``isolation.audit_absent_path_forms``): ``Path '<path>' not found`` is recognised the same way."""
    given = {i: args.get("path") for i, _, args, host in _tool_events(lines)
             if i and host == "omp" and isinstance(args, Mapping)}
    missing: dict[str, str] = {}
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") != "tool_execution_end" \
                or event.get("isError") is not True:
            continue
        texts = [t for k, t in _strings(event.get("result")) if k == "text"]
        call = event.get("toolCallId")
        if len(texts) == 1 and call in given and (
                (texts[0].startswith(_NOT_FOUND) and texts[0][len(_NOT_FOUND):] == given[call])
                or (quoted and isinstance(given[call], str) and texts[0] == _quoted_not_found(given[call]))):
            missing[call] = given[call]
    return missing


def _quoted_not_found(path: str) -> str:
    return f"Path '{path}' not found"


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
_ASSIGNMENT = re.compile(r"^\w+=")
_WRAPPERS = frozenset({"env", "sudo", "command", "exec", "nohup", "time", "nice", "xargs", "timeout",
                       "builtin"})
_SHELLS = frozenset({"sh", "bash", "zsh", "dash"})
_KEYWORDS = frozenset({"{", "!", "if", "then", "else", "elif", "do", "while", "until"})  # before a command
# A heredoc fed to one of these (``bash <<EOF``, ``cat <<EOF | sh``, ``source /dev/stdin <<EOF``) is RUN.
_HEREDOC_RUNNERS = _SHELLS | {"source", ".", "eval"}
# Executables an arm (local or cloud) has no business running (tracker, merge, network, other agents, keychain).
FORBIDDEN_EXECUTABLES = frozenset({"gh", "curl", "wget", "claude", "codex", "omp", "ssh", "scp", "nc",
                                   "security", "open", "npm", "launchctl", "osascript"})
FORBIDDEN_GIT = frozenset({"push", "remote", "clone", "fetch", "pull"})
_WORD_BREAK = " \t\n;&|()"  # a ``#`` right after one of these (or at the start) opens a comment
_HEREDOC = re.compile(r"<<(-?)[ \t]*((?:'[^'\n]*'|\"[^\"\n]*\"|\\?[^\s;&|<>()'\"`]+)+)")
_QUOTED_PIECES = re.compile(r"[\s'\"`;|&()]+")
_REDIRECTION = re.compile(r"^\d*(?:<<<|<<-?|>>|<>|>\||[<>])&?")


def _closing(text: str, start: int) -> int:
    """Index of the ``)`` that closes a ``$(`` whose content starts at ``start`` (quotes skipped),
    or ``len(text)`` when it is never closed."""
    depth, i = 1, start
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 1
        elif ch in "'\"":
            end = text.find(ch, i + 1)
            i = len(text) if end < 0 else end
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(text)


def _segments(command: str) -> list[list[tuple[str, str]]]:
    """Simple command segments of a shell command line, split on ``&& || ; | & ( )`` and newlines
    OUTSIDE quotes only (``grep -E "claude|codex" src`` is one segment), a ``#`` comment dropped. Each
    word is ``(neutral, raw)``: ``raw`` has its quotes removed; ``neutral`` also has what the shell does
    NOT expand neutralised for the path audit (a ``~`` inside quotes, a ``$`` inside single quotes
    become ``_``). A ``$(...)`` or a backquoted command, unquoted or inside double quotes, is run: its
    own segments come first. Not a shell parser: a command built at run time or hidden in a script is
    not seen."""
    segments: list[list[tuple[str, str]]] = []
    words: list[tuple[str, str]] = []
    neutral: list[str] = []
    raw: list[str] = []
    started = False
    i, n = 0, len(command)

    def end_word() -> None:
        nonlocal started
        if started:
            words.append(("".join(neutral), "".join(raw)))
        neutral.clear()
        raw.clear()
        started = False

    def end_segment() -> None:
        nonlocal words
        end_word()
        if words:
            segments.append(words)
        words = []

    def add(ch: str, quote: str | None) -> None:
        nonlocal started
        started = True
        raw.append(ch)
        neutral.append("_" if quote and (ch == "~" or (ch == "$" and quote == "'")) else ch)

    while i < n:
        ch = command[i]
        if ch in " \t":
            end_word()
        elif ch in "\n;&|()":
            end_segment()
        elif ch == "#" and not started:
            newline = command.find("\n", i)
            i = n if newline < 0 else newline
            continue
        elif ch == "\\":
            if i + 1 < n and command[i + 1] != "\n":
                add(command[i + 1], "\\")
            i += 2
            continue
        elif ch == "'":
            end = command.find("'", i + 1)
            end = n if end < 0 else end
            for c in command[i + 1:end]:
                add(c, "'")
            started = True
            i = end + 1
            continue
        elif command.startswith("$(", i):
            end = _closing(command, i + 2)
            segments += _segments(command[i + 2:end])
            started = True
            i = end + 1
            continue
        elif ch == "`":
            end = command.find("`", i + 1)
            end = n if end < 0 else end
            segments += _segments(command[i + 1:end])
            started = True
            i = end + 1
            continue
        elif ch == '"':
            started = True
            j = i + 1
            while j < n and command[j] != '"':
                c = command[j]
                if c == "\\" and j + 1 < n:
                    if command[j + 1] in '$`"\\':
                        add(command[j + 1], '"')
                    elif command[j + 1] != "\n":
                        add(c, '"')
                        add(command[j + 1], '"')
                    j += 2
                    continue
                if command.startswith("$(", j):
                    end = _closing(command, j + 2)
                    segments += _segments(command[j + 2:end])
                    j = end + 1
                    continue
                if c == "`":
                    end = command.find("`", j + 1)
                    end = n if end < 0 else end
                    segments += _segments(command[j + 1:end])
                    j = end + 1
                    continue
                add(c, '"')
                j += 1
            i = j + 1
            continue
        else:
            add(ch, None)
        i += 1
    end_segment()
    return segments


def _strip_wrappers(tokens: Sequence[str]) -> list[str]:
    """Drop leading ``VAR=value``, ``env``, ``sudo``, ``command``, ``exec``, ``nohup``, ``time``,
    ``nice``, ``xargs``, ``timeout`` and their options, and the shell keywords that precede a command
    (``{``, ``!``, ``if``, ``then``, ``else``, ``elif``, ``do``, ``while``, ``until``)."""
    rest = list(tokens)
    while rest:
        if _ASSIGNMENT.match(rest[0]) or rest[0] in _KEYWORDS:
            rest.pop(0)
        elif os.path.basename(rest[0].lstrip("\\({")) in _WRAPPERS:
            wrapper = os.path.basename(rest.pop(0).lstrip("\\({"))
            while rest and (rest[0].startswith("-") or _ASSIGNMENT.match(rest[0]) or (
                    wrapper == "timeout" and re.fullmatch(r"[\d.]+[smhd]?", rest[0]))):
                rest.pop(0)
        else:
            break
    return rest


def _unwrapped(segment: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    """``segment`` without its wrappers (decided on the raw words)."""
    raw = [r for _, r in segment]
    return list(segment[len(raw) - len(_strip_wrappers(raw)):])


def _shell_script(raw: Sequence[str]) -> str | None:
    """The script a ``sh|bash|zsh|dash -c <script>`` segment (wrappers stripped) runs, else ``None``."""
    if not raw or os.path.basename(raw[0].lstrip("\\({")) not in _SHELLS:
        return None
    for n, arg in enumerate(raw[1:], 1):
        if arg.startswith("-") and "c" in arg[1:]:
            return raw[n + 1] if n + 1 < len(raw) else ""
    return None


def _forbidden_commands(command: str) -> list[str]:
    """The forbidden executables the command line runs (after wrappers, descending into ``sh -c``)."""
    found: list[str] = []
    for segment in _segments(command):
        rest = [r for _, r in _unwrapped(segment)]
        if not rest:
            continue
        script = _shell_script(rest)
        if script is not None:  # the script is a command line, with its own heredocs
            stripped, executed = _without_heredocs(script)
            found += [x for text in (stripped, *executed) for x in _forbidden_commands(text)]
        elif (label := _forbidden_label(rest)) is not None:
            found.append(label)
    return found


def _forbidden_label(rest: Sequence[str]) -> str | None:
    """The forbidden executable a segment (wrappers stripped) starts with, or ``None``."""
    exe, args = os.path.basename(rest[0].lstrip("\\({")), list(rest[1:])
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


def _heredoc_end(command: str, start: int, delimiter: str, tabs: bool) -> tuple[int, int] | None:
    """``(start of the delimiter line, end of it)`` of a heredoc body that starts at ``start``, or
    ``None`` when no line closes it."""
    pos = start
    while True:
        newline = command.find("\n", pos)
        end = len(command) if newline < 0 else newline
        line = command[pos:end]
        if (line.lstrip("\t") if tabs else line) == delimiter:
            return pos, (end if newline < 0 else end + 1)
        if newline < 0:
            return None
        pos = newline + 1


def _substitutions(body: str) -> list[str]:
    """The commands a heredoc body with an UNQUOTED delimiter runs when the shell expands it: every
    ``$(...)`` and backquoted command (an escaped ``\\$`` stays literal)."""
    found, i = [], 0
    while i < len(body):
        if body[i] == "\\":
            i += 2
            continue
        if body.startswith("$(", i):
            end = _closing(body, i + 2)
            found.append(body[i + 2:end])
            i = end + 1
            continue
        if body[i] == "`":
            end = body.find("`", i + 1)
            end = len(body) if end < 0 else end
            found.append(body[i + 1:end])
            i = end + 1
            continue
        i += 1
    return found


def _runs_heredoc(line: str) -> bool:
    """Whether a segment of the line that holds a ``<<`` feeds its input to a shell (``bash <<EOF``,
    ``sh -s <<EOF``, ``cat <<EOF | bash``, ``source /dev/stdin <<EOF``)."""
    for segment in _segments(line):
        rest = [r for _, r in _unwrapped(segment)]
        if rest and os.path.basename(rest[0].lstrip("\\({")) in _HEREDOC_RUNNERS \
                and _shell_script(rest) is None:
            return True
    return False


def _without_heredocs(command: str, unclosed: list[bool] | None = None) -> tuple[str, list[str]]:
    """The command line without its heredoc bodies and ``#`` comments, and the bodies a shell RUNS.
    The shell never runs nor expands a heredoc body given to ``cat``, ``python3 -`` or ``tee`` (a test
    file that lists ``"gh pr merge 12"``, a doc that names ``~/.config/foundry``): it is text. A body
    fed to a shell (see ``_runs_heredoc``) is returned to be audited as a command line. A ``<<`` inside
    quotes or arithmetic (``$((1 << 2))``) is not a heredoc; a heredoc whose delimiter line never comes
    is kept as it is (audited). With an UNQUOTED delimiter (``<<EOF``) the shell expands the body: its
    ``$(...)`` and backquoted commands are returned too (``_substitutions``). ``unclosed`` (audit revision 2)
    receives ``True`` when a heredoc is never closed: what follows is then text for the shell, commands here."""
    out: list[str] = []
    executed: list[str] = []
    stack: list[str] = []  # open contexts: ' " $( ( ` ((
    pending: list[tuple[str, bool, bool]] = []  # (delimiter, ``<<-``, quoted delimiter)
    line_start, i, n = 0, 0, len(command)
    while i < n:
        ch = command[i]
        top = stack[-1] if stack else ""
        if top == "'":
            if ch == "'":
                stack.pop()
        elif top == "((":
            if command.startswith("))", i):
                stack.pop()
                out.append("))")
                i += 2
                continue
        elif ch == "\\" and i + 1 < n:
            out.append(command[i:i + 2])
            i += 2
            continue
        elif command.startswith("$((", i):
            stack.append("((")
            out.append("$((")
            i += 3
            continue
        elif command.startswith("$(", i):
            stack.append("$(")
            out.append("$(")
            i += 2
            continue
        elif ch == "`":
            if top == "`":
                stack.pop()
            else:
                stack.append("`")
        elif top == '"':
            if ch == '"':
                stack.pop()
        elif ch in "'\"":
            stack.append(ch)
        elif command.startswith("((", i) and (i == 0 or command[i - 1] in _WORD_BREAK):
            stack.append("((")
            out.append("((")
            i += 2
            continue
        elif ch == "(":
            stack.append("(")
        elif ch == ")":
            if top in ("(", "$("):
                stack.pop()
        elif ch == "#" and (i == 0 or command[i - 1] in _WORD_BREAK):
            newline = command.find("\n", i)
            i = n if newline < 0 else newline
            continue
        elif command.startswith("<<<", i):  # a here-string: its word is on the line, no body
            out.append("<<<")
            i += 3
            continue
        elif command.startswith("<<", i) and (match := _HEREDOC.match(command, i)):
            word = re.sub(r"['\"\\]", "", match.group(2))
            pending.append((word, match.group(1) == "-", word != match.group(2)))
            out.append(match.group(0))
            i = match.end()
            continue
        elif ch == "\n" and pending:
            out.append("\n")
            holding, start = "".join(out[line_start:]), i + 1
            bodies: list[str] = []
            for delimiter, tabs, quoted in pending:
                found = _heredoc_end(command, start, delimiter, tabs)
                if found is None:  # never closed: what follows stays audited as commands
                    if unclosed is not None:
                        unclosed.append(True)
                    break
                bodies.append(command[start:found[0]])
                if not quoted:  # ``<<EOF``: the shell runs the body's ``$(...)`` and backquotes
                    executed += _substitutions(bodies[-1])
                start = found[1]
            pending = []
            if holding.rstrip().endswith("|"):  # ``cat <<EOF |`` <body> ``bash``: the pipe goes on after
                holding += command[start:].split("\n", 1)[0]
            if _runs_heredoc(holding):
                executed += bodies
            i = start
            line_start = len(out)
            continue
        out.append(ch)
        if ch == "\n" and not stack:
            line_start = len(out)
        i += 1
    return "".join(out), executed


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
    and a ``$HOME`` inside double quotes are kept (``cat ~/.config/x`` stays an access). Expects the
    command without its heredoc bodies (``_without_heredocs``)."""
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
    ``/`` only as an argument of a filesystem reader (``find / -name x``, see ``_reads_root``); the
    script of a ``sh -c`` is read the same way. The flag says the path was RELATIVE to the bundle (an
    honest arm never climbs out of it). ``home`` is what ``~`` and ``$HOME`` mean for the arm. Expects
    the command without its heredoc bodies (``_without_heredocs``). Audit revision 1 only: revision 2 reads a
    command line with ``_shell_walk``."""
    cwd, out = bundle, []

    def resolve(token: str) -> tuple[Path, bool] | None:
        expanded = _expand(token, home)
        if expanded is None:
            return None
        relative = not os.path.isabs(expanded) and cwd == bundle
        return Path(os.path.realpath(expanded if os.path.isabs(expanded) else cwd / expanded)), relative

    def walk(text: str, depth: int = 0) -> None:
        nonlocal cwd
        for segment in _segments(text):
            words = _unwrapped(segment)
            tokens = [w for w, _ in words]
            script = _shell_script([r for _, r in words])
            if script is not None and depth < 8:
                stripped, executed = _without_heredocs(script)
                for text in (stripped, *executed):
                    walk(text, depth + 1)
                continue
            if tokens and os.path.basename(tokens[0]) == "cd":
                target = next((t for t in tokens[1:] if not t.startswith("-")), "~")
                found = resolve(target)
                if found is not None:
                    out.append(found)
                    cwd = found[0]
                continue
            root_reader = _reads_root(tokens)
            for word in tokens:  # a quoted word is also read piece by piece (``python3 -c "open('../x')"``)
                for token in dict.fromkeys([word, *_QUOTED_PIECES.split(word)]):
                    token = _REDIRECTION.sub("", token)
                    if token.startswith("-"):
                        token = token.partition("=")[2]
                    if token and (_pathlike(token) or (root_reader and not token.strip("/"))) \
                            and (found := resolve(token)) is not None:
                        out.append(found)

    walk(command)
    return out


# ------------------------------------------- audit revision 2 (PAT-123): where the shell of a call may be
#
# Revision 2 resolves a relative path against the directory the command runs in. It never knows that directory;
# it keeps a SET of candidate directories and flags a path as soon as ONE candidate takes it out of the allowed
# zone. The whole rule is one invariant: the candidate set contains the real directory. A ``cd`` REPLACES the set
# (the only step that can make the audit flag less than revision 1) under the conditions of ``_walk_simple`` and
# nowhere else; every other step only ADDS candidates.
#
# THREAT MODEL (maintainer's decision, 2026-10-08). This lexical audit is a JOURNAL of the accidental excursions
# of an arm that is doing its job. It is NOT a barrier against an arm that tries to deceive it. The barrier is
# Claude Code's native Bash sandbox, enforced by the operating system (PAT-124). The invariant is claimed for an
# arm that is not trying to deceive the audit, under the assumptions H1 to H9 below (numbered: the launcher
# document cites them), and for nothing else.
#
# COUPLING WITH THE BARRIER (PAT-124). Revision 2 is accepted only together with the native sandbox key
# (``isolation.cloud_native_sandbox``, checked at load). A record that ran to completion under revision 2 carries
# ``audit.barrier``: what the launcher verified (``BARRIERS``: settings transmitted, Claude Code version observed in
# the stream), never "confined". A record cut by ``_tool_error`` carries no ``audit`` at all, so it is never
# readable as verified. A record of revision 2 is a journal entry.
#
# KNOWN LIMITS: DELIBERATE EVASION, OUT OF SCOPE (PAT-123). Listed, not fixed one by one; the tests named
# ``..._is_a_known_limit_...`` hold what the audit does today for the first two.
#  L1 A directory or a symbolic link on the path of a candidate is renamed, moved, replaced or removed after
#     the shell entered it (``mkdir d; cd d; mv ../d ../../d2``; ``ln -s ../scratch l; cd l; rm ../bundle/l``):
#     a program moves the place the shell stands in, and the audit, which reads names, still resolves from the
#     old name.
#  L2 ``CDPATH`` or other state ``cd`` reads is set through a name the script builds or through arithmetic
#     evaluation (``print $O NAME`` with ``O=-v``, ``let E`` or ``typeset -i X=E`` with ``E='CDPATH=5'``, ``((
#     ))``). Hardened only where it costs one line: ``print -v``, and ``integer``/``float`` with an expanded
#     argument, taint; ``let`` with one gives up the directory.
#  L3 Aliases, functions, hooks (``chpwd``, ``precmd``), modules and shell options (``autocd``, ``cdablevars``,
#     ``chaselinks``) of the host profile: H4 assumes there are none; nothing in the stream shows it.
#  L4 A command built at run time (a name or a script the shell expands or reads from a file, ``$X ..``, a file
#     written then sourced by another program). The audit gives up the directory for the forms it reads
#     (``UNKNOWN_CWD``); it does not see the others.
#  L5 A ``cd`` hidden in a script or an interpreter (``python3 -c "os.chdir('..'); ..."``, ``make -C``, ``git
#     -C``, a script file the arm wrote): the directory of that program is not followed (H8). L5 and L6 are blind
#     spots INHERITED from revision 1 that ordinary work meets (``git -C <dir>``, ``make -C <dir>``, a script the
#     arm wrote, a path assembled at run time), not deliberate evasion; the target of a ``-C`` is itself a path
#     the audit reads and still flags when it is outside the zone.
#  L6 A path built at run time, or kept in a file for a later call (H8, as in revision 1).
#
#  KNOWN COSTS: OVER-FLAGS OF ORDINARY WORK (PAT-124, to carry to the v5 ticket). Revision 2 flags, or gives up
#  the directory on, work that stays in the zone: ``source .venv/bin/activate`` (a builtin outside the inert
#  allow-list: ``UNKNOWN_CWD``), ``break`` and ``continue`` (same: they do not move the shell but end a loop early,
#  so treating them as inert would let the audit believe a later ``cd`` that did not run: not changed), ``trap``,
#  ``cd "$(git rev-parse --show-toplevel)"`` (a substitution: unreadable target), and a word ``cd`` in a commit
#  message or a heredoc. Each can show as a flag on a record; none is a barrier decision (the barrier is the sandbox).
#
#  H1 host    The stream is a Claude Code stream whose ``system``/``init`` events all name a version of
#             ``OBSERVED_CLAUDE_CODE``. Observed in the 30 cloud streams of the v4 campaign (2.1.285): a Bash call
#             is a new shell that runs the command with ``eval`` (zsh error lines read ``(eval):cd:1: ...``); the
#             directory the previous call ended in is kept when that call returned 0 and the directory is inside
#             the project (the bundle); a call that ends outside is put back at the project root and its result
#             says ``Shell cwd was reset to ...``. NOT established: whether the directory is kept after a call
#             that returned non-zero (in one v4 stream a call of status 1 that began with a ``cd`` left the next
#             call at the root; whether that ``cd`` had run is not known), so the audit keeps both. The launcher
#             refuses a cloud stream whose ``init`` lists a tool outside Bash, Edit, Read, Write: no sub-agent, whose
#             shell may behave otherwise. Any other stream (omp, another or an unnamed Claude Code version): nothing
#             is assumed, see H9.
#  H2 result  A result that is not an error (``is_error`` false), carries the host's detail object without
#             ``interrupted``, ``returnCodeInterpretation`` (the host's name for an accepted non-zero status, e.g.
#             ``grep`` 1) or a background task id, for a call not sent to the background, means the script
#             returned 0. Such a result is "clean". Every other result, and a missing one, proves nothing.
#  H3 output  A result of at most ``_RESULT_WHOLE`` characters without a truncation marker (``_TRUNCATED``) holds
#             all of stdout and stderr of the call. No truncated result was seen in the v4 streams; the markers
#             are the host's as known, not observed.
#  H4 shell   The host shell is bash or zsh with its default options for ``cd`` (no ``autocd``, ``cdablevars``,
#             ``chaselinks``, ``cdspell``), no ``CDPATH``, and no alias, function, hook (``chpwd``) or loaded
#             module that changes the directory or ends the shell under a name outside ``_SHELL_NAMES``. A
#             command name outside ``_SHELL_NAMES`` is a program: it cannot make the shell run ``cd``, but it CAN
#             move the shell without one, by renaming, moving or replacing a directory or a symbolic link of the
#             path the shell stands in (limit L1). The audit assumes the arm does not do that; it does not check
#             it. Shell state other than the directory (variables, functions, options) does not pass from a call
#             to the next. Nothing in the stream shows these: they are assumed.
#  H5 status  A script that stops before its end returns non-zero (an expansion error, a signal, a timeout),
#             unless it runs ``exit``, ``return``, ``exec``, ``logout`` or ``bye`` (read by the audit, which then
#             believes no later ``cd`` and no end directory). Observed for the generated forms under bash 3.2 and
#             zsh 5.9 by the property test, and once on the host (a zsh ``no matches found`` gave ``Exit code 1``);
#             not proven for every shell error.
#  H6 cd      A failed ``cd`` writes a line holding ``cd:`` on the stderr of the shell (bash ``cd: x: No such
#             file``, zsh ``(eval):cd:1: no such file``), so with H3 a result without such a line shows that a
#             ``cd`` whose stderr is not redirected, when it ran, succeeded.
#  H7 lexing  The audit's readers (``_without_heredocs``, ``_segments``, ``_simple_script``) split a script as the
#             shell does. Guards: a ``cd``/``chdir``/``pushd``/``popd`` word of the call (its comments and heredoc
#             bodies included) that was not read as a command, and ``$'...'`` quoting, add ``UNKNOWN_CWD``; a
#             heredoc that is never closed removes the proof.
#  H8 use     A relative path named in a call is used, if at all, by the command that names it or a later one of
#             the SAME call, by a program that resolves it against the directory of the shell that ran it (not
#             ``git -C``, ``make -C``, ``os.chdir``). A path built at run time or kept in a file for a later call
#             is not seen (as in revision 1). A bare name (``cat x``) is not a path, in either revision: revision 2
#             flags instead any command that may RUN in a directory outside the allowed places.
#  H9 else    When H1 does not hold the audit does not carry a directory: every call starts from the UNION of the
#             project root and every directory an earlier call may have been in, and no ``cd`` replaces
#             anything. When H2, H3 or H6 does not hold for a call, no ``cd`` of that call replaces anything and
#             the next call starts from every directory the call may have been in, its start included.

OBSERVED_CLAUDE_CODE = ("2.1.285",)
UNKNOWN_CWD = Path("/<unknown-working-directory>")  # a directory the audit cannot name: a relative path is a hit
_CWD_CAP = 32  # more candidate directories than this collapse to ``UNKNOWN_CWD``
_RESULT_WHOLE = 20000
_TRUNCATED = re.compile(r"\[\d+ lines truncated\]|<persisted-output>|Output too large", re.I)
_CD_WORD = re.compile(r"(?<![\w./$-])(?:cd|chdir|pushd|popd)(?![\w.-])")
_CD_LINE = re.compile(r"(?<![\w./-])cd:")  # a failed ``cd``, whatever the shell says after it
_CWD_RESET = re.compile(r"Shell cwd was reset to ")
_CD_VARS = frozenset({"CDPATH", "cdpath", "PWD", "OLDPWD"})
_CD_VARS_TEXT = re.compile(r"cdpath|\$\{[!(]", re.I)  # ``CDPATH`` named, or a variable reached by another name
_MOVERS = frozenset({"cd", "chdir", "pushd", "popd"})
# Every builtin and reserved word of bash (3.2 and 5) and zsh 5.9 (a test compares it with the shells installed).
# A command word outside this set is a program (H4: it runs no ``cd`` in the shell; limit L1 is what it can do).
_SHELL_NAMES = frozenset("""
. : [ [[ ]] { } ! - alias autoload bg bind bindkey break builtin bye caller case cd chdir command compadd
comparguments compcall compctl compdescribe compfiles compgen compgroups complete compopt compquote compset comptags
comptry compvalues continue coproc declare dirs disable disown do done echo echotc echoti elif else emulate enable end
esac eval exec exit export false fc fg fi float for foreach function functions getln getopts hash help history if in
integer jobs kill let limit local log logout mapfile nocorrect noglob popd print printf private pushd pushln pwd r read
readarray readonly rehash repeat return sched select set setopt shift shopt source suspend test then time times trap
true ttyctl type typeset ulimit umask unalias unfunction unhash unlimit unset unsetopt until vared wait whence where
which while zcompile zformat zle zmodload zparseopts zregexparse zstyle""".split())
# The allow-list: builtins that neither move the shell, nor end it, nor define or run a command, nor set a variable
# (``print -v NAME``, which sets one, is read apart in ``_classify``).
_INERT = frozenset(": [ true false echo print pwd test type which whence where hash rehash jobs kill wait times "
                   "umask ulimit limit unlimit dirs help history shift".split())
_NAMERS = frozenset("export declare typeset local readonly integer float private".split())  # NAME[=value] words
_READERS = frozenset("read getopts let unset mapfile readarray".split())  # set variables named by static words
_ENDERS = frozenset("exit return logout bye exec".split())  # end the script, possibly with status 0
_MODIFIERS = frozenset({"command", "builtin", "noglob", "nocorrect", "-"})
_SET_OPTIONS = frozenset({"errexit", "nounset", "pipefail", "xtrace", "verbose", "noglob", "noclobber"})
_RESERVED = frozenset("if then else elif fi case esac for select while until do done in function time coproc repeat "
                      "foreach end { } ! [[ ]]".split())
_EXPANDED = re.compile(r"[$`*?\[\]{}~!]")  # in a word of the loose reader: the shell may expand it
_WORD_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./:,@%+=^-")
_WORD_END = frozenset(" \t\n;&|<>()")
_REDIRECT_OP = re.compile(r"&>>|&>|<<<|<<-|<<|<&|<>|<|>>|>&|>\||>")
_PARAMETER = re.compile(r"[A-Za-z_]\w*|[?$#!@*\-0-9]")
_NAME_SET = re.compile(r"([A-Za-z_]\w*)\+?=")


class _NotSimple(Exception):
    """The script is outside the grammar of ``_simple_script``."""


class _Word(NamedTuple):
    text: str  # quotes removed; an expansion kept as written (``$X``)
    static: bool  # no shell expands it: no ``$``, glob, brace, ``~``, leading ``=``
    start: int
    end: int


class _Command(NamedTuple):
    words: list[_Word]
    assigns: list[str]  # the names it sets
    redirected: bool
    start: int
    end: int


def _read_word(text: str, start: int) -> _Word:
    """The shell word that starts at ``start`` (``end == start``: none). Only what the grammar names is read: a
    backquote, ``$(``, ``${x:-y}``, ``$'..'``, ``$[`` or an unclosed quote raises ``_NotSimple``."""
    out: list[str] = []
    state = {"static": True}
    i, n = start, len(text)

    def parameter(at: int, quoted: bool) -> int:
        nxt = text[at + 1:at + 2]
        if nxt == "{":
            close = text.find("}", at)
            if close < 0 or not _PARAMETER.fullmatch(text, at + 2, close):
                raise _NotSimple
            end = close + 1
        elif (match := _PARAMETER.match(text, at + 1)) is not None:
            end = match.end()
        elif nxt in ("(", "[", "`") or (nxt in ("'", '"') and not quoted):
            raise _NotSimple
        else:  # a lone ``$``
            out.append("$")
            return at + 1
        state["static"] = False
        out.append(text[at:end])
        return end

    while i < n:
        ch = text[i]
        if ch in _WORD_END:
            break
        if ch == "\\":
            if i + 1 >= n:
                raise _NotSimple
            if text[i + 1] != "\n":
                out.append(text[i + 1])
            i += 2
        elif ch == "'":
            close = text.find("'", i + 1)
            if close < 0:
                raise _NotSimple
            out.append(text[i + 1:close])
            i = close + 1
        elif ch == '"':
            i += 1
            while True:
                if i >= n:
                    raise _NotSimple
                ch = text[i]
                if ch == '"':
                    i += 1
                    break
                if ch == "\\" and i + 1 < n:
                    if text[i + 1] in '$`"\\':
                        out.append(text[i + 1])
                    elif text[i + 1] != "\n":
                        out.append(ch + text[i + 1])
                    i += 2
                elif ch == "`":
                    raise _NotSimple
                elif ch == "$":
                    i = parameter(i, True)
                else:
                    out.append(ch)
                    i += 1
        elif ch == "$":
            i = parameter(i, False)
        elif ch == "`":
            raise _NotSimple
        elif ch in _WORD_CHARS or ch.isalnum():
            if ch == "=" and i == start:  # zsh expands ``=name`` to the path of a command
                state["static"] = False
            out.append(ch)
            i += 1
        elif ch in "*?[]~{}!#":  # a glob, a brace, a tilde: expanded, never a command name or a ``cd`` target
            state["static"] = False
            out.append(ch)
            i += 1
        else:
            raise _NotSimple
    value = "".join(out)
    if text[start:i] in ("[", "]"):  # the ``[`` command and its closing word
        state["static"] = True
    return _Word(value, state["static"], start, i)


def _simple_script(text: str) -> list[list[tuple[str, list[_Command]]]]:
    """The allow-list grammar of audit revision 2, or ``_NotSimple``. A simple script is a sequence of chains
    separated by ``;`` or a newline; a chain is pipelines joined by ``&&`` or ``||`` (returned as ``(link,
    pipeline)``, the first link empty); a pipeline is simple commands joined by ``|``; a simple command is
    assignments, words and redirections. A word is plain characters, quotes and ``$NAME``/``${NAME}``. Nothing
    else: no subshell, group, substitution, backquote, ``&``,
    reserved word (``if``, ``for``, ``while``, ``{``, ``!``, ``[[``, ``time``...), function definition or empty
    command. Expects the script without its heredoc bodies and comments (``_without_heredocs``)."""
    chains: list[list[tuple[str, list[_Command]]]] = []
    chain: list[tuple[str, list[_Command]]] = []
    pipeline: list[_Command] = []
    link, need, i, n = "", False, 0, len(text)

    def blanks(at: int) -> int:
        while at < n and (text[at] in " \t" or text.startswith("\\\n", at)):
            at += 2 if text[at] == "\\" else 1
        return at

    def command(at: int) -> tuple[_Command | None, int]:
        words: list[_Word] = []
        assigns: list[str] = []
        redirected, first = False, None
        while True:
            at = blanks(at)
            if at >= n or text[at] in "\n;|" or text.startswith("&&", at):
                break
            if text[at] in "()" or (text[at] == "&" and not text.startswith("&>", at)):
                raise _NotSimple
            if text[at] == "#":  # a comment, at the start of a word
                while at < n and text[at] != "\n":
                    at += 1
                continue
            first = at if first is None else first
            if (op := _REDIRECT_OP.match(text, at)) is not None:
                target = _read_word(text, blanks(op.end()))
                if target.end == target.start:
                    raise _NotSimple
                redirected, at = True, target.end
                continue
            word = _read_word(text, at)
            if word.end == at:
                raise _NotSimple
            at = word.end
            if text[word.start:word.end].isdigit() and text[at:at + 1] in ("<", ">"):
                continue  # the file descriptor of a redirection (``2>&1``)
            if not words and (named := _NAME_SET.match(text, word.start, word.end)) is not None:
                assigns.append(named.group(1))
            else:
                words.append(word)
        if first is None:
            return None, at
        if words and text[words[0].start:words[0].end] in _RESERVED:
            raise _NotSimple
        return _Command(words, assigns, redirected, first, at), at

    while i < n:
        made, i = command(i)
        op = next((o for o in ("&&", "||", "|&", "|", ";", "\n") if text.startswith(o, i)), "")
        if made is None:
            if op == "\n" and need:  # a newline after ``&&``, ``||`` or ``|``
                i += 1
                continue
            if need or op not in ("", "\n"):
                raise _NotSimple
        else:
            pipeline.append(made)
        if op in ("|", "|&"):
            need = True
        elif op in ("&&", "||"):
            chain.append((link, pipeline))
            pipeline, link, need = [], op, True
        else:
            if pipeline:
                chain.append((link, pipeline))
            if chain:
                chains.append(chain)
            chain, pipeline, link, need = [], [], "", False
        i += len(op)
        if op == ";" and text.startswith(";", i):
            raise _NotSimple
    if need:
        raise _NotSimple
    return chains


def _classify(texts: Sequence[str], static: Sequence[bool]) -> tuple[str, int | None, int]:
    """What a simple command (its words, assignments and redirections removed) does to the shell that runs it:
    ``(kind, index of the cd target, movers read)``. ``inert``: nothing (a program, or a builtin of the
    allow-list used in an allowed way); ``end``: may end the script with status 0; ``taint``: may set a variable
    that changes what a later ``cd`` does; ``cd``: a ``cd`` with one target word (index) or one the audit cannot
    read (``None``); ``unknown``: anything else (another builtin or reserved word, a command name the shell
    builds), after which the shell may be anywhere."""
    at = 0
    while at < len(texts) and static[at] and texts[at] in _MODIFIERS:  # ``command -v x``, ``builtin cd``
        at += 1
        while at < len(texts) and static[at] and texts[at].startswith("-"):
            at += 1
        if at < len(texts) and static[at] and texts[at] in _SHELL_NAMES and texts[at] not in _INERT:
            return "unknown", None, int(texts[at] in _MOVERS)
    if at >= len(texts):
        return "inert", None, 0
    name, args, fixed = texts[at], list(texts[at + 1:]), list(static[at + 1:])
    if not static[at] or not name:
        return "unknown", None, 0
    if "/" in name or name not in _SHELL_NAMES:
        return "inert", None, 0
    if name == "cd":
        operands = list(range(len(args)))
        while operands and fixed[operands[0]] and args[operands[0]] in ("-L", "-P", "-e", "-q", "-s"):
            operands.pop(0)
        if operands and fixed[operands[0]] and args[operands[0]] == "--":
            operands.pop(0)
        return "cd", (at + 1 + operands[0] if len(operands) == 1 else None), 1
    if name in _MOVERS:
        return "unknown", None, 1
    if name in _ENDERS:
        return "end", None, 0
    if name == "print" and any(p and re.fullmatch(r"-[A-Za-z]*v[A-Za-z]*", a) for a, p in zip(args, fixed)):
        return "taint", None, 0  # zsh ``print -v NAME`` sets a variable, as ``printf -v`` does
    if name in _INERT:
        return "inert", None, 0
    if name in _NAMERS:
        for arg, plain in zip(args, fixed):
            if plain and re.fullmatch(r"[-+][A-Za-z]*", arg):
                if set(arg[1:]) - set("aAgilrtuxUpE"):  # ``-n`` (a name reference), ``-f``, ``-T``...
                    return "unknown", None, 0
            elif (named := _NAME_SET.match(arg) or re.fullmatch(r"([A-Za-z_]\w*)", arg)) is None:
                return "unknown", None, 0
            elif named.group(1) in _CD_VARS:
                return "taint", None, 0
        if name in ("integer", "float") and not all(fixed):  # the value is evaluated: it may set another variable
            return "taint", None, 0
        return "inert", None, 0
    if name in _READERS:
        if not all(fixed):
            return "unknown", None, 0
        return ("taint" if set(args) & _CD_VARS else "inert"), None, 0
    if name == "printf":  # ``printf -v NAME`` sets a variable
        if (args and not fixed[0]) or any(a == "-v" and p for a, p in zip(args, fixed)):
            return "taint", None, 0
        return "inert", None, 0
    if name == "set":
        at = 0
        while at < len(args):
            if not fixed[at]:
                return "unknown", None, 0
            if args[at] == "--":
                break
            if re.fullmatch(r"[-+][efuvxC]*o", args[at]) and at + 1 < len(args) and fixed[at + 1] \
                    and args[at + 1] in _SET_OPTIONS:  # ``-o pipefail``, ``-euo pipefail``
                at += 2
            elif re.fullmatch(r"[-+][efuvxC]+", args[at]):
                at += 1
            else:
                return "unknown", None, 0
        return "inert", None, 0
    return "unknown", None, 0


def _child_scripts(words: Sequence[str]) -> list[str]:
    """The scripts a command hands to a child shell, wherever the shell stands in it (``bash -c S``, ``env sh -c
    S``, ``xargs sh -c S``, ``find . -exec sh -c S``)."""
    found = []
    for at, word in enumerate(words):
        if os.path.basename(word.lstrip("\\({")) in _SHELLS:
            for n in range(at + 1, len(words)):
                if words[n].startswith("-") and not words[n].startswith("--") and "c" in words[n][1:]:
                    if n + 1 < len(words):
                        found.append(words[n + 1])
                    break
                if not words[n].startswith("-"):
                    break
    return found


def _fixed(word: str) -> bool:
    """A word of the loose reader (quotes already removed) that no shell expands. A ``$`` or a glob that stood
    inside quotes is refused too: the reader cannot tell, and refusing only adds candidates."""
    return bool(word) and not _EXPANDED.search(word) and not word.startswith("=")


def _capped(dirs: Collection[Path]) -> frozenset[Path]:
    return frozenset(dirs) if len(dirs) <= _CWD_CAP else frozenset({UNKNOWN_CWD})


def _cd_into(dirs: Collection[Path], target: str) -> set[Path]:
    """Where ``cd <target>`` may leave a shell that is in one of ``dirs``. The logical path (what bash and zsh
    compute by default) and the physical one (``cd -P``, ``set -P``, a symbolic link crossed) are both kept."""
    out: set[Path] = set()
    for cwd in dirs:
        if os.path.isabs(target):
            out |= {Path(os.path.normpath(target)), Path(os.path.realpath(target))}
        elif cwd == UNKNOWN_CWD:
            out.add(UNKNOWN_CWD)
        else:
            out |= {Path(os.path.normpath(cwd / target)), Path(os.path.realpath(cwd / target))}
    return out


def _resolved(token: str, dirs: Collection[Path], home: str) -> list[tuple[Path, bool]]:
    """``token`` as a path from each candidate directory: ``(path, it was relative)``."""
    expanded = _expand(token, home)
    if expanded is None:
        return []
    if os.path.isabs(expanded):
        return [(Path(os.path.realpath(expanded)), False)]
    found: list[tuple[Path, bool]] = []
    for cwd in sorted(dirs):
        if cwd == UNKNOWN_CWD:
            item = (UNKNOWN_CWD, True)
        else:
            item = (Path(os.path.realpath(cwd / expanded)), True)
        if item not in found:
            found.append(item)
    return found


def _segment_tokens(segment: Sequence[tuple[str, str]]) -> list[str]:
    """The path-like tokens of one command segment: every word of it (the wrappers and assignments too), whole,
    piece by piece (``python3 -c "open('../x')"``) and as path tokens inside a word (``X=../x``); a superset of
    what revision 1 reads in the segment."""
    words = [w for w, _ in _unwrapped(segment)]
    root_reader = _reads_root(words)
    found: list[str] = []
    if words and os.path.basename(words[0]) == "cd" and not any(not w.startswith("-") for w in words[1:]):
        found.append("~")  # ``cd`` alone goes home
    for word, _ in segment:
        value = [word.partition("=")[2]] if _ASSIGNMENT.match(word) else []  # ``X=sub/../../x``
        for token in dict.fromkeys([word, *value, *_QUOTED_PIECES.split(word), *_path_tokens(word)]):
            token = _REDIRECTION.sub("", token)
            if token.startswith("-"):
                token = token.partition("=")[2]
            if token and (_pathlike(token) or (root_reader and not token.strip("/"))):
                found.append(token)
    return found


def _raw_relative(script: str, home: str) -> list[tuple[int, str]]:
    """``(offset, token)`` of the relative path tokens of the raw text (the token pass of ``audit_transcript``)."""
    found = []
    for match in _PATH_TOKEN.finditer(_unquoted_expansions(script)):
        expanded = _expand(match.group(0), home) if match.group(0).strip("/") else None
        if expanded is not None and not os.path.isabs(expanded):
            found.append((match.start(), match.group(0)))
    return found


def _walk_simple(chains: Sequence[Sequence[tuple[str, Sequence[_Command]]]], script: str, start: frozenset[Path],
                 home: str, proof: Mapping[str, bool] | None, depth: int
                 ) -> tuple[list[tuple[Path, bool]], frozenset[Path], frozenset[Path], int]:
    """A script of the allow-list grammar, command by command (``_shell_walk`` gives the contract).

    A ``cd`` REPLACES the candidate set when ALL of this holds, and only then:
     1. the script is in the grammar of ``_simple_script`` (so each command is known to run at most once, in order);
     2. the result of the call is clean (H2) and whole (H3): the script returned 0, so by H5 it ran to its end and
        reached every chain;
     3. no command before the ``cd`` may have ended the script with status 0 (``exit``, ``return``, ``exec``),
        moved the shell out of sight or set a variable ``cd`` reads (any builtin outside the allow-list, a command
        name the shell builds, ``CDPATH``);
     4. the ``cd`` is the first pipeline of its chain (nothing decides whether it runs), alone in its pipeline (a
        pipeline may run its commands in subshells), with no assignment and no redirection of its own, and no
        ``||`` follows it in the chain (what follows a ``||`` runs when the ``cd`` FAILED);
     5. it has exactly one target, a word no shell expands (no ``$``, glob, brace, ``~``), not starting with ``-``
        or ``+`` (``cd -``, the directory stack), after the options ``-L``, ``-P``, ``-e``, ``-q``, ``-s``, ``--``;
     6. the result holds no ``cd:`` line (H6).
    Otherwise the ``cd`` ADDS its target to the candidates (it may or may not have run), or ``UNKNOWN_CWD`` when
    the target cannot be read. The target is resolved logically and physically (``_cd_into``).

    A relative path is resolved against every directory the shell may be in FROM the command that names it TO the
    end of the script (H8: ``X=../x; cd ..; cat $X``, the arguments after ``sh -c``), the child shells it starts
    included; the words of a ``cd`` against the directories before it only (it uses them where it stands). Every
    other command also names ``.``, the directory it runs in (a bare name such as ``cat x`` is read there): a
    command that may run outside the allowed places is a hit by itself, as the target of the ``cd`` that led
    there is in revision 1, also when that target could not be read (``cd $X; cat y``)."""
    dirs, seen, movers, out = frozenset(start), set(start), 0, []
    ended = False
    tainted = bool(_CD_VARS_TEXT.search(script))
    steps: list[tuple[_Command, frozenset[Path], set[Path], bool, str | None]] = []  # (.., is a cd, its target)
    for chain in chains:
        for rank, (_, pipeline) in enumerate(chain):
            for cmd in pipeline:
                before, extra = dirs, set()
                target: str | None = None
                kind, index, found = _classify([w.text for w in cmd.words], [w.static for w in cmd.words])
                movers += found
                tainted = tainted or bool(_CD_VARS.intersection(cmd.assigns))
                if kind == "end":
                    ended = True
                elif kind == "taint":
                    tainted = True
                elif kind == "unknown":
                    dirs = dirs | {UNKNOWN_CWD}
                    ended = tainted = True
                elif kind == "cd":
                    word = cmd.words[index] if index is not None else None
                    if word is not None and not cmd.assigns and not tainted and word.static and word.text \
                            and word.text[0] not in "-+":
                        target = word.text
                    if target is None:
                        dirs = dirs | {UNKNOWN_CWD}
                    else:
                        certain = (proof is not None and proof["clean"] and proof["cd"] and not ended
                                   and rank == 0 and len(pipeline) == 1 and not cmd.redirected
                                   and not any(lk == "||" for lk, _ in chain[rank + 1:]))
                        after = _cd_into(dirs, target)
                        dirs = frozenset(after) if certain else dirs | after
                for child in _child_scripts([w.text for w in cmd.words]) if depth < 8 else ():
                    stripped, executed = _without_heredocs(child)
                    for text in (stripped, *executed):
                        paths, _, child_seen, found = _shell_walk(text, before | dirs, home, None, depth + 1)
                        out += paths
                        extra |= child_seen
                        movers += found
                dirs = _capped(dirs)
                seen |= dirs | extra
                steps.append((cmd, before, extra, kind == "cd", target))
    reach: list[frozenset[Path]] = []
    later = frozenset(dirs)
    for _, before, extra, _, _ in reversed(steps):
        later = later | before | extra
        reach.append(later)
    reach.reverse()
    raw = _raw_relative(script, home)
    for (cmd, before, _, is_cd, target), dirs_from_here in zip(steps, reach):
        if is_cd:  # a ``cd`` uses its words where it stands, before it moves
            dirs_from_here = before
        elif cmd.words:  # a command reads bare names (``cat x``, ``ls``) in the directory it runs in
            out += _resolved(".", before, home)
        if target is not None:
            out += _resolved(target, before, home)
        for segment in _segments(script[cmd.start:cmd.end]):
            for token in _segment_tokens(segment):
                out += _resolved(token, dirs_from_here, home)
        for offset, token in raw:
            if cmd.start <= offset < cmd.end:
                out += _resolved(token, dirs_from_here, home)
    spans = [(cmd.start, cmd.end) for cmd, *_ in steps]
    for offset, token in raw:  # a token no command holds: every directory of the call
        if not any(a <= offset < b for a, b in spans):
            out += _resolved(token, seen, home)
    # after ``exit``, ``return`` or ``exec`` the shell may have ended anywhere, with status 0, before the host
    # could read its directory: the end is then any directory of the script, its start included
    return out, frozenset(seen if ended else dirs), frozenset(seen), movers


def _outside_quotes(script: str, pattern: re.Pattern) -> bool:
    """Whether ``pattern`` matches at a ``$`` that stands outside quotes."""
    quote, escaped = None, False
    for at, ch in enumerate(script):
        if escaped:
            escaped = False
        elif ch == "\\" and quote != "'":
            escaped = True
        elif quote is None and ch in "'\"":
            quote = ch
        elif ch == quote:
            quote = None
        elif ch == "$" and quote is None and pattern.match(script, at):
            return True
    return False


_ANSI_QUOTE = re.compile(r"\$['\"]")
_HEADS = frozenset({"if", "then", "else", "elif", "do", "{", "!", "time"})  # a command follows in the segment
_LOOPS = frozenset({"while", "until"})
_LISTS = frozenset({"for", "select", "case", "fi", "done", "esac", "}", "in", "[[", "]]"})  # no command follows


def _walk_loose(script: str, start: frozenset[Path], home: str, depth: int
                ) -> tuple[list[tuple[Path, bool]], frozenset[Path], frozenset[Path], int]:
    """A script outside the allow-list grammar (``_shell_walk`` gives the contract): no order is trusted. The
    commands are those of ``_segments`` (each simple command at any depth, its leading reserved words and
    redirections dropped). Every ``cd`` with a target no shell expands MAY have run: once each, in the order of
    the text, when the script has no loop and no function (a shell without them runs a command at most once);
    any number of times in any order otherwise (fixed point, ``UNKNOWN_CWD`` past ``_CWD_CAP`` candidates). Any
    other ``cd``, any builtin outside the allow-list, a command name the shell builds, ``CDPATH`` and ``$'...'``
    quoting (which ``_segments`` does not read) add ``UNKNOWN_CWD``. Every relative path of the script is resolved
    against EVERY candidate directory of the script (no order), the child shells' included, and so is ``.``, the
    directory its commands run in."""
    unknown = _outside_quotes(script, _ANSI_QUOTE)
    tainted = bool(_CD_VARS_TEXT.search(script))
    repeated = bool(re.search(r"\(\s*\)", script))  # a function definition: its body may run any number of times
    targets: list[str] = []
    movers, children = 0, []
    segments = _segments(script)
    for segment in segments:
        raw, assigned, words = [r for _, r in segment], False, []
        skip = False
        for word in raw:  # redirections are not words of the command
            if skip:
                skip = False
            elif (op := _REDIRECTION.match(word)) is not None and op.group(0):
                skip = op.end() == len(word)
            else:
                words.append(word)
        while words:
            head = words[0]
            if (named := _NAME_SET.match(head)) is not None:
                assigned = True
                tainted = tainted or named.group(1) in _CD_VARS
            elif head in _LOOPS:
                repeated = True
            elif head == "function":
                repeated = True
                words.pop(0)
            elif head in ("for", "select"):
                repeated = True
                words = []
            elif head in _LISTS:
                words = []
            elif head not in _HEADS:
                break
            if words:
                words.pop(0)
        children += _child_scripts(words)
        kind, index, found = _classify(words, [_fixed(w) for w in words])
        movers += found
        if kind == "taint":
            tainted = True
        elif kind == "unknown":
            unknown = True
        elif kind == "cd":
            word = words[index] if index is not None else None
            if word is None or assigned or not _fixed(word) or word[0] in "-+":
                unknown = True
            else:
                targets.append(word)
    dirs = set(start)
    if unknown or (tainted and targets):
        dirs.add(UNKNOWN_CWD)
    if repeated:
        grown = True
        while grown and len(dirs) <= _CWD_CAP:
            more = dirs | {d for target in targets for d in _cd_into(dirs, target)}
            grown, dirs = more != dirs, more
    else:
        for target in targets:
            dirs |= _cd_into(dirs, target)
    end = _capped(dirs)
    seen, out = set(end), []
    for child in children if depth < 8 else ():
        stripped, executed = _without_heredocs(child)
        for text in (stripped, *executed):
            paths, _, child_seen, found = _shell_walk(text, end, home, None, depth + 1)
            out += paths
            seen |= child_seen
            movers += found
    if any(segments):  # a command reads bare names in the directory it runs in: any directory of the script
        out += _resolved(".", seen, home)
    for segment in segments:
        for token in _segment_tokens(segment):
            out += _resolved(token, seen, home)
    for _, token in _raw_relative(script, home):
        out += _resolved(token, seen, home)
    return out, end, frozenset(seen), movers


def _shell_walk(script: str, start: Collection[Path], home: str, proof: Mapping[str, bool] | None, depth: int = 0
                ) -> tuple[list[tuple[Path, bool]], frozenset[Path], frozenset[Path], int]:
    """Audit revision 2 reading of one script: ``(paths, end, seen, movers)``. ``start`` is the set of directories
    the shell may start in; ``end`` those it may be in when the script ends, IF it ran to its end; ``seen`` every
    directory it, or a child shell it starts, may have been in (the caller takes ``seen`` and not ``end`` when
    nothing proves the script ran to its end). Under H3 to H8, for an arm that is not trying to deceive the audit
    (the threat model and the known limits at the top of this block), ``start`` containing the real directory
    implies that ``seen`` contains every real one and ``end`` the real last one. ``paths`` are the paths the script names,
    each relative one resolved against every candidate directory it may be used in. ``proof`` is what the result
    of the call shows (``_proof``; ``None``: a child shell, a heredoc body, a host the audit does not know):
    without it no ``cd`` replaces anything. ``movers`` counts the ``cd``/``pushd``/``popd`` read as commands: when
    the text holds more such words than that (one inside quotes that ``eval`` may run, ``x=cd; $x ..``), the
    script is read again from ``start`` plus ``UNKNOWN_CWD`` with no proof. Expects the script without its heredoc
    bodies (``_without_heredocs``)."""
    start = frozenset(start)
    try:
        chains = _simple_script(script)
    except _NotSimple:
        chains = None
    for attempt in (0, 1):
        if chains is not None:
            result = _walk_simple(chains, script, start, home, proof, depth)
        else:
            result = _walk_loose(script, start, home, depth)
        if attempt or result[3] >= len(_CD_WORD.findall(script)):
            break
        start, proof = start | {UNKNOWN_CWD}, None
    return result


def _call_outcomes(lines: Sequence[str]) -> dict[str, dict[str, Any]]:
    """What the stream shows of the result of each tool call, by call id: ``text``; ``error`` (Claude
    ``is_error``, ``None`` when the stream does not say); ``plain`` (the Claude Code detail object is there and
    names neither an interruption, nor an accepted non-zero status, nor a background task); ``line`` (its rank in
    the stream). omp ``tool_execution_end``: the text only."""
    out: dict[str, dict[str, Any]] = {}
    for rank, line in enumerate(lines):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "user" and isinstance(event.get("message"), dict):
            content = event["message"].get("content")
            detail = event.get("tool_use_result")
            plain = (isinstance(detail, dict) and detail.get("interrupted") is False
                     and not detail.get("returnCodeInterpretation") and not detail.get("backgroundTaskId"))
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id"):
                    text = "\n".join(t for k, t in _strings(block.get("content")) if k in (None, "text"))
                    out[str(block["tool_use_id"])] = {"text": text, "error": block.get("is_error"),
                                                      "plain": plain, "line": rank}
        elif event.get("type") == "tool_execution_end" and event.get("toolCallId"):
            text = "\n".join(t for k, t in _strings(event.get("result")) if k == "text")
            out[str(event["toolCallId"])] = {"text": text, "error": None, "plain": False, "line": rank}
    return out


def _proof(outcome: Mapping[str, Any] | None, args: Any) -> dict[str, bool] | None:
    """What the result of a call proves (H2, H3, H6): ``clean`` (the script returned 0) and ``cd`` (no failed
    ``cd``). ``None`` without a result."""
    if outcome is None:
        return None
    text = outcome["text"]
    whole = len(text) <= _RESULT_WHOLE and not _TRUNCATED.search(text)
    background = isinstance(args, Mapping) and bool(args.get("run_in_background"))
    clean = (outcome["error"] is False and outcome["plain"] and whole and not background
             and not text.startswith("Exit code"))
    return {"clean": clean, "cd": whole and not _CD_LINE.search(text)}


def _host_model(lines: Sequence[str]) -> str:
    """``claude-code-<version>`` when every ``system``/``init`` event of the stream names the same version of
    ``OBSERVED_CLAUDE_CODE`` (H1), else ``unverified`` (H9: no directory is carried, no ``cd`` is believed)."""
    versions = set()
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "system" and event.get("subtype") == "init":
            versions.add(event.get("claude_code_version"))
    if len(versions) == 1 and next(iter(versions)) in OBSERVED_CLAUDE_CODE:
        return f"claude-code-{next(iter(versions))}"
    return "unverified"


def _overlapping(lines: Sequence[str], outcomes: Mapping[str, Mapping[str, Any]]) -> set[str]:
    """Ids of the shell calls sent before the result of an earlier shell call came back (their order of
    execution is not known; none in the v4 streams)."""
    found: set[str] = set()
    pending: list[tuple[str, float]] = []
    for rank, line in enumerate(lines):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") != "assistant" \
                or not isinstance(event.get("message"), dict):
            continue
        for block in event["message"].get("content") or []:
            if not (isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id")
                    and any(k in _COMMAND_KEYS for k, _ in _strings(block.get("input")))):
                continue
            pending = [(i, end) for i, end in pending if end > rank]
            if pending:
                found |= {str(block["id"]), *(i for i, _ in pending)}
            end = (outcomes.get(str(block["id"])) or {}).get("line", float("inf"))
            pending.append((str(block["id"]), end))
    return found


def _next_session(start: Collection[Path], end: Collection[Path], seen: Collection[Path], clean: bool, result: str,
                  bundle: Path) -> frozenset[Path]:
    """The directories a Claude Code shell (H1) may start the next call in. A result that says ``Shell cwd was
    reset to ...`` decides: the project root (the shallowest directory of the project, so a spoofed line can only
    make the audit stricter). Otherwise: ``end`` after a clean result; after any other result every directory the
    call may have been in, its start included (the script may have stopped anywhere, and whether the host keeps
    the directory of a call that failed is not established). A candidate outside the project WITHOUT that line is
    not the observed behaviour: it is kept, with the root."""
    if _CWD_RESET.search(result):
        return frozenset({bundle})
    dirs = set(end) if clean else {*start, *seen}
    inside = {c for c in dirs if c != UNKNOWN_CWD and _within(Path(os.path.realpath(c)), bundle)}
    nxt = set(inside)
    if len(inside) != len(dirs):
        nxt |= dirs | {bundle}
    return _capped(nxt)


def _result_token(token: str) -> str:
    """A path token of result text without a ``file.py:12:`` location suffix or a trailing ``:``/``.``."""
    return re.sub(r":\d+(?::\d+)?:?$", "", token).rstrip(":.")


def base_literals(bundle: Path, home: str) -> frozenset[str]:
    """Literal absolute paths under the real ``home`` that the bundle's OWN files contain (a qualification
    doc of the base that records ``/Users/<u>/.codex/worktrees/...``), with the token shapes of the
    tool-result scan. Read with ``git grep`` on the bundle as built, BEFORE any arm runs (and before a
    previous attempt's patch is applied): the arm cannot add to it. Such a path shown in a tool RESULT is
    the text of a bundle file, not an access (``audit_transcript``)."""
    prefixes = list(dict.fromkeys(p.rstrip("/") + "/" for p in (str(home), os.path.realpath(home)) if p))
    patterns = [arg for prefix in prefixes for arg in ("-e", prefix)]
    out = _git_in(bundle, "grep", "-I", "-h", "-F", *patterns, ok=(0, 1))
    found = set()
    for line in out.decode("utf-8", "replace").splitlines():
        for token in _path_tokens(line):
            token = _result_token(token)
            if token.startswith(tuple(prefixes)):
                found.add(token)
    return frozenset(found)


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
                     home: str, arm_home: str | None = None,
                     own_session: tuple[str, str] | None = None,
                     literals: Collection[str] = frozenset(),
                     sandbox_denied: tuple[str, Sequence[str], Sequence[str]] | None = None,
                     attempt_dir: Path | None = None, private_root: Path | None = None,
                     revision: int = 1, not_found: list[str] | None = None,
                     host_model: list[str] | None = None,
                     trace: list[frozenset[Path]] | None = None,
                     policy_denied: tuple[Sequence[Path], Sequence[Path]] | None = None,
                     journal: list[str] | None = None,
                     refused: dict[str, int] | None = None, absent_forms: bool = False) -> list[str]:
    """What an arm's stream shows it did outside its bundle and attempt directory. Only the path
    arguments of its tool calls (``_PATH_KEYS``, the ``pattern`` of a Glob/find tool) and its shell
    commands are read: the text it writes or searches (Edit ``old_string``/``new_string``, Write
    ``content``, Grep ``pattern``...) is not an access. Paths (relative ones resolved against the bundle,
    or against the directory of a ``cd``) that fall among ``sensitive`` (plugin cache and the rest of the
    user's configuration, other checkouts of this repository, the launcher's own files); for a path key
    or a Bash command also any path that resolves to the real home or below it, to a directory that
    contains a sensitive path, or to an ancestor of the bundle (``find ~``, ``src/../../..``). In a
    command, ``~`` and ``$HOME`` are expanded only where the shell expands them (``grep -rn '~/.claude'
    .`` is clean, ``cat ~/.config/x`` is not), and to ``arm_home``, the HOME the arm really had (the
    isolated one, inside the attempt directory, for a local arm; the real ``home`` by default). A
    heredoc body is text unless a shell runs it, a ``#`` comment is text, and a separator inside quotes
    does not split a command (``grep -E "claude|codex" src``): see ``_without_heredocs`` and
    ``_segments``. ``tool_result:<path>`` for a LITERAL absolute path under a sensitive root that a tool
    RESULT shows (``~``/``$HOME`` in result text are not expanded: a file of the bundle that mentions
    ``~/.claude`` is text, not an access); ``command:<label>`` for a Bash command that runs a forbidden
    executable (see ``FORBIDDEN_EXECUTABLES``, ``git push|remote|clone|fetch|pull``, ``python -m http``,
    ``pip install``). ``own_session`` (``(projects_dir, session_id)``, a cloud arm) allows the arm's OWN
    session directory ``<projects_dir>/*/<session_id>/``, where Claude Code saves a tool output too large
    for the stream (the result names it, the arm then reads it); another session's directory stays a
    hit. ``literals`` (``base_literals``): a path whose literal text is one of them is not flagged in a tool
    RESULT (the arm read a bundle file that mentions it); path keys and commands that name it still are.
    ``sandbox_denied`` (``(home, allowed, denied)``, a LOCAL arm run under the deny-home profile): a path
    the arm's own sandbox made unreadable (under ``home`` and outside ``allowed``, or under an entry of
    the explicit ``denied`` list) is a BLOCKED attempt, not an access, and is not flagged: the read
    the sandbox refuses failed (EPERM). The profile is ``(allow default)``, so a bypass through a system
    service (``launchctl submit``, ``osascript``) would not be refused: those are forbidden commands
    (``FORBIDDEN_EXECUTABLES``) and contaminate. Commands, climbs out of the bundle to readable places and
    readable paths are audited as before. A cloud arm has no such sandbox: never passed for it.
    ``attempt_dir`` (the launcher-managed parent of the bundle and scratch) is allowed itself, so ``ls ..``
    from the bundle is clean; its parent (the work root, which may hold another attempt) is not.
    ``private_root`` (PAT-121, ``isolation.private_attempt_root``: the parent of ``attempt_dir``, which holds
    nothing but this attempt) is allowed too, so ``ls ../..`` is clean; the caller then lists the work root
    among ``sensitive``, so the work root itself and every other attempt under it stay hits.
    Best effort on a command line (a path or a command built at run time, or run by a script, is
    not seen). A non-empty result makes the attempt ``contaminated``. ``~`` shows as the home directory
    in the result. Known limit: a token made only of slashes (``/``, ``//``) is the division operator of
    code, not a path, EXCEPT as an argument of a filesystem reader (``find``, ``grep -r``, ``rg``, ``ls``,
    ``du``, ``tree``, ``cat``): ``find / -name x -exec cat {} +`` is flagged, while a bare ``/`` reaching
    the shell any other way (``echo / | xargs ls``, a script) is not seen.

    ``revision`` 2 (PAT-123, ``isolation.audit_revision``; 1 keeps everything above as it was): a relative
    path of a command is resolved against every directory the shell MAY be in where the command runs, and is a
    hit as soon as one of them takes it out of the allowed places (``_shell_walk``; the assumptions H1 to H9
    under which those candidates contain the real directory are listed above ``OBSERVED_CLAUDE_CODE``, with the
    threat model: a journal of the accidental excursions of an arm that is not trying to deceive the audit, not
    a barrier; the known limits are listed there). The raw token pass no longer resolves a relative token against the bundle:
    ``_shell_walk`` does, against the candidates. A ``cd`` replaces the candidates only in a script of the
    allow-list grammar whose result proves it ran (``_walk_simple``); everywhere else it adds its target, or
    ``UNKNOWN_CWD`` (every relative path is then a hit). In a stream of an observed Claude Code version
    (``OBSERVED_CLAUDE_CODE``) the candidates pass from one shell call to the next (``_next_session``); in any
    other stream (omp, another or an unnamed version) every call starts from the union of the bundle and of
    every directory an earlier call may have been in, and no ``cd`` replaces anything. ``host_model`` receives
    which of the two applied (``claude-code-<version>`` or ``unverified``) and ``trace`` the candidates each
    shell call started from. ``cd $T`` after ``T=$(mktemp -d)`` is a ``cd`` the audit cannot read
    (``UNKNOWN_CWD``): a fresh temporary directory is NOT modelled (the audit could not see a symbolic link the arm
    puts in it). A command that may run in a directory outside the allowed places is a hit by itself (a bare name,
    ``cat x``, is read there). As in revision 1, a path built at run time is not seen. A path an omp tool call gave and the tool answered ``Path not found: <that path>`` was not
    read: it is not a hit, nor is the echo of it in the error result; it is appended to ``not_found`` (apart,
    never counted as a read). A path that exists, a command and every other failure text stay audited as
    before. ``absent_forms`` (PAT-128, ``isolation.audit_absent_path_forms``, revision 2; off keeps everything above
    as it was): the answer ``Path '<that path>' not found`` is the same absent path (omp ``read``).

    ``policy_denied`` (PAT-126, ``isolation.audit_policy``, revision 2 and an observed barrier only: ``(denyRead,
    allowRead)`` of the settings the execution received): (1) a tool call whose result is a whole-call refusal of the
    host (``_refused_call``, an error result) did not run: it contributes nothing and moves no directory; it is counted
    per tool in ``refused`` (no text); (2) a finding of a SHELL command whose path lies under a ``denyRead`` root and
    outside every ``allowRead`` entry, or at ``UNKNOWN_CWD``, is appended to ``journal`` and left out of the result; a ``tool_result:`` path under such a root (or a truncated
    prefix of one) is journaled too; every other finding (a file-tool path, a path the OS does not deny, a result path
    outside them, a forbidden command) stays in the result.
    Without it the result is exactly what it was."""
    try:
        lines = Path(stream_log).read_text("utf-8", "replace").splitlines()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise RunnerError(f"transcript unreadable, it cannot be audited: {exc}") from None
    bundle_real, home_real = Path(os.path.realpath(bundle)), Path(os.path.realpath(home))
    tilde = os.path.realpath(arm_home) if arm_home is not None else str(home_real)
    allowed = [bundle_real, Path(os.path.realpath(scratch))]
    if attempt_dir is not None:
        allowed.append(Path(os.path.realpath(attempt_dir)))
    if private_root is not None:
        allowed.append(Path(os.path.realpath(private_root)))
    roots = [Path(os.path.realpath(p)) for p in sensitive]
    projects: Path | None = None
    if own_session is not None and own_session[1]:
        expanded = _expand(own_session[0], str(home_real))
        projects = Path(os.path.realpath(os.path.expanduser(own_session[0]) if expanded is None else expanded))

    walled: tuple[Path, list[Path], list[Path]] | None = None
    if sandbox_denied is not None:
        walled = (Path(os.path.realpath(sandbox_denied[0])),
                  [Path(os.path.realpath(p)) for p in sandbox_denied[1]],
                  [Path(os.path.realpath(p)) for p in sandbox_denied[2]])

    def blocked(path: Path) -> bool:  # the local sandbox refused this read: an attempt, not an access
        if walled is None:
            return False
        w_home, w_allow, w_deny = walled
        return (any(_within(path, d) for d in w_deny)
                or (_within(path, w_home) and not any(_within(path, a) for a in w_allow)))

    def permitted(path: Path) -> bool:
        if any(_within(path, a) for a in allowed) or blocked(path):
            return True
        if projects is None or path == projects or projects not in path.parents:
            return False
        parts = path.relative_to(projects).parts
        return len(parts) >= 2 and parts[1] == own_session[1]

    def shown(path: Path) -> str:
        return str(path).replace(str(home_real), "~", 1)

    hits: dict[str, None] = {}
    hit_paths: dict[str, Path] = {}
    file_tool: set[str] = set()  # hits some FILE-TOOL call (not only a shell command) produced
    commands: dict[str, None] = {}
    follow = revision >= AUDIT_REVISION
    missing = _missing_paths(lines, absent_forms) if follow else {}
    outcomes = _call_outcomes(lines) if follow else {}
    policy = follow and policy_denied is not None
    refused_ids: set[str] = set()
    if policy:
        names = {str(i): n for i, n, _, _ in _tool_events(lines) if i is not None}
        refused_ids = {i for i, o in outcomes.items() if o.get("error") is True
                       and _refused_call(names.get(i, ""), o["text"])}
    model = _host_model(lines) if follow else "unverified"
    if follow and host_model is not None:
        host_model.append(model)
    carried = model != "unverified"  # H1; otherwise H9: no directory is carried, no ``cd`` is believed
    session = frozenset({bundle_real})  # H1: where the shell may start the next call
    anywhere = {bundle_real}  # H9: every directory a call may have been in so far
    unordered = (_overlapping(lines, outcomes) - refused_ids) if follow else set()  # order of execution not known
    unordered_moves = any(
        set(_shell_walk(_without_heredocs(text)[0], {bundle_real}, tilde, None)[2]) != {bundle_real}
        for call_id, _, args, _ in _tool_events(lines) if call_id is not None and str(call_id) in unordered
        for key, text in _strings(args) if key in _COMMAND_KEYS)

    def consider(path: Path, broad: bool, relative: bool = False, shell: bool = False) -> None:
        if permitted(path):
            return
        if any(_within(path, r) for r in roots) or relative or (broad and (
                _within(path, home_real) or _within(bundle_real, path)
                or any(_within(r, path) for r in roots))):
            hits[shown(path)] = None
            hit_paths[shown(path)] = path
            if not shell:
                file_tool.add(shown(path))

    for call_id, name, args, host in _tool_events(lines):
        if call_id is not None and str(call_id) in refused_ids:  # the host refused the whole call: it did not run
            if refused is not None:
                refused[name] = refused.get(name, 0) + 1
            continue
        path_keys = _PATH_KEYS | ({"pattern"} if name.lower() in _GLOB_TOOLS else frozenset())
        kept = follow and carried and host == "claude"
        ordered = call_id is not None and str(call_id) not in unordered
        call_start = frozenset(session if kept else anywhere)
        if kept and not ordered and unordered_moves:
            call_start = call_start | {UNKNOWN_CWD}
        call_end: frozenset[Path] | None = None
        call_seen, call_clean, scripts_run = call_start, True, 0
        if follow and trace is not None and any(k in _COMMAND_KEYS for k, _ in _strings(args)):
            trace.append(call_start)
        for key, text in _strings(args):
            if call_id in missing and key == "path" and text == missing[call_id]:
                if not_found is not None:
                    not_found.append(shown(Path(os.path.realpath(text))))
                continue
            if key in _COMMAND_KEYS:
                unclosed: list[bool] = []
                stripped, executed = _without_heredocs(text, unclosed)
                scripts = [stripped, *executed]  # a body a shell runs is a command line too
                tokens = [t for script in scripts for t in _path_tokens(_unquoted_expansions(script))]
                # H7: a heredoc never closed, or a ``cd`` word of the WHOLE text (comments and heredoc bodies
                # included) that no script read as a command: the readers may not have split as the shell does
                unread = follow and (bool(unclosed) or len(_CD_WORD.findall(text)) > sum(
                    _shell_walk(script, {bundle_real}, tilde, None)[3] for script in scripts))
            elif key in path_keys:
                tokens = _path_tokens(text) + [text.strip()]
            else:  # text the arm writes or searches (Edit, Write, Grep pattern...): not an access
                continue
            for token in tokens:
                token = _expand(token, tilde)
                if token is not None and follow and key in _COMMAND_KEYS and not os.path.isabs(token):
                    continue  # read by ``_shell_walk``, against the directories the shell may be in there
                if token is not None:
                    consider(Path(os.path.realpath(token if os.path.isabs(token) else bundle / token)),
                             key in path_keys, key in path_keys and not os.path.isabs(token),
                             shell=key in _COMMAND_KEYS)
            if key in _COMMAND_KEYS:
                for n, script in enumerate(scripts):
                    if not follow:
                        walked = _command_paths(script, bundle_real, tilde)
                    elif n == 0:  # the line itself; a heredoc body a shell runs may start wherever the line was
                        scripts_run += 1
                        proof = _proof(outcomes.get(str(call_id)), args) \
                            if kept and ordered and scripts_run == 1 and not unread else None
                        call_clean = proof is not None and proof["clean"]
                        walked, end, seen_now, _ = _shell_walk(
                            script, call_seen | ({UNKNOWN_CWD} if unread else set()), tilde, proof)
                        call_end = end if call_end is None else call_end | end
                        call_seen = call_seen | seen_now
                    else:
                        walked = _shell_walk(script, call_seen, tilde, None)[0]
                    for path, relative in walked:
                        consider(path, True, relative, shell=True)
                    for found in _forbidden_commands(script):
                        commands[f"command:{found}"] = None
        if follow and call_end is not None:
            if kept:  # an unordered call proves nothing, not even by its ``Shell cwd was reset`` line
                said = (outcomes.get(str(call_id)) or {}).get("text", "") if ordered else ""
                session = _next_session(call_start, call_end, call_seen, call_clean and scripts_run == 1, said,
                                        bundle_real)
            else:
                anywhere |= call_seen
    seen: dict[str, None] = {}
    echoed: dict[str, None] = {}  # policy: result paths under a root the OS denies to the shell (journal)
    if policy:
        denied_roots = [Path(os.path.realpath(p)) for p in policy_denied[0]]
        allowed_roots = [Path(os.path.realpath(p)) for p in policy_denied[1]]

    def os_denied(path: Path, raw: str = "") -> bool:
        """Under a ``denyRead`` root and outside every ``allowRead`` entry; a truncated path (an output cut by the
        arm's own ``sed`` or ``cut``) is under it when its text starts with the denied root followed by ``/``: a sibling
        directory whose name merely begins like the root (``<root>-other``) is not."""
        if any(_within(path, a) for a in allowed_roots):
            return False
        return any(_within(path, d) or (raw != "" and raw.startswith(str(d).rstrip("/") + "/")) for d in denied_roots)

    for text in _tool_results(lines):
        if follow and ((text.startswith(_NOT_FOUND) and text[len(_NOT_FOUND):] in missing.values())
                       or (absent_forms and any(text == _quoted_not_found(m) for m in missing.values()))):
            continue  # the error of a path that does not exist: it names the path, it shows nothing
        for token in _path_tokens(text):
            token = _result_token(token)  # ``file.py:12:`` locations
            if token in literals:  # the text of a bundle file (``base_literals``), not an access
                continue
            if os.path.isabs(token):  # a literal path only: ``~``/``$HOME`` in text are not expanded
                path = Path(os.path.realpath(token))
                if any(_within(path, r) for r in roots) and not permitted(path):
                    # policy: text shown in an output is not an access, and the shell that printed it could not read there
                    (echoed if policy and os_denied(path, token) else seen)[f"tool_result:{shown(path)}"] = None
    if policy:  # journal what the operating system denies to the shell anyway, and the directory nobody can name
        def journaled(name: str) -> bool:
            path = hit_paths[name]
            if name in file_tool:
                return False
            if path == UNKNOWN_CWD or UNKNOWN_CWD in path.parents:
                return True
            return (any(_within(path, d) for d in denied_roots)
                    and not any(_within(path, a) for a in allowed_roots))

        moved = [h for h in hits if journaled(h)]
        if journal is not None:
            journal.extend(sorted(moved))
            journal.extend(sorted(echoed))
        hits = {h: None for h in hits if h not in moved}
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


_TEMP_OWN = re.compile(r"foundry-|pytest-of-")  # the launcher's own temp dirs and pytest's own tree: never touched


TEMP_WALK_BOUND = 20000  # entries read under one new temp entry before the inspection gives up (recorded, never "no")


def _temp_names(directory: Path) -> set[str] | None:
    """Top-level NAMES of the shared per-user temp directory (nothing is stat-ed: an entry vanishing meanwhile cannot
    fail the listing). ``None`` when the directory cannot be listed: unknown, never "empty"."""
    try:
        return set(os.listdir(directory))
    except OSError:
        return None


def _holds_bundle_material(entry: Path, files: Sequence[str]) -> bool | None:
    """Does a new temp entry hold a copy of bundle material (bounded walk, symbolic links not followed)? A tree that
    contains ``plugins/foundry``, a file whose path relative to the entry equals a changed file of the task, or a file
    named like a changed product or test file (``__init__.py`` and ``conftest.py`` excepted). ``None`` (not known,
    never "no") when the walk gave up after ``TEMP_WALK_BOUND`` entries or could not read a directory."""
    rels = {f for f in files}
    names = {Path(f).name for f in files} - {"__init__.py", "conftest.py"}
    if not entry.is_dir() or entry.is_symlink():
        return entry.name in names
    seen = 0
    unread: list[OSError] = []
    for dirpath, dirs, fnames in os.walk(entry, followlinks=False, onerror=unread.append):
        rel = os.path.relpath(dirpath, entry).replace(os.sep, "/")
        if f"/{rel}/".find("/plugins/foundry/") != -1 or rel.endswith("plugins/foundry"):
            return True
        for name in fnames:
            seen += 1
            if name in names or f"{rel}/{name}".removeprefix("./") in rels:
                return True
        seen += len(dirs)
        if seen > TEMP_WALK_BOUND:
            return None
    return None if unread else False


TEMP_QUARANTINE = "temp-quarantine"  # under the state directory (denied to the arms): moved leftovers, never deleted
# Why one cloud execution was not watched at all (``audit.temp_leftovers.unwatched``): nothing was moved for it and
# what it left in the temp directory is unknown.
TEMP_UNWATCHED = ("no_tmpdir", "listing_before_failed", "listing_after_failed")


def _move_temp_leftovers(directory: Path, before: Collection[str] | None, dest: Path,
                         files: Sequence[str]) -> dict[str, Any]:
    """PAT-126: every top-level entry of ``directory`` whose NAME was absent from ``before`` (the names listed right
    before the execution), owned by this user, not a symbolic link, not the launcher's own or pytest's own tree, that
    holds bundle material is MOVED into ``dest``, a quarantine directory of the launcher under its state directory.
    Nothing is ever deleted, here or later: the launcher keeps the quarantine. An entry that existed before is never
    touched, even when it changed during the execution. The launcher cannot tell who created a new entry: one created
    by ANOTHER process of the same account during the execution, and matching, is moved to the quarantine too (kept
    whole, to be put back by hand).

    Absent data is never a zero. ``before`` ``None`` (the listing made before the execution failed): NOTHING is
    moved - without it no entry can be told new, and a pre-existing one must never be touched - and the result says
    ``unwatched: listing_before_failed``; the listing made after failing gives ``listing_after_failed``. Otherwise
    the result holds ``moved`` (entry names, no content, no path; a name already taken in ``dest`` gets a numeric
    suffix there, the recorded name stays the original one), ``not_moved`` (a new entry that could not be examined or
    moved: it is still in the temp directory) and ``not_inspected`` (a new entry whose walk gave up at
    ``TEMP_WALK_BOUND`` entries or could not read a directory: left in place, not known to hold bundle material or
    not). An entry that vanished meanwhile is not a leftover. No ``OSError`` leaves this function: a paid execution
    is never aborted by its temp watch."""
    if before is None:
        return {"unwatched": "listing_before_failed"}
    after = _temp_names(directory)
    if after is None:
        return {"unwatched": "listing_after_failed"}
    out: dict[str, Any] = {"moved": [], "not_moved": [], "not_inspected": []}
    for name in sorted(after):
        if name in before or _TEMP_OWN.match(name):
            continue
        entry = directory / name
        try:
            if entry.is_symlink() or entry.lstat().st_uid != os.getuid():
                continue
            holds = _holds_bundle_material(entry, files)
            if holds is None and os.path.lexists(entry):
                out["not_inspected"].append(name)
            if not holds:
                continue
            dest.mkdir(parents=True, exist_ok=True)
            target, n = dest / name, 0
            while os.path.lexists(target):  # never overwrite what an earlier execution left here
                n += 1
                target = dest / f"{name}.{n}"
            shutil.move(str(entry), str(target))
            out["moved"].append(name)
        except OSError:
            if os.path.lexists(entry):  # still there: examined or moved in vain, said so (a vanished entry is none)
                out["not_moved"].append(name)
    return out


def _temp_note(watches: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """``audit.temp_leftovers`` of one record, from the watch of each of its cloud executions. ``watch``:
    ``complete`` (every execution listed before and after, every matching entry moved, every new entry inspected),
    ``unavailable`` (no execution was watched), ``partial`` (anything in between), ``not_applicable`` (the record
    holds no cloud execution: a local exploration is not watched). ``count`` is the number of entries moved, and is
    ``None`` - never 0 - as soon as one execution of the record was not watched."""
    if not watches:
        return {"watch": "not_applicable", "count": None, "names": [], "not_moved": [], "not_inspected": [],
                "unwatched": []}
    unwatched = [w["unwatched"] for w in watches if w.get("unwatched")]
    names = [n for w in watches for n in w.get("moved", [])]
    not_moved = [n for w in watches for n in w.get("not_moved", [])]
    not_inspected = [n for w in watches for n in w.get("not_inspected", [])]
    watch = ("unavailable" if len(unwatched) == len(watches) else
             "partial" if unwatched or not_moved or not_inspected else "complete")
    return {"watch": watch, "count": None if unwatched else len(names), "names": names, "not_moved": not_moved,
            "not_inspected": not_inspected, "unwatched": unwatched}


def probe_interpreters(host_env: Mapping[str, str], driver: Mapping[str, Any] | None) -> dict[str, Any]:
    """PAT-126: can the interpreters that matter import pytest? ``launcher``: ``sys.executable -P`` with the judge's
    environment (the judge runs pytest with it); ``arm_python3`` and ``arm_python``: what ``python3`` and ``python``
    resolve to on the PATH the launcher gives a cloud arm (``isolated_environment``, the R6 allow-list). Paths are
    reported with the home masked as ``~``; ``refusals`` names each failure. Read-only: runs ``-c`` one-liners only."""
    home = str(host_env.get("HOME") or os.path.expanduser("~"))

    def mask(text: str) -> str:
        return text.replace(home, "~") if home and home != "/" else text

    def import_pytest(exe: str, args: Sequence[str], env: Mapping[str, str], cwd: str) -> tuple[str | None, str]:
        try:
            done = subprocess.run([exe, *args, "-c", "import pytest; print(pytest.__version__)"], env=dict(env),
                                  cwd=cwd, capture_output=True, text=True, timeout=60, check=False,
                                  stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as exc:
            return None, type(exc).__name__
        lines = done.stdout.strip().splitlines()
        return (lines[-1] if done.returncode == 0 and lines else None), ""

    out: dict[str, Any] = {"refusals": []}
    with tempfile.TemporaryDirectory(prefix="foundry-probe-") as tmp:
        judge_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(Path(tmp) / "home"),
                     "TMPDIR": tmp, "PYTHONNOUSERSITE": "1"}
        version, why = import_pytest(sys.executable, ["-P"], judge_env, tmp)
        out["launcher"] = {"path": mask(sys.executable), "pytest": version}
        if version is None:
            out["refusals"].append(f"launcher_python_cannot_import_pytest:{mask(sys.executable)}{why and ':' + why}")
        if driver is not None:
            env = isolated_environment(driver, Path(tmp) / "arm", host_env)
            for name in ("python3", "python"):
                found = shutil.which(name, path=env.get("PATH") or os.defpath)
                if found is None:
                    out[f"arm_{name}"] = {"path": None, "pytest": None}
                    out["refusals"].append(f"arm_{name}_not_found_on_the_arm_path")
                    continue
                version, why = import_pytest(found, [], env, tmp)
                out[f"arm_{name}"] = {"path": mask(found), "pytest": version}
                if version is None:
                    out["refusals"].append(f"arm_{name}_cannot_import_pytest:{mask(found)}{why and ':' + why}")
    return out


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
                 screening_campaign: str | None = None, truths: Mapping[str, Any] | None = None):
        if mode not in MODES:
            raise RunnerError(f"unknown mode {mode!r}")
        if (mode in EXPLORE_MODES) != (campaign.get("schema") == CAMPAIGN_SCHEMA_V2):
            raise RunnerError(f"mode {mode!r} does not match the campaign schema "
                              f"{campaign.get('schema')!r} (protocol v2 modes need a v2 campaign, and "
                              "protocol v1 modes a v1 campaign)")
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
        if (self.state_dir / NATIVE_TRIAL_MARKER).exists() and campaign.get(NATIVE_TRIAL_KEY) is not True:
            raise RunnerError(f"{self.state_dir} holds a native-sandbox trial (marker {NATIVE_TRIAL_MARKER}): "
                              "a campaign never shares a state directory with a trial")
        # PAT-126: the pilot of protocol v5 and its campaign never share a campaign id (results and ledger files)
        pilot = campaign.get("protocol") == PROTOCOL_V5_PILOT
        if campaign.get("protocol") in V5_PROTOCOLS and pilot != ("pilot" in envelope["campaign_id"]):
            raise RunnerError(f"protocol {campaign['protocol']} needs a campaign id "
                              f"{'containing' if pilot else 'NOT containing'} 'pilot' (the pilot is never mixed with "
                              f"the campaign): got {envelope['campaign_id']!r}")
        self.results_path = self.state_dir / f"results-{envelope['campaign_id']}.jsonl"
        self.seen = self._scan_results()  # refuses a mixed or foreign state before anything starts
        self.prior_ledger: list[dict[str, Any]] = []
        if sandbox:  # a configuration error must cost nothing: checked before any claim or reservation
            for driver in campaign["drivers"].values():
                if (mode in CLOUD_MODES or driver["kind"] in LOCAL_KINDS) and driver.get("sandbox", True):
                    sandbox_text(driver, [self.work_root, *_extra_write(driver)], self._deny_read(driver),
                                 *self._home_policy(driver))
        self.sessions: list[str] = []  # session id of every cloud execution reserved in the ledger
        self.audited: dict[str, list[str]] = {}  # contamination found in each audited cloud execution
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
        self.literals: dict[Path, frozenset[str]] = {}  # ``base_literals`` of each bundle handed out
        self.exploring = mode in EXPLORE_MODES  # protocol v2: exploration screening and comparison
        # protocol v3: at most one undecided task per launch (the operator reloads the model between launches);
        # ``work_remains`` is set by such a launch (None when the rule is off or the launch was cut)
        self.one_task = bool((campaign.get("exploration") or {}).get("one_task_per_launch")) and self.exploring
        self.work_remains: bool | None = None
        # protocol v4 (PAT-121), all off when the keys are absent: the hidden-test feedback of a refusal, a
        # private parent directory per attempt, and a candidate fixed by the protocol (no screening)
        feedback = campaign.get("correction_feedback") or {}
        self.feedback_spec = feedback if feedback.get("hidden_test_failures") else None
        self.private_root = bool((campaign.get("isolation") or {}).get("private_attempt_root"))
        # PAT-123 (a protocol after v4 only, see ``load_campaign``): the repaired audit and capture
        self.audit_revision = (campaign.get("isolation") or {}).get("audit_revision", 1)
        self.patch_excludes = _PATCH_EXCLUDES_V2 if self.audit_revision >= AUDIT_REVISION else _PATCH_EXCLUDES
        self.not_found: dict[str, list[str]] = {}  # paths an arm named that do not exist, by stream log
        self.host_models: dict[str, str] = {}  # audit revision 2: what the audit could assume of each stream's host
        # PAT-124 (a protocol after v4 only): the cloud drivers run under Claude Code's native Bash sandbox
        self.native_sandbox = bool((campaign.get("isolation") or {}).get(NATIVE_SANDBOX_KEY))
        # bundle -> paths under ``.claude`` removed from the patch applied to it (filled under the key only)
        self.stripped: dict[Path, list[str]] = {}
        self.settings_sent: set[str] = set()  # stream logs of the executions that started with ``--settings``
        self.barriers: dict[str, str] = {}  # what the launcher verified of the barrier, by audited stream log
        self.roles: dict[str, str] = {}  # role of each reserved cloud session
        self.fixed_candidate = (campaign.get("exploration") or {}).get("fixed_candidate")
        # PAT-126 (a protocol after v4 only): a judge that cannot run is an instrument error, never a 0/0/0 refusal,
        # and the interpreters the judge and the arms will use are probed before anything is claimed
        self.strict_judge = bool(_PROTOCOL_AFTER_V4.fullmatch(str(campaign.get("protocol"))))
        self.interpreters: dict[str, Any] | None = None
        self.audit_policy = (campaign.get("isolation") or {}).get(AUDIT_POLICY_KEY) == AUDIT_POLICY
        self.absent_forms = (campaign.get("isolation") or {}).get(ABSENT_FORMS_KEY) == ABSENT_FORMS  # PAT-128
        self.bundle_files: dict[Path, list[str]] = {}  # changed files of the task, by bundle (temp leftovers match)
        self.leftovers: dict[str, dict[str, Any]] = {}  # the temp watch of each cloud execution, by stream
        self.stream_roles: dict[str, str] = {}  # role of each audited cloud stream log
        self.journals: dict[str, list[str]] = {}  # audit policy: journaled findings by stream log
        self.refusals: dict[str, dict[str, int]] = {}  # audit policy: calls the host refused, per tool, by stream log
        # PAT-126 (a protocol after v4 only): the ``task.set`` label of the comparison records ("comparison" before)
        self.compare_set = _compare_set(campaign)
        self.screen_path = "XS" if self.exploring else "S"  # path name of the screening attempts
        self.dedicated: dict[str, Any] | None = None  # observed values of the last dedicated-machine check
        self.truths: dict[Any, dict[str, Any]] = {}  # localization ground truth of each task, by PR
        self.frozen_truths = truths  # the committed truth file (CLI); ``None``: computed from the repository

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
        return _resume_state(self.prior_records, self.prior_ledger,
                             (path, task["pr"], task_set, candidate, segment, attempt))

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

    def _audited_since(self, mark: int) -> list[str]:
        """Contamination found by the audits of the cloud executions reserved since ``mark``."""
        return list(dict.fromkeys(c for sid in self.sessions[mark:] for c in self.audited.get(sid, [])))

    def _flags_by_role(self, mark: int) -> dict[str, list[str]]:
        """``_audited_since`` with the reviewer's flags apart (audit revision 2: they stay on the review)."""
        arm, reviewer = [], []
        for sid in self.sessions[mark:]:
            (reviewer if self.roles.get(sid) == "reviewer" else arm).extend(self.audited.get(sid, []))
        return {"contamination": list(dict.fromkeys(arm)), "review_contamination": list(dict.fromkeys(reviewer))}

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
        check_binary_version(driver, env)  # before any claim or reservation (local_attempt, screen, compare)
        return resolve_executable(driver, env)

    def _check_harness(self, driver_id: str) -> None:
        """A harness executable that cannot be resolved is refused before any claim or spend."""
        self._executable(self._driver(driver_id))

    def _audit(self, stream_log: Path, bundle: Path, scratch: Path, driver: Mapping[str, Any],
               session_id: str | None = None, exe: Mapping[str, str] | None = None,
               native: Mapping[str, Any] | None = None) -> list[str]:
        """``_audit_stream`` that keeps the paths the arm named without their existing (audit revision 2)
        in ``self.not_found``, to be recorded apart. Under ``isolation.audit_policy`` and an observed barrier
        (``native``: the settings this execution received) the returned list is the DECISIVE findings only; the
        journaled ones and the calls the host refused are kept apart (``self.journals``, ``self.refusals``)."""
        notes: list[str] = []
        models: list[str] = []
        barrier = self._stream_barrier(stream_log, driver)
        denied = None
        if self.audit_policy and barrier == BARRIER_OBSERVED and native is not None:
            fs = (native.get("sandbox") or {}).get("filesystem") or {}
            denied = (fs.get("denyRead") or [], fs.get("allowRead") or [])
        journal: list[str] = []
        refused: dict[str, int] = {}
        hits = self._audit_stream(stream_log, bundle, scratch, driver, session_id, exe, notes, models,
                                  policy_denied=denied, journal=journal, refused=refused)
        self.not_found[str(stream_log)] = list(dict.fromkeys(notes))
        self.host_models[str(stream_log)] = models[0] if models else "unverified"
        self.barriers[str(stream_log)] = barrier
        self.journals[str(stream_log)] = journal
        self.refusals[str(stream_log)] = refused
        return hits

    def _stream_barrier(self, stream_log: Path, driver: Mapping[str, Any]) -> str:
        """What the launcher verified of the native sandbox for ONE audited stream (``BARRIERS``). It never
        observes the operating system enforce the sandbox: ``--settings`` was passed to a process that started
        (``BARRIER_SETTINGS``), and the stream names one Claude Code version, the permission mode asked for when
        it names one, and ends on a ``result`` that is not an error (``BARRIER_OBSERVED``). A permission mode that
        differs from the one asked for is a contradiction: ``not_verified``."""
        if (not self.native_sandbox or driver["kind"] not in CLOUD_KINDS
                or str(stream_log) not in self.settings_sent):
            return AUDIT_BARRIER
        versions: set[Any] = set()
        modes: set[Any] = set()
        results: list[Any] = []
        try:
            lines = Path(stream_log).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return BARRIER_SETTINGS
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "system" and event.get("subtype") == "init":
                versions.add(event.get("claude_code_version"))
                if "permissionMode" in event:
                    modes.add(event["permissionMode"])
            elif event.get("type") == "result":
                results.append(event.get("is_error"))
        if modes - {NATIVE_PERMISSION_MODE}:
            return AUDIT_BARRIER
        if (len(versions) == 1 and isinstance(next(iter(versions)), str) and next(iter(versions))
                and results and all(r is False for r in results)):
            return BARRIER_OBSERVED
        return BARRIER_SETTINGS

    def _audit_stream(self, stream_log: Path, bundle: Path, scratch: Path, driver: Mapping[str, Any],
                      session_id: str | None, exe: Mapping[str, str] | None, notes: list[str],
                      models: list[str] | None = None, **policy: Any) -> list[str]:
        """Contamination audit of one arm's tool calls, on the stream the launcher kept. Always against
        the strictest list (the one of a local arm): a cloud arm keeps ``~/.claude`` for its identity,
        but reading anything there (the plugin cache holds the merged tests) is a contamination, except
        its OWN session directory (``session_log.projects_dir``/*/<session id>/: Claude Code's saved tool
        outputs). A local arm's ``~`` is its isolated HOME (``isolated_environment``), not the real one.
        A literal real-home path that the bundle's own files contain (``base_literals``, computed when the
        bundle was built) is not flagged in a tool result."""
        home = self._home()
        sensitive = read_deny_list(
            repo=self.repo, home=home, state_dir=self.state_dir, input_paths=self.input_paths,
            kind="local_harness", isolation=(self.campaign.get("isolation") or {}).get("deny_read_home"))
        if self.private_root:  # the work root holds the other attempts: all of it but this root is off limits
            sensitive.append(self.work_root)
        isolated = driver.get("home", "isolated") == "isolated"
        own = None
        if session_id and not isolated:
            own = ((driver.get("session_log") or {}).get("projects_dir", "~/.claude/projects"), session_id)
        walled = None
        if driver["kind"] in LOCAL_KINDS and self._sandboxed(driver):  # what its sandbox made unreadable
            deny_home, allow = self._home_policy(driver, exe)
            if deny_home is not None:
                walled = (str(deny_home), [str(p) for p in allow], [str(p) for p in self._deny_read(driver)])
        return audit_transcript(stream_log, bundle=bundle, scratch=scratch, sensitive=sensitive, home=home,
                                arm_home=str(scratch / "home") if isolated else None, own_session=own,
                                literals=self.literals.get(bundle, frozenset()), sandbox_denied=walled,
                                attempt_dir=bundle.parent,  # ``_bundle`` builds <attempt dir>/bundle
                                private_root=bundle.parent.parent if self.private_root else None,
                                revision=self.audit_revision, not_found=notes, host_model=models,
                                absent_forms=self.absent_forms, **policy)

    def _audit_note(self, stream_logs: Sequence[Path]) -> dict[str, Any]:
        """The ``audit`` field of a record that ran to completion under audit revision 2 (a record cut by
        ``_tool_error`` carries none: it is never readable as verified): the revision, the paths named that do not
        exist (apart from the hits, never a read) and, per audited stream in order, what the audit could assume of
        its host (``claude-code-<observed version>``, or ``unverified``: no directory carried, no ``cd`` believed).
        ``barrier`` is the weakest ``_stream_barrier`` of the streams (``not_verified`` without any): what the
        launcher verified of the native sandbox, never "confined". Absent under revision 1: the frozen records keep
        their shape."""
        if self.audit_revision < AUDIT_REVISION:
            return {}
        names = [p for log in stream_logs for p in self.not_found.get(str(log), [])]
        barrier = min((self.barriers.get(str(log), AUDIT_BARRIER) for log in stream_logs),
                      key=BARRIERS.index, default=AUDIT_BARRIER)
        note = {"revision": self.audit_revision, "barrier": barrier, "not_found": list(dict.fromkeys(names)),
                "host_models": [self.host_models.get(str(log), "unverified") for log in stream_logs]}
        if self.native_sandbox:  # PAT-126: bundle material found in the shared temp directory and moved out of it
            note["temp_leftovers"] = _temp_note([self.leftovers[str(log)] for log in stream_logs
                                                 if str(log) in self.leftovers])
        if self.audit_policy:  # PAT-126: absent without the key, so the record of every other campaign keeps its shape
            roles: dict[str, int] = {}
            refused: dict[str, int] = {}
            for log in stream_logs:
                role = "reviewer" if self.stream_roles.get(str(log)) == "reviewer" else "arm"
                roles[role] = roles.get(role, 0) + len(self.journals.get(str(log), []))
                for tool, n in self.refusals.get(str(log), {}).items():
                    refused[tool] = refused.get(tool, 0) + n
            note.update(policy=AUDIT_POLICY,
                        journal=list(dict.fromkeys(p for log in stream_logs for p in self.journals.get(str(log), []))),
                        journal_by_role=roles, refused_calls=refused)
        if self.absent_forms:  # PAT-128: absent without the key, so every earlier record keeps its shape
            note["absent_path_forms"] = ABSENT_FORMS
        return {"audit": note}

    def _home(self) -> str:
        """The user's real HOME (the launcher's environment, or the injected ``host_env``)."""
        env = os.environ if self.host_env is None else self.host_env
        return str(env.get("HOME") or Path.home())

    def _attempt_name(self, task: Mapping[str, Any], label: str) -> str:
        """Unique across the launches of one campaign (the launch rank comes from the ledger): a
        relaunch never overwrites the stream log of an attempt a previous launcher left."""
        self.counter += 1
        return f"attempt-l{self.launch:02d}-{self.counter:04d}-pr{task['pr']}-{label}"

    def _bundle(self, task: Mapping[str, Any], label: str, patch: bytes | None = None,
                name: str | None = None, statement_extra: str = "") -> tuple[Path, Path]:
        """A FRESH bundle for one attempt (a judged bundle is never handed back to a candidate)
        and its attempt directory (scratch). The statement carries the launcher footer, identical
        for every arm; ``statement_extra`` (protocol v2: an exploration report) follows the footer and
        is part of the committed base, like the footer. The patch (a previous attempt) is applied to the
        index so that ``git diff HEAD`` shows every change, new files included."""
        name = name or self._attempt_name(task, label)
        # PAT-121: with a private root the parent of the attempt holds this attempt and nothing else
        attempt_dir = self.work_root / (f"private-{name}" if self.private_root else ".") / name
        bundle = lfc.build_bundle(self.repo, task, attempt_dir / "bundle")
        try:
            dropped: list[str] = []
            if self.native_sandbox:  # PAT-124: no project settings of the corpus, of an arm or of a patch in a session
                _strip_claude_dirs(bundle)  # of the base: for EVERY driver of the campaign, local ones included
                if patch:
                    patch, dropped = _without_claude_dirs(patch)
            statement = bundle / "TASK.md"
            statement.write_text(statement.read_text(encoding="utf-8")
                                 + self.campaign["statement_footer"] + statement_extra, encoding="utf-8")
            _git_in(bundle, "add", "TASK.md")  # the footer is part of the base: a diff shows only the arm
            _git_in(bundle, "commit", "-q", "--amend", "--no-edit", "--no-verify")
            root = _git_in(bundle, "rev-parse", "HEAD").decode().strip()
            self.guards[bundle] = (root, _git_surface(bundle))
            self.bundle_files[bundle] = [str(f["path"]) for f in task.get("files", []) if isinstance(f, Mapping)]
            self.literals[bundle] = base_literals(bundle, self._home())  # before any patch or arm
            if patch:
                _apply_patch(bundle, patch)
                if self.native_sandbox:  # a ``.claude`` the patch still brought (a symbolic link has no file section)
                    dropped += _strip_claude_dirs(bundle)
            if dropped:
                self.stripped[bundle] = dropped  # what the arm of this bundle does not see of the patch
            if lfc.is_judged(bundle) or bundle in self.handed:
                raise RunnerError("refusing to hand a judged or reused bundle to a candidate")
        except BaseException:  # nothing is left on disk when the bundle cannot be prepared
            self.guards.pop(bundle, None)
            self.literals.pop(bundle, None)
            self.stripped.pop(bundle, None)
            lfc.remove_bundle(bundle)
            self._remove_attempt(attempt_dir)
            raise
        self.handed.add(bundle)
        return bundle, attempt_dir

    def _discard(self, bundle: Path, attempt_dir: Path) -> None:
        self.guards.pop(bundle, None)
        self.bundle_files.pop(bundle, None)
        self.literals.pop(bundle, None)
        self.stripped.pop(bundle, None)
        lfc.remove_bundle(bundle)
        self._remove_attempt(attempt_dir)

    def _remove_attempt(self, attempt_dir: Path) -> None:
        shutil.rmtree(attempt_dir.parent if self.private_root else attempt_dir, ignore_errors=True)

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
            return _capture_patch(bundle, root, self.patch_excludes)
        except (RunnerError, OSError) as exc:
            raise CandidateFault(f"bundle unreadable by git: {exc}") from None

    def _judge(self, task: Mapping[str, Any], bundle: Path) -> dict[str, Any]:
        """``lfc.judge``, where an ``OSError`` on the bundle's own content (a file the arm made
        unreadable, a bytecode file it made undeletable) is the arm's doing: a ``REFUSED`` verdict
        (``candidate_fault``), never a void attempt. Any other ``OSError`` (the launcher's environment)
        propagates and leaves the attempt void."""
        try:
            return lfc.judge(self.repo, task, bundle, **({"failures": True} if self.feedback_spec else {}),
                             **({"strict_report": True} if self.strict_judge else {}))
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
        steps, seconds = ((b["explorer_max_steps"], b["explorer_max_seconds"]) if self.exploring
                          else (b["local_max_steps"], b["local_max_seconds"]))  # v2: the explorer's bounds
        return {"workdir": str(bundle), "statement_file": str(bundle / "TASK.md"),
                "scratch": str(attempt_dir / "scratch"), "max_steps": str(steps),
                "max_seconds": str(seconds),
                "max_duration": f"{seconds // 60}m" if seconds % 60 == 0 else f"{seconds}s",
                "review_file": str(attempt_dir / "scratch" / "review.json"),
                "feedback_file": str(attempt_dir / "scratch" / "feedback.md"), **extra}

    def _prompt(self, key: str, values: Mapping[str, str]) -> str:
        return _substitute(self.campaign["prompts"][key], values)

    def _tool_error(self, task: Mapping[str, Any], path: str, segment: str, attempt: int,
                    task_set: str, exc: BaseException, *, wall: float = 0.0, sessions: Sequence[str] = (),
                    by_role: Mapping[str, Any] | None = None, by_model: Mapping[str, Any] | None = None,
                    local: Mapping[str, Any] | None = None,
                    replay_of: Mapping[str, Any] | None = None, judge: Mapping[str, Any] | None = None,
                    review: Mapping[str, Any] | None = None, contamination: Sequence[str] = (),
                    review_contamination: Sequence[str] | None = None) -> dict[str, Any] | None:
        """A tool failure or an interruption (Ctrl-C, SIGTERM, any other exception) is recorded as
        such, never as a new verdict, before the campaign stops. An interrupted attempt has an unknown
        cost (``billing_total`` null), like any record whose cloud executions were not all read. A cut
        that comes AFTER a verdict (``judge``) or a review keeps them on the record: the attempt is then
        decided and a relaunch never replays it (no second chance after a verdict). ``contamination``
        already found by an audit of the attempt (a cut right after it) makes the record ``contaminated``,
        never replayed, like a refusal that carries one. ``review_contamination`` (audit revision 2, cloud path:
        the caller then gives the arm's flags and the reviewer's apart) is recorded on the review as on an
        uncut attempt (``review.contamination``, ``unknown["review.contaminated"]``) and does not make the arm's
        attempt ``contaminated``; the exception's own ``contamination`` is the audit of one of those sessions and
        is not added again."""
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
        found = list(dict.fromkeys([*contamination, *(
            [] if review_contamination is not None else getattr(exc, "contamination", None) or [])]))
        if review_contamination:
            record["review"]["contamination"] = _contamination(review_contamination)
            record["unknown"]["review.contaminated"] = _REVIEW_CONTAMINATED
        if found:  # refused or cut AND contaminated: never replayed either
            record.update(contaminated=True, outcome="contaminated", contamination=_contamination(found))
            record["unknown"]["contaminated"] = _CONTAMINATED
        if isinstance(exc, FoundryStateChanged):  # not accepted, undecided, never replayed; cost kept
            record.update(contaminated=True, outcome="contaminated", foundry_state_changed=exc.fingerprints)
            record["unknown"][STATE_CHANGED_STOP] = reason
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
        contamination: list[str] = []
        hold = contextlib.ExitStack()  # once a verdict exists, a signal waits until the attempt is settled
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
                with self._critical():  # a signal right after the audit never loses its result
                    contamination = self._audit(stream_log, bundle, attempt_dir / "scratch", driver,
                                                exe=exe)
                try:
                    patch = self._patch_of(bundle)
                except CandidateFault as fault:  # the arm's doing: refused, never void and replayed
                    patch, verdict = b"", _fault_verdict(fault)
                else:
                    verdict = self._judge(task, bundle)
                hold.enter_context(self._critical())  # no gap between the try and the settle below
            finally:
                self._discard(bundle, attempt_dir)
        except BaseException as exc:  # tool failure or interruption: time is counted, the attempt
            with hold, self._critical():  # is recorded; a verdict already received is kept on it,
                                          # so the attempt is decided and never replayed
                wall = 0.0
                if started is not None:
                    wall = execution["wall_seconds"] if execution else time.monotonic() - started
                self.ledger.settle(cloud=False, seconds=wall, premium_tokens=None, attempt_dir=name,
                                   **({"interrupted": True} if _cut(exc) == "interrupted" else {}))
                self._tool_error(task, path, "local", attempt, task_set, exc, wall=wall,
                                 local={"harness": driver_id, "harness_kind": driver["kind"],
                                        "candidate": candidate_id}, replay_of=replay_of,
                                 judge=_judge_summary(verdict) if verdict else None,
                                 contamination=contamination)
            raise
        with hold, self._critical():  # settled and registered as unrecorded together (see ``_stop``)
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
                      **self._audit_note([stream_log]), **({"replay_of": dict(replay_of)} if replay_of else {})}
            self.unrecorded = record  # until ``_emit`` writes it, an interruption writes it as interrupted
        return record, patch

    # ---- one cloud execution
    def cloud_execution(self, role: str, driver_id: str, bundle: Path, attempt_dir: Path,
                        prompt_key: str, feedback: str | None = None, seconds_key: str = "cloud_max_seconds"
                        ) -> tuple[dict[str, Any], dict[str, int | None] | None, str | None]:
        if self.mode not in CLOUD_MODES:
            raise EnvelopeError(f"mode {self.mode!r} can never start a cloud execution")
        driver = self._driver(driver_id)
        if driver["kind"] not in CLOUD_KINDS:
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
        native = None
        if self.native_sandbox:  # settings that cannot be built must not burn a reservation either
            iso = self.campaign.get("isolation") or {}
            native = native_sandbox_settings(
                attempt_dir=attempt_dir, work_root=self.work_root, home=self._home(), deny_read=deny,
                allow_read=[Path(self._home()) / rel for rel in iso.get("allow_read_home", [])],
                allow_write=extra, protect=[bundle / ".claude"])
        budget = min(self.campaign["bounds"][seconds_key],
                     max(self.ledger.remaining_seconds(), 0))
        reserved = False
        started = time.monotonic()
        try:
            with self._critical():  # a signal here is raised at the end of the block: inside the try
                self.ledger.reserve_cloud(role, session_id)  # cap checked, then recorded, then run
                reserved = True
                self.sessions.append(session_id)  # every reserved execution is named by a record
                self.roles[session_id] = role
            stream_log = self.state_dir / "streams" / f"{self.envelope['campaign_id']}-{session_id}.jsonl"
            env = os.environ if self.host_env is None else self.host_env
            fingerprint = registry_fingerprints(env)
            temp_dir = Path(env["TMPDIR"]) if self.native_sandbox and env.get("TMPDIR") else None
            temp_before = _temp_names(temp_dir) if temp_dir else None  # None: not listed (unknown, never empty)
            execution = execute_driver(
                driver, values, workdir=bundle, scratch=scratch, stream_log=stream_log,
                max_seconds=budget, max_steps=None, sandbox=self._sandboxed(driver), deny_read=deny,
                extra_write=extra, host_env=self.host_env, native_settings=native)
            if native is not None:
                execution["native_settings"] = native  # the object this execution received (the trial reports it)
            if native is not None and not execution["start_error"]:
                self.settings_sent.add(str(stream_log))
            fingerprint_after = registry_fingerprints(env)
            if self.native_sandbox:  # the shared per-user temp directory: what this execution left of the bundle
                self.leftovers[str(stream_log)] = {"unwatched": "no_tmpdir"} if temp_dir is None else (
                    _move_temp_leftovers(temp_dir, temp_before, self.state_dir / TEMP_QUARANTINE / stream_log.stem,
                                         self.bundle_files.get(bundle, [])))
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
        execution["stream_log"] = stream_log
        execution["by_model"] = by_model if tokens is not None else {}
        refusal = tool_allowlist_refusal(driver, execution["stream"])
        with self._critical():  # refused or not; a signal right after the audit never loses its result
            self.stream_roles[str(stream_log)] = role
            execution["contamination"] = self._audit(stream_log, bundle, scratch, driver, session_id, native=native)
            self.audited[session_id] = execution["contamination"]
        if fingerprint_after != fingerprint:  # PAT-120: settled and audited above, then the campaign stops
            raise FoundryStateChanged(fingerprint, fingerprint_after, execution["contamination"])
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
            execution["review_excluded"] = excluded + self.stripped.get(bundle, [])
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
                   segment: str = "cloud", first_attempt: int = 0, statement_extra: str = ""
                   ) -> list[dict[str, Any]]:
        """Implementer, mechanical judge, independent review, corrections (bounded). Protocol v2:
        ``statement_extra`` (an exploration report) follows the statement of the implementer and of every
        correction bundle, never the reviewer's (the review is identical for every arm)."""
        implementer = IMPLEMENTER_DRIVER.get(path, IMPLEMENTER_DRIVER["A"])
        records: list[dict[str, Any]] = []
        patch: bytes | None = None
        feedback: str | None = None
        told: dict[str, Any] | None = None  # protocol v4: counters of what the corrector of this round was told
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
            state = {"seconds": 0.0, "rounds": [], "accepted": None, "outcome": "judge_refused", "logs": []}
            findings = ""
            mark = len(self.sessions)

            def emit() -> None:
                spent = self.sessions[mark:]
                records.append(self._emit({
                    "record_type": "attempt", "task": _task_ref(task, task_set), "path": path,
                    "segment": segment, "attempt": first_attempt + index, "outcome": state["outcome"],
                    "judge": _judge_summary(verdict) if verdict else None,
                    "accepted": state["accepted"],
                    "review": {"rounds": len(state["rounds"]), "verdicts": state["rounds"],
                               **({"contamination": _contamination(state["review_contamination"])}
                                  if state.get("review_contamination") else {})},
                    "wall_seconds": round(state["seconds"], 3), "cloud_executions": len(spent),
                    "cloud_sessions": spent,
                    "premium": {"by_role": {r: _classes(t) for r, t in by_role.items()},
                                "by_model": models, "billing_total": _record_total(by_role, spent)},
                    "local": None, "machine": None, "unknown": unknown,
                    **({"contaminated": True, "contamination": _contamination(state["contamination"])}
                       if state.get("contamination") else {}),
                    **({"review_excluded": state["review_excluded"]} if state.get("review_excluded")
                       else {}),
                    **({"correction_excluded": state["correction_excluded"]}
                       if state.get("correction_excluded") else {}),
                    **({"feedback": told} if told else {}), **self._audit_note(state["logs"]),
                    **({"replay_of": again} if again else {})}))

            try:
                bundle, attempt_dir = self._bundle(task, f"{path}-{role}{index}", patch,
                                                   statement_extra=statement_extra)
                # PAT-124, under the key only: the ``.claude`` paths of the previous round's patch that this
                # corrector's bundle does not hold (same shape as the reviewer's ``review_excluded``)
                state["correction_excluded"] = self.stripped.get(bundle)
                try:
                    execution, tokens, reason = self.cloud_execution(
                        role, implementer, bundle, attempt_dir,
                        "implement" if index == 0 else "correct", feedback)
                    state["seconds"] = execution["wall_seconds"]
                    state["logs"].append(execution["stream_log"])
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
                    state["logs"].append(rev_exec["stream_log"])
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
                    if rev_exec["contamination"] and self.audit_revision >= AUDIT_REVISION:
                        # PAT-123: the reviewer's session is flagged on the review, not on the arm's attempt. The
                        # judge and the review verdicts stay readable on the record, but a flagged reviewer decides
                        # nothing, whatever it said: the attempt is undecided (``review_unreadable``, never replayed),
                        # the loop stops and its findings reach no corrector (a possible leak flows nowhere)
                        state["review_contamination"] = rev_exec["contamination"]
                        unknown["review.contaminated"] = _REVIEW_CONTAMINATED
                        state.update(outcome="review_unreadable", accepted=None)
                    elif rev_exec["contamination"]:  # the review itself touched sensitive paths
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
                    if state["rounds"] else None,
                    **(self._flags_by_role(mark) if self.audit_revision >= AUDIT_REVISION
                       else {"contamination": self._audited_since(mark)}))
                raise
            outcome = state["outcome"]
            if outcome in ("accepted", "review_unreadable", "contaminated"):
                break
            told = (_feedback_counters(verdict, self.feedback_spec)
                    if self.feedback_spec and outcome != "review_block" else None)
            feedback = (findings if outcome == "review_block" else
                        "The mechanical acceptance check refused the change: "
                        f"{verdict.get('note') or 'tests failing'} "
                        f"(passed {verdict['passed']}, failed {verdict['failed']}, "
                        f"errors {verdict['errors']})."
                        + (_failure_feedback(verdict, self.feedback_spec) if self.feedback_spec else ""))
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
                found = self._audited_since(mark) or getattr(exc, "contamination", None)
                if found:  # a review that touched paths, refused or cut after its audit
                    local.update(contaminated=True, accepted=None, outcome="contaminated",
                                 contamination=_contamination(found))
                    local["unknown"]["contaminated"] = "the reviewer's " + _CONTAMINATED[len("the arm's "):]
                if isinstance(exc, FoundryStateChanged):  # same record as on the cloud path
                    local.update(contaminated=True, accepted=None, outcome="contaminated",
                                 foundry_state_changed=exc.fingerprints)
                    local["unknown"][STATE_CHANGED_STOP] = local["reason"]
                self._emit(local)
                raise
        else:
            local["outcome"] = "contaminated" if local.get("contaminated") else "local_refused"
        self._emit(local)
        if takeover:  # a failed local attempt is neither a failure nor an escalation (ADR-0015)
            records += self.cloud_path(task, "C", task_set, segment="takeover", first_attempt=1)
        return records

    # ---- protocol v2: read-only exploration (PAT-114)
    def _full_budget_left(self) -> None:
        """An exploration whose time budget the envelope would cut below the protocol bound is not started
        (a cap stop): its refusal would be the envelope's doing, not the model's."""
        if self.ledger.remaining_seconds() < self.campaign["bounds"]["explorer_max_seconds"]:
            raise CapReached("wall_clock_seconds")

    @staticmethod
    def _bundle_status(bundle: Path) -> str | None:
        """``git status --porcelain`` of a bundle (neutral git), ``None`` when git cannot read it."""
        try:
            return _git_in(bundle, "status", "--porcelain", "--untracked-files=all").decode("utf-8", "replace")
        except (RunnerError, OSError):
            return None

    @staticmethod
    def _bundle_modified(before: str | None, after: str | None) -> bool:
        """An explorer is read-only: a bundle whose status changed (or became unreadable) refuses the
        exploration. Observed, not guaranteed, for the unsandboxed cloud explorer."""
        return after is None or after != before

    def _truth(self, task: Mapping[str, Any]) -> dict[str, Any]:
        """Ground truth of a task from its merged diff, read before any claim or spend (fail closed)."""
        if task["pr"] not in self.truths:
            if self.frozen_truths is None:
                self.truths[task["pr"]] = lfe.ground_truth(self.repo, task)
            else:  # the committed file is the single source; it must describe this very task
                entry = self.frozen_truths.get(str(task["pr"]))
                if entry is None or (entry["base_sha"], entry["head_sha"]) != (task["base_sha"], task["head_sha"]):
                    raise RunnerError(f"no frozen ground truth for PR {task['pr']} at these SHAs")
                self.truths[task["pr"]] = entry
        return self.truths[task["pr"]]

    @contextlib.contextmanager
    def _guarded(self):
        """The signal, cap and failure handling of ``screen`` and ``compare`` for a whole v2 call: an
        interruption anywhere leaves a record and a stop; a cap is a stop, not an error."""
        with self._signals():
            try:
                yield
            except PreflightRefused:
                raise
            except CapReached as exc:
                self._stop(f"cap_reached:{exc}", exc)
            except (RunnerError, lfc.CorpusError) as exc:
                self._stop(_stop_reason(exc), exc)
                raise
            except BaseException as exc:  # Ctrl-C, SIGTERM/SIGHUP (SystemExit) or an unexpected error
                self._stop(f"interrupted:{type(exc).__name__}", exc)
                raise

    def explore_local(self, task: Mapping[str, Any], candidate_id: str, driver_id: str, path: str,
                      task_set: str, *, segment: str = "local", attempt: int = 0,
                      replay_of: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """One bounded read-only exploration by a local candidate (10 minutes, 25 steps), judged on
        localization (``lfe.verdict_of``): the bundle is READ-ONLY in the sandbox profile, only the attempt
        scratch is writable; the report is read from the scratch or from the final message. The verdict is
        the ``judge`` of the record, so the resume rules of v1 apply as they are: a decided attempt is never
        replayed, a missing or unusable report is a refusal (score 0) and never a void attempt, only a
        launcher failure before the arm ran is void. A contaminated attempt counts as 0 (v2 rule)."""
        driver = self._driver(driver_id)
        if driver["kind"] != "local_explorer":
            raise RunnerError(f"{driver_id} is not a local explorer")
        exe = self._executable(driver)  # refused before any claim, reservation or file
        truth = self._truth(task)
        self.ledger.check(cloud=False)
        self._full_budget_left()
        self._claim(path, task, task_set, candidate_id, segment, attempt, 1 if replay_of else 0)
        bounds = self.campaign["bounds"]
        model = self.campaign["candidates"][candidate_id]["model"]
        execution, started, verdict = None, None, None
        contamination: list[str] = []
        hold = contextlib.ExitStack()  # once a verdict exists, a signal waits until the attempt is settled
        name = self._attempt_name(task, f"{path}-explore-{candidate_id}")
        self.ledger.append("attempt_started", attempt_dir=name, path=path, pr=task["pr"],
                           set=task_set, candidate=candidate_id, segment=segment, attempt=attempt)
        try:
            bundle, attempt_dir = self._bundle(task, "", name=name)
            try:
                values = self._values(bundle, attempt_dir, model=model,  # v2: the explorer's bounds
                                      **({exe["placeholder"]: exe["path"]} if exe else {}))
                values["prompt"] = self._prompt("explore", values)
                before = machine_snapshot(self.run, self.campaign.get("server_process_pattern"))
                budget = min(bounds["explorer_max_seconds"], max(self.ledger.remaining_seconds(), 0))
                deny_home, allow_read = self._home_policy(driver, exe)
                status_before = self._bundle_status(bundle)
                started = time.monotonic()
                stream_log = (self.state_dir / "streams"
                              / f"{self.envelope['campaign_id']}-{attempt_dir.name}.jsonl")
                execution = execute_driver(
                    driver, values, workdir=bundle, scratch=attempt_dir / "scratch", stream_log=stream_log,
                    max_seconds=budget, max_steps=bounds["explorer_max_steps"],
                    sandbox=self._sandboxed(driver), deny_read=self._deny_read(driver),
                    host_env=self.host_env, deny_home=deny_home, allow_read=allow_read,
                    workdir_writable=False)
                if execution["start_error"]:  # the arm never ran: a launcher/environment failure, void (replayable)
                    raise RunnerError(f"the explorer could not be started: {execution['start_error']}")
                after = machine_snapshot(self.run, self.campaign.get("server_process_pattern"))
                with self._critical():  # a signal right after the audit never loses its result
                    contamination = self._audit(stream_log, bundle, attempt_dir / "scratch", driver, exe=exe)
                modified = self._bundle_modified(status_before, self._bundle_status(bundle))
                if (execution["timed_out"] or execution["step_limit_hit"]
                        or execution["wall_seconds"] >= bounds["explorer_max_seconds"]):  # a bound: refused, even with a draft
                    report, source, refusal = None, "none", BOUND_HIT
                elif modified:
                    report, source, refusal = None, "none", BUNDLE_MODIFIED
                else:
                    report, source, refusal = lfe.load_report(
                        attempt_dir / "scratch", stream_log, (driver.get("stream") or {}).get("format", "none"),
                        root=str(bundle))
                verdict = lfe.verdict_of(truth, report, refusal, bool(contamination))
                hold.enter_context(self._critical())  # no gap between the try and the settle below
            finally:
                self._discard(bundle, attempt_dir)
        except BaseException as exc:  # tool failure or interruption: time is counted, the attempt is
            with hold, self._critical():  # recorded; a verdict already received is kept on it
                wall = 0.0
                if started is not None:
                    wall = execution["wall_seconds"] if execution else time.monotonic() - started
                self.ledger.settle(cloud=False, seconds=wall, premium_tokens=None, attempt_dir=name,
                                   **({"interrupted": True} if _cut(exc) == "interrupted" else {}))
                self._tool_error(task, path, segment, attempt, task_set, exc, wall=wall,
                                 local={"harness": driver_id, "harness_kind": driver["kind"],
                                        "candidate": candidate_id}, replay_of=replay_of, judge=verdict,
                                 contamination=contamination)
            raise
        with hold, self._critical():  # settled and registered as unrecorded together (see ``_stop``)
            self.ledger.settle(cloud=False, seconds=execution["wall_seconds"], premium_tokens=None,
                               attempt_dir=name)
            stream = execution["stream"]
            unknown = {f"local.{k}": v for k, v in stream["unknown"].items()}
            unknown.update({f"machine.{k}": v for k, v in {**before["unknown"], **after["unknown"]}.items()})
            local = {"harness": driver_id, "harness_kind": driver["kind"], "candidate": candidate_id,
                     "max_seconds": round(budget, 3), "nominal_max_seconds": bounds["explorer_max_seconds"],
                     "max_steps": bounds["explorer_max_steps"], "steps": stream["steps"],
                     "stream_tokens": stream["stream_tokens"],
                     "prefill_tokens_per_second": stream["prefill_tokens_per_second"],
                     "generation_tokens_per_second": stream["generation_tokens_per_second"],
                     "timed_out": execution["timed_out"], "step_limit_hit": execution["step_limit_hit"],
                     "exit_code": execution["exit_code"], "signal": execution["signal"],
                     "ended_by_external_signal": execution["signal"] is not None
                     and not execution["launcher_kill"], "start_error": execution["start_error"],
                     **({"harness_executable": {k: exe[k] for k in ("env", "package", "version")}}
                        if exe else {})}
            if contamination:
                unknown["contaminated"] = _CONTAMINATED
            if verdict["verdict"] == "REFUSED":
                unknown["exploration.report"] = verdict["note"]
            record = {"record_type": "attempt", "task": _task_ref(task, task_set), "path": path,
                      "segment": segment, "attempt": attempt, "judge": verdict,
                      "local_outcome": {"SCORED": "scored", "REFUSED": "refused",
                                       "CONTAMINATED": "contaminated"}[verdict["verdict"]],
                      **({"contaminated": True, "contamination": _contamination(contamination),
                          "outcome": "contaminated"} if contamination else {}),
                      "accepted": None, "review": {"rounds": 0, "verdicts": []},
                      "wall_seconds": execution["wall_seconds"], "cloud_executions": 0, "cloud_sessions": [],
                      "premium": {"by_role": {}, "by_model": {}, "billing_total": 0}, "local": local,
                      "machine": {"before": _strip(before), "after": _strip(after),
                                  **({"dedicated": self.dedicated} if self.dedicated else {})},
                      "exploration": {"report": None if contamination else report, "report_source": source,
                                      "refusal": refusal, "bundle_modified": modified,
                                      "score": {k: verdict[k] for k in ("verdict", "file_recall", "file_precision",
                                                                        "function_recall", "counts")}},
                      "unknown": unknown, **self._audit_note([stream_log]),
                      **({"replay_of": dict(replay_of)} if replay_of else {})}
            self.unrecorded = record  # until ``_emit`` writes it, an interruption writes it as interrupted
        return record

    def screen_exploration(self, tasks: Sequence[Mapping[str, Any]], candidate_ids: Sequence[str],
                           driver_id: str = LOCAL_EXPLORER_DRIVER) -> list[dict[str, Any]]:
        """Local read-only explorations only (candidate x task) judged on localization: no cloud, ever.
        The machine preflight, dedicated-machine admission included, is re-run before every task."""
        out: list[dict[str, Any]] = []
        if self.fixed_candidate is not None:
            raise RunnerError("this campaign fixes its candidate (exploration.fixed_candidate): no screening")
        self._check_harness(driver_id)  # an unresolvable harness executable costs nothing
        with self._guarded():
            pending = [(c, t) for c in candidate_ids for t in tasks]
            for index, (candidate_id, task) in enumerate(pending):
                state, info = self._local_state("XS", task, "screening", candidate_id, "local", 0)
                if state in ("decided", "undecided"):
                    continue  # resume: a decided task is never replayed, an undecided one stays so
                self.preflight(candidate_id)
                out.append(self._emit(self.explore_local(task, candidate_id, driver_id, "XS", "screening",
                                                         replay_of=info)))
                if self.one_task:  # v3: one task per launch, the operator reloads the model
                    self.work_remains = any(
                        self._local_state("XS", t, "screening", c, "local", 0)[0] in ("fresh", "replay")
                        for c, t in pending[index + 1:])
                    break
            if self.one_task and self.work_remains is None:
                self.work_remains = False  # nothing was left to play
        return out

    def _report_for_arm(self, report: Mapping[str, Any]) -> Mapping[str, Any]:
        """Protocol v4 (private attempt roots): the free text of an explorer's report may cite an absolute path
        of the explorer's own (discarded) bundle. Arm L would read it in its statement and the audit, which
        treats the work root as sensitive, would flag the arm (arm A has no report). The resolved spelling of the
        work root (the only one the launcher hands out) is therefore replaced: an explorer bundle path by the
        bundle-relative path, any other path under the work root by ``<work-root>``. Unchanged (the very same
        report) when the private root is off: the v1 to v3 rendering is byte for byte what it was."""
        if not self.private_root:
            return report
        spellings = sorted({str(self.work_root), os.path.realpath(self.work_root)}, key=len, reverse=True)
        root = "|".join(re.escape(x) for x in spellings)
        bundle = re.compile(rf"(?:{root})/private-[^/\s]+/[^/\s]+/bundle(/)?")
        work = re.compile(root)

        def mask(text: str) -> str:
            return work.sub("<work-root>", bundle.sub(lambda m: "" if m.group(1) else ".", text))

        return {**report, "files": [mask(f) for f in report["files"]], "rationale": mask(report["rationale"]),
                "functions": [{**f, "file": mask(f["file"]), "name": mask(f["name"])} for f in report["functions"]]}

    def _explore_step_local(self, task: Mapping[str, Any], candidate_id: str, driver_id: str
                            ) -> tuple[list[dict[str, Any]], dict[str, Any] | None, bool]:
        """Exploration of arm L: ``(records, report, skip)``; ``skip`` leaves the arm undecided (a
        contaminated explorer, or an exploration cut twice): no implementation is spent on it."""
        state, info = self._local_state("L", task, self.compare_set, candidate_id, "explore", 0)
        if state == "undecided":
            return [], None, True
        if state == "decided":  # resume: the report was kept on the record
            skip = bool(info.get("contaminated")) or info.get("outcome") in LOST_OUTCOMES
            return [], (None if skip else (info.get("exploration") or {}).get("report")), skip
        record = self._emit(self.explore_local(task, candidate_id, driver_id, "L", self.compare_set,
                                               segment="explore", replay_of=info))
        return [record], record["exploration"]["report"], bool(record.get("contaminated"))

    def _explore_step_cloud(self, task: Mapping[str, Any]
                            ) -> tuple[list[dict[str, Any]], dict[str, Any] | None, bool]:
        """Exploration of arm E by the cloud economy explorer (Haiku 4.5, same read-only role and report
        format): one execution, premium tokens counted. A first execution cut before it was recorded is
        replayed once (it passes the caps again); a recorded one is never replayed."""
        prior = [r for r in self.prior_records if r.get("record_type") == "attempt"
                 and (r["path"], r["task"]["pr"], r["task"].get("set"), r.get("segment")) ==
                 ("E", task["pr"], self.compare_set, "explore")]
        replay = None
        if prior:
            kept = [r for r in prior if r.get("outcome") not in LOST_OUTCOMES]
            if kept:  # recorded: never replayed; its report was kept on the record
                rec = kept[0]
                skip = bool(rec.get("contaminated")) or rec.get("outcome") == "stopped_by_cap"
                return [], (None if skip else (rec.get("exploration") or {}).get("report")), skip
            if len(prior) != 1:  # cut twice: undecided
                return [], None, True
            self.ledger.check(cloud=True)
            replay = {"attempt": 0, "attempt_dir": None, "outcome": prior[0]["outcome"],
                      "reason": prior[0].get("reason"), "cloud_sessions": list(prior[0]["cloud_sessions"])}
        truth = self._truth(task)
        self._full_budget_left()
        self._claim("E", task, self.compare_set, None, "explore", 0, 1 if replay else 0)
        mark = len(self.sessions)
        by_role: dict[str, Any] = {}
        models: dict[str, dict[str, int | None]] = {}
        unknown: dict[str, str] = {}
        seconds, report, source, refusal = 0.0, None, None, None
        try:
            bundle, attempt_dir = self._bundle(task, "E-explore")
            try:
                status_before = self._bundle_status(bundle)
                execution, tokens, reason = self.cloud_execution(
                    "explorer", CLOUD_EXPLORER_DRIVER, bundle, attempt_dir, "explore",
                    seconds_key="explorer_max_seconds")
                seconds = execution["wall_seconds"]
                if execution["start_error"]:  # the arm never ran: void (replayable once), not a refusal
                    raise RunnerError(f"the explorer could not be started: {execution['start_error']}")
                modified = self._bundle_modified(status_before, self._bundle_status(bundle))
                by_role["explorer"] = tokens
                _merge_models(models, execution["by_model"])
                if tokens is None:
                    unknown["premium.explorer"] = reason or "unknown"
                if execution["timed_out"] or seconds >= self.campaign["bounds"]["explorer_max_seconds"]:
                    # cut by the time bound (or stopped at it by the harness): refused, even with a draft
                    report, source, refusal = None, "none", BOUND_HIT
                elif modified:  # the explorer is read-only: a changed bundle refuses the exploration
                    report, source, refusal = None, "none", BUNDLE_MODIFIED
                else:
                    report, source, refusal = lfe.load_report(
                        attempt_dir / "scratch", execution["stream_log"],
                        (self._driver(CLOUD_EXPLORER_DRIVER).get("stream") or {}).get("format", "none"),
                        root=str(bundle))
            finally:
                self._discard(bundle, attempt_dir)
            contamination = execution["contamination"]
            verdict = lfe.verdict_of(truth, report, refusal, bool(contamination))
            spent = self.sessions[mark:]
            if contamination:
                unknown["contaminated"] = _CONTAMINATED
            record = self._emit({
                "record_type": "attempt", "task": _task_ref(task, self.compare_set), "path": "E",
                "segment": "explore", "attempt": 0, "outcome": "contaminated" if contamination else "explored",
                "judge": None, "accepted": None, "review": {"rounds": 0, "verdicts": []},
                "wall_seconds": round(seconds, 3), "cloud_executions": len(spent), "cloud_sessions": spent,
                "premium": {"by_role": {r: _classes(t) for r, t in by_role.items()}, "by_model": models,
                            "billing_total": _record_total(by_role, spent)},
                "local": None, "machine": None, "unknown": unknown,
                "exploration": {"report": None if contamination else report, "report_source": source,
                                "refusal": refusal, "bundle_modified": modified,
                                "score": {k: verdict[k] for k in ("verdict", "file_recall", "file_precision",
                                                                  "function_recall", "counts")}},
                **({"contaminated": True, "contamination": _contamination(contamination)}
                   if contamination else {}),
                **({"replay_of": replay} if replay else {})})
        except CapReached:  # the cut execution is recorded: the task is not decided, never a refusal
            spent = self.sessions[mark:]
            self._emit({"record_type": "attempt", "task": _task_ref(task, self.compare_set), "path": "E",
                        "segment": "explore", "attempt": 0, "outcome": "stopped_by_cap", "judge": None,
                        "accepted": None, "review": {"rounds": 0, "verdicts": []}, "wall_seconds": 0.0,
                        "cloud_executions": len(spent), "cloud_sessions": spent,
                        "premium": {"by_role": {}, "by_model": {}, "billing_total": _record_total({}, spent)},
                        "local": None, "machine": None, "unknown": {"stopped_by_cap": "cap reached"}})
            raise
        except BaseException as exc:  # tool failure, Ctrl-C, SIGTERM, anything: never silent
            self._tool_error(task, "E", "explore", 0, self.compare_set, exc, wall=seconds,
                             sessions=self.sessions[mark:], by_role=by_role, by_model=models, replay_of=replay,
                             contamination=self._audited_since(mark))
            raise
        return [record], None if contamination else report, bool(contamination)

    def _check_interpreters(self) -> None:
        """PAT-126 (a protocol after v4 only): refuse, before any claim, model use or cloud reservation, when the
        launcher's interpreter or the ``python3`` / ``python`` of a cloud arm cannot import pytest (the pilot of
        2026-10-08 was void because they could not). The probe is kept for the ledger's preflight entries."""
        if not self.strict_judge:
            return
        driver = self.campaign["drivers"].get(IMPLEMENTER_DRIVER["A"])
        self.interpreters = probe_interpreters(os.environ if self.host_env is None else self.host_env,
                                               driver if driver and driver["kind"] in CLOUD_KINDS else None)
        if self.interpreters["refusals"]:
            self.ledger.append("preflight", candidate=None, ok=False, refusals=self.interpreters["refusals"],
                               interpreters=self.interpreters, phase="start")
            raise PreflightRefused(self.interpreters["refusals"])

    def _check_cloud_binaries(self, arms: Sequence[str]) -> None:
        """PAT-126: a cloud driver that declares ``binary_version`` (protocol v5 pins Claude Code) is checked with its
        read-only version command before the first claim or reservation of the launch, once per distinct command; any
        other version, or an unreadable one, is refused. No-op for a driver that declares nothing (v1 to v4)."""
        env = os.environ if self.host_env is None else self.host_env
        done: set[tuple[str, ...]] = set()
        for driver_id in dict.fromkeys([IMPLEMENTER_DRIVER[a] for a in arms if a in IMPLEMENTER_DRIVER]
                                       + [REVIEWER_DRIVER] + ([CLOUD_EXPLORER_DRIVER] if "E" in arms else [])):
            driver = self.campaign["drivers"].get(driver_id)
            command = tuple(((driver or {}).get("binary_version") or {}).get("command") or ())
            if driver and command and command not in done:
                done.add(command)
                check_binary_version(self._driver(driver_id), env)

    def compare_exploration(self, tasks: Sequence[Mapping[str, Any]], candidate_id: str,
                            arms: Sequence[str] = EXPLORE_ARMS, driver_id: str = LOCAL_EXPLORER_DRIVER
                            ) -> list[dict[str, Any]]:
        """Protocol v2 comparison, each task in each requested arm from the same base: ``A`` the current
        cloud implementer without a report, ``L`` the same with the report of the retained local explorer
        appended to the statement, ``E`` the same with the report of the cloud economy explorer; the review
        is identical for every arm (A's: Opus, at most ``max_correction_rounds`` corrections). The
        exploration comes first, inside the same envelope; an arm whose explorer yields no usable report
        runs its implementer on the plain statement (the explorer's cost stays counted). An explorer that
        is contaminated, or cut twice, leaves the arm undecided. Arm ``L`` needs the local model (preflight,
        dedicated machine included, before each task) and the candidate the screening selected. The
        start-of-run preflight is ledgered ``phase: "start"``; the one right before a local exploration is
        not run again when this launcher wrote nothing to the ledger since (it would be the same check twice
        in a row, and two preflights with nothing between them read as a launcher killed in between)."""
        protocol = self.campaign.get("protocol")
        if protocol in (PROTOCOL_V4, *V5_PROTOCOLS) and not set(arms) <= set(V4_ARMS):
            raise RunnerError(f"protocol {'v4' if protocol == PROTOCOL_V4 else 'v5'} has arms {list(V4_ARMS)} only "
                              f"(no Haiku arm): refused before any claim or spend, got {list(arms)}")
        self._check_cloud_binaries(arms)  # PAT-126: the pinned Claude Code version, before any claim or spend
        self._check_interpreters()  # PAT-126: pytest importable by the judge and by the arms, before any claim
        out: list[dict[str, Any]] = []
        limits = self.campaign["exploration"]["report_render_limits"]
        checked: int | None = None  # ledger position right after the start-of-run preflight
        if "L" in arms:  # refused before any claim or reservation
            self._check_selected(candidate_id)
            self._check_harness(driver_id)
            if any(self._local_state("L", t, self.compare_set, candidate_id, "explore", 0)[0] in ("fresh", "replay")
                   for t in tasks):
                self.preflight(candidate_id, phase="start")  # a busy machine is refused before any cloud spend
                checked = self.ledger.appended
        with self._guarded():
            for index, task in enumerate(tasks):
                played = len(out)
                for arm in arms:
                    if arm == "A":
                        out += self.cloud_path(task, "A", self.compare_set)
                        continue
                    if (arm == "L" and self.ledger.appended != checked
                            and self._local_state("L", task, self.compare_set, candidate_id, "explore",
                                                  0)[0] in ("fresh", "replay")):
                        self.preflight(candidate_id)  # right before each local exploration, after the cloud work
                    explored, report, skip = (self._explore_step_local(task, candidate_id, driver_id)
                                              if arm == "L" else self._explore_step_cloud(task))
                    out += explored
                    if not skip:
                        out += self.cloud_path(task, arm, self.compare_set, statement_extra=(
                            lfe.render_report_section(self._report_for_arm(report), limits) if report else ""))
                if self.one_task and len(out) > played:  # v3: one task per launch (reload between launches)
                    # conservative: a later task with an arm that has no record yet may still have work; a
                    # launch that then plays nothing says "no", so the operator loop always ends
                    self.work_remains = any(
                        not all(any(r.get("record_type") == "attempt" and r["path"] == arm
                                    and r["task"]["pr"] == t["pr"] and r["task"].get("set") == self.compare_set
                                    for r in self.prior_records) for arm in arms)
                        for t in tasks[index + 1:])
                    break
            if self.one_task and self.work_remains is None:
                self.work_remains = False
        return out

    # ---- orchestration
    def preflight(self, candidate_id: str, phase: str | None = None) -> dict[str, Any]:
        model = self.campaign["candidates"][candidate_id]["model"]
        result = preflight(self.campaign, model, self.preflight_run(candidate_id), self.disk_free_gib,
                           self.campaign["candidates"][candidate_id], dedicated=self.exploring)
        self.dedicated = result["facts"].get("dedicated_machine")
        self.ledger.append("preflight", candidate=candidate_id, ok=result["ok"],
                           refusals=result["refusals"],
                           **({"dedicated_machine": self.dedicated} if self.exploring else {}),
                           **({"interpreters": self.interpreters} if self.interpreters else {}),
                           **({"phase": phase} if phase else {}))
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
                self._stop(_stop_reason(exc), exc)
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
                self._stop(_stop_reason(exc), exc)
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
        completed screening that selected this candidate. A campaign that fixes its candidate
        (``exploration.fixed_candidate``, protocol v4) has no screening: only that candidate is accepted."""
        if self.fixed_candidate is not None:
            if candidate_id != self.fixed_candidate:
                raise RunnerError(f"candidate {candidate_id} is not the candidate this campaign fixes "
                                  f"({self.fixed_candidate})")
            return
        attempts = [r for r in self.prior_records if r.get("record_type") == "attempt"]
        if not any(r["path"] == self.screen_path for r in attempts):
            if not self.require_screening:
                return
            attempts = self._foreign_screening()
        screening = (_report_exploration_screening(self.campaign["rules"]["exploration_screening"], attempts)
                     if self.exploring else _report_screening(self.campaign["rules"]["screening"], attempts))
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
        if not any(r["path"] == self.screen_path for r in attempts):
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
        record.update(outcome="contaminated" if record.get("contaminated") else status, status=status,
                      reason=reason, accepted=None)  # a contamination found is never lost by the cut
        record["unknown"][status] = reason
        if status == "interrupted":
            record["premium"] = {**record["premium"], "billing_total": None}
        self._emit(record)


# ----------------------------------------------------------------------------- helpers

_PATCH_EXCLUDES = (":(exclude)TASK.md", ":(exclude,glob)**/__pycache__/**", ":(exclude,glob)**/*.pyc",
                   ":(exclude,glob)**/node_modules/**", ":(exclude,glob)**/.venv/**",
                   ":(exclude,glob)**/venv/**")
# PAT-123 (audit revision 2): the tool caches an arm's own test and lint runs leave in its bundle. The capture
# forces ignored files in (``add -A -f``), so the repository's ``.gitignore`` did not keep them out of the
# patch: v4 reviewers saw ``.pytest_cache/`` (and ``.ruff_cache/``) as the bulk of the diff. Only these two caches
# are excluded, the two the v4 streams show; ``-f`` stays, because dropping it would change what the capture keeps
# beyond them (any other ignored file an arm legitimately adds) and the judge's inputs with it; another cache
# (``.mypy_cache``, ``.hypothesis``, ``.coverage``) would still be captured until a campaign shows it.
_PATCH_EXCLUDES_V2 = (*_PATCH_EXCLUDES, ":(exclude,glob)**/.pytest_cache/**", ":(exclude,glob)**/.ruff_cache/**")
# git never reads a user/system configuration, hooks, a filesystem monitor or file protocol helper on
# a bundle the candidate controlled.
_GIT_NEUTRAL = ("-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}",
                "-c", "protocol.file.allow=never", "-c", f"core.attributesFile={os.devnull}",
                "-c", "commit.gpgsign=false")


def _resume_state(records: Sequence[Mapping[str, Any]], ledger: Sequence[Mapping[str, Any]],
                  base: tuple) -> tuple[str, Any]:
    """The resume rule of a local attempt (``Runner._local_state``), from the records and the ledger
    entries alone, so that the report reads a task the same way a relaunch would."""
    recs = [r for r in records
            if r.get("record_type") == "attempt" and _logical_key(r) == base]
    for rec in recs:
        if rec.get("judge"):
            return "decided", rec
    starts = [e for e in ledger if e.get("kind") == "attempt_started" and
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
             for e in ledger):
        return "undecided", None
    else:
        outcome, reason = "hard_kill", "attempt_started without settlement"
    return "replay", {"attempt": base[5], "attempt_dir": name, "outcome": outcome, "reason": reason}


def _stop_reason(exc: BaseException) -> str:
    return STATE_CHANGED_STOP if isinstance(exc, FoundryStateChanged) else f"tool_error:{str(exc)[:160]}"


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


def _git_in(bundle: Path, *args: str, stdin: bytes | None = None, ok: Sequence[int] = (0,)) -> bytes:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", **lfc.BUNDLE_AUTHOR}
    proc = subprocess.run(["git", "-C", str(bundle), *_GIT_NEUTRAL, *args], input=stdin,
                          capture_output=True, env=env, check=False)
    if proc.returncode not in ok:
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


def _capture_patch(bundle: Path, root: str, excludes: Sequence[str] = _PATCH_EXCLUDES) -> bytes:
    """The candidate's change as a patch against the ROOT commit ``root`` (recorded when the bundle
    was built, so an arm that commits its work still yields it), taken BEFORE judging (the judge
    writes the protected tests into the bundle, which is then never reused). New files included."""
    _git_in(bundle, "add", "-A", "-f", "--", ".", *excludes)
    return _git_in(bundle, "diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv", root,
                   "--", ".", *excludes)


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


def _strip_claude_dirs(bundle: Path) -> list[str]:
    """Remove every ``.claude`` directory, and every symbolic link named ``.claude`` (whatever it points at), of a
    bundle from the tree and the index, and return their relative paths (the caller amends the base commit, so the
    base holds none and a later diff shows no deletion). PAT-124, under the native sandbox key only: Claude Code
    loads ``<cwd>/.claude/settings*.json`` (``--setting-sources project,local``) and merges arrays. A regular FILE
    named ``.claude`` is left alone (it is not a settings directory). Fails closed if one cannot be removed."""
    removed: list[str] = []
    for root, dirs, files in os.walk(bundle):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in [n for n in (*dirs, *files) if n == ".claude"]:
            path = Path(root) / name
            if not (path.is_symlink() or path.is_dir()):
                continue
            rel = path.relative_to(bundle).as_posix()
            _git_in(bundle, "rm", "-rq", "--cached", "--ignore-unmatch", "--", rel)
            if path.is_symlink():  # ``rmtree`` refuses a link: it would survive
                path.unlink()
            else:
                shutil.rmtree(path, ignore_errors=True)
            if os.path.lexists(path):
                raise RunnerError(f"cannot remove {rel} from the bundle: refusing to hand it to an arm")
            removed.append(rel)
        dirs[:] = [d for d in dirs if d != ".claude"]
    return removed


def _apply_patch(bundle: Path, patch: bytes) -> None:
    """Apply onto the index and the tree: the reviewer's ``git diff HEAD`` shows every change."""
    if patch:
        _git_in(bundle, "apply", "--index", "--whitespace=nowarn", "-", stdin=patch)


def _classes(tokens: Mapping[str, int | None] | None) -> dict[str, int | None] | None:
    return None if tokens is None else dict(tokens)


def _compare_set(campaign: Mapping[str, Any]) -> str:
    """``task.set`` of the comparison records: ``exploration.comparison_task_set`` (protocol v5), else ``comparison``."""
    return (campaign.get("exploration") or {}).get("comparison_task_set") or COMPARISON_SET


def _task_ref(task: Mapping[str, Any], task_set: str) -> dict[str, Any]:
    return {"pr": task["pr"], "issue": task.get("issue"), "set": task_set}


_CONTAMINATED = "the arm's tool calls touched sensitive paths or ran a forbidden command"
_REVIEW_CONTAMINATED = ("the reviewer's " + _CONTAMINATED[len("the arm's "):] + "; its verdict is kept on the record "
                        "but decides nothing")


def _contamination(items: Sequence[str]) -> dict[str, list[str]]:
    """``audit_transcript`` hits as the record's ``paths`` and ``commands``."""
    return {"paths": [i for i in items if not i.startswith("command:")],
            "commands": [i[len("command:"):] for i in items if i.startswith("command:")]}


def _fault_verdict(fault: BaseException) -> dict[str, Any]:
    """The mechanical refusal of an attempt whose bundle the arm left unsafe to judge: the judge did
    not run, and the attempt counts as refused (decided), never as a launcher failure."""
    return {"verdict": "REFUSED", "passed": 0, "failed": 0, "errors": 0, "skipped": 0,
            "note": f"candidate_fault: {fault}"[:200]}


def _failure_feedback(verdict: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    """What a corrector learns of a judge refusal (PAT-121, same text for every arm): the names of the
    failing or erroring hidden tests, at most ``max_failures``, each name cut to ``max_name_chars`` and
    each message to ``max_message_chars``. Never a test's source code (the module and name are visible). Empty when the judge gave no list."""
    failures = verdict.get("failures") or []
    if not failures:
        return ""
    shown = failures[:spec["max_failures"]]
    lines = ["", "", f"Failing hidden tests ({len(shown)} shown of {len(failures)}):"]
    for item in shown:
        message = " ".join(str(item.get("message", "")).split())[:spec["max_message_chars"]]
        name = str(item["name"])[:spec["max_name_chars"]]  # a parametrized id can carry test data
        lines.append(f"- {name}: {message}" if message else f"- {name}")
    return "\n".join(lines)


def _feedback_counters(verdict: Mapping[str, Any], spec: Mapping[str, Any]) -> dict[str, Any]:
    """What a corrector was told after a judge refusal, as counters only (never a message): the tests shown,
    the failing tests in total and whether a collection failure was among them."""
    failures = verdict.get("failures") or []
    return {"failing_shown": min(len(failures), spec["max_failures"]), "failing_total": len(failures),
            "collection_failure": any(str(f.get("message", "")).startswith("collection failure")
                                      for f in failures)}


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
    no economy verdict is given and the decision is ``inconclusive``. ``contaminated`` lists the records whose arm
    was flagged; ``review_contaminated`` (audit revision 2; the key is absent when empty, so the report of a frozen
    campaign keeps its keys) those whose REVIEWER was flagged, which decide nothing."""
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
                                       for r in attempts if r.get("replay_of")]}
    flagged_reviews = [{"path": r["path"], "task": r["task"], "segment": r.get("segment"),
                        "attempt": r.get("attempt"), "outcome": r.get("outcome"),
                        "paths": r["review"]["contamination"].get("paths", []),
                        "commands": r["review"]["contamination"].get("commands", [])}
                       for r in attempts if (r.get("review") or {}).get("contamination")]
    if flagged_reviews:  # audit revision 2 only (a frozen campaign has none: its report keeps its keys)
        out["review_contaminated"] = flagged_reviews
    watched = [(r, (r.get("audit") or {}).get("temp_leftovers") or {}) for r in attempts]
    leftover = [{"path": r["path"], "task": r["task"], "segment": r.get("segment"), "attempt": r.get("attempt"), **t}
                for r, t in watched if t.get("count") or t.get("watch") in ("partial", "unavailable")]
    if leftover:  # PAT-126: bundle material found new in the shared temp directory (quarantined), or a watch that
        # did not fully happen (``watch`` partial or unavailable: unknown, never "nothing left"); decides nothing
        out["temp_leftovers"] = leftover
    journaled = [r for r in attempts if (r.get("audit") or {}).get("policy")]
    if journaled:  # PAT-126, isolation.audit_policy only (the key is absent otherwise): the journal, apart from the rule
        by_arm: dict[str, int] = {}
        by_role: dict[str, int] = {}
        refused_calls: dict[str, int] = {}
        for r in journaled:
            a = r["audit"]
            by_arm[r["path"]] = by_arm.get(r["path"], 0) + len(a.get("journal") or [])
            for role, n in (a.get("journal_by_role") or {}).items():
                by_role[role] = by_role.get(role, 0) + n
            for tool, n in (a.get("refused_calls") or {}).items():
                refused_calls[tool] = refused_calls.get(tool, 0) + n
        out["audit_journal"] = {"policy": AUDIT_POLICY, "journal_paths_by_arm": dict(sorted(by_arm.items())),
                                "journal_paths_by_role": dict(sorted(by_role.items())),
                                "records_with_journal": sum(1 for r in journaled if r["audit"].get("journal")),
                                "host_refused_calls": dict(sorted(refused_calls.items())),
                                "host_refused_calls_total": sum(refused_calls.values()),
                                "note": "findings kept apart from `contaminated`/`review_contaminated`: they decide nothing"}
    exploring = campaign.get("schema") == CAMPAIGN_SCHEMA_V2  # protocol v2: other paths, other rules
    screen_path = "XS" if exploring else "S"
    killed = len({(h["path"], h["pr"], h["candidate"], h["segment"], h["attempt"])
                  for h in open_holes if h["kind"] == "attempt_started" and h["path"] == screen_path
                  and (h["path"], h["pr"], h["set"], h["candidate"], h["segment"],
                       h["attempt"]) not in counted})
    comparison = [r for r in attempts if r["task"]["set"] == (
        _compare_set(campaign) if exploring else COMPARISON_SET)]
    if exploring:
        fixed = (campaign.get("exploration") or {}).get("fixed_candidate")
        if fixed is not None:  # protocol v4: no screening, the candidate is fixed by the protocol
            out["exploration_screening"] = {"state": "no_screening", "selected": fixed,
                                            "reason": "no screening: candidate fixed by the protocol"}
        else:
            out["exploration_screening"] = _report_exploration_screening(rules["exploration_screening"],
                                                                         attempts, killed, ledger)
            out["exploration_screening"]["warnings"] = [h["warning"] for h in open_holes
                                                        if h["mode"] == "screen_exploration"]
        out["exploration_comparison"] = _report_exploration_comparison(
            rules["exploration_comparison"], comparison, stops, bool(unknown_work),
            [h["warning"] for h in open_holes if h["mode"] == "compare_exploration"], unknown_work)
        if fixed is not None:
            compared = {(r.get("local") or {}).get("candidate") for r in comparison if r["path"] == "L"}
            compared.discard(None)
            if compared - {fixed}:
                raise RunnerError(f"the compared candidate(s) {sorted(compared)} are not the one the "
                                  f"protocol fixes ({fixed})")
            out["exploration_comparison"]["screening_selected"] = f"fixed_by_protocol:{fixed}"
        else:
            out["exploration_comparison"]["screening_selected"] = _check_compared_explorer(
                out["exploration_screening"], attempts, comparison)
        return out
    out["screening"] = _report_screening(rules["screening"], attempts, killed)
    out["screening"]["warnings"] = [h["warning"] for h in open_holes if h["mode"] == "screen"]
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
    not one. ``mode`` is the mode of the session that wrote the entry. Protocol v2: a start-of-run
    preflight (``phase: "start"``, written by ``compare_exploration`` only) that the same launch follows
    with another preflight was not killed: the launcher went on to the check right before the local
    exploration (after cloud work, or after nothing at all on a resume), so it is not a hole."""
    settled = {e.get("attempt_dir") for e in ledger if e.get("kind") == "settled" and not e.get("cloud")}
    out: list[dict[str, Any]] = []
    mode: Any = None
    window: dict[str, Any] | None = None

    def close(by_preflight: bool = False) -> None:
        if window is not None and not window["attempts"] and not (by_preflight and window["start"]):
            out.append({"kind": "preflight", "mode": window["mode"], "ledger_line": window["line"],
                        "candidate": window["candidate"],
                        "warning": f"preflight_without_attempt:{window['candidate']}"
                                   f"@ledger_line_{window['line']}"})

    for line, entry in enumerate(ledger, 1):
        kind = entry.get("kind")
        if kind in ("session_started", "preflight"):
            close(kind == "preflight")
            window = None
        elif kind == "stopped":
            window = None
        if kind == "session_started":
            mode = entry.get("mode")
        elif kind == "preflight" and entry.get("ok") is True:
            window = {"mode": mode, "line": line, "candidate": entry.get("candidate"), "attempts": False,
                      "start": entry.get("phase") == "start"}
        elif kind == "cloud_started" and mode == "compare_exploration" and window is not None:
            window["attempts"] = True  # v2: the start-of-run preflight is followed by the cloud arms first
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


# ------------------------------------------------------ protocol v2 report (PAT-114)

def _report_exploration_screening(rule: Mapping[str, Any], attempts: Sequence[Mapping[str, Any]],
                                  killed: int = 0, ledger: Sequence[Mapping[str, Any]] = ()
                                  ) -> dict[str, Any]:
    """The v2 screening table (function recall first, then file precision and file recall, durations per
    candidate) and the pre-registered rule (``lfe.apply_screening_rule``). A tool failure or an interruption is not a
    refusal: it leaves the task undecided and the screening incomplete (no candidate selected). A
    contaminated attempt is decided and counts as 0 (``judge.verdict`` ``CONTAMINATED``).
    ``campaign_conclusion``: ``candidate_selected``, ``keep_cloud`` (the rule stopped),
    ``incomplete_screening`` (a relaunch can still complete it), or ``keep_cloud_insufficient_evidence``
    when the screening can no longer be completed under the resume rule: every frozen candidate has all
    its tasks either decided or undecided for good (cut twice, or settled without a record:
    ``not_completable_tasks``). PAT-ADR-0015: insufficient proof keeps the cloud; no candidate is retained
    and a new attempt needs a protocol v3."""
    by_candidate: dict[str, list[dict[str, Any]]] = {}
    undecided: dict[tuple, dict[str, Any]] = {}
    gone = _superseded(attempts)
    for rec in attempts:
        if rec["path"] != "XS":
            continue
        cid = rec["local"]["candidate"]
        if rec.get("outcome") in LOST_OUTCOMES:
            if id(rec) not in gone:
                undecided[_logical_key(rec)] = {"candidate": cid, "pr": rec["task"]["pr"],
                                                "outcome": rec["outcome"], "replayed": bool(rec.get("replay_of"))}
        else:
            by_candidate.setdefault(cid, []).append({"pr": rec["task"]["pr"], "judge": rec["judge"],
                                                     "wall_seconds": rec["wall_seconds"], "rec": rec})
    rows = {cid: lfe.candidate_row(items) for cid, items in sorted(by_candidate.items())}
    for cid, row in rows.items():
        swaps = [i["rec"]["machine"]["after"]["swap_used_mib"] for i in by_candidate[cid]]
        row["peak_swap_used_mib"] = None if any(x is None for x in swaps) else max(swaps)
        row["harness"] = sorted({i["rec"]["local"]["harness"] for i in by_candidate[cid]})
    undecided_tasks = list(undecided.values())
    lost = len(undecided_tasks) + killed
    frozen = list(rule["candidates"])  # complete only when ALL the frozen candidates have all their tasks
    missing = [c for c in frozen if c not in rows or rows[c]["tasks"] != rule["tasks"]]
    complete = bool(rows) and not lost and not missing
    outcome = (lfe.apply_screening_rule(rule, {c: r for c, r in rows.items() if c in frozen}, complete)
               if rows or not undecided_tasks else {"selected": None, "reason": "incomplete_screening", "stop": None})
    keys = {_logical_key(r) for r in attempts if r["path"] == "XS"} | {
        (e.get("path"), e.get("pr"), e.get("set"), e.get("candidate"), e.get("segment"), e.get("attempt"))
        for e in ledger if e.get("kind") == "attempt_started" and e.get("path") == "XS"}
    states = {key: _resume_state(attempts, ledger, key)[0] for key in keys}  # what a relaunch would do
    dead = sorted((key[3], key[1]) for key, state in states.items() if state == "undecided")
    settled_for_good = all(
        (rows[c]["tasks"] if c in rows else 0) + sum(1 for cid, _ in dead if cid == c) == rule["tasks"]
        for c in frozen)
    stuck = bool(dead) and settled_for_good and "replay" not in states.values()
    conclusion = ("candidate_selected" if outcome["selected"] else
                  "keep_cloud" if outcome.get("stop") == "keep_cloud" else
                  "keep_cloud_insufficient_evidence" if stuck else
                  "incomplete_screening" if not complete else "no_candidate_selected")
    return {"candidates": {c: {k: v for k, v in r.items() if not k.startswith("_")} for c, r in rows.items()},
            **outcome, "complete": complete, "missing_candidates": missing,
            "undecided_tasks": undecided_tasks, "killed_not_replayed": killed,
            "not_completable_tasks": [{"candidate": c, "pr": pr} for c, pr in dead],
            "campaign_conclusion": conclusion}


def _check_compared_explorer(screening: Mapping[str, Any], attempts: Sequence[Mapping[str, Any]],
                             comparison: Sequence[Mapping[str, Any]]) -> str:
    """Arm ``L`` must use the candidate the v2 screening selected (as ``_check_compared_candidate``)."""
    compared = {(r.get("local") or {}).get("candidate") for r in comparison if r["path"] == "L"}
    compared.discard(None)
    if not any(r["path"] == "XS" for r in attempts):
        return "screening_results_not_available"
    if compared and compared != {screening["selected"]}:
        raise RunnerError(f"the compared candidate(s) {sorted(compared)} are not the one the exploration "
                          f"screening selected ({screening['selected']}: {screening['reason']})")
    return screening["selected"] or "none_selected"


def _report_exploration_comparison(rule: Mapping[str, Any], attempts: Sequence[Mapping[str, Any]],
                                   stops: Sequence[str], unknown_work: bool = False,
                                   warnings: Sequence[str] = (), unknown_detail: Sequence[str] = ()
                                   ) -> dict[str, Any]:
    """Arms ``L`` (retained local explorer) and ``E`` (cloud economy explorer) against ``A`` with the three
    verdicts kept separate. The decision concerns ``L`` only (``retained`` = the local exploration is
    worth it for this use); ``E`` is informative, never recommended. Nothing is promoted. ``decision`` and
    ``campaign_conclusion`` are the authoritative keys; under protocol v5 ``paired_rule.verdict`` always equals
    ``decision`` (``_apply_paired_rule``). ``unknown_detail``: the reasons of ``_check_ledger`` (v5 only reads them)."""
    arms = {p: _arm_totals([r for r in attempts if r["path"] == p]) for p in EXPLORE_ARMS
            if any(r["path"] == p for r in attempts)}
    out: dict[str, Any] = {"arms": {}, "decision": "inconclusive", "recommendation": None,
                           "warnings": list(warnings),
                           # PAT-123: only the informative arm that was played (v4 has no arm E)
                           "informative_arms": [p for p in ("E",) if p in arms]}
    reference = arms.get("A")
    if reference is None:
        out["reason"] = "no_reference_path_A"
        out["campaign_conclusion"] = "incomplete_campaign"
        return out
    gone = _superseded(attempts)
    for path in ("L", "E"):
        if path not in arms:
            continue
        arm = arms[path]
        shared = sorted(set(arm["tasks"]) & set(reference["tasks"]))
        a_tasks = [reference["tasks"][t] for t in shared]
        x_tasks = [arm["tasks"][t] for t in shared]
        explores = [r for r in attempts if r["path"] == path and r.get("segment") == "explore"]
        # An exploration that yielded its report (or a refusal) but whose implementer has no record yet (the
        # launcher was cut between the two; a relaunch plays it) is NOT a rejected task: it is undecided.
        played = {r["task"]["pr"] for r in attempts if r["path"] == path and r.get("segment") != "explore"}
        awaiting = sorted({r["task"]["pr"] for r in explores if not r.get("contaminated")
                           and r.get("outcome") not in UNKNOWN_OUTCOMES} - played)
        for pr in awaiting:
            arm["tasks"][pr]["unknown"] = True
        economy = lfe.economy_verdict(rule, a_tasks, x_tasks, unknown_work)
        if path == "L":
            locals_ = [r for r in explores if r.get("outcome") not in LOST_OUTCOMES]
            lost = any(r.get("outcome") in LOST_OUTCOMES and id(r) not in gone for r in explores)
            compat = _compatibility(rule, locals_, lost)
        else:
            compat = "pass"  # no local work: nothing machine-bound to fail
        models_x: dict[str, dict[str, int | None]] = {}
        models_a: dict[str, dict[str, int | None]] = {}
        for t in x_tasks:
            _merge_models(models_x, t["premium_by_model"])
        for t in a_tasks:
            _merge_models(models_a, t["premium_by_model"])
        economy["detail"].update(premium_by_model=models_x, reference_premium_by_model=models_a,
                                 wall_seconds=round(sum(t["wall_seconds"] for t in x_tasks), 3),
                                 reference_wall_seconds=round(sum(t["wall_seconds"] for t in a_tasks), 3),
                                 local_explorer_wall_seconds=(round(sum(r["wall_seconds"] for r in explores), 3)
                                                              if path == "L" else None))
        out["arms"][path] = {
            "tasks_compared": len(shared), "informative": path == "E", "awaiting_implementer": awaiting,
            "compatibility": compat, "quality": lfe.quality_verdict(a_tasks, x_tasks) if shared else "unavailable",
            "economy": economy["verdict"], "economy_detail": economy["detail"],
            "accepted": sum(1 for t in x_tasks if t["accepted"]),
            "reference_accepted": sum(1 for t in a_tasks if t["accepted"]),
            "explorer_localization": [
                {"pr": r["task"]["pr"], "outcome": r.get("outcome"),
                 **{k: ((r.get("exploration") or {}).get("score") or {}).get(k)
                    for k in ("verdict", "file_recall", "file_precision", "function_recall")}}
                for r in explores]}
    mine = out["arms"].get("L")
    # The rule is the rule of the whole corpus (v2 has no sequential stop): a partial campaign (cap, tool
    # failure, interruption) concludes nothing, whatever the verdicts of the tasks played so far.
    out["complete"] = bool(mine) and mine["tasks_compared"] == rule["tasks"] and (
        arms["A"]["tasks"].keys() == arms["L"]["tasks"].keys() and len(arms["A"]["tasks"]) == rule["tasks"]
        and not mine["awaiting_implementer"])
    v5 = rule.get("paired_decided_min") is not None and bool(mine)
    if v5:
        # The ratio that counts under v5 is ``paired_rule.ratio`` (on D). The v2 to v4 figures of ``economy_detail``
        # are computed over every compared task, undecided ones included, and the v2 to v4 readings of quality and
        # economy are not the v5 rule: never printed under v5, complete campaign or not; the totals stay.
        mine["quality"], mine["economy"] = "unavailable", "unavailable"
        mine["economy_detail"].update(
            ratio=None, premium_pass=None, premium_per_accepted=None, reference_premium_per_accepted=None,
            superseded_by="paired_rule: under protocol v5 the ratio and the premium per accepted task are those of "
                          "exploration_comparison.paired_rule (paired decided set; absent while the campaign is "
                          "incomplete); the totals here cover every compared task")
    paired = v5 and out["complete"]
    if paired:
        # protocol v5: undecided tasks leave the paired set; the campaign-level reasons are applied inside, so that
        # ``paired_rule.verdict`` and ``decision`` cannot disagree
        _apply_paired_rule(rule, out, arms, attempts, gone, unknown_detail, warnings)
    elif mine and out["complete"]:
        verdicts = (mine["compatibility"], mine["quality"], mine["economy"])
        if all(v == "pass" for v in verdicts):
            out["decision"], out["recommendation"] = "retained", "L"
        elif "fail" in verdicts:
            out["decision"], out["recommendation"] = "keep_cloud", "A"
    if not paired and (unknown_work or warnings or any(r.get("outcome") == "interrupted" and id(r) not in gone
                                                       for r in attempts)):
        out["decision"], out["recommendation"] = "inconclusive", None
    # PAT-ADR-0015: insufficient proof keeps the cloud. A COMPLETE comparison that is neither retained nor
    # failed (an ``unavailable`` verdict) concludes the campaign on keeping the cloud; no replay, no extra
    # task under v2 (a new attempt needs a v3). An incomplete campaign concludes nothing yet.
    out["campaign_conclusion"] = (
        "incomplete_campaign" if not out["complete"] else
        "retain_local_explorer" if out["decision"] == "retained" else
        "keep_cloud" if out["decision"] == "keep_cloud" else "keep_cloud_insufficient_evidence")
    return out


def _cut_without_cloud(rec: Mapping[str, Any]) -> bool:
    """Protocol v5 report only: an ``interrupted`` record that names NO cloud execution. ``_tool_error`` and
    ``_flush_unrecorded`` give every interrupted record a null ``billing_total`` (v1 to v4 read it so, unchanged),
    also when no cloud execution was reserved for it: a local exploration cut, a cloud round cut before its
    reservation. Such a record spent no premium token: a session id is appended to the record's sessions in the same
    signal-deferred block that reserves it in the ledger (``cloud_execution``), so an empty ``cloud_sessions`` means
    no reservation; and a cloud execution the ledger started that NO record names is a campaign-level reason of its
    own (``no_result_record``), whatever this function says."""
    return (rec.get("status") == "interrupted" and rec["premium"]["billing_total"] is None
            and not rec.get("cloud_sessions") and not rec.get("cloud_executions"))


def _apply_paired_rule(rule: Mapping[str, Any], out: dict[str, Any], arms: Mapping[str, Any],
                       attempts: Sequence[Mapping[str, Any]], gone: Collection[int],
                       unknown_detail: Sequence[str] = (), warnings: Sequence[str] = ()) -> None:
    """Protocol v5 (PAT-126, ``rules.exploration_comparison.paired_decided_min``, protocol section 2), replacing for
    v5 the v2 to v4 behaviour, where one undecided task made the ECONOMY verdict ``unavailable`` (so ``retained`` was
    impossible) while quality stayed read by bounds (``lfe.quality_verdict``: pass, fail or unavailable) and the
    compatibility did not depend on it (``keep_cloud`` stayed possible).

    (i) UNDECIDED TASK. A task is DECIDED for an arm when it is accepted or not unknown (``_arm_totals``: no record
    left with an outcome of ``UNKNOWN_OUTCOMES`` - contaminated, unreadable review, tool error, cap, interruption -
    that a replay did not supersede); the paired set D holds the tasks decided in both arms. An undecided task only
    leaves D, also when one of its cloud executions was interrupted or has unknown tokens (its cost is then printed
    as unknown, ``premium_tokens_on_undecided_tasks``).

    The rule on D, in this order: fewer than ``paired_decided_min`` tasks in D: inconclusive. A premium total unknown
    on a record of a task of D (iii): inconclusive. ACCEPTANCE: accepted_L < accepted_A: the criterion fails, keep
    the cloud (``not_retained_on_paired_set``, ``failed_criteria`` names ``acceptance``), whatever the zeros -
    accepted_L = 0 with accepted_A >= 1 included (review round 4: it was read ``inconclusive``, an undefined ratio,
    before the comparison; v2 to v4 gave ``keep_cloud`` there). Else (accepted_L >= accepted_A) accepted_A = 0 - both
    arms accepted nothing, or A nothing and L something: inconclusive, A's premium per accepted task is undefined and
    no ratio can be computed (never zero, never an infinite ratio read as a pass). Else ECONOMY (premium per accepted
    task of L <= ratio_max x A's; every record of the arm on D's tasks, superseded ones and the reviewer included,
    local tokens excluded): failing, keep the cloud (``failed_criteria`` names ``economy``). Then the compatibility
    criterion: FAILING, keep the cloud. Acceptance and economy passing and
    compatibility ``unavailable`` (unmeasured swap, a local exploration lost without a successful replay):
    inconclusive, ``compatibility_unavailable`` - absent data is not a failure. All three passing: L is retained,
    subject to the robustness reading (every undecided task of L counted not accepted, every undecided task of A
    counted accepted, over all the tasks): below A's worst case, inconclusive. Only ``retained`` has a robustness
    reading: ``keep_cloud`` is the conservative outcome, the reading cannot make it more conservative. The cost ratio
    is not recomputed in the worst case (the acceptance of an undecided task is unknown).

    (ii) CAMPAIGN-LEVEL reasons, which give ``inconclusive`` whatever D says (money or integrity of the ledger): a
    cloud execution the ledger started and never settled, one no result record names, an interrupted or
    unknown-token cloud execution that belongs to a task of D or to no task of the comparison, and a start the ledger
    holds with neither record nor replay (``warnings``). The verdict of the rule on D is kept in
    ``verdict_before_campaign_level``; ``verdict`` is the final one and always equals the report's ``decision``.

    (iii) PREMIUM OF A RECORD. Unknown (never zero) when the record's ``billing_total`` is null: an interrupted
    record that names a cloud execution, a record one of whose cloud executions has unreadable tokens, a tool-error
    record whose cloud executions were not all read back into it. EXCEPT an interrupted record that names no cloud
    execution (``_cut_without_cloud``): it counts 0, known - no cloud execution was reserved for it, so an
    interruption that spent nothing, replayed as the resume prescribes, does not make the campaign inconclusive.
    ``premium_total_unknown`` is the reason of the rule on D; it comes WITH a campaign-level reason when the ledger
    marks the execution interrupted or its tokens unknown (a cloud execution cut while it ran, a session log without
    tokens), and WITHOUT one when the ledger knows the tokens the record does not carry (a tool error or an
    interruption that came after the execution was settled and before any verdict: toolset refusal, audit, patch
    capture, judge): inconclusive either way. ``arms.L.quality`` and ``arms.L.economy`` are the readings on D,
    ``unavailable`` when not read."""
    a_tasks, l_tasks = arms["A"]["tasks"], arms["L"]["tasks"]
    mine = out["arms"]["L"]
    minimum = rule["paired_decided_min"]

    def decided(task: Mapping[str, Any]) -> bool:
        return bool(task["accepted"]) or not task["unknown"]

    both = sorted(set(a_tasks) & set(l_tasks))
    paired = [pr for pr in both if decided(a_tasks[pr]) and decided(l_tasks[pr])]

    def causes(path: str, tasks: Mapping[Any, Mapping[str, Any]]) -> dict[str, list[str]]:
        """Exactly the tasks ``decided`` holds undecided, each with the records that leave it so."""
        found: dict[str, list[str]] = {str(pr): [] for pr, task in sorted(tasks.items()) if not decided(task)}
        for r in attempts:
            key = str(r["task"]["pr"])
            if r["path"] == path and key in found and id(r) not in gone and r.get("outcome") in UNKNOWN_OUTCOMES:
                found[key].append(f"{r.get('segment')}:{r['outcome']}")
        return {key: why or ["unknown"] for key, why in found.items()}

    def premium(path: str, pr: Any) -> int | None:
        """Premium tokens of an arm on a task under v5: every record of the arm, superseded ones included. A record
        whose total is unknown keeps the task's premium unknown, EXCEPT a cloud-less interrupted record (iii)."""
        return _sum_known([0 if _cut_without_cloud(r) else r["premium"]["billing_total"]
                           for r in attempts if r["path"] == path and r["task"]["pr"] == pr])

    undecided = {"A": causes("A", a_tasks), "L": causes("L", l_tasks)}
    wasted = {"A": [premium("A", pr) for pr, t in a_tasks.items() if not decided(t)],
              "L": [premium("L", pr) for pr, t in l_tasks.items() if not decided(t)]}
    acc_a = sum(1 for pr in paired if a_tasks[pr]["accepted"])
    acc_l = sum(1 for pr in paired if l_tasks[pr]["accepted"])
    prem_a = _sum_known([premium("A", pr) for pr in paired])
    prem_l = _sum_known([premium("L", pr) for pr in paired])
    ratio_max = Fraction(str(rule["premium_per_accepted_ratio_max"]))
    detail: dict[str, Any] = {
        "paired_decided_min": minimum, "paired_decided": paired, "paired_decided_count": len(paired),
        "undecided": undecided, "premium_tokens_on_undecided_tasks": {
            k: (None if any(v is None for v in vals) else sum(vals)) for k, vals in wasted.items()},
        "accepted_on_paired": {"A": acc_a, "L": acc_l}, "premium_on_paired": {"A": prem_a, "L": prem_l},
        "premium_per_accepted": {"A": None if prem_a is None or not acc_a else round(prem_a / acc_a, 3),
                                 "L": None if prem_l is None or not acc_l else round(prem_l / acc_l, 3)},
        "ratio_max": float(ratio_max), "ratio": None, "compatibility": mine["compatibility"],
        "failed_criteria": []}  # of ``not_retained_on_paired_set``: "acceptance", "economy" (when it was read)
    worst_a = sum(1 for t in a_tasks.values() if t["accepted"] or not decided(t))
    worst_l = sum(1 for t in l_tasks.values() if t["accepted"])
    detail["worst_case"] = {"A_undecided_counted_accepted": worst_a, "L_undecided_counted_not_accepted": worst_l,
                            "robust": None, "ratio_recomputed": False,
                            "why_no_ratio": "the acceptance of an undecided task is unknown (its cost is known "
                                            "unless one of its cloud executions was cut)"}
    verdict, reason = "inconclusive", None
    if len(paired) < minimum:
        reason = f"paired_decided_set_below_{minimum}"
    elif prem_a is None or prem_l is None:
        reason = "premium_total_unknown"
    elif acc_l < acc_a:
        # The acceptance criterion FAILS on D, whatever the zeros (accepted_L = 0 with accepted_A >= 1 included: it
        # is a real failure, as under v2 to v4, never an "undefined ratio"). The economy is still printed when it can
        # be computed (accepted_L >= 1; accepted_A >= 1 holds here); it does not change this verdict.
        mine["quality"] = "fail"
        detail["failed_criteria"] = ["acceptance"]
        if acc_l:
            detail["ratio"] = round(float((prem_l * acc_a) / (prem_a * acc_l)), 4) if prem_a else None
            economy = prem_l * acc_a <= ratio_max * prem_a * acc_l
            mine["economy"] = "pass" if economy else "fail"
            detail["failed_criteria"] += [] if economy else ["economy"]
        verdict, reason = "keep_cloud", "not_retained_on_paired_set"
    elif not acc_a:
        # accepted_L >= accepted_A = 0: both arms accepted nothing, or A accepted nothing and L something. A's premium
        # per accepted task is undefined, no ratio can be computed: never zero, never an infinite ratio read as a pass
        mine["quality"] = "pass"
        reason = "no_accepted_task_in_one_arm_ratio_undefined"
    else:
        detail["ratio"] = round(float((prem_l * acc_a) / (prem_a * acc_l)), 4) if prem_a else None
        economy = prem_l * acc_a <= ratio_max * prem_a * acc_l
        mine["quality"] = "pass"
        mine["economy"] = "pass" if economy else "fail"
        if not economy:
            detail["failed_criteria"] = ["economy"]
            verdict, reason = "keep_cloud", "not_retained_on_paired_set"
        elif mine["compatibility"] == "fail":
            verdict, reason = "keep_cloud", "compatibility_failed"
        elif mine["compatibility"] != "pass":  # unavailable: absent data, never read as a failure
            reason = "compatibility_unavailable"
        else:
            detail["worst_case"]["robust"] = worst_l >= worst_a
            verdict, reason = ("retained", None) if worst_l >= worst_a else ("inconclusive", "not_robust_to_undecided_tasks")
    # Campaign-level reasons (ii). An interrupted or unknown-token cloud execution named by a record of a task
    # OUTSIDE D is task-level: that task is undecided and its tokens enter no ratio.
    outside = {sid for r in attempts if r["task"]["pr"] not in paired for sid in r.get("cloud_sessions") or []}
    campaign_level = sorted(
        entry for entry in unknown_detail
        if not (entry.partition(":")[0] in ("interrupted", "tokens_unknown") and entry.partition(":")[2] in outside))
    campaign_level += [f"start_without_record_nor_replay:{w}" for w in warnings]
    detail.update(verdict_before_campaign_level=verdict, reason_before_campaign_level=reason,
                  campaign_level_reasons=campaign_level)
    if campaign_level:
        verdict, reason = "inconclusive", "campaign_level_unknown_work"
    detail.update(verdict=verdict, reason=reason)
    out["paired_rule"] = detail
    out["decision"], out["recommendation"] = ({"retained": ("retained", "L"), "keep_cloud": ("keep_cloud", "A")}
                                              .get(verdict, ("inconclusive", None)))


# ------------------------------------------------------------------ offline audit replay (PAT-123)

REPLAY_SCHEMA = "foundry.local-first-audit-replay.v1"


def _stream_cwd(stream: Path) -> Path | None:
    """The bundle of a recorded stream: the ``cwd`` its first events name (Claude ``system``/``init``, omp
    ``session``). ``None`` when the stream names none."""
    try:
        with Path(stream).open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and isinstance(event.get("cwd"), str) and event["cwd"]:
                    return Path(event["cwd"])
    except OSError:
        return None
    return None


def _hidden_home(hit: str, home: str) -> str:
    """A hit as it may be committed: the name of anything under the real home is not copied."""
    prefix, path = ("tool_result:", hit[len("tool_result:"):]) if hit.startswith("tool_result:") else ("", hit)
    if path == "~" or path.startswith("~/") or path.startswith(home.rstrip("/") + "/"):
        path = "~/<hidden>"
    return prefix + path


def _policy_reading(rec: Mapping[str, Any], streams: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per stream, the revision 2 findings (``strict``) and what ``isolation.audit_policy`` makes of them
    (``decisive`` / ``journal``), and a NON-DECISIONAL reading of what the record's outcome would have been: nothing is
    recomputed, the recorded outcome and verdicts stay what they are."""
    rows = []
    for st in streams:
        pol = st.get("policy")
        if pol is None:
            continue
        strict = st["new"]
        rows.append({"role": st["role"], "stream": st["stream"], "strict": strict, "decisive": pol["decisive"],
                     "journal": pol["journal"], "refused_calls": pol["refused_calls"],
                     "class": ("clean" if not strict else
                               "decisive_kept" if set(pol["decisive"]) == set(strict) else
                               "decisive_reduced" if pol["decisive"] else
                               "journal_only" if pol["journal"] else "refused_calls_only")})
    arm_decisive = any(r["decisive"] for r in rows if r["role"] == "arm")
    review_decisive = any(r["decisive"] for r in rows if r["role"] == "reviewer")
    verdicts = (rec.get("review") or {}).get("verdicts") or []
    recorded = rec.get("outcome")
    reading = None
    if recorded == "review_unreadable" and not arm_decisive and not review_decisive and any(
            r["role"] == "reviewer" and r["class"] in ("journal_only", "refused_calls_only") for r in rows):
        last = verdicts[-1] if verdicts else None
        reading = {"PASS": "accepted", "BLOCK": "review_block"}.get(last, "undetermined (verdict not on the record)")
    return {"streams": rows, "outcome_recorded": recorded, "outcome_reading_non_decisional": reading
            if reading is not None else recorded,
            "note": "a reading, not a recomputation: the record, its verdicts and the report are unchanged"}


def replay_audit(campaign: Mapping[str, Any], records: Sequence[Mapping[str, Any]],
                 ledger: Sequence[Mapping[str, Any]], *, streams_dir: Path, work_root: Path, repo: Path,
                 state_dir: Path, home: str, results_sha256: str | None = None,
                 work_root_sensitive: bool = True, absent_forms: bool = False) -> dict[str, Any]:
    """Audit revision 1 (what ran) and revision 2 (PAT-123) applied to the RAW transcripts of a finished
    campaign, record by record. Offline: it reads the streams, the results and the ledger and runs no
    driver, no model and no cloud call; it writes and recomputes nothing (no verdict, no outcome, no
    report). Each record gets the hits as recorded, as revision 1 finds them again (``fidelity``: the
    replay reproduces the recorded flags), and as revision 2 finds them, with the paths named but not
    existing apart. The context of a stream (bundle, attempt directory) is the ``cwd`` it names; the
    sensitive list is rebuilt from the repository, the state directory and the home of NOW (``repo``,
    ``state_dir``, ``home``), the base literals come from ``repo`` and the local sandbox is not
    reconstructed: the approximations are in ``replay_limits``. A record whose revision-1 replay differs from
    what was recorded is ``not_comparable``. A stream that is missing leaves its record ``unavailable``,
    never clean. Nothing under ``home`` is copied to the result. The summary counts apart the records whose arm
    session is flagged now (``flagged_now``) and those whose reviewer session is (``reviewer_flagged_now``), and
    the streams by what revision 2 could assume of their host (``host_models``, see ``_host_model``).
    ``absent_forms`` (PAT-128; off by default, and then the result is exactly what it was): revision 2 is applied with
    ``isolation.audit_absent_path_forms``, a MEASUREMENT of the new coordinate on a campaign that does not carry it; the
    result then says so (``absent_path_forms``)."""
    campaign_id = next((r.get("campaign_id") for r in records if r.get("campaign_id")), None)
    started = {}
    for entry in ledger:
        if entry.get("kind") == "attempt_started" and entry.get("attempt_dir"):
            started[(entry.get("path"), entry.get("pr"), entry.get("segment"), entry.get("attempt"))] = \
                entry["attempt_dir"]
    iso = campaign.get("isolation") or {}
    private = bool(iso.get("private_attempt_root"))
    work = Path(os.path.realpath(work_root))
    sensitive = read_deny_list(repo=Path(repo), home=home, state_dir=Path(state_dir), input_paths=[],
                               kind="local_harness", isolation=iso.get("deny_read_home"))
    if private and work_root_sensitive:  # False: the measurement of what the v4 sensitive-root change explains
        sensitive.append(work)
    home_real = os.path.realpath(home)
    literals = base_literals(Path(repo), home)  # the checkout of now stands for the bundles of then

    cloud_deny = read_deny_list(repo=Path(repo), home=home, state_dir=Path(state_dir), input_paths=[],
                                kind="cloud_implementer", isolation=iso.get("deny_read_home"))
    policy_on = iso.get(AUDIT_POLICY_KEY) == AUDIT_POLICY

    def os_roots(attempt: Path) -> tuple[Sequence[Path], Sequence[Path]]:
        """denyRead and allowRead of the settings an execution of that attempt received (rebuilt, not read)."""
        fs = native_sandbox_settings(attempt_dir=attempt, work_root=work, home=home, deny_read=cloud_deny,
                                     allow_read=[Path(home) / rel for rel in iso.get("allow_read_home", [])]
                                     )["sandbox"]["filesystem"]
        return [Path(x) for x in fs["denyRead"]], [Path(x) for x in fs["allowRead"]]

    def audit(stream: Path, bundle: Path, role: str, path: str, session: str | None, revision: int,
              notes: list[str], models: list[str], policy: Mapping[str, Any] | None = None) -> list[str]:
        attempt = bundle.parent
        driver = (campaign.get("drivers") or {}).get(
            REVIEWER_DRIVER if role == "reviewer" else IMPLEMENTER_DRIVER.get(path, ""), {})
        own = ((driver.get("session_log") or {}).get("projects_dir", "~/.claude/projects"), session) \
            if session else None
        return audit_transcript(stream, bundle=bundle, scratch=attempt / "scratch", sensitive=sensitive,
                                home=home, arm_home=None if session else str(attempt / "scratch" / "home"),
                                own_session=own, attempt_dir=attempt, literals=literals,
                                private_root=attempt.parent if private else None,
                                revision=revision, not_found=notes, host_model=models,
                                absent_forms=absent_forms and revision >= AUDIT_REVISION, **(policy or {}))

    rows = []
    for rec in records:
        if rec.get("record_type") != "attempt":
            continue
        key = (rec["path"], rec["task"]["pr"], rec.get("segment"), rec.get("attempt"))
        # (role, stream file, cloud session id, label): the label names a stream without its session id
        sources: list[tuple[str, Path, str | None, str]] = []
        for n, session in enumerate(rec.get("cloud_sessions") or []):
            sources.append(("arm" if n == 0 else "reviewer",
                            Path(streams_dir) / f"{campaign_id}-{session}.jsonl", session, f"cloud_sessions[{n}]"))
        if key in started:
            sources.append(("arm", Path(streams_dir) / f"{campaign_id}-{started[key]}.jsonl", None,
                            f"local:{started[key]}"))
        recorded = [*((rec.get("contamination") or {}).get("paths") or []),
                    *(f"command:{c}" for c in (rec.get("contamination") or {}).get("commands") or [])]
        row: dict[str, Any] = {"path": key[0], "pr": key[1], "segment": key[2], "attempt": key[3],
                               "recorded": {"outcome": rec.get("outcome"),
                                            "contaminated": bool(rec.get("contaminated")),
                                            "hits": sorted(_hidden_home(h, home_real) for h in recorded)}}
        streams, unavailable = [], None
        for role, stream, session, label in sources:
            cwd = _stream_cwd(stream) if stream.is_file() else None
            bundle = Path(os.path.realpath(cwd)) if cwd is not None else None
            if bundle is None or work not in bundle.parents:
                unavailable = f"{label}: " + ("stream missing" if not stream.is_file() else
                                                    "no usable cwd inside the work root")
                break
            notes: list[str] = []
            models: list[str] = []
            old = audit(stream, bundle, role, key[0], session, 1, [], [])
            new = audit(stream, bundle, role, key[0], session, AUDIT_REVISION, notes, models)
            entry = {"role": role, "stream": label, "host_model": models[0],
                     "sha256": hashlib.sha256(stream.read_bytes()).hexdigest(),
                     "old": [_hidden_home(h, home_real) for h in old],
                     "new": [_hidden_home(h, home_real) for h in new],
                     "not_found": list(dict.fromkeys(_hidden_home(n, home_real) for n in notes))}
            if policy_on and (rec.get("audit") or {}).get("barrier") == BARRIER_OBSERVED:
                journal, refused = [], {}
                decisive = audit(stream, bundle, role, key[0], session, AUDIT_REVISION, [], [], {
                    "policy_denied": os_roots(bundle.parent), "journal": journal, "refused": refused})
                entry["policy"] = {"decisive": [_hidden_home(h, home_real) for h in decisive],
                                   "journal": [_hidden_home(h, home_real) for h in journal],
                                   "refused_calls": refused}
            streams.append(entry)
        if unavailable:
            row.update(classification="unavailable", unavailable=unavailable)
            rows.append(row)
            continue
        old_arm = [h for st in streams if st["role"] == "arm" for h in st["old"]]
        old_review = [h for st in streams if st["role"] == "reviewer" for h in st["old"]]
        new_arm = [h for st in streams if st["role"] == "arm" for h in st["new"]]
        new_review = [h for st in streams if st["role"] == "reviewer" for h in st["new"]]
        not_found = [n for st in streams for n in st["not_found"]]
        old_all = sorted(set(old_arm + old_review))
        was, now = bool(old_all), bool(new_arm)
        row["fidelity"] = "match" if old_all == row["recorded"]["hits"] else "mismatch"
        row["old"] = {"contaminated": was, "session": "arm" if old_arm else "reviewer" if old_review else None,
                      "hits": old_all}
        row["new"] = {"contaminated": now, "hits": sorted(set(new_arm)), "reviewer_hits": sorted(set(new_review)),
                      "not_found": not_found}
        row["classification"] = (
            "not_comparable" if row["fidelity"] == "mismatch" else
            ("flag_kept" if set(old_arm) == set(new_arm) else "flag_kept_changed") if was and now else
            "flag_moved_to_review" if was and new_review else
            "flag_moved_to_not_found" if was and not_found else
            "flag_removed" if was else
            "flag_added" if now or new_review else "clean")
        row["streams"] = streams
        if any("policy" in st for st in streams):
            row["policy"] = _policy_reading(rec, streams)
        if policy_on:  # PAT-128: the arm findings the rules of that campaign (revision 2 and policy) give, against the record
            kept = sorted({h for st in streams if st["role"] == "arm" for h in st.get("policy", {}).get("decisive", st["new"])})
            row["arm_findings_replayed"] = {"hits": kept, "same_as_recorded": kept == row["recorded"]["hits"]}
        rows.append(row)
    classes: dict[str, int] = {}
    for row in rows:
        classes[row["classification"]] = classes.get(row["classification"], 0) + 1
    out = {"schema": REPLAY_SCHEMA, "campaign_id": campaign_id, "revisions": {"old": 1, "new": AUDIT_REVISION},
           **({"absent_path_forms": ABSENT_FORMS} if absent_forms else {}),
           "results_sha256": results_sha256, "records": rows,
           "summary": {"records": len(rows), "classification": dict(sorted(classes.items())),
                       "fidelity_mismatch": sum(1 for r in rows if r.get("fidelity") == "mismatch"),
                       "flagged_recorded": sum(1 for r in rows if r["recorded"]["contaminated"]),
                       **({"policy_classes": {c: sum(1 for r in rows if "policy" in r for x in r["policy"]["streams"] if x["class"] == c)
                                              for c in sorted({x["class"] for r in rows if "policy" in r
                                                               for x in r["policy"]["streams"]})}}
                          if any("policy" in r for r in rows) else {}),
                       "flagged_now": sum(1 for r in rows if r.get("new", {}).get("contaminated")),
                       "reviewer_flagged_now": sum(1 for r in rows if r.get("new", {}).get("reviewer_hits")),
                       "host_models": {m: sum(1 for r in rows for st in r.get("streams", [])
                                              if st["host_model"] == m)
                                       for m in sorted({st["host_model"] for r in rows
                                                        for st in r.get("streams", [])})}},
           "replay_limits": ["offline: no model, no cloud call; nothing is recomputed (verdicts, outcomes, report)",
                             "sensitive roots rebuilt from the repository, state directory and home of the replay",
                             "base literals come from the checkout of the replay, not from each bundle; the local "
                             "sandbox denial is not reconstructed",
                             "roles: the first cloud session of a record is taken as the arm's and the others as the "
                             "reviewer's (true for paths A and L; wrong for path C or a resumed record)",
                             "work root sensitive in the rebuilt list: " + ("yes" if work_root_sensitive else
                                                                         "no (scope measurement)")]}
    text = json.dumps(out, sort_keys=True)
    if home_real not in ("/", "") and (home_real in text or str(home).rstrip("/") in text):
        raise RunnerError("the replay result would copy a path of the home directory: refused")
    return out


# ----------------------------------------------------------------------------------- CLI

def _tasks(manifest: Mapping[str, Any], snapshot: Mapping[str, Any], group: str) -> list[dict[str, Any]]:
    by_pr = {t["pr"]: t for t in snapshot["prs"]}
    return [by_pr[t["pr"]] for t in manifest[group]]


def _listed_tasks(manifest: Mapping[str, Any], snapshot: Mapping[str, Any], prs: Sequence[int]
                  ) -> list[dict[str, Any]]:
    """PAT-126: the tasks of ``exploration.comparison_tasks``, in the listed order. Each PR must belong to the
    corpus manifest (its ``comparison`` or ``screening`` group) and to the snapshot; otherwise the launch is refused
    before any claim."""
    corpus = {t["pr"] for g in ("comparison", "screening") for t in manifest.get(g, [])}
    by_pr = {t["pr"]: t for t in snapshot["prs"]}
    missing = [n for n in prs if n not in corpus or n not in by_pr]
    if missing:
        raise RunnerError(f"comparison task(s) {missing} are not in the corpus manifest and the snapshot")
    return [by_pr[n] for n in prs]


GOLDEN_SCHEMA = "foundry.local-first-golden-check.v1"


def golden_check(campaign: Mapping[str, Any], campaign_path: Path, tasks: Sequence[Mapping[str, Any]], *,
                 repo: Path, work_root: Path, today: dt.date, manifest_path: Path | None = None,
                 snapshot_path: Path | None = None) -> dict[str, Any]:
    """PAT-126, offline (local git and pytest only: no model, no cloud, no ``claude``): for each task, a bundle built
    exactly as a cloud implementer's bundle is built under this config (``Runner._bundle``), judged untouched
    (expected: ``REFUSED`` with at least one failing or erroring hidden test, never 0/0/0), then a fresh bundle with the
    merged product change applied and judged (expected: ``ACCEPTED``). A judge that cannot run shows as a failed
    expectation here, before any campaign money is spent. Pure function of its inputs: it writes nothing but the
    throwaway bundles under ``work_root``."""
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="foundry-golden-") as scratch:
        envelope_path = Path(scratch) / "envelope.json"
        envelope_path.write_text(json.dumps({
            "schema": ENVELOPE_SCHEMA, "expires_on": (today + dt.timedelta(days=1)).isoformat(),
            "campaign_id": "golden-check-pilot" if campaign.get("protocol") == PROTOCOL_V5_PILOT else "golden-check",
            "allowed_modes": ["compare_exploration"],
            "caps": {"cloud_executions": 0, "premium_tokens": 1, "wall_clock_seconds": 1}}), encoding="utf-8")
        runner = Runner(
            repo=repo, campaign=campaign, envelope=load_envelope(envelope_path, "compare_exploration", today),
            state_dir=Path(scratch) / "state", work_root=work_root, mode="compare_exploration", dry_run=True,
            sandbox=False, today=lambda: today, input_paths=[campaign_path])

        def judged(task: Mapping[str, Any], label: str, solved: bool) -> dict[str, Any]:
            bundle, attempt_dir = runner._bundle(task, f"golden-{label}")
            try:
                if solved:
                    lfc.apply_solution(runner.repo, task, bundle)
                verdict = runner._judge(task, bundle)
            except lfc.CorpusError as exc:  # includes a judge that cannot run (JudgeInstrumentError)
                return {"verdict": None, "instrument_error": str(exc)[:300]}
            finally:
                runner._discard(bundle, attempt_dir)
            return {k: verdict.get(k) for k in ("verdict", "passed", "failed", "errors", "skipped", "pytest_exit_code",
                                                "note")}

        for task in tasks:
            untouched, solved = judged(task, "untouched", False), judged(task, "solved", True)
            expect_u = untouched["verdict"] == "REFUSED" and (untouched.get("failed", 0) + untouched.get("errors", 0)) >= 1
            expect_s = solved["verdict"] == "ACCEPTED"
            rows.append({"pr": task["pr"], "untouched": untouched, "solved": solved,
                         "untouched_as_expected": expect_u, "solved_as_expected": expect_s,
                         "ok": expect_u and expect_s})
    def digest(path: Path | None) -> str:
        try:
            return hashlib.sha256(Path(path).read_bytes()).hexdigest() if path else "unknown"
        except OSError:
            return "unknown"

    try:  # the checkout this tooling runs from
        commit = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=False, timeout=30).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        commit = "unknown"
    try:  # uncommitted changes in the tooling: the commit alone then does not describe the code that ran
        dirty = bool(subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "status", "--porcelain", "--", "."],
                                    capture_output=True, text=True, check=False, timeout=30).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        dirty = None
    provenance = {"campaign_sha256": digest(campaign_path), "manifest_sha256": digest(manifest_path),
                  "snapshot_sha256": digest(snapshot_path), "tooling_commit": commit, "tooling_dirty": dirty,
                  "date": today.isoformat()}
    return {"schema": GOLDEN_SCHEMA, "protocol": campaign.get("protocol"), "provenance": provenance, "tasks": rows,
            "all_ok": bool(rows) and all(r["ok"] for r in rows),
            "expectations": {"untouched": "REFUSED with at least one failing or erroring hidden test (never 0/0/0)",
                             "solved": "ACCEPTED"}}


def main(argv: Sequence[str] | None = None, *, today: dt.date | None = None) -> int:
    parser = argparse.ArgumentParser(prog="foundry.local_first_runner", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("preflight", "screen", "compare", "screen-exploration", "compare-exploration"):
        p = sub.add_parser(name)
        p.add_argument("--campaign", required=True, help="the campaign config JSON")
        p.add_argument("--dry-run", action="store_true", help="fake drivers only, canned machine facts")
        p.add_argument("--candidate", nargs="+" if name.startswith("screen") else None, required=True)
        if name == "preflight":
            p.add_argument("--dedicated", action="store_true",
                           help="also run the protocol v2 dedicated-machine admission")
        else:
            p.add_argument("--envelope", help="authorization envelope (mandatory)")
            p.add_argument("--state-dir", required=True)
            p.add_argument("--work-root", required=True, help="disposable area outside the checkout")
            p.add_argument("--repo", default=".")
            p.add_argument("--snapshot", required=True)
            p.add_argument("--manifest", required=True)
            if not name.endswith("exploration"):  # protocol v2 has one explorer driver per kind
                p.add_argument("--harness", default="local_harness",
                               choices=("local_harness", "neutral_harness"))
            p.add_argument("--sandbox", action="store_true", help="also sandbox a dry run")
        if name in ("compare", "compare-exploration"):
            p.add_argument("--paths", default=None,
                           help="arms to play (default A,L,E for compare-exploration, A,B,C for compare; "
                                "A,L under protocol v4)")
            p.add_argument("--screening-campaign", default=None,
                           help="campaign id of a completed screening (results read-only in --state-dir) "
                                "when this campaign id holds none; C and N need one or the other")
    gc = sub.add_parser("golden-check", help="PAT-126: offline self-check of the judge on the config's tasks "
                        "(untouched bundle refused with failing tests, merged change accepted); no model, no cloud")
    gc.add_argument("--campaign", required=True)
    gc.add_argument("--repo", required=True, help="the full clone the corpus tasks are built from")
    gc.add_argument("--work-root", required=True, help="a throwaway area outside every checkout")
    gc.add_argument("--snapshot", required=True)
    gc.add_argument("--manifest", required=True)
    gc.add_argument("--out", required=True, help="the result file (never overwritten)")
    ra = sub.add_parser("replay-audit", help="apply audit revisions 1 and 2 to the raw streams of a finished "
                                             "campaign, offline (no model, no cloud call)")
    ra.add_argument("--campaign", required=True)
    ra.add_argument("--results", required=True, help="results-<campaign>.jsonl; the ledger is read beside it")
    ra.add_argument("--streams-dir", required=True, help="the raw transcripts (kept off the repository)")
    ra.add_argument("--work-root", required=True, help="the work root the campaign used (it need not exist)")
    ra.add_argument("--repo", default=".")
    ra.add_argument("--home", default=None, help="the home to rebuild the sensitive list from (default $HOME)")
    ra.add_argument("--work-root-not-sensitive", action="store_true",
                    help="leave the work root out of the sensitive list (measures what that v4 change explains)")
    ra.add_argument("--quoted-not-found", action="store_true",
                    help="PAT-128: apply revision 2 with isolation.audit_absent_path_forms (measures the new coordinate)")
    ra.add_argument("--out", default=None, help="write the result here instead of stdout (never overwrites)")
    nt = sub.add_parser("native-sandbox-trial", help="PAT-124: the bounded REAL trial of the native Bash sandbox "
                        "(two real cloud executions through the launcher: spend, run once by the maintainer)")
    nt.add_argument("--campaign", required=True, help="a campaign config with cloud drivers (read, never edited; "
                    "the native sandbox is switched on in memory for the trial)")
    nt.add_argument("--envelope", required=True)
    nt.add_argument("--state-dir", required=True)
    nt.add_argument("--work-root", required=True)
    nt.add_argument("--repo", default=".")
    nt.add_argument("--snapshot", required=True)
    nt.add_argument("--task-pr", type=int, required=True, help="the one corpus task (a PR of the snapshot)")
    nt.add_argument("--probe-test", required=True, help="one FAST test file of the bundle, relative to it: probe P2 "
                    "really runs it under the sandbox (the operator's choice; checked before any spend)")
    nt.add_argument("--no-reviewer", action="store_true")
    nt.add_argument("--out", required=True, help="the result file (never overwritten)")
    nr = sub.add_parser("native-sandbox-trial-reeval", help="PAT-124: rebuild the result of a finished trial from "
                        "its own streams with the current classification (offline, no cloud call)")
    nr.add_argument("--from-dir", required=True, help="the trial directory (result.json and state/), read only")
    nr.add_argument("--out", required=True, help="the new result file (never overwritten)")
    r = sub.add_parser("report")
    r.add_argument("--campaign", required=True)
    r.add_argument("--results", required=True,
                   help="results-<campaign>.jsonl; ledger-<campaign>.jsonl is read beside it (mandatory)")
    args = parser.parse_args(argv)
    if args.cmd == "native-sandbox-trial-reeval":
        from foundry import local_first_native_trial as trial
        try:
            trial.reevaluate(Path(args.from_dir), Path(args.out))
        except (RunnerError, trial.lfr.RunnerError, OSError, ValueError, KeyError) as exc:  # ``-m``: two module objects
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        return 0
    try:
        campaign = load_campaign(Path(args.campaign))
    except (RunnerError, OSError, ValueError) as exc:  # e.g. a frozen ground truth whose sha256 changed
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if args.cmd == "golden-check":
        try:
            manifest = json.loads(Path(args.manifest).read_text("utf-8"))
            snapshot = json.loads(Path(args.snapshot).read_text("utf-8"))
            listed = (campaign.get("exploration") or {}).get("comparison_tasks")
            group = (campaign.get("exploration") or {}).get("comparison_task_group", "comparison")
            tasks = _listed_tasks(manifest, snapshot, listed) if listed else _tasks(manifest, snapshot, group)
            out = Path(args.out)
            if out.exists():
                raise RunnerError(f"{out} exists: a result is never overwritten")
            result = golden_check(campaign, Path(args.campaign), tasks, repo=Path(args.repo),
                                  work_root=Path(args.work_root), today=today or dt.date.today(),
                                  manifest_path=Path(args.manifest), snapshot_path=Path(args.snapshot))
            with out.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
        except (RunnerError, lfc.CorpusError, OSError, ValueError, KeyError) as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        for row in result["tasks"]:
            print(f"PR {row['pr']}: untouched {row['untouched'].get('verdict')} "
                  f"({row['untouched'].get('passed')}/{row['untouched'].get('failed')}/{row['untouched'].get('errors')}) "
                  f"solved {row['solved'].get('verdict')} -> {'ok' if row['ok'] else 'FAIL'}")
        return 0 if result["all_ok"] else 1
    if args.cmd == "native-sandbox-trial":
        from foundry import local_first_native_trial as trial
        try:
            return trial.run(args, campaign, today=today)
        except (RunnerError, trial.lfr.RunnerError, OSError) as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
    if args.cmd == "replay-audit":
        try:
            results = Path(args.results)
            name = re.fullmatch(r"results-(.+)\.jsonl", results.name)
            ledger = results.with_name(f"ledger-{name.group(1)}.jsonl") if name else None
            if ledger is None or not ledger.is_file():
                raise RunnerError(f"no ledger beside {results} (expected ledger-<campaign>.jsonl)")
            streams = Path(args.streams_dir)
            result = replay_audit(
                campaign, _read_jsonl(results), _read_jsonl(ledger), streams_dir=streams,
                work_root=Path(args.work_root), repo=Path(args.repo), state_dir=streams.parent,
                home=args.home or os.environ.get("HOME") or str(Path.home()),
                results_sha256=hashlib.sha256(results.read_bytes()).hexdigest(),
                work_root_sensitive=not args.work_root_not_sensitive, absent_forms=args.quoted_not_found)
            text = json.dumps(result, indent=2, sort_keys=True) + "\n"
            if args.out:
                with Path(args.out).open("x", encoding="utf-8") as handle:  # never rewrites a result
                    handle.write(text)
            else:
                print(text, end="")
        except (RunnerError, OSError) as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        return 0
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
        if args.dedicated and "dedicated_machine" not in campaign:
            print("refused: this campaign declares no dedicated_machine", file=sys.stderr)
            return 2
        result = preflight(campaign, model, run, (lambda: 1e6) if args.dry_run else None,
                           campaign["candidates"][args.candidate], dedicated=args.dedicated)
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 2
    mode = args.cmd.replace("-", "_")
    try:
        envelope = load_envelope(Path(args.envelope) if args.envelope else None, mode,
                                 today or dt.date.today())
        candidates = args.candidate if mode in ("screen", "screen_exploration") else [args.candidate]
        for cid in candidates:
            if cid not in campaign["candidates"]:
                raise RunnerError(f"unknown candidate {cid}")
        default = ",".join(V4_ARMS) if campaign.get("protocol") in (PROTOCOL_V4, *V5_PROTOCOLS) else (
            "A,L,E" if mode == "compare_exploration" else "A,B,C")
        paths = (default if args.paths is None else args.paths).split(",") if mode in CLOUD_MODES else []
        if not set(paths) <= set(EXPLORE_ARMS if mode == "compare_exploration" else PATHS):
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
            truths=(lfe.load_truth_set(Path(args.campaign).parent, campaign["exploration"]["ground_truth"])
                    if mode in EXPLORE_MODES else None),
            sandbox=(not args.dry_run) or args.sandbox,
            run=(lambda _argv: None) if args.dry_run else default_run,
            preflight_run=(lambda cid: dry_run_facts(campaign, campaign["candidates"][cid]["model"]))
            if args.dry_run else None,
            disk_free_gib=(lambda: 1e6) if args.dry_run else None)
        if mode == "screen":
            runner.screen(_tasks(manifest, snapshot, "screening"), candidates, args.harness)
        elif mode == "screen_exploration":
            runner.screen_exploration(_tasks(manifest, snapshot, "screening"), candidates)
        elif mode == "compare_exploration":
            listed = campaign["exploration"].get("comparison_tasks")  # v5: an explicit, ordered list of PRs
            group = campaign["exploration"].get("comparison_task_group", "comparison")  # v4: "screening"
            runner.compare_exploration(_listed_tasks(manifest, snapshot, listed) if listed
                                       else _tasks(manifest, snapshot, group), candidates[0], paths)
        else:
            runner.compare(_tasks(manifest, snapshot, "comparison"), candidates[0], paths, args.harness)
        if runner.stopped and runner.stopped.startswith("cap_reached"):
            print(f"stopped: {runner.stopped}", file=sys.stderr)
            return 3
        if runner.work_remains is not None:  # protocol v3: the operator loop reads this line
            print(WORK_REMAINS_LINE.format("yes" if runner.work_remains else "no"))
    except PreflightRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except FoundryStateChanged as exc:  # stopped, recorded, nothing replayed: the operator investigates
        print(f"stopped: {STATE_CHANGED_STOP}: {exc}", file=sys.stderr)
        return 4
    except (RunnerError, lfc.CorpusError) as exc:  # includes a frozen truth that changed (ExplorationError)
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
