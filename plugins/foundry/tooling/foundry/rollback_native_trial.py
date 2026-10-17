"""PAT-198: one bounded native trial per rollback line of PAT-17 (eight lines, one call each, no retry).

PAT-17 proved the eight rollback configurations by CLI resolution and constructed plans, with zero native calls
(``pat-17-config-rollback-2026-10-11.json``). That is configuration compatibility, NOT native proof. This module
runs, by hand and by the coordinator only, ONE native call for ONE line and records it in the dated matrix
``qualification/pat-198-native-rollback-2026-10-17.json``. The protocol (scope, surface, ceilings, stops,
restoration, observed-vs-unknown) is ``qualification/pat-198-native-rollback-protocol.md``; this module enforces
only what it can see. The tests never launch it (fake launcher, synthetic logs).

Claude lines reuse the PAT-125 surface of ``claude_profile_trial``: one ``claude -p`` parent loads this checkout
(``--plugin-dir``), delegates once to the logical role through the routing hook, from an out-of-repository fixture
whose own project policy maps the role's tier to the line's model/effort. Codex lines have a dry-run plan only: a
real Codex run is refused here (the host is not usable before the date in the protocol; no run is implemented that
has not been exercised).

Requested (policy), transmitted (profile chosen offline), observed (child transcript) and account availability stay
four separate values; whatever was not observed is ``unknown``. An unavailable model is recorded as such, with the
verbatim (masked) host error; nothing is substituted, no baseline is invented, nothing is promoted.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import sys
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
_UNAVAILABLE = re.compile(r"(?i)(model.{0,60}(not (found|available|exist|supported)|unavailable|invalid|unknown)"
                          r"|(not (found|available)|unavailable|invalid).{0,60}model"
                          r"|model.{0,60}(does not exist|no access)|(does not exist|no access).{0,60}model)")
_MASKS = ((re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "<masked-key>"),
          (re.compile(r"(?i)(bearer|basic)\s+[A-Za-z0-9._\-=+/]{8,}"), r"\1 <masked>"),
          (re.compile(r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|KEY|PASSWORD|AUTH)[A-Z0-9_]*)\s*[=:]\s*\S+"), r"\1=<masked>"),
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
        text = pattern.sub(replacement, text)
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


def declares_unavailable(facts: dict[str, Any]) -> bool:
    """No real model observed, and a masked host text (error, child API-error record, parent reply) matches."""
    texts = [*facts["errors"], facts["parent_result"].get("reply") or ""]
    return not facts["observed"]["models"] and any(_UNAVAILABLE.search(t) for t in texts)


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


def codex_plan(role: str, work: Path) -> dict[str, Any]:
    """The argv a Codex run WOULD use (the shape of the measurement harness); never launched by this module."""
    model, effort = ROLLBACK[("codex", role)]
    return {"surface": "codex exec (not launched by this module)",
            "command": ["codex", "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--sandbox",
                        "read-only", "--json", "--skip-git-repo-check", "-C", str(work), "--model", model, "-c", f'model_reasoning_effort="{effort}"', "-"],
            "requested": {"role": role, "tier": TIER[role], "model": model, "effort": effort},
            "transmitted": "the two values above, as the command line carries them; no Foundry routing is in the path",
            "observed": "unknown until a run; the JSONL events are the only source",
            "run": "refused: no Codex run is implemented (host not usable before the date in the protocol)"}


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
                    errors.append(mask(_text(message)))
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


def run(args: Any, *, launch: Callable[..., dict[str, Any]] = base._launch, today: dt.date | None = None) -> int:
    if args.line not in LINES:
        raise RollbackTrialError(f"unknown line {args.line}; expected one of {', '.join(LINES)}")
    host, role = LINES[args.line]
    model, effort = ROLLBACK[(host, role)]
    work = Path(args.work_dir).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    matrix_path = Path(args.matrix).expanduser().resolve()
    if host == "codex":
        if not args.dry_run:
            raise RollbackTrialError("a real Codex run is not implemented: only --dry-run prints the plan")
        print(json.dumps({"schema": RESULT_SCHEMA, "line": args.line, "dry_run": True, **codex_plan(role, work)},
                         indent=2, ensure_ascii=False))
        return 0
    if work.exists() or out.exists():
        raise RollbackTrialError("the work directory and the result file must not exist: the trial is never replayed")
    if base._inside_repository(work):
        raise RollbackTrialError("the work directory must be outside any git repository: the fixture carries its own policy")
    # taken before the lock file exists, so the lock never makes the checkout look dirty
    source = base._head()
    locks: list[Path] = []
    try:
        if not args.dry_run:  # matrix lock, then the lock of the ledger shared by every matrix copy
            locks.append(_take_lock(matrix_path))
            locks.append(_take_lock(ledger_path()))
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
    if not args.dry_run and not (isinstance(args.max_budget_usd, (int, float)) and args.max_budget_usd > 0):
        raise RollbackTrialError("--max-budget-usd is required for a real run (the maintainer-authorised amount)")
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
                   "data_dir_from": "FOUNDRY_DATA" if os.environ.get("FOUNDRY_DATA") else "default"},
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
    if facts["conformity"]["profile"] == "divergent" and facts["account_availability"] == "unavailable_declared":
        facts["account_availability"] = "unknown"  # the model tried was not necessarily the line's
    if not guard_ok:
        state, open_points = "not_conforming", ["policy_guard", *open_points]
    body["policy_guard"].update({"real_policy_digests_after": after, "unchanged": guard_ok})
    body.update({"process": process, **facts, "verdict": state, "not_established": open_points})
    out.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    row.update({
        "status": "run", "verdict": state, "resolved_route": body["requested"], "transmitted": body["transmitted"],
        "native_observation": {"models": facts["observed"]["models"] or "unknown",
                               "agent_types": facts["observed"]["agent_types"] or "unknown",
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


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m foundry.rollback_native_trial", description=__doc__.split("\n")[0])
    parser.add_argument("--line", required=True, choices=sorted(LINES))
    parser.add_argument("--work-dir", required=True, help="new directory outside any git repository")
    parser.add_argument("--out", required=True, help="result file to write; must not exist")
    parser.add_argument("--matrix", default=str(DEFAULT_MATRIX), help="dated matrix to update (a run claims its row)")
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--parent-model", default="sonnet", help="model of the parent session (not under trial)")
    parser.add_argument("--projects-dir", default="~/.claude/projects")
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
