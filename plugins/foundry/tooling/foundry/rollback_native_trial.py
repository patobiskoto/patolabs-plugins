"""PAT-198: one bounded native trial per rollback line of PAT-17 (eight lines, one call each, no retry).

PAT-17 proved the eight rollback configurations by CLI resolution and constructed plans, with zero native calls
(``pat-17-config-rollback-2026-10-11.json``). That is configuration compatibility, NOT native proof. This module
runs, by hand and by the coordinator only, ONE native call for ONE line and records it in the dated matrix
``qualification/pat-198-native-rollback-2026-10-17.json``. The protocol (scope, surface, ceilings, stops,
restoration, observed-vs-unknown) is ``qualification/pat-198-native-rollback-protocol.md``; this module enforces
only what it can see. The tests never launch it (fake launcher, synthetic logs).

Claude lines reuse the PAT-125 surface of ``claude_profile_trial``: one ``claude -p`` parent loads this checkout
(``--plugin-dir``), delegates once to the logical role through the routing hook, from an out-of-repository fixture
whose own project policy maps the role's tier to the line's model/effort. Codex lines (PAT-245, protocol section 10)
run one ``codex exec --json`` from an out-of-repository fixture with the same lock, ledger, claim and matrix
machinery; the line's model and effort are carried by the command line (no Foundry routing is in that path), and
the model and effort are observed only in the host's own session file of a COMPLETED turn.

Requested (policy), transmitted (profile chosen offline), observed (child transcript) and account availability stay
four separate values; whatever was not observed is ``unknown``. An unavailable model is recorded as such, with the
verbatim (masked) host error; nothing is substituted, no baseline is invented, nothing is promoted.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Sequence

from foundry import claude_profile_trial as base
from foundry import registry
from foundry.command_runtime import _claude_child_environment
from foundry.routing_facades import _load_claude_policy, claude_invocation_binding

SCHEMA = "foundry.pat198-native-rollback.v1"
RESULT_SCHEMA = "foundry.pat198-native-trial.v1"
MATRIX_DATE = "2026-10-17"
MAX_NATIVE_CALLS = 8  # standing authorisation recorded by PAT-198: one per line, no retry
DEFAULT_MATRIX = Path(__file__).resolve().parents[2] / "docs" / "qualification" / "pat-198-native-rollback-2026-10-17.json"
LOGICAL = {"scout": "foundry:lupin", "implementer": "foundry:eiffel", "reviewer": "foundry:maigret",
           "architect": "foundry:vauban"}
CAPABILITY = {"scout": "readonly", "implementer": "worker", "reviewer": "readonly", "architect": "readonly"}
TIER = {"scout": "economy", "implementer": "balanced", "reviewer": "frontier", "architect": "apex"}
# (host, role) -> (model, effort); the eight configurations of examples/claude-pre-promotion-rollback.json and
# examples/codex-gpt-5.6-rollback.json (PAT-17). A line here is a configuration to examine, availability NOT asserted.
ROLLBACK = {
    ("codex", "scout"): ("gpt-5.6-luna", "low"), ("codex", "implementer"): ("gpt-5.6-terra", "medium"),
    ("codex", "reviewer"): ("gpt-5.6-sol", "high"), ("codex", "architect"): ("gpt-5.6-sol", "max"),
    ("claude", "scout"): ("haiku-4.5", None), ("claude", "implementer"): ("sonnet-5", "medium"),
    ("claude", "reviewer"): ("opus-5", "high"), ("claude", "architect"): ("fable-5", "high"),
}
LINES = {f"{host}-{role}": (host, role) for host, role in ROLLBACK}
_UNAVAILABLE = re.compile(r"(?i)(model.{0,60}(not (found|available|exist|supported)|unavailable|invalid)"
                          r"|(not (found|available)|unavailable|invalid).{0,60}model|unknown model"
                          r"|model.{0,60}(does not exist|no access)|(does not exist|no access).{0,60}model)")
_SECRET_WORDS = frozenset({"token", "secret", "password", "passwd", "key", "apikey", "auth", "authorization", "oauth"})
_SECRET_SUBSTRINGS = ("token", "secret", "password", "passwd", "apikey", "accesskey", "privatekey", "oauth")
_NOT_SECRET = re.compile(r"(?:tokeniz\w*|tokenizers?|secretar(?:y|ies))\d*")
_NAME_SEPARATOR = re.compile(r"(?<![A-Za-z0-9_\-])([A-Za-z][A-Za-z0-9_\-]*)(\s*[=:]\s*)")
_VALUE = re.compile(r"[^\s&]+")


def _secret_name(name: str) -> bool:
    """An UPPER_CASE name containing TOKEN, SECRET, KEY, PASSWORD or AUTH; else a name one of whose segments (split on
    ``_``, ``-`` and camelCase boundaries, lower-cased) is a secret word, optionally plural and/or followed by digits, or
    a longer lower-case segment containing a secret substring (``accesstoken``, ``secretkey``); ``key`` and ``auth``
    count only as a whole segment, and ``tokenizer`` / ``secretary`` are kept."""
    if name == name.upper() and any(word in name for word in ("TOKEN", "SECRET", "KEY", "PASSWORD", "AUTH")):
        return True
    for segment in re.split(r"[_\-]", re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()):
        core = re.sub(r"\d+$", "", segment)
        if core in _SECRET_WORDS or core.endswith("s") and core[:-1] in _SECRET_WORDS:
            return True
        if not _NOT_SECRET.fullmatch(segment) and any(word in core for word in _SECRET_SUBSTRINGS):
            return True
    return False


def _mask_named_secrets(text: str) -> str:
    """``name=value`` / ``name: value`` becomes ``name=<masked>`` when the name is secret (``_secret_name``); the value
    stops at whitespace or ``&``. A pair whose name is not secret is never consumed beyond its separator, so a secret
    pair inside its value is still found; ``?``, ``&``, ``;``, ``,`` and any other non-name character start a name."""
    pieces: list[str] = []
    position = 0
    while (found := _NAME_SEPARATOR.search(text, position)) is not None:
        pieces.append(text[position:found.start()])
        value = _VALUE.match(text, found.end()) if _secret_name(found.group(1)) else None
        pieces.append(f"{found.group(1)}=<masked>" if value else found.group(0))
        position = value.end() if value else found.end()
    pieces.append(text[position:])
    return "".join(pieces)


_MASKS = ((re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "<masked-key>"),
          (re.compile(r"(?i)(bearer|basic)\s+[A-Za-z0-9._\-=+/]{8,}"), r"\1 <masked>"),
          # secret-named pairs (``_mask_named_secrets``): never "monkey:" or "Authentication: failed"
          (_mask_named_secrets, None),
          # OpenAI identifiers shorter than 32 characters (org-..., proj_..., user-...): a digit is required so that a
          # plain word such as "user-configuration" is kept
          (re.compile(r"\b(?:org|proj|user)[-_](?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{10,}"), "<masked-id>"),
          (re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+"), "<masked-email>"),
          (re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"), "<masked-long-token>"))
_ERROR_LIMIT = 2000
LEDGER_NAME = "pat198-native-rollback-ledger.jsonl"


class RollbackTrialError(base.TrialError):
    """The trial was refused before any launch."""


def mask(text: str, home: str | None = None) -> str:
    """Secrets, e-mail addresses, long opaque tokens and the home directory removed; length bounded."""
    home = home if home is not None else str(Path.home())
    if home and home != "/":
        text = text.replace(home, "~")
    for pattern, replacement in _MASKS:
        text = pattern(text) if replacement is None else pattern.sub(replacement, text)
    return text[:_ERROR_LIMIT]


def policy_for(role: str, model: str, effort: str | None) -> dict[str, Any]:
    """The disposable fixture policy: only the role's tier, only the Claude host."""
    return {"version": 1, "mappings": {"claude": {TIER[role]: {"model": model, "effort": effort}}}}


def empty_row(line: str) -> dict[str, Any]:
    host, role = LINES[line]
    model, effort = ROLLBACK[(host, role)]
    return {"line": line, "host": host, "role": role, "tier": TIER[role], "rollback": {"model": model, "effort": effort},
            "status": "not_run", "date": None, "verdict": "not_run",
            "resolved_route": "unknown", "transmitted": "unknown",
            "native_observation": {"models": "unknown", "agent_types": "unknown", "efforts": "unknown",
                                   "host_version": "unknown"},
            "account_availability": "unknown", "errors": "unknown", "parent_reply": "unknown",
            "fixture_compliance": "unknown", "policy_guard": "unknown",
            "result_file_sha256": "unknown", "limits": "unknown"}


def empty_matrix() -> dict[str, Any]:
    return {"schema": SCHEMA, "date": MATRIX_DATE, "evidence_kind": "native_trial_matrix",
            "pat17_record": "pat-17-config-rollback-2026-10-11.json (configuration only; not native proof)",
            "budget": {"max_paid_native_calls": MAX_NATIVE_CALLS, "per_line": 1, "retries": 0},
            "native_calls": 0, "rows": [empty_row(line) for line in LINES]}


def _load_matrix(path: Path) -> dict[str, Any]:
    try:
        matrix = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RollbackTrialError(f"the matrix {path.name} is unreadable: {type(exc).__name__}") from exc
    if not isinstance(matrix, dict) or matrix.get("schema") != SCHEMA or not isinstance(matrix.get("rows"), list):
        raise RollbackTrialError("the matrix does not carry the expected schema")
    return matrix


def _write_matrix(path: Path, matrix: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(matrix, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _row(matrix: dict[str, Any], line: str) -> dict[str, Any]:
    rows = [r for r in matrix["rows"] if isinstance(r, dict) and r.get("line") == line]
    if len(rows) != 1:
        raise RollbackTrialError(f"the matrix has {len(rows)} rows for {line}, expected one")
    return rows[0]


def ledger_path() -> Path:
    """Out-of-checkout record of reservations (Foundry data directory): a git revert, stash or other worktree of the
    matrix cannot re-arm a line recorded here."""
    return Path(registry.data_dir()).expanduser() / LEDGER_NAME


def data_dir_fingerprint() -> str:
    """First 12 hex characters of the sha256 of the resolved data directory path: two runs recorded with different
    fingerprints used two ledgers (the ledger guarantee then does not hold), and the path itself is not written."""
    return hashlib.sha256(str(Path(registry.data_dir()).expanduser().resolve()).encode("utf-8")).hexdigest()[:12]


def _ledger_lines(path: Path) -> list[str]:
    try:
        entries = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise RollbackTrialError(f"the reservation ledger is unreadable: {type(exc).__name__}") from exc
    return [e.get("line") if isinstance(e, dict) else None for e in entries]


def _check_ledger(path: Path, line: str) -> None:
    reserved = _ledger_lines(path)
    if line in reserved:
        raise RollbackTrialError(f"{line} is already reserved in the ledger: one call per line, no retry")
    if len(reserved) >= MAX_NATIVE_CALLS:
        raise RollbackTrialError("the ceiling of eight native calls is reached (ledger)")


def _reserve(path: Path, line: str, date: str, session_id: str) -> None:
    """Append-only, one line per reservation; written BEFORE the call."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = json.dumps({"line": line, "date": date, "session": session_id}) + "\n"
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(descriptor, entry.encode("utf-8"))
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise RollbackTrialError(f"the reservation ledger cannot be written: {type(exc).__name__}") from exc


def _take_lock(target: Path) -> Path:
    """Exclusive lock next to ``target`` (O_EXCL): the matrix, then the ledger. A present file refuses the run, a
    stale one needs the maintainer."""
    lock = target.with_name(target.name + ".lock")
    if target.name == LEDGER_NAME:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RollbackTrialError(f"{lock.name} cannot be created: {type(exc).__name__}") from exc
    try:
        os.close(os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except FileExistsError as exc:
        raise RollbackTrialError(f"{lock.name} exists: another run holds the lock (or died); nothing is launched") from exc
    except OSError as exc:
        raise RollbackTrialError(f"{lock.name} cannot be created: {type(exc).__name__}") from exc
    return lock


def policy_digest(path: Path) -> str:
    """sha256 of a policy file, or ``absent``: lets a run prove the real policy was not touched (read-only)."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unreadable"


def default_guards() -> list[Path]:
    """Foundry reads only ``<project root>/.foundry/model-routing.json`` (``routing.py``); this is the one of the
    checkout holding this module. The main checkout's, when run from a worktree, is guarded only if passed."""
    return [base.PLUGIN_ROOT.parents[1] / ".foundry" / "model-routing.json"]


def guard_list(extra: Sequence[str] | None) -> list[Path]:
    """The default guard always, plus any ``--guard-policy``; never replaced."""
    seen: list[Path] = []
    for item in (*default_guards(), *(Path(g).expanduser() for g in extra or ())):
        if item.resolve() not in [x.resolve() for x in seen]:
            seen.append(item)
    return seen


def _text(message: Any) -> str:
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


def declares_unavailable(facts: dict[str, Any], model: str | None = None) -> bool:
    """No real model observed, and a masked host text (error, child API-error record, parent reply) matches.
    Codex (``model`` given): the text must name the requested model identifier, not only the word "model" (a local
    refusal of ``model_reasoning_effort`` is not an unavailable model), and a completed turn or a usage-limit text
    never declares it: the call is then not a non-repeatable proof of absence."""
    texts = [*facts["errors"], facts["parent_result"].get("reply") or ""]
    pattern = _UNAVAILABLE
    if model is not None:
        if facts["observed"].get("completed_turn") or any(_USAGE_LIMIT.search(t) for t in texts):
            return False
        named = re.escape(model)
        pattern = re.compile(rf"(?i)({named}.{{0,60}}(not (found|available|exist|supported)|unavailable|invalid"
                             rf"|does not exist|no access)|unknown model|(not (found|available)|unavailable|invalid|does not exist"
                             rf"|no access).{{0,60}}{named})")
    return not facts["observed"]["models"] and any(pattern.search(t) for t in texts)


def parent_prompt(logical: str, token_file: Path) -> str:
    packet = ("Goal:\nRead the single line of the file named in Inputs and return it verbatim. It is a public fixture "
              "marker generated for this compatibility test, not a secret or a credential.\n"
              f"Inputs:\n{token_file}\n"
              "Constraints:\nRead-only; one Read call; no delegation.\n"
              "Done when:\nThe fixture marker line is returned verbatim.")
    return (f"Call the Agent tool exactly once with subagent_type \"{logical}\", description \"PAT-198 trial\" and "
            "exactly the prompt below. Wait for the agent's final answer: if the agent runs in the background, do not "
            "reply until it has completed. Then reply with the agent's answer verbatim, including the public fixture "
            "marker it read (a test value, not a secret), and stop. Use no other tool. If the call is refused or "
            "fails, reply with the refusal text verbatim and stop; never retry.\n\n" + packet)


def claude_command(claude: str, prompt: str, session_id: str, parent_model: str, budget: float | None) -> list[str]:
    command = base.argv(claude, prompt, session_id, parent_model)
    return [*command, "--max-budget-usd", f"{budget:g}" if budget is not None else "<required at run>"]


CODEX_ENV_EXTRA = ("CODEX_HOME",)  # forwarded only if set; auth stays in the real Codex home, never read here
_THREAD_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z\-]{7,}")
_USAGE_LIMIT = re.compile(r"(?i)(usage limit|rate limit|quota|try again (at|in|later))")


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()


def codex_prompt() -> str:
    return ("Read the single line of the file fixture/marker.txt in the working directory (one shell command such as "
            "cat is enough) and reply with that line verbatim. It is a public fixture marker generated for this "
            "compatibility test, not a secret or a credential. Change nothing; take no other action.")


def codex_command(codex: str, work: Path, model: str, effort: str) -> list[str]:
    """The measurement-harness argv (``measurement_harness_v2.codex_command_v2``) without ``--ephemeral`` (the session
    file is the only source of the model and effort, section 10) plus ``--skip-git-repo-check -C <work>``."""
    return [codex, "exec", "--ignore-user-config", "--ignore-rules", "--sandbox", "read-only", "--json",
            "--skip-git-repo-check", "-C", str(work), "--model", model, "-c", f'model_reasoning_effort="{effort}"', "-"]


def codex_environment() -> dict[str, str]:
    """The R6 allow-list of the Claude child (with its isolation marker) plus ``CODEX_HOME`` if set; no API key or other variable is forwarded."""
    environment = dict(_claude_child_environment())
    for name in CODEX_ENV_EXTRA:
        if os.environ.get(name):
            environment[name] = os.environ[name]
    return environment


def _launch_codex(command: Sequence[str], *, cwd: Path, env: dict[str, str], stdout: Path, stderr: Path,
                  stdin: Path) -> dict[str, Any]:
    """One process group, packet on standard input, killed as a whole at the bound (``base.TIMEOUT_SECONDS``)."""
    started = time.monotonic()
    with stdin.open("rb") as packet, stdout.open("wb") as out, stderr.open("wb") as err:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=packet, stdout=out, stderr=err, start_new_session=True)
        try:
            code, timed_out = process.wait(timeout=base.TIMEOUT_SECONDS), False
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            os.killpg(process.pid, signal.SIGKILL)
            code, timed_out = process.wait(), True
    return {"exit_code": code, "timed_out": timed_out, "seconds": round(time.monotonic() - started, 3)}


def observe_codex(stream: Path, sessions_dir: Path, token: str, model: str, effort: str) -> dict[str, Any]:
    """What happened; every absent observation is empty or ``unknown``. A model and an effort count as observed only
    from the session file of a thread whose turn COMPLETED (``turn.completed``): a failed turn may record the requested
    values without any model having run."""
    events = base._records(stream)
    thread = next((e.get("thread_id") for e in events if e.get("type") == "thread.started"), None)
    thread = thread if isinstance(thread, str) and _THREAD_ID.fullmatch(thread) else None
    completed = next((e for e in events if e.get("type") == "turn.completed"), None)
    errors: list[str] = []
    for event in events:
        if event.get("type") == "turn.failed":
            event = event.get("error") if isinstance(event.get("error"), dict) else {}
        elif event.get("type") != "error":
            continue
        if isinstance(event.get("message"), str):
            errors.append(mask(event["message"]))
    replies = [e["item"]["text"] for e in events if e.get("type") == "item.completed" and isinstance(e.get("item"), dict)
               and e["item"].get("type") == "agent_message" and isinstance(e["item"].get("text"), str)]
    usage = completed.get("usage") if isinstance(completed, dict) and isinstance(completed.get("usage"), dict) else {}
    usage = {k: v for k, v in usage.items() if isinstance(k, str) and type(v) is int}
    sessions = sorted(sessions_dir.glob(f"**/rollout-*-{thread}.jsonl")) if thread else []
    models: set[str] = set()
    efforts: set[str] = set()
    versions: set[str] = set()
    for record in base._records(sessions[0]) if len(sessions) == 1 else []:
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if record.get("type") == "turn_context":
            if isinstance(payload.get("model"), str):
                models.add(payload["model"])
            if isinstance(payload.get("effort"), str):  # a null is an absence
                efforts.add(payload["effort"])
        elif record.get("type") == "session_meta" and isinstance(payload.get("cli_version"), str):
            versions.add(payload["cli_version"])
    unconfirmed = sorted(models) if completed is None else []
    if completed is None:
        models, efforts = set(), set()
    reply = replies[-1] if replies else ""
    conformity = {
        "model": ("unknown" if not models else "exact" if models == {model}
                  else "dated_snapshot" if all(re.fullmatch(re.escape(model) + r"-\d{8}", m) for m in models) else "divergent"),
        "effort": "unknown" if not efforts else "exact" if efforts == {effort} else "divergent",
        "host_version": "unknown" if not versions else "observed",
    }
    facts = {
        "host": {"codex_cli_version": sorted(versions)[0] if len(versions) == 1 else None},
        "observed": {"children": 0, "models": sorted(models), "efforts": sorted(efforts), "completed_turn": completed is not None,
                     "unconfirmed_models": unconfirmed, "usage": usage, "session_files": len(sessions),
                     "log_sha256": {"stream": base._sha256(stream) if stream.exists() else None,
                                    "session": base._sha256(sessions[0]) if len(sessions) == 1 else None}},
        "errors": errors,
        "account_availability": "available_observed" if conformity["model"] in ("exact", "dated_snapshot") else "unknown",
        "conformity": conformity,
        "fixture_compliance": "unknown" if not reply else "exact" if token in reply else "divergent",
        "parent_result": {"is_error": completed is None, "subtype": "turn.completed" if completed else "no_completed_turn",
                          "reply": mask(reply)},
    }
    if declares_unavailable(facts, model):
        facts["account_availability"] = "unavailable_declared"
    return facts


def observe_line(stream: Path, projects_dir: Path, session_id: str, token: str, profile: str, wire: str,
                 effort: str | None) -> dict[str, Any]:
    """What happened; every absent observation is ``None`` or ``unknown``. Reuses the PAT-125 readers."""
    events = base._records(stream)
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    final = next((e for e in reversed(events) if e.get("type") == "result"), {})
    parents = sorted(projects_dir.glob(f"*/{session_id}.jsonl"))
    children = sorted(projects_dir.glob(f"*/{session_id}/subagents/agent-*.jsonl"))
    hook = next((f for record in (base._records(parents[0]) if len(parents) == 1 else [])
                 if (f := base._hook_context(record)) is not None), None)
    types, models, versions = [], set(), set()
    efforts: set[Any] = set()
    errors: list[str] = []
    for child in children:
        try:
            types.append(json.loads(child.with_suffix(".meta.json").read_text(encoding="utf-8")).get("agentType"))
        except (OSError, ValueError, AttributeError):
            types.append(None)
        for record in base._records(child):
            if isinstance(record.get("version"), str):
                versions.add(record["version"])
            for key in ("effort", "perTurnEffort"):
                if record.get(key) is not None:  # a logged null is an absence, not a value (haiku 4.5)
                    efforts.add(record[key])
            message = record.get("message")
            if record.get("type") == "assistant" and isinstance(message, dict):
                if record.get("isApiErrorMessage") or message.get("model") == "<synthetic>":
                    # a host-written API-error record (deduced from Claude Code, not observed here): not a model
                    errors.append(mask(_text(message)) or "<API-error record without text>")
                elif isinstance(message.get("model"), str):
                    models.add(message["model"])
    host_versions = versions or ({init["claude_code_version"]} if isinstance(init.get("claude_code_version"), str) else set())
    expected_efforts = set() if effort is None else {effort}
    if final.get("is_error") and isinstance(final.get("result"), str):
        errors.append(mask(final["result"]))
    result_text = final.get("result") if isinstance(final.get("result"), str) else ""
    conformity = {
        "profile": ("unknown" if not children or None in types
                    else "exact" if types == [f"foundry:{profile}"] else "divergent"),
        "model": ("unknown" if not models else "exact" if models == {wire}
                  else "dated_snapshot" if all(re.fullmatch(re.escape(wire) + r"-\d{8}", m) for m in models) else "divergent"),
        # effort None (haiku 4.5): native metadata may still carry the parent's effort, so absence is the only
        # non-divergent state and it never proves non-applicability.
        "effort": ("unknown" if not efforts else "exact" if efforts == expected_efforts else "divergent"),
        "host_version": "unknown" if not host_versions else "observed",
    }
    fixture = "unknown" if not result_text else "exact" if token in result_text else "divergent"
    facts = {
        "host": {"claude_code_version": init.get("claude_code_version")},
        "hook": None if hook is None else {k: hook.get(k) for k in
                                           ("model", "effort", "effort_parameters", "transmitted_launch", "host_version",
                                            "sources", "warnings")},
        "observed": {"children": len(children), "agent_types": types, "models": sorted(models),
                     "efforts": sorted(efforts, key=str), "child_host_versions": sorted(versions),
                     "log_sha256": {"parent": base._sha256(parents[0]) if len(parents) == 1 else None,
                                    "children": [base._sha256(c) for c in children]}},
        "errors": errors,
        "account_availability": "available_observed" if conformity["model"] in ("exact", "dated_snapshot") else "unknown",
        "conformity": conformity,
        # reported apart: a role contract may refuse the trivial task; that never decides the model verdict
        "fixture_compliance": fixture,
        "parent_result": {"is_error": final.get("is_error"), "subtype": final.get("subtype"), "reply": mask(result_text)},
    }
    if declares_unavailable(facts):
        facts["account_availability"] = "unavailable_declared"
    return facts


def verdict(process: dict[str, Any], facts: dict[str, Any]) -> tuple[str, list[str]]:
    c = facts["conformity"]
    failed = [n for n, s in c.items() if s == "divergent"]
    if process["timed_out"] or process["exit_code"] != 0:
        failed.append("process")
    if facts["observed"]["children"] > 1:
        failed.append("children")
    if facts["account_availability"] == "unavailable_declared" and set(failed) <= {"process"}:
        return "unavailable", failed + [n for n, s in c.items() if s in ("unknown", "dated_snapshot")]
    open_points = [n for n, s in c.items() if s in ("unknown", "dated_snapshot")]
    if failed:
        return "not_conforming", failed + open_points
    return ("unknown" if open_points else "conforming"), open_points


def _backup_dir(work: Path) -> Path:
    """Where a present Codex ``config.toml`` is copied before the call: a sibling of the work directory."""
    return work.with_name(work.name + ".backup")


def run(args: Any, *, launch: Callable[..., dict[str, Any]] = base._launch, today: dt.date | None = None,
        codex_launch: Callable[..., dict[str, Any]] = _launch_codex) -> int:
    if args.line not in LINES:
        raise RollbackTrialError(f"unknown line {args.line}; expected one of {', '.join(LINES)}")
    host, role = LINES[args.line]
    model, effort = ROLLBACK[(host, role)]
    work = Path(args.work_dir).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    matrix_path = Path(args.matrix).expanduser().resolve()
    if work.exists() or out.exists():
        raise RollbackTrialError("the work directory and the result file must not exist: the trial is never replayed")
    if base._inside_repository(work):
        raise RollbackTrialError("the work directory must be outside any git repository: the fixture carries its own policy")
    if host == "codex" and (_backup_dir(work).exists() or base._inside_repository(_backup_dir(work))):
        raise RollbackTrialError("the backup directory <work dir>.backup must not exist and must be outside any git repository")
    # taken before the lock file exists, so the lock never makes the checkout look dirty
    source = base._head()
    locks: list[Path] = []
    try:
        if not args.dry_run:  # matrix lock, then the lock of the ledger shared by every matrix copy
            locks.append(_take_lock(matrix_path))
            locks.append(_take_lock(ledger_path()))
        if host == "codex":
            return _run_codex(args, role, model, effort, work, out, matrix_path, codex_launch, today, source)
        return _run_claude(args, role, model, effort, work, out, matrix_path, launch, today, source)
    finally:
        for lock in locks:
            lock.unlink(missing_ok=True)


def _run_claude(args: Any, role: str, model: str, effort: str | None, work: Path, out: Path, matrix_path: Path,
                launch: Callable[..., dict[str, Any]], today: dt.date | None, source: dict[str, Any]) -> int:
    matrix = _load_matrix(matrix_path)
    row = _row(matrix, args.line)
    if row.get("status") != "not_run":
        raise RollbackTrialError(f"{args.line} is already recorded ({row.get('status')}): one call per line, no retry")
    if matrix.get("native_calls", 0) >= MAX_NATIVE_CALLS:
        raise RollbackTrialError("the ceiling of eight native calls is reached")
    _check_ledger(ledger_path(), args.line)
    if not args.dry_run and not (isinstance(args.max_budget_usd, (int, float))
                                   and math.isfinite(args.max_budget_usd) and args.max_budget_usd > 0):
        raise RollbackTrialError("--max-budget-usd is required for a real run: a finite amount above 0 (the "
                                 "maintainer-authorised amount)")
    policy = policy_for(role, model, effort)
    with base._policy_root(policy) as root:
        route = _load_claude_policy(root).resolve(role, "claude")
        binding = claude_invocation_binding(route, CAPABILITY[role], plugin_root=base.PLUGIN_ROOT,
                                            project_models=_load_claude_policy(root).claude_models)
    if (route.model, route.effort) != (model, effort):
        raise RollbackTrialError(f"this checkout does not resolve the fixture to {model} / {effort}")
    wire = binding["transmitted_model"]
    session_id = str(uuid.uuid4())
    token = f"marqueur-de-fixture-{secrets.randbelow(10**8):08d}"
    token_file = work / "fixture" / "marker.txt"
    command = claude_command(args.claude, parent_prompt(LOGICAL[role], token_file), session_id, args.parent_model,
                             args.max_budget_usd)
    environment = {**_claude_child_environment(), "FOUNDRY_DATA": str(work / "foundry-data")}
    guards = guard_list(args.guard_policy)
    before = {f"guard-{n}:{g.name}": policy_digest(g) for n, g in enumerate(guards, 1)}
    body: dict[str, Any] = {
        "schema": RESULT_SCHEMA, "line": args.line,
        "kind": "bounded native trial of one rollback line (PAT-198); not a benchmark, a comparison, a promotion or "
                "a statement about other lines",
        "date": (today or dt.date.today()).isoformat(),
        "bounds": {"native_calls": 1, "parents": 1, "children_expected": 1, "timeout_seconds": base.TIMEOUT_SECONDS,
                   "max_budget_usd": args.max_budget_usd, "concurrency": 1, "replays": 0,
                   "environment_names": sorted(environment)},
        "source": source,
        "ledger": {"path": f"<data_dir>/{LEDGER_NAME}",
                   "data_dir_from": "FOUNDRY_DATA" if os.environ.get("FOUNDRY_DATA") else "default",
                   "data_dir_fingerprint": data_dir_fingerprint()},
        "argv": [p if p != session_id else "<session id>" for p in
                 (command[0], "-p", "<parent prompt>", *command[3:-3], "<plugins/foundry of this checkout>", *command[-2:])],
        "requested": {"role": role, "tier": route.selected_tier, "model": route.model, "effort": route.effort},
        "transmitted": {"profile": binding["profile"], "model": wire, "effort": binding["effort_parameters"]["transmitted"],
                        "effort_status": binding["effort_parameters"]["status"], "source": binding["model_source"],
                        "basis": "computed offline from this checkout before launch"},
        "policy_guard": {"fixture_policy": "disposable, in the work directory",
                         "real_policy_digests_before": before},
    }
    if args.dry_run:
        print(json.dumps({**body, "dry_run": True, "command": command, "cwd": str(work),
                          "matrix": str(matrix_path.name), "would_write": [str(out), str(matrix_path.name) + " (row " + args.line + ")"]},
                         indent=2, ensure_ascii=False))
        return 0
    base._create_fixture(work, policy)
    token_file.write_text(token + "\n", encoding="utf-8")
    # Reserve the line out of the checkout, then claim the row, BEFORE the call: a killed or failed run, a git
    # revert of the matrix or another worktree still cannot launch a second one (no retry).
    _reserve(ledger_path(), args.line, body["date"], session_id)
    row.update({"status": "started", "date": body["date"]})
    matrix["native_calls"] = matrix.get("native_calls", 0) + 1
    _write_matrix(matrix_path, matrix)
    process = launch(command, cwd=work, env=environment, stdout=work / "stream.ndjson", stderr=work / "stderr.txt")
    facts = observe_line(work / "stream.ndjson", Path(args.projects_dir).expanduser(), session_id, token,
                         binding["profile"], wire, effort)
    try:
        stderr_text = (work / "stderr.txt").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        stderr_text = ""
    if stderr_text:
        facts["errors"].append(mask(stderr_text[-_ERROR_LIMIT:]))
        if declares_unavailable(facts):
            facts["account_availability"] = "unavailable_declared"
    after = {f"guard-{n}:{g.name}": policy_digest(g) for n, g in enumerate(guards, 1)}
    guard_ok = before == after
    state, open_points = verdict(process, facts)
    if (facts["conformity"]["profile"] == "divergent" or facts["observed"]["children"] > 1) and \
            facts["account_availability"] == "unavailable_declared":
        facts["account_availability"] = "unknown"  # the model tried was not necessarily the line's (also with
        # several children, whatever their readable agent types)
    if not guard_ok:
        state, open_points = "not_conforming", ["policy_guard", *open_points]
    body["policy_guard"].update({"real_policy_digests_after": after, "unchanged": guard_ok})
    body.update({"process": process, **facts, "verdict": state, "not_established": open_points})
    out.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    row.update({
        "status": "run", "verdict": state, "resolved_route": body["requested"], "transmitted": body["transmitted"],
        "native_observation": {"models": facts["observed"]["models"] or "unknown",
                               "agent_types": (facts["observed"]["agent_types"]
                                        if any(t is not None for t in facts["observed"]["agent_types"]) else "unknown"),
                               "efforts": facts["observed"]["efforts"] or "unknown",
                               "host_version": facts["host"]["claude_code_version"] or "unknown"},
        "account_availability": facts["account_availability"], "errors": facts["errors"],
        "parent_reply": facts["parent_result"]["reply"], "fixture_compliance": facts["fixture_compliance"],
        "policy_guard": "unchanged" if guard_ok else "CHANGED",
        "result_file_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "limits": ["one call; not a sample", "effort is observed only if the host logs it; absence is unknown",
                   "account availability is observed for this account and this date only"] + (
            [f"not established: {', '.join(open_points)}"] if open_points else [])})
    _write_matrix(matrix_path, matrix)
    print(f"{state}: {out}")
    return 0 if state == "conforming" else 1


def _run_codex(args: Any, role: str, model: str, effort: str, work: Path, out: Path, matrix_path: Path,
               launch: Callable[..., dict[str, Any]], today: dt.date | None, source: dict[str, Any]) -> int:
    matrix = _load_matrix(matrix_path)
    row = _row(matrix, args.line)
    if row.get("status") != "not_run":
        raise RollbackTrialError(f"{args.line} is already recorded ({row.get('status')}): one call per line, no retry")
    if matrix.get("native_calls", 0) >= MAX_NATIVE_CALLS:
        raise RollbackTrialError("the ceiling of eight native calls is reached")
    _check_ledger(ledger_path(), args.line)
    session_id = str(uuid.uuid4())  # ours, for the ledger only; Codex has its own thread id, never written out
    token = f"marqueur-de-fixture-{secrets.randbelow(10**8):08d}"
    command = codex_command(getattr(args, "codex", "codex"), work, model, effort)
    environment = codex_environment()
    guards = [*guard_list(args.guard_policy), codex_home() / "config.toml"]
    before = {f"guard-{n}:{g.name}": policy_digest(g) for n, g in enumerate(guards, 1)}
    sessions_dir = Path(getattr(args, "codex_sessions_dir", None) or codex_home() / "sessions").expanduser()
    body: dict[str, Any] = {
        "schema": RESULT_SCHEMA, "line": args.line,
        "kind": "bounded native trial of one rollback line (PAT-198, Codex amendment PAT-245); not a benchmark, a "
                "comparison, a promotion or a statement about other lines",
        "date": (today or dt.date.today()).isoformat(),
        "bounds": {"native_calls": 1, "parents": 1, "children_expected": 0, "timeout_seconds": base.TIMEOUT_SECONDS,
                   "max_budget_usd": None, "budget_note": "codex exec has no spend flag: the ceiling is the timeout, "
                   "one call, and the usage the stream reports", "concurrency": 1, "replays": 0,
                   "environment_names": sorted(environment)},
        "source": source,
        "ledger": {"path": f"<data_dir>/{LEDGER_NAME}",
                   "data_dir_from": "FOUNDRY_DATA" if os.environ.get("FOUNDRY_DATA") else "default",
                   "data_dir_fingerprint": data_dir_fingerprint()},
        "argv": [p if p != str(work) else "<work dir>" for p in command],
        "stdin": "the packet of codex_prompt(), fixed text naming fixture/marker.txt",
        "requested": {"role": role, "tier": TIER[role], "model": model, "effort": effort},
        "transmitted": {"model": model, "effort": effort, "surface": "codex exec command line",
                        "basis": "the two values above, as the command line carries them; no Foundry routing is in the path"},
        "policy_guard": {"fixture_policy": "none: --ignore-user-config and --ignore-rules, empty work directory",
                         "real_policy_digests_before": before},
    }
    if args.dry_run:
        print(json.dumps({**body, "dry_run": True, "command": command, "cwd": str(work),
                          "observation": "session file of the completed turn under the Codex sessions directory; else unknown",
                          "matrix": str(matrix_path.name),
                          "would_write": [str(out), str(matrix_path.name) + " (row " + args.line + ")",
                                          str(_backup_dir(work) / "config.toml")]},
                         indent=2, ensure_ascii=False))
        return 0
    if launch is _launch_codex and not shutil.which(getattr(args, "codex", "codex")):
        raise RollbackTrialError("the codex binary is not found or not executable: nothing is reserved or written")
    work.mkdir(parents=True)
    (work / "fixture").mkdir()
    (work / "fixture" / "marker.txt").write_text(token + "\n", encoding="utf-8")
    (work / "packet.txt").write_text(codex_prompt() + "\n", encoding="utf-8")
    config = codex_home() / "config.toml"
    if config.is_file():  # copied, never printed: the way back if the guard reports CHANGED (protocol section 10)
        _backup_dir(work).mkdir()  # a sibling, outside the -C directory the model runs in
        shutil.copy2(config, _backup_dir(work) / "config.toml")
    _reserve(ledger_path(), args.line, body["date"], session_id)
    row.update({"status": "started", "date": body["date"]})
    matrix["native_calls"] = matrix.get("native_calls", 0) + 1
    _write_matrix(matrix_path, matrix)
    process = launch(command, cwd=work, env=environment, stdout=work / "stream.ndjson", stderr=work / "stderr.txt",
                     stdin=work / "packet.txt")
    facts = observe_codex(work / "stream.ndjson", sessions_dir, token, model, effort)
    try:
        stderr_text = (work / "stderr.txt").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        stderr_text = ""
    if stderr_text:
        facts["errors"].append(mask(stderr_text[-_ERROR_LIMIT:]))
    if declares_unavailable(facts, model):  # recomputed from every text: a stderr usage limit undoes a stream match
        facts["account_availability"] = "unavailable_declared"
    elif facts["account_availability"] == "unavailable_declared":
        facts["account_availability"] = "unknown"
    facts["usage_limit_text"] = any(_USAGE_LIMIT.search(t) for t in facts["errors"])
    after = {f"guard-{n}:{g.name}": policy_digest(g) for n, g in enumerate(guards, 1)}
    guard_ok = before == after
    state, open_points = verdict(process, facts)
    if not guard_ok:
        state, open_points = "not_conforming", ["policy_guard", *open_points]
    body["policy_guard"].update({"real_policy_digests_after": after, "unchanged": guard_ok})
    body.update({"process": process, **facts, "verdict": state, "not_established": open_points})
    out.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    row.update({
        "status": "run", "verdict": state, "resolved_route": body["requested"], "transmitted": body["transmitted"],
        "native_observation": {"models": facts["observed"]["models"] or "unknown", "agent_types": "not_applicable",
                               "efforts": facts["observed"]["efforts"] or "unknown",
                               "host_version": facts["host"]["codex_cli_version"] or "unknown"},
        "account_availability": facts["account_availability"], "errors": facts["errors"],
        "parent_reply": facts["parent_result"]["reply"], "fixture_compliance": facts["fixture_compliance"],
        "policy_guard": "unchanged" if guard_ok else "CHANGED",
        "result_file_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "limits": ["one call; not a sample", "model and effort are those the host recorded for a completed turn; "
                   "absence is unknown", "account availability is observed for this account and this date only"] + (
            ["the error text matches a usage-limit pattern: stop the sequence"] if facts["usage_limit_text"] else []) + (
            [f"not established: {', '.join(open_points)}"] if open_points else [])})
    _write_matrix(matrix_path, matrix)
    print(f"{state}: {out}")
    return 0 if state == "conforming" else 1


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m foundry.rollback_native_trial", description=__doc__.split("\n")[0])
    parser.add_argument("--line", required=True, choices=sorted(LINES))
    parser.add_argument("--work-dir", required=True, help="new directory outside any git repository")
    parser.add_argument("--out", required=True, help="result file to write; must not exist")
    parser.add_argument("--matrix", default=str(DEFAULT_MATRIX), help="dated matrix to update (a run claims its row)")
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--parent-model", default="sonnet", help="model of the parent session (not under trial)")
    parser.add_argument("--projects-dir", default="~/.claude/projects")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--codex-sessions-dir", default=None, help="default: <CODEX_HOME or ~/.codex>/sessions")
    parser.add_argument("--max-budget-usd", type=float, default=None, help="per-call ceiling; required for a real run")
    parser.add_argument("--guard-policy", action="append", help="real policy file to digest before/after (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="print everything; launch and write nothing")
    try:
        return run(parser.parse_args(arguments))
    except base.TrialError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
