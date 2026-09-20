import copy
import json
import importlib.util
import io
import multiprocessing
import os
import stat
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from foundry.routing import RoutingPolicy
from foundry.effort_policy import EffortScope
import foundry.telemetry as telemetry
from foundry.routing_facades import (
    claude_invocation_completed,
    codex_invocation_completed,
    codex_spawn_plan,
    observe_invocation_completion,
)
from foundry.telemetry import (
    SCHEMA_VERSION,
    TelemetryObserver,
    TelemetryRun,
    aggregate_outcome,
    export_aggregates,
    new_run_id,
    unknown_metric,
    validate_event,
)


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_hook(name):
    path = PLUGIN_ROOT / "hooks" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"telemetry_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


route_agent = _load_hook("route_agent")
observe_agent = _load_hook("observe_agent")


def _packet(body="Inspect the requested scope."):
    return (
        f"Goal:\n{body}\nInputs:\nIssue and paths.\n"
        "Constraints:\nPreserve unrelated work.\nDone when:\nReturn validation."
    )


def _without_execution_instance(plan):
    """Compare a plan's stable routing contract without its fresh host instance."""
    normalized = copy.deepcopy(plan)
    execution_id = normalized.pop("execution_id")
    task_name = normalized["spawn"].pop("task_name")
    assert task_name.endswith(f"_{execution_id}")
    normalized["spawn"]["task_name"] = task_name[:-(len(execution_id) + 1)] + "_<execution>"
    return normalized


def _invocation():
    unknown = unknown_metric()
    return {
        "schema_version": SCHEMA_VERSION,
        "event": "invocation_completed",
        "host": "codex",
        "kind": "delegated",
        "role": "implementer",
        "tier": "balanced",
        "model": "gpt-5.6-terra",
        "effort": "medium",
        "scope": "bounded_packet",
        "context_policy": "fresh",
        "resolution_source": "default",
        "signals": {"availability_probed": False,
                    "fallback_steps": {"value": 0, "provenance": "observed"},
                    "host_override_count": {"value": 0, "provenance": "observed"},
                    "escalation_floor_active": False},
        "usage": {"input_tokens": unknown, "output_tokens": unknown, "total_tokens": unknown},
        "duration_ms": unknown,
        "packet_bytes": {"value": 42, "provenance": "observed"},
        "file_count": unknown,
        "estimated_cost": None,
        "provider_reported_cost": None,
        "status": "completed",
        "failure_class": "none",
    }


def _outcome():
    return {
        "tests": {"state": "passed", "passed": {"value": 2, "provenance": "observed"},
                  "failed": {"value": 0, "provenance": "observed"},
                  "skipped": {"value": 1, "provenance": "observed"}},
        "correction_cycles": {"value": 0, "provenance": "observed"}, "review_state": "passed",
        "severities": {"info": {"value": 0, "provenance": "observed"}, "warning": {"value": 1, "provenance": "observed"}, "error": {"value": 0, "provenance": "observed"}, "blocking": {"value": 0, "provenance": "observed"}},
        "violations": {"info": {"value": 0, "provenance": "observed"}, "warning": {"value": 0, "provenance": "observed"}, "error": {"value": 0, "provenance": "observed"}, "blocking": {"value": 0, "provenance": "observed"}},
    }


def _raw(event):
    return {"schema_version": SCHEMA_VERSION,
            "event": "invocation_completed", "run_id": new_run_id(), **event}


def _pending_event(host="codex"):
    event = _invocation()
    event.pop("schema_version")
    event.pop("event")
    event["status"] = "unknown"
    event["failure_class"] = "unknown"
    if host == "claude":
        event.update({"host": "claude", "model": "sonnet-5"})
    return event


def _process_prepare(data_dir, kind, worker, count, maximum=256):
    telemetry.MAX_PENDING_CAPABILITIES = maximum
    observer = TelemetryObserver(data_dir)
    observed = 0
    for offset in range(count):
        sequence = worker * count + offset
        if kind == "pending":
            observed += observer.prepare_invocation(**_pending_event()) is not None
        elif kind == "links":
            observed += observer.prepare_correlated_invocation(
                f"callback-{sequence}", **_pending_event("claude"),
            )
        elif kind == "outcomes":
            observed += observer.invocation_completed(**_invocation()) is not None
        else:
            raise AssertionError(kind)
    return observed


def _process_prepare_same_link(data_dir, worker):
    observer = TelemetryObserver(data_dir)
    return observer.prepare_correlated_invocation(
        "same-callback", **_pending_event("claude"),
    )


def _process_consume(data_dir, kind, bearer, now_ns=None):
    if now_ns is not None:
        telemetry._now_ns = lambda: now_ns
    observer = TelemetryObserver(data_dir)
    if kind == "pending":
        return observer.complete_invocation(
            bearer, status="completed", failure_class="none", expected_host="codex",
        ) is not None
    if kind == "links":
        return observer.complete_correlated_invocation(
            bearer, status="completed", failure_class="none",
        )
    if kind == "outcomes":
        return observer.run_outcome_capability(bearer, **_outcome())
    raise AssertionError(kind)


def _process_special_entry_call(data_dir, operation, output):
    observer = TelemetryObserver(data_dir)
    if operation == "journal":
        result = observer.invocation_completed(**_invocation()) is not None
    elif operation == "correlation_key":
        result = observer.prepare_correlated_invocation(
            "fifo-callback",
            **_pending_event("claude"),
        )
    else:
        raise AssertionError(operation)
    output.put(result)


def _process_append_after_rotation(data_dir, event, opened, rotated, output):
    original_flock = telemetry.fcntl.flock
    waiting = False

    def wait_before_first_journal_lock(fd, operation):
        nonlocal waiting
        if operation == telemetry.fcntl.LOCK_EX and not waiting:
            waiting = True
            opened.set()
            if not rotated.wait(5):
                raise OSError("rotation interleaving timed out")
        return original_flock(fd, operation)

    telemetry.fcntl.flock = wait_before_first_journal_lock
    output.put(TelemetryObserver(data_dir)._emit(event))


def _call_special_entry_without_blocking(data_dir, operation):
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    process = context.Process(
        target=_process_special_entry_call,
        args=(str(data_dir), operation, output),
    )
    process.start()
    process.join(5)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail(f"telemetry blocked while opening {operation}")
    assert process.exitcode == 0
    return output.get(timeout=2)


def _process_abort_key_creation(data_dir, stage):
    if stage == "before_publish":
        write_all = TelemetryObserver._write_all

        def write_then_abort(fd, payload):
            write_all(fd, payload)
            os.fsync(fd)
            os._exit(91)

        TelemetryObserver._write_all = staticmethod(write_then_abort)
        unexpected_exit = 93
    elif stage == "after_publish":
        link = telemetry.os.link

        def link_then_abort(*args, **kwargs):
            link(*args, **kwargs)
            os._exit(92)

        telemetry.os.link = link_then_abort
        unexpected_exit = 94
    else:
        os._exit(95)
    TelemetryObserver(data_dir).prepare_correlated_invocation(
        "interrupted-callback", **_pending_event("claude"),
    )
    os._exit(unexpected_exit)


def test_schema_is_append_only_linked_and_uses_null_for_unknown_metrics(tmp_path):
    observer = TelemetryObserver(tmp_path)
    run = observer.invocation_completed(**_invocation())
    assert run is not None
    assert observer.run_outcome(run, **_outcome())
    journal = tmp_path / "telemetry" / "journal.ndjson"
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    assert [row["event"] for row in rows] == ["invocation_completed", "run_outcome"]
    assert len({row["run_id"] for row in rows}) == 1
    assert rows[0]["usage"]["input_tokens"] == {"value": None, "provenance": "unknown"}
    assert journal.stat().st_mode & 0o077 == 0


def test_observer_is_disabled_without_data_and_never_changes_routing(tmp_path):
    before = RoutingPolicy.load(tmp_path).resolve("implementer", "codex").to_dict()
    assert TelemetryObserver.from_environ({}).invocation_completed(**_invocation()) is None
    after = RoutingPolicy.load(tmp_path).resolve("implementer", "codex").to_dict()
    assert after == before


def test_whitelist_rejects_unknown_model_and_sensitive_free_fields(tmp_path):
    sentinel = "FOUNDRY_TELEMETRY_SECRET_SENTINEL"
    event = _invocation()
    event["model"] = sentinel
    with pytest.raises(
        telemetry.TelemetryValidationError,
        match="model is not declared in the telemetry vocabulary",
    ):
        validate_event(_raw(event))
    assert TelemetryObserver(tmp_path).invocation_completed(**event) is None
    event["prompt"] = sentinel
    assert TelemetryObserver(tmp_path).invocation_completed(**event) is None
    assert not (tmp_path / "telemetry" / "journal.ndjson").exists()


def test_project_model_and_effort_survive_write_and_export(
    tmp_path, monkeypatch, capsys,
):
    config = tmp_path / ".foundry" / "model-routing.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "mappings": {"codex": {"balanced": {
            "model": "project-model-v1", "effort": "deep",
        }}},
        "effort_scopes": {"codex": {"project-model": {
            "version": 3,
            "levels": ["careful", "deep"],
            "inadmissible": {},
        }}},
    }), encoding="utf-8")
    policy = RoutingPolicy.load(tmp_path)
    data_dir = tmp_path / "state"
    data_dir.mkdir()
    event = _invocation()
    event.update({"model": "project-model-v1", "effort": "deep"})
    observer = TelemetryObserver(
        data_dir,
        effort_scopes=policy.effort_scopes,
        project_models=policy.project_models,
    )

    assert observer.invocation_completed(**event) is not None
    journal_event = json.loads(
        (data_dir / "telemetry" / "journal.ndjson").read_text().splitlines()[0]
    )
    assert (journal_event["model"], journal_event["effort"]) == (
        "project-model-v1", "deep",
    )
    exported = export_aggregates(
        data_dir,
        effort_scopes=policy.effort_scopes,
        project_models=policy.project_models,
    )
    assert (exported["invocations"][0]["model"], exported["invocations"][0]["effort"]) == (
        "project-model-v1", "deep",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FOUNDRY_DATA", str(data_dir))
    telemetry.main(["export"])
    cli_export = json.loads(capsys.readouterr().out)
    assert cli_export["invocations"] == exported["invocations"]


def test_signed_project_model_survives_retirement_from_current_configuration(tmp_path):
    model = "project-model-v1"
    event = _invocation()
    event.update({"model": model})
    observer = TelemetryObserver(tmp_path, project_models=(model,))
    assert observer.invocation_completed(**event) is not None

    exported = export_aggregates(tmp_path, project_models=())
    assert [(row["model"], row["count"]) for row in exported["invocations"]] == [(model, 1)]


def test_telemetry_rejects_unsafe_project_model_and_effort_identifiers(tmp_path):
    event = _invocation()
    event.update({"model": "alice.smith@example.test"})
    assert TelemetryObserver(
        tmp_path, project_models=(event["model"],),
    ).invocation_completed(**event) is None

    unsafe_scopes = {"codex": {"default": EffortScope(
        "codex", "default", 1, ("low", "alice.smith@example.test"), {},
    )}}
    event = _invocation()
    event.update({"effort": "alice.smith@example.test"})
    assert TelemetryObserver(tmp_path, effort_scopes=unsafe_scopes).invocation_completed(**event) is None


def test_export_separates_reused_policy_versions_with_different_level_order(tmp_path):
    model = "project-model-v1"
    event = _invocation()
    event.update({"model": model, "effort": "medium"})
    first = {"codex": {"default": EffortScope(
        "codex", "default", 1, ("low", "medium", "high"), {},
    )}}
    reordered = {"codex": {"default": EffortScope(
        "codex", "default", 1, ("medium", "low", "high"), {},
    )}}
    assert TelemetryObserver(
        tmp_path, effort_scopes=first, project_models=(model,),
    ).invocation_completed(**event) is not None
    assert TelemetryObserver(
        tmp_path, effort_scopes=reordered, project_models=(model,),
    ).invocation_completed(**event) is not None

    rows = export_aggregates(tmp_path, project_models=()) ["invocations"]
    assert [(row["policy_version"], row["policy_levels"], row["count"]) for row in rows] == [
        (1, ["low", "medium", "high"], 1),
        (1, ["medium", "low", "high"], 1),
    ]


def test_export_keeps_policy_versions_separate_after_a_project_policy_changes(tmp_path):
    def scopes(version):
        return {"codex": {
            "default": EffortScope(
                "codex", "default", version, ("careful", "deep"), {},
            ),
        }}

    event = _invocation()
    event.update({"model": "project-model-v1", "effort": "deep"})
    observer_v1 = TelemetryObserver(
        tmp_path, effort_scopes=scopes(1), project_models=("project-model-v1",),
    )
    observer_v2 = TelemetryObserver(
        tmp_path, effort_scopes=scopes(2), project_models=("project-model-v1",),
    )
    assert observer_v1.invocation_completed(**event) is not None
    assert observer_v2.invocation_completed(**event) is not None

    exported = export_aggregates(
        tmp_path,
        project_models=("project-model-v1",),
        effort_scopes={"codex": {"default": EffortScope(
            "codex", "default", 1, ("low", "medium", "high", "xhigh", "max"), {},
        )}},
    )
    assert [
        (row["model_family"], row["policy_version"], row["count"])
        for row in exported["invocations"]
    ] == [("default", 1, 1), ("default", 2, 1)]


def test_tampered_pending_completion_is_revalidated_before_journal_write(tmp_path):
    observer = TelemetryObserver(tmp_path)
    bearer = observer.prepare_invocation(**_pending_event())
    assert bearer is not None
    pending_path = tmp_path / "telemetry" / "pending" / bearer
    pending = json.loads(pending_path.read_text())
    assert isinstance(pending.get("_record_digest"), str)
    # ``max`` is otherwise a valid default Codex effort.  The rejection proves
    # that the sealed pending record, rather than only schema validation, binds it.
    pending["event"]["effort"] = "max"
    pending_path.write_text(json.dumps(pending), encoding="utf-8")

    assert observer.complete_invocation(
        bearer, status="completed", failure_class="none", expected_host="codex",
    ) is None
    assert not (tmp_path / "telemetry" / "journal.ndjson").exists()


def test_failed_pending_emission_restores_the_sealed_capability(tmp_path, monkeypatch):
    observer = TelemetryObserver(tmp_path)
    bearer = observer.prepare_invocation(**_pending_event())
    assert bearer is not None
    pending_path = tmp_path / "telemetry" / "pending" / bearer
    original_append = TelemetryObserver._append

    def unavailable_journal(*_args, **_kwargs):
        raise OSError("journal unavailable")

    monkeypatch.setattr(TelemetryObserver, "_append", unavailable_journal)
    assert observer.complete_invocation(
        bearer, status="completed", failure_class="none", expected_host="codex",
    ) is None
    assert pending_path.exists()

    monkeypatch.setattr(TelemetryObserver, "_append", original_append)
    assert observer.complete_invocation(
        bearer, status="completed", failure_class="none", expected_host="codex",
    ) is not None


@pytest.mark.parametrize("rotate_between_attempts", [False, True])
def test_retry_after_post_write_fsync_failure_does_not_duplicate_the_event(
    tmp_path, monkeypatch, rotate_between_attempts,
):
    observer = TelemetryObserver(tmp_path)
    bearer = observer.prepare_invocation(**_pending_event())
    assert bearer is not None
    monkeypatch.setattr(telemetry, "MAX_JOURNAL_BYTES", 1)
    original_fsync = telemetry.os.fsync
    failed_once = False

    def fail_first_sync(_fd):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise OSError("journal sync unavailable")
        return original_fsync(_fd)

    monkeypatch.setattr(telemetry.os, "fsync", fail_first_sync)
    assert observer.complete_invocation(
        bearer, status="completed", failure_class="none", expected_host="codex",
    ) is None
    pending_path = tmp_path / "telemetry" / "pending" / bearer
    assert pending_path.exists()
    journal = tmp_path / "telemetry" / "journal.ndjson"
    assert len([line for line in journal.read_text().splitlines() if line]) == 1

    monkeypatch.setattr(telemetry.os, "fsync", original_fsync)
    if rotate_between_attempts:
        assert observer.invocation_completed(**_invocation()) is not None
        assert list(journal.parent.glob("journal.[0-9]*.ndjson"))
    assert observer.complete_invocation(
        bearer, status="completed", failure_class="none", expected_host="codex",
    ) is not None
    records = [
        json.loads(line)
        for path in journal.parent.glob("journal*.ndjson")
        for line in path.read_text().splitlines() if line
    ]
    assert len(records) == (2 if rotate_between_attempts else 1)
    assert len({record["run_id"] for record in records}) == len(records)


def test_failed_correlated_emission_restores_the_sealed_capabilities(tmp_path, monkeypatch):
    observer = TelemetryObserver(tmp_path)
    correlation = "sealed-callback"
    assert observer.prepare_correlated_invocation(correlation, **_pending_event("claude"))
    original_append = TelemetryObserver._append

    def unavailable_journal(*_args, **_kwargs):
        raise OSError("journal unavailable")

    monkeypatch.setattr(TelemetryObserver, "_append", unavailable_journal)
    assert not observer.complete_correlated_invocation(
        correlation, status="completed", failure_class="none",
    )
    assert list((tmp_path / "telemetry" / "pending").iterdir())
    assert list((tmp_path / "telemetry" / "links").iterdir())

    monkeypatch.setattr(TelemetryObserver, "_append", original_append)
    assert observer.complete_correlated_invocation(
        correlation, status="completed", failure_class="none",
    )


@pytest.mark.parametrize("model", [None, "", " project-model-v1"])
def test_malformed_models_fail_explicitly(model):
    event = _invocation()
    event["model"] = model
    with pytest.raises(
        telemetry.TelemetryValidationError,
        match="model must be a non-empty canonical identifier",
    ):
        validate_event(_raw(event))


def test_export_contains_aggregates_only_and_never_raw_identifiers_or_metrics(tmp_path):
    observer = TelemetryObserver(tmp_path)
    run = observer.invocation_completed(**_invocation())
    assert run is not None
    assert observer.run_outcome(run, **_outcome())
    exported = export_aggregates(tmp_path)
    rendered = json.dumps(exported)
    assert "run_id" not in rendered
    assert "packet_bytes" not in rendered
    assert exported["invocations"][0]["count"] == 1
    assert exported["outcomes"] == {"passed": 1}


def test_unknown_provenance_cannot_be_masqueraded_as_zero():
    event = _raw(_invocation())
    event["duration_ms"] = {"value": 0, "provenance": "unknown"}
    try:
        validate_event(event)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown metric with zero must be rejected")


def test_estimated_and_provider_reported_costs_are_separate_contracts():
    event = _invocation()
    event["estimated_cost"] = {
        "amount_micros": 17, "currency": "USD", "price_table_id": "foundry-public-v1",
        "price_table_version": "v1", "source": "estimated", "completeness": "complete",
    }
    event["provider_reported_cost"] = {
        "amount_micros": 19, "currency": "USD", "source": "provider_reported",
        "completeness": "complete",
    }
    clean = validate_event(_raw(event))
    assert clean["estimated_cost"]["amount_micros"] == 17
    assert clean["provider_reported_cost"]["amount_micros"] == 19


@pytest.mark.parametrize("status,failure_class", sorted(telemetry.COMPLETION_CLASSIFICATIONS))
def test_completion_status_and_failure_class_share_one_controlled_cross_host_vocabulary(
    status, failure_class,
):
    event = _invocation()
    event["status"] = status
    event["failure_class"] = failure_class
    clean = validate_event(_raw(event))
    assert (clean["status"], clean["failure_class"]) == (status, failure_class)


def test_incoherent_completion_classification_is_rejected():
    event = _invocation()
    event.update({"status": "completed", "failure_class": "host"})
    with pytest.raises(ValueError, match="combination"):
        validate_event(_raw(event))


def test_enabled_shared_completion_adapters_preserve_real_claude_and_codex_routes(tmp_path):
    observer = TelemetryObserver(tmp_path)
    for host, completion in (("claude", claude_invocation_completed),
                             ("codex", codex_invocation_completed)):
        before = RoutingPolicy.load(tmp_path).resolve("implementer", host)
        run = completion(
            observer, before, status="completed", failure_class="none",
        )
        after = RoutingPolicy.load(tmp_path).resolve("implementer", host)
        assert run is not None
        assert after.to_dict() == before.to_dict()
    rows = [json.loads(line) for line in (tmp_path / "telemetry" / "journal.ndjson").read_text().splitlines()]
    assert [row["host"] for row in rows] == ["claude", "codex"]
    assert all(row["event"] == "invocation_completed" for row in rows)


def test_outcome_counters_require_provenance_and_public_identity_is_never_accepted(tmp_path):
    observer = TelemetryObserver(tmp_path)
    assert observer.invocation_completed(**_invocation(), run_id="sensitive-caller-id") is None
    run = observer.invocation_completed(**_invocation())
    assert run is not None
    outcome = _outcome()
    outcome["tests"]["passed"] = {"value": 0, "provenance": "unknown"}
    assert observer.run_outcome(run, **outcome) is False
    assert observer.run_outcome(object(), **_outcome()) is False


def test_observer_never_changes_shared_data_root_permissions_or_exports_sentinels(tmp_path):
    sentinel = "FOUNDRY_TELEMETRY_SECRET_SENTINEL"
    os_mode = (tmp_path.stat().st_mode & 0o777)
    observer = TelemetryObserver(tmp_path)
    event = _invocation()
    event["failure_class"] = sentinel
    assert observer.invocation_completed(**event) is None
    assert tmp_path.stat().st_mode & 0o777 == os_mode
    assert sentinel not in json.dumps(export_aggregates(tmp_path))


def test_partial_journal_write_is_completed_under_the_append_lock(tmp_path, monkeypatch):
    original_write = telemetry.os.write
    calls = 0

    def partial_write(fd, payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(fd, payload[:7])
        return original_write(fd, payload)

    monkeypatch.setattr(telemetry.os, "write", partial_write)
    assert TelemetryObserver(tmp_path).invocation_completed(**_invocation()) is not None
    rows = (tmp_path / "telemetry" / "journal.ndjson").read_text().splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["event"] == "invocation_completed"


def test_journal_append_and_rotation_keep_the_verified_dirfd_during_path_replacement(
    tmp_path, monkeypatch,
):
    data_dir = tmp_path / "state"
    data_dir.mkdir()
    telemetry_dir = data_dir / "telemetry"
    telemetry_dir.mkdir(mode=0o700)
    journal = telemetry_dir / "journal.ndjson"
    journal.write_bytes(b"x")
    journal.chmod(0o600)
    retained_dir = data_dir / "telemetry-retained"
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    original_open = telemetry.os.open
    journal_dir_fds = []
    replaced = False

    def replace_path_before_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        if os.fspath(path).endswith("journal.ndjson"):
            journal_dir_fds.append(dir_fd)
            if not replaced:
                telemetry_dir.rename(retained_dir)
                telemetry_dir.symlink_to(outside_dir, target_is_directory=True)
                replaced = True
        if dir_fd is None:
            return original_open(path, flags, mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(telemetry.os, "open", replace_path_before_open)
    monkeypatch.setattr(telemetry, "MAX_JOURNAL_BYTES", 1)

    assert TelemetryObserver(data_dir)._emit(_raw(_invocation()))
    assert replaced
    assert journal_dir_fds and all(directory_fd is not None for directory_fd in journal_dir_fds)
    assert len(set(journal_dir_fds)) == 1
    assert not list(outside_dir.iterdir())
    rows = json.loads((retained_dir / "journal.ndjson").read_text())
    assert rows["event"] == "invocation_completed"
    rotated = list(retained_dir.glob("journal.*.ndjson"))
    assert len(rotated) == 1 and rotated[0].read_bytes() == b"x"
    assert retained_dir.parent == data_dir
    assert retained_dir.stat().st_mode & 0o077 == 0
    assert (retained_dir / "journal.ndjson").stat().st_mode & 0o077 == 0
    assert (retained_dir / ".retention.lock").stat().st_mode & 0o077 == 0


def test_journal_fifo_fails_open_without_blocking(tmp_path):
    data_dir = tmp_path / "fifo-journal"
    telemetry_dir = data_dir / "telemetry"
    telemetry_dir.mkdir(parents=True)
    journal = telemetry_dir / "journal.ndjson"
    os.mkfifo(journal, mode=0o600)

    assert _call_special_entry_without_blocking(data_dir, "journal") is False
    assert stat.S_ISFIFO(os.lstat(journal).st_mode)
    assert list(telemetry_dir.iterdir()) == [journal]


def test_journal_hardlink_fails_open_without_writing_the_external_inode(tmp_path):
    data_dir = tmp_path / "hardlinked-journal"
    telemetry_dir = data_dir / "telemetry"
    telemetry_dir.mkdir(parents=True)
    journal = telemetry_dir / "journal.ndjson"
    sentinel = b"SENSITIVE_EXTERNAL_JOURNAL_SENTINEL\n"
    journal.write_bytes(sentinel)
    journal.chmod(0o600)
    outside = tmp_path / "outside-journal"
    os.link(journal, outside)

    assert TelemetryObserver(data_dir).invocation_completed(**_invocation()) is None
    assert journal.read_bytes() == sentinel
    assert outside.read_bytes() == sentinel
    assert journal.stat().st_nlink == 2


def test_journal_swap_after_open_is_rejected_before_append(tmp_path, monkeypatch):
    data_dir = tmp_path / "swapped-journal"
    telemetry_dir = data_dir / "telemetry"
    telemetry_dir.mkdir(parents=True)
    journal = telemetry_dir / "journal.ndjson"
    original_payload = b"original-private-journal\n"
    replacement_payload = b"replacement-private-journal\n"
    journal.write_bytes(original_payload)
    journal.chmod(0o600)
    outside = tmp_path / "moved-outside-journal"
    original_flock = telemetry.fcntl.flock
    swapped = False

    def swap_before_lock(fd, operation):
        nonlocal swapped
        if operation == telemetry.fcntl.LOCK_EX and not swapped:
            journal.rename(outside)
            journal.write_bytes(replacement_payload)
            journal.chmod(0o600)
            swapped = True
        return original_flock(fd, operation)

    monkeypatch.setattr(telemetry.fcntl, "flock", swap_before_lock)

    assert TelemetryObserver(data_dir)._emit(_raw(_invocation())) is False
    assert swapped
    assert outside.read_bytes() == original_payload
    assert journal.read_bytes() == replacement_payload


def test_partial_append_exception_is_isolated_before_the_next_valid_event(
    tmp_path, monkeypatch,
):
    original_write = telemetry.os.write
    calls = 0

    def partial_then_error(fd, payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(fd, payload[:9])
        if calls == 2:
            raise OSError("simulated interrupted append")
        return original_write(fd, payload)

    monkeypatch.setattr(telemetry.os, "write", partial_then_error)
    observer = TelemetryObserver(tmp_path)
    assert observer.invocation_completed(**_invocation()) is None
    assert observer.invocation_completed(**_invocation()) is not None

    lines = (tmp_path / "telemetry" / "journal.ndjson").read_text().splitlines()
    valid = []
    for line in lines:
        try:
            valid.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    assert [row["event"] for row in valid] == ["invocation_completed"]
    assert export_aggregates(tmp_path)["invocations"][0]["count"] == 1


def test_capabilities_are_factory_issued_immutable_private_and_one_time(tmp_path):
    observer = TelemetryObserver(tmp_path)
    foreign = TelemetryObserver(tmp_path / "foreign")
    (tmp_path / "foreign").mkdir()

    with pytest.raises(TypeError):
        TelemetryRun(observer, "0" * 64)
    run = observer.invocation_completed(**_invocation())
    assert run is not None
    with pytest.raises(AttributeError):
        run._capability = "0" * 64
    assert foreign.run_outcome(run, **_outcome()) is False
    assert observer.run_outcome(run, **_outcome()) is True
    assert observer.run_outcome(run, **_outcome()) is False

    before = (tmp_path / "telemetry" / "journal.ndjson").read_text()
    assert observer.run_outcome_capability("caller-chosen-sensitive-identity", **_outcome()) is False
    assert (tmp_path / "telemetry" / "journal.ndjson").read_text() == before


@pytest.mark.parametrize("kind", ("pending", "outcomes"))
def test_capability_hardlink_under_another_valid_bearer_cannot_replay(
    tmp_path, kind,
):
    observer = TelemetryObserver(tmp_path)
    if kind == "pending":
        capability = observer.prepare_invocation(**_pending_event())
        assert capability is not None
        journal_before = None
    else:
        run = observer.invocation_completed(**_invocation())
        assert run is not None
        capability = run.capability
        journal_before = (tmp_path / "telemetry" / "journal.ndjson").read_bytes()
    alternate = "f" * telemetry.CAPABILITY_HEX_LENGTH
    if capability == alternate:
        alternate = "e" * telemetry.CAPABILITY_HEX_LENGTH
    capability_dir = tmp_path / "telemetry" / kind
    os.link(capability_dir / capability, capability_dir / alternate)

    if kind == "pending":
        assert observer.complete_invocation(
            capability,
            status="completed",
            failure_class="none",
            expected_host="codex",
        ) is None
        assert observer.complete_invocation(
            alternate,
            status="completed",
            failure_class="none",
            expected_host="codex",
        ) is None
        assert not (tmp_path / "telemetry" / "journal.ndjson").exists()
    else:
        assert observer.run_outcome_capability(capability, **_outcome()) is False
        assert observer.run_outcome_capability(alternate, **_outcome()) is False
        assert (tmp_path / "telemetry" / "journal.ndjson").read_bytes() == journal_before
    assert list(capability_dir.iterdir()) == []


def test_capability_relink_during_consume_cannot_complete_before_atomic_unlink(
    tmp_path, monkeypatch,
):
    observer = TelemetryObserver(tmp_path)
    capability = observer.prepare_invocation(**_pending_event())
    assert capability is not None
    original_unlink = telemetry.os.unlink
    relinked = False

    def relink_before_unlink(path, *args, dir_fd=None, **kwargs):
        nonlocal relinked
        if str(path).startswith(".consume-") and not relinked:
            telemetry.os.link(
                path,
                capability,
                src_dir_fd=dir_fd,
                dst_dir_fd=dir_fd,
                follow_symlinks=False,
            )
            relinked = True
        return original_unlink(path, *args, dir_fd=dir_fd, **kwargs)

    monkeypatch.setattr(telemetry.os, "unlink", relink_before_unlink)

    assert observer.complete_invocation(
        capability,
        status="completed",
        failure_class="none",
        expected_host="codex",
    ) is None
    assert relinked
    assert not (tmp_path / "telemetry" / "journal.ndjson").exists()

    run = observer.complete_invocation(
        capability,
        status="completed",
        failure_class="none",
        expected_host="codex",
    )
    assert run is not None
    assert observer.complete_invocation(
        capability,
        status="completed",
        failure_class="none",
        expected_host="codex",
    ) is None
    rows = (tmp_path / "telemetry" / "journal.ndjson").read_text().splitlines()
    assert len(rows) == 1


def test_pending_state_is_private_bounded_stale_cleaned_and_identity_free(
    tmp_path, monkeypatch,
):
    observer = TelemetryObserver(tmp_path)
    event = _invocation()
    event.pop("schema_version")
    event.pop("event")
    event["status"] = "unknown"
    event["failure_class"] = "unknown"
    monkeypatch.setattr(telemetry, "MAX_PENDING_CAPABILITIES", 2)
    clock = {"now": 1_000_000_000_000_000_000}
    monkeypatch.setattr(telemetry, "_now_ns", lambda: clock["now"])
    for _ in range(5):
        assert observer.prepare_invocation(**event) is not None
        clock["now"] += 1
    pending = tmp_path / "telemetry" / "pending"
    assert len(list(pending.iterdir())) == 2
    assert pending.stat().st_mode & 0o077 == 0
    assert all(path.stat().st_mode & 0o077 == 0 for path in pending.iterdir())
    private = "".join(path.read_text() for path in pending.iterdir())
    assert all(value not in private for value in (
        "FOUNDRY-42", "thread-7", "/private/repo", "user@example.test",
    ))

    oldest = min(
        pending.iterdir(), key=lambda path: json.loads(path.read_text())["created_ns"],
    )
    clock["now"] += telemetry.PENDING_TTL_SECONDS * 1_000_000_000
    observer._cleanup_capabilities(tmp_path / "telemetry")
    assert oldest.exists() is False


def test_all_capability_classes_expire_on_cross_process_consume_at_exact_boundary(
    tmp_path, monkeypatch,
):
    created_ns = 1_000_000_000_000_000_000
    ttl_ns = telemetry.PENDING_TTL_SECONDS * 1_000_000_000
    monkeypatch.setattr(telemetry, "_now_ns", lambda: created_ns)
    cases = []
    for kind in telemetry.CAPABILITY_KINDS:
        for label, offset, expected in (
            ("before", -1, True), ("at", 0, False), ("after", 1, False),
        ):
            data_dir = tmp_path / f"{kind}-{label}"
            data_dir.mkdir()
            observer = TelemetryObserver(data_dir)
            if kind == "pending":
                bearer = observer.prepare_invocation(**_pending_event())
                target = data_dir / "telemetry" / "pending" / bearer
            elif kind == "links":
                bearer = f"callback-{label}"
                assert observer.prepare_correlated_invocation(
                    bearer, **_pending_event("claude"),
                )
                target = next((data_dir / "telemetry" / "links").iterdir())
            else:
                run = observer.invocation_completed(**_invocation())
                assert run is not None
                bearer = run.capability
                target = data_dir / "telemetry" / "outcomes" / bearer
            assert target.exists()
            cases.append((data_dir, kind, bearer, created_ns + ttl_ns + offset,
                          expected, target))

    with ProcessPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(_process_consume, str(data_dir), kind, bearer, now_ns)
            for data_dir, kind, bearer, now_ns, _expected, _target in cases
        ]
        results = [future.result(timeout=20) for future in futures]

    assert results == [case[4] for case in cases]
    assert all(not case[5].exists() for case in cases)


def test_consume_expiry_uses_persisted_created_ns_not_mutable_file_mtime(
    tmp_path, monkeypatch,
):
    created_ns = 1_000_000_000_000_000_000
    clock = {"now": created_ns}
    monkeypatch.setattr(telemetry, "_now_ns", lambda: clock["now"])
    observer = TelemetryObserver(tmp_path)
    capability = observer.prepare_invocation(**_pending_event())
    target = tmp_path / "telemetry" / "pending" / capability
    os.utime(target, ns=(1, 1))
    clock["now"] = created_ns + 1
    assert observer.complete_invocation(
        capability, status="completed", failure_class="none", expected_host="codex",
    ) is not None


@pytest.mark.parametrize("kind", telemetry.CAPABILITY_KINDS)
def test_concurrent_process_writers_keep_each_capability_class_atomically_bounded(
    tmp_path, kind,
):
    data_dir = tmp_path / kind
    data_dir.mkdir()
    maximum = 16
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(_process_prepare, str(data_dir), kind, worker, 12, maximum)
            for worker in range(4)
        ]
        assert sum(future.result(timeout=30) for future in futures) == 48

    telemetry_dir = data_dir / "telemetry"
    entries = list((telemetry_dir / kind).iterdir())
    assert len(entries) == maximum
    assert telemetry_dir.stat().st_mode & 0o077 == 0
    assert (telemetry_dir / kind).stat().st_mode & 0o077 == 0
    assert (telemetry_dir / ".capabilities.lock").stat().st_mode & 0o077 == 0
    assert all(path.stat().st_mode & 0o077 == 0 for path in entries)
    assert all(TelemetryObserver._valid_capability(path.name) for path in entries)


def test_concurrent_link_replacement_and_all_consumers_are_atomic_and_one_shot(tmp_path):
    data_dir = tmp_path / "replace"
    data_dir.mkdir()
    with ProcessPoolExecutor(max_workers=8) as pool:
        prepared = list(pool.map(
            _process_prepare_same_link,
            [str(data_dir)] * 8,
            range(8),
            timeout=30,
        ))
    assert all(prepared)
    assert len(list((data_dir / "telemetry" / "links").iterdir())) == 1
    assert len(list((data_dir / "telemetry" / "pending").iterdir())) == 1

    with ProcessPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(_process_consume, str(data_dir), "links", "same-callback")
            for _ in range(8)
        ]
        assert sum(future.result(timeout=20) for future in futures) == 1
    rows = [
        json.loads(line) for line in
        (data_dir / "telemetry" / "journal.ndjson").read_text().splitlines()
    ]
    assert len(rows) == 1
    assert (rows[0]["status"], rows[0]["failure_class"]) == ("completed", "none")


@pytest.mark.parametrize("kind", ("pending", "outcomes"))
def test_concurrent_process_consumers_win_exactly_once(tmp_path, kind):
    data_dir = tmp_path / kind
    data_dir.mkdir()
    observer = TelemetryObserver(data_dir)
    if kind == "pending":
        bearer = observer.prepare_invocation(**_pending_event())
    else:
        run = observer.invocation_completed(**_invocation())
        assert run is not None
        bearer = run.capability
    with ProcessPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(_process_consume, str(data_dir), kind, bearer)
            for _ in range(8)
        ]
        assert sum(future.result(timeout=20) for future in futures) == 1
    assert not (data_dir / "telemetry" / kind / bearer).exists()


@pytest.mark.parametrize(
    ("available", "overrides", "fallbacks", "override_count"),
    [
        (None, (), 0, 0),
        ({"gpt-5.6-luna"}, (), 1, 0),
        (None, ("profile.model",), 0, 1),
        ({"gpt-5.6-luna"}, ("profile.model",), 1, 1),
    ],
)
def test_route_signals_count_actual_fallbacks_and_only_override_warnings(
    tmp_path, available, overrides, fallbacks, override_count,
):
    route = RoutingPolicy.load(tmp_path).resolve(
        "implementer", "codex", available_models=available,
        active_host_overrides=overrides,
    )
    observer = TelemetryObserver(tmp_path)
    run = observe_invocation_completion(
        observer, route, status="completed", failure_class="none",
    )
    assert run is not None
    row = json.loads((tmp_path / "telemetry" / "journal.ndjson").read_text().splitlines()[-1])
    assert row["signals"]["fallback_steps"]["value"] == fallbacks
    assert row["signals"]["host_override_count"]["value"] == override_count


def test_real_claude_post_hook_main_is_silent_and_records_only_controlled_completion(
    tmp_path, monkeypatch, capsys,
):
    data_dir = tmp_path / "state"
    data_dir.mkdir()
    environ = {"FOUNDRY_DATA": str(data_dir)}
    tool_input = {"subagent_type": "foundry:implementer", "prompt": _packet(
        "SENSITIVE_PROMPT_SENTINEL /private/name.py user@example.test",
    )}

    disabled = route_agent.route_tool_input(tool_input, cwd=tmp_path, environ={})
    enabled = route_agent.route_tool_input(
        tool_input, cwd=tmp_path, environ=environ, correlation="host-tool-call-1",
    )
    assert enabled == disabled
    assert not (data_dir / "telemetry" / "journal.ndjson").exists()

    monkeypatch.setenv("FOUNDRY_DATA", str(data_dir))
    monkeypatch.setattr(observe_agent.sys, "stdin", io.StringIO(json.dumps({
        "hook_event_name": "PostToolUse",
        "tool_use_id": "host-tool-call-1",
        "tool_input": tool_input,
        "tool_response": "SENSITIVE_RESPONSE_SENTINEL",
    })))
    observe_agent.main()
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
    journal = (data_dir / "telemetry" / "journal.ndjson").read_text()
    rows = [json.loads(line) for line in journal.splitlines()]
    assert [(row["event"], row["status"], row["failure_class"]) for row in rows] == [
        ("invocation_completed", "completed", "none"),
    ]
    assert not (data_dir / "telemetry" / "outcomes").exists()
    assert "SENSITIVE_" not in journal and "user@example.test" not in journal

    monkeypatch.setattr(observe_agent.sys, "stdin", io.StringIO(json.dumps({
        "hook_event_name": "PostToolUse", "tool_use_id": "host-tool-call-1",
    })))
    observe_agent.main()
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""

    route_agent.route_tool_input(
        tool_input, cwd=tmp_path, environ=environ, correlation="host-tool-call-failed",
    )
    monkeypatch.setattr(observe_agent.sys, "stdin", io.StringIO(json.dumps({
        "hook_event_name": "PostToolUseFailure",
        "tool_use_id": "host-tool-call-failed",
        "error": "SENSITIVE_FREE_ERROR_SENTINEL /private/path",
    })))
    observe_agent.main()
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
    failed_row = json.loads((data_dir / "telemetry" / "journal.ndjson").read_text().splitlines()[-1])
    assert (failed_row["status"], failed_row["failure_class"]) == ("failed", "host")
    assert "SENSITIVE_FREE_ERROR_SENTINEL" not in json.dumps(failed_row)

    before = (data_dir / "telemetry" / "journal.ndjson").read_text()
    monkeypatch.setattr(
        observe_agent,
        "claude_invocation_completed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("SENSITIVE_INTERNAL_HOOK_SENTINEL")
        ),
    )
    monkeypatch.setattr(observe_agent.sys, "stdin", io.StringIO(json.dumps({
        "hook_event_name": "PostToolUse", "tool_use_id": "unprepared",
    })))
    observe_agent.main()
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
    assert (data_dir / "telemetry" / "journal.ndjson").read_text() == before


def test_real_codex_plan_completion_cli_and_outcome_preserve_spawn_and_result(
    tmp_path, monkeypatch, capsys,
):
    data_dir = tmp_path / "state"
    data_dir.mkdir()
    packet = _packet("SENSITIVE_CODEX_PACKET /secret/repo Alice@example.test")
    monkeypatch.delenv("FOUNDRY_DATA", raising=False)
    disabled = codex_spawn_plan("implementer", packet, root=tmp_path)

    monkeypatch.setenv("FOUNDRY_DATA", str(data_dir))
    enabled = codex_spawn_plan("implementer", packet, root=tmp_path)
    telemetry_plan = enabled.pop("telemetry")
    assert enabled["execution_id"] != disabled["execution_id"]
    assert _without_execution_instance(enabled) == _without_execution_instance(disabled)
    assert "completion_capability" not in json.dumps(enabled["spawn"])
    private_state = "".join(
        path.read_text(errors="ignore") for path in (data_dir / "telemetry").rglob("*")
        if path.is_file()
    )
    assert "SENSITIVE_CODEX_PACKET" not in private_state
    assert "Alice@example.test" not in private_state

    telemetry.main([
        "complete", "--capability", telemetry_plan["completion_capability"],
        "--status", "completed", "--failure-class", "none",
    ])
    completion = json.loads(capsys.readouterr().out)
    assert completion["observed"] is True
    telemetry.main([
        "outcome", "--capability", completion["outcome_capability"],
        "--tests-state", "passed", "--tests-passed", "4", "--tests-failed", "0",
        "--review-state", "passed", "--correction-cycles", "0",
    ])
    assert json.loads(capsys.readouterr().out) == {"observed": True}
    rows = [
        json.loads(line) for line in
        (data_dir / "telemetry" / "journal.ndjson").read_text().splitlines()
    ]
    assert [row["host"] if row["event"] == "invocation_completed" else row["review_state"]
            for row in rows] == ["codex", "passed"]
    invocation = rows[0]
    assert invocation["usage"] == {
        name: {"value": None, "provenance": "unknown"}
        for name in ("input_tokens", "output_tokens", "total_tokens")
    }
    assert all(invocation[name] == {"value": None, "provenance": "unknown"}
               for name in ("duration_ms", "packet_bytes", "file_count"))
    assert invocation["estimated_cost"] is None
    assert invocation["provider_reported_cost"] is None

    telemetry.main(["outcome", "--capability", completion["outcome_capability"]])
    assert json.loads(capsys.readouterr().out) == {"observed": False}


def test_codex_current_context_fallback_does_not_fake_a_host_completion_boundary(
    tmp_path, monkeypatch,
):
    data_dir = tmp_path / "state"
    data_dir.mkdir()
    monkeypatch.setenv("FOUNDRY_DATA", str(data_dir))
    plan = codex_spawn_plan(
        "implementer", _packet(), root=tmp_path, subagent_available=False,
    )
    assert plan["mode"] == "current_context"
    assert "telemetry" not in plan
    assert not (data_dir / "telemetry").exists()


@pytest.mark.parametrize(
    ("status", "failure_class"),
    [
        ("completed", "none"),
        ("failed", "host"),
        ("failed", "timeout"),
        ("cancelled", "cancelled"),
        ("unknown", "unknown"),
    ],
)
def test_codex_completion_cli_records_each_truthful_host_return_classification(
    tmp_path, monkeypatch, status, failure_class,
):
    data_dir = tmp_path / f"{status}-{failure_class}"
    data_dir.mkdir()
    monkeypatch.setenv("FOUNDRY_DATA", str(data_dir))
    capability = TelemetryObserver(data_dir).prepare_invocation(**_pending_event())
    assert capability is not None

    completed = subprocess.run(
        [
            sys.executable, str(PLUGIN_ROOT / "tooling" / "foundry_cli.py"),
            "telemetry", "complete", "--capability", capability,
            "--status", status, "--failure-class", failure_class,
        ],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "FOUNDRY_DATA": str(data_dir)},
    )
    assert completed.stderr == ""
    result = json.loads(completed.stdout)
    assert result["observed"] is True
    assert TelemetryObserver._valid_capability(result["outcome_capability"])
    row = json.loads((data_dir / "telemetry" / "journal.ndjson").read_text())
    assert (row["status"], row["failure_class"]) == (status, failure_class)


def test_codex_completion_cli_uses_the_sealed_project_telemetry_vocabulary(tmp_path):
    config = tmp_path / ".foundry" / "model-routing.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "mappings": {"codex": {"balanced": {
            "model": "project-model-v1", "effort": "deep",
        }}},
        "effort_scopes": {"codex": {"project-model": {
            "version": 3, "levels": ["careful", "deep"], "inadmissible": {},
        }}},
    }), encoding="utf-8")
    data_dir = tmp_path / "state"
    data_dir.mkdir()
    policy = RoutingPolicy.load(tmp_path)
    event = _pending_event()
    event.update({"model": "project-model-v1", "effort": "deep"})
    capability = TelemetryObserver(
        data_dir, effort_scopes=policy.effort_scopes, project_models=policy.project_models,
    ).prepare_invocation(**event)
    assert capability is not None
    config.unlink()
    callback_cwd = tmp_path / "callback-cwd"
    callback_cwd.mkdir()

    completed = subprocess.run(
        [
            sys.executable, str(PLUGIN_ROOT / "tooling" / "foundry_cli.py"),
            "telemetry", "complete", "--capability", capability,
            "--status", "completed", "--failure-class", "none",
        ],
        check=True, capture_output=True, text=True,
        cwd=callback_cwd, env={**os.environ, "FOUNDRY_DATA": str(data_dir)},
    )
    assert json.loads(completed.stdout)["observed"] is True
    row = json.loads((data_dir / "telemetry" / "journal.ndjson").read_text())
    assert (row["model"], row["effort"], row["effort_scope"]["version"]) == (
        "project-model-v1", "deep", 3,
    )


def test_claude_post_hook_uses_the_sealed_project_telemetry_vocabulary(tmp_path, monkeypatch):
    config = tmp_path / ".foundry" / "model-routing.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "effort_scopes": {"claude": {"project-model": {
            "version": 2, "levels": ["low", "high"], "inadmissible": {},
        }}},
        "claude_models": {"project-model-v1": "project-wire"},
    }), encoding="utf-8")
    data_dir = tmp_path / "state"
    data_dir.mkdir()
    policy = RoutingPolicy.load(tmp_path)
    event = _pending_event("claude")
    event.update({"model": "project-model-v1", "effort": "high"})
    correlation = "project-claude-callback"
    observer = TelemetryObserver(
        data_dir, effort_scopes=policy.effort_scopes, project_models=policy.project_models,
    )
    assert observer.prepare_correlated_invocation(correlation, **event)
    config.unlink()
    callback_cwd = tmp_path / "callback-cwd"
    callback_cwd.mkdir()

    monkeypatch.chdir(callback_cwd)
    assert observe_agent.observe(
        {"hook_event_name": "PostToolUse", "tool_use_id": correlation},
        environ={"FOUNDRY_DATA": str(data_dir)},
    )
    row = json.loads((data_dir / "telemetry" / "journal.ndjson").read_text())
    assert (row["model"], row["effort"], row["effort_scope"]["version"]) == (
        "project-model-v1", "high", 2,
    )


def test_codex_completion_cli_has_no_success_default_and_rejects_incoherent_pairs_safely(
    tmp_path, monkeypatch, capsys,
):
    data_dir = tmp_path / "state"
    data_dir.mkdir()
    monkeypatch.setenv("FOUNDRY_DATA", str(data_dir))
    capability = TelemetryObserver(data_dir).prepare_invocation(**_pending_event())
    assert capability is not None

    with pytest.raises(SystemExit):
        telemetry.main(["complete", "--capability", capability])
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "invalid telemetry arguments\n"
    assert (data_dir / "telemetry" / "pending" / capability).exists()

    with pytest.raises(SystemExit):
        telemetry.main([
            "complete", "--capability", capability,
            "--status", "completed", "--failure-class", "host",
        ])
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "invalid telemetry arguments\n"
    assert (data_dir / "telemetry" / "pending" / capability).exists()

    sentinel = "SENSITIVE_STATUS_SENTINEL_user@example.test"
    with pytest.raises(SystemExit):
        telemetry.main([
            "complete", "--capability", capability,
            "--status", sentinel, "--failure-class", "unknown",
        ])
    captured = capsys.readouterr()
    assert sentinel not in captured.out + captured.err
    assert (data_dir / "telemetry" / "pending" / capability).exists()

    telemetry.main([
        "complete", "--capability", capability,
        "--status", "unknown", "--failure-class", "unknown",
    ])
    assert json.loads(capsys.readouterr().out)["observed"] is True


def test_host_completion_adapters_refuse_missing_classification_without_consuming(tmp_path):
    observer = TelemetryObserver(tmp_path)
    capability = observer.prepare_invocation(**_pending_event())
    assert capability is not None
    assert codex_invocation_completed(observer, capability) is None
    assert (tmp_path / "telemetry" / "pending" / capability).exists()

    route = RoutingPolicy.load(tmp_path).resolve("implementer", "claude")
    assert claude_invocation_completed(observer, route) is None


def test_capability_partial_write_exception_recovers_without_replayed_fragments(
    tmp_path, monkeypatch,
):
    original_write = telemetry.os.write
    calls = 0

    def partial_then_error(fd, payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(fd, payload[:7])
        if calls == 2:
            raise OSError("SENSITIVE_CAPABILITY_WRITE_SENTINEL")
        return original_write(fd, payload)

    monkeypatch.setattr(telemetry.os, "write", partial_then_error)
    observer = TelemetryObserver(tmp_path)
    assert observer.prepare_invocation(**_pending_event()) is None
    capability = observer.prepare_invocation(**_pending_event())
    assert capability is not None
    pending = tmp_path / "telemetry" / "pending"
    assert [path.name for path in pending.iterdir()] == [capability]
    run = observer.complete_invocation(
        capability, status="completed", failure_class="none", expected_host="codex",
    )
    assert run is not None


def test_correlation_key_partial_write_exception_is_recoverable_and_identity_free(
    tmp_path, monkeypatch,
):
    original_write = telemetry.os.write
    calls = 0

    def partial_then_error(fd, payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(fd, payload[:5])
        if calls == 2:
            raise OSError("SENSITIVE_KEY_WRITE_SENTINEL")
        return original_write(fd, payload)

    monkeypatch.setattr(telemetry.os, "write", partial_then_error)
    observer = TelemetryObserver(tmp_path)
    correlation = "SENSITIVE_RAW_TOOL_ID"
    assert not observer.prepare_correlated_invocation(
        correlation, **_pending_event("claude"),
    )
    assert observer.prepare_correlated_invocation(
        correlation, **_pending_event("claude"),
    )
    telemetry_dir = tmp_path / "telemetry"
    assert (telemetry_dir / ".correlation.key").stat().st_size == 32
    assert not list(telemetry_dir.glob(".key-*"))
    private = "".join(
        path.read_text(errors="ignore")
        for path in telemetry_dir.rglob("*") if path.is_file()
    )
    assert correlation not in private and "SENSITIVE_KEY_WRITE_SENTINEL" not in private


def test_correlation_key_fifo_fails_open_without_blocking_or_persisting_state(tmp_path):
    data_dir = tmp_path / "fifo-key"
    telemetry_dir = data_dir / "telemetry"
    telemetry_dir.mkdir(parents=True)
    key = telemetry_dir / ".correlation.key"
    os.mkfifo(key, mode=0o600)

    assert _call_special_entry_without_blocking(data_dir, "correlation_key") is False
    assert stat.S_ISFIFO(os.lstat(key).st_mode)
    assert not (telemetry_dir / "pending").exists()
    assert not (telemetry_dir / "links").exists()


def test_correlation_key_hardlink_is_rejected_without_external_state_change(tmp_path):
    data_dir = tmp_path / "hardlinked-key"
    telemetry_dir = data_dir / "telemetry"
    telemetry_dir.mkdir(parents=True)
    outside = tmp_path / "outside-correlation-key"
    secret = b"SENSITIVE_PRIVATE_KEY_SENTINEL!!"
    assert len(secret) == 32
    outside.write_bytes(secret)
    outside.chmod(0o600)
    key = telemetry_dir / ".correlation.key"
    os.link(outside, key)

    assert not TelemetryObserver(data_dir).prepare_correlated_invocation(
        "hardlinked-callback",
        **_pending_event("claude"),
    )
    assert outside.read_bytes() == secret
    assert key.read_bytes() == secret
    assert outside.stat().st_nlink == 2
    assert not (telemetry_dir / "pending").exists()
    assert not (telemetry_dir / "links").exists()


def test_interrupted_key_temporaries_are_bounded_cleaned_and_symlink_safe(tmp_path):
    data_dir = tmp_path / "state"
    data_dir.mkdir()
    context = multiprocessing.get_context("spawn")

    for _attempt in range(4):
        process = context.Process(
            target=_process_abort_key_creation,
            args=(str(data_dir), "before_publish"),
        )
        process.start()
        process.join(20)
        if process.is_alive():
            process.kill()
            process.join(5)
        assert process.exitcode == 91
        temporaries = list((data_dir / "telemetry").glob(".key-*"))
        assert len(temporaries) == 1
        temporary_stat = os.lstat(temporaries[0])
        assert stat.S_ISREG(temporary_stat.st_mode)
        assert stat.S_IMODE(temporary_stat.st_mode) == 0o600

    process = context.Process(
        target=_process_abort_key_creation,
        args=(str(data_dir), "after_publish"),
    )
    process.start()
    process.join(20)
    if process.is_alive():
        process.kill()
        process.join(5)
    assert process.exitcode == 92

    telemetry_dir = data_dir / "telemetry"
    key = telemetry_dir / ".correlation.key"
    temporaries = list(telemetry_dir.glob(".key-*"))
    assert len(temporaries) == 1
    key_stat = key.stat()
    temporary_stat = os.lstat(temporaries[0])
    assert (temporary_stat.st_dev, temporary_stat.st_ino) == (key_stat.st_dev, key_stat.st_ino)
    assert stat.S_IMODE(key_stat.st_mode) == 0o600
    secret = key.read_bytes()
    assert len(secret) == 32

    outside = tmp_path / "outside-key"
    outside.write_bytes(b"SENSITIVE_OUTSIDE_KEY_SENTINEL")
    outside.chmod(0o640)
    outside_mode = outside.stat().st_mode
    symlink = telemetry_dir / (".key-" + "f" * 32)
    if symlink.exists():
        symlink = telemetry_dir / (".key-" + "e" * 32)
    symlink.symlink_to(outside)

    observer = TelemetryObserver(data_dir)
    assert observer.prepare_invocation(**_pending_event()) is not None
    assert not list(telemetry_dir.glob(".key-*"))
    cleaned_key_stat = key.stat()
    assert (cleaned_key_stat.st_dev, cleaned_key_stat.st_ino) == (key_stat.st_dev, key_stat.st_ino)
    assert stat.S_IMODE(cleaned_key_stat.st_mode) == 0o600
    assert key.read_bytes() == secret
    assert outside.read_bytes() == b"SENSITIVE_OUTSIDE_KEY_SENTINEL"
    assert outside.stat().st_mode == outside_mode
    assert telemetry_dir.stat().st_mode & 0o077 == 0
    assert (telemetry_dir / ".capabilities.lock").stat().st_mode & 0o077 == 0
    assert observer.prepare_correlated_invocation(
        "surviving-key-callback", **_pending_event("claude"),
    )
    assert key.read_bytes() == secret


def test_capability_symlinks_fail_open_without_following_or_mutating_targets(tmp_path):
    outside_file = tmp_path / "outside"
    outside_file.write_text("SENSITIVE_OUTSIDE_SENTINEL", encoding="utf-8")
    original_mode = outside_file.stat().st_mode

    lock_root = tmp_path / "lock-root"
    (lock_root / "telemetry").mkdir(parents=True)
    (lock_root / "telemetry" / ".capabilities.lock").symlink_to(outside_file)
    assert TelemetryObserver(lock_root).prepare_invocation(**_pending_event()) is None

    kind_root = tmp_path / "kind-root"
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    (kind_root / "telemetry").mkdir(parents=True)
    (kind_root / "telemetry" / "pending").symlink_to(outside_dir, target_is_directory=True)
    assert TelemetryObserver(kind_root).prepare_invocation(**_pending_event()) is None
    assert not list(outside_dir.iterdir())

    token_root = tmp_path / "token-root"
    pending = token_root / "telemetry" / "pending"
    pending.mkdir(parents=True)
    token = "a" * telemetry.CAPABILITY_HEX_LENGTH
    (pending / token).symlink_to(outside_file)
    assert TelemetryObserver(token_root)._consume_capability("pending", token) is None
    assert not (pending / token).exists()

    key_root = tmp_path / "key-root"
    (key_root / "telemetry").mkdir(parents=True)
    (key_root / "telemetry" / ".correlation.key").symlink_to(outside_file)
    assert not TelemetryObserver(key_root).prepare_correlated_invocation(
        "callback", **_pending_event("claude"),
    )

    assert outside_file.read_text() == "SENSITIVE_OUTSIDE_SENTINEL"
    assert outside_file.stat().st_mode == original_mode


def test_enabled_observation_preserves_gate_plan_retry_and_host_result(tmp_path, monkeypatch):
    data_dir = tmp_path / "state"
    data_dir.mkdir()
    claim = {
        "diff_hash": "a" * 64, "should_run": True, "state": "in_progress",
        "generation": 1, "claim_id": "b" * 64,
        "root": str(tmp_path), "base": "origin/main",
    }
    packet = _packet("Review the exact claimed diff.")
    monkeypatch.delenv("FOUNDRY_DATA", raising=False)
    disabled = codex_spawn_plan(
        "reviewer", packet, root=tmp_path, review_claim=claim,
        available_models={"gpt-5.6-sol"},
    )
    monkeypatch.setenv("FOUNDRY_DATA", str(data_dir))
    first = codex_spawn_plan(
        "reviewer", packet, root=tmp_path, review_claim=claim,
        available_models={"gpt-5.6-sol"},
    )
    second = codex_spawn_plan(
        "reviewer", packet, root=tmp_path, review_claim=claim,
        available_models={"gpt-5.6-sol"},
    )
    for observed in (first, second):
        observed = dict(observed)
        observed.pop("telemetry")
        assert observed["execution_id"] != disabled["execution_id"]
        assert _without_execution_instance(observed) == _without_execution_instance(disabled)
        assert observed["route"]["gate_floor"] == "frontier"
        assert _without_execution_instance(observed)["spawn"] == _without_execution_instance(disabled)["spawn"]
        assert observed["escalation"] == disabled["escalation"]
    host_result = {"verdict": "AC: PASS · QUALITY: OK to merge"}
    result_before = json.loads(json.dumps(host_result))
    cap = first["telemetry"]["completion_capability"]
    run = codex_invocation_completed(
        TelemetryObserver(data_dir), cap, status="completed", failure_class="none",
    )
    assert run is not None
    assert TelemetryObserver(data_dir).run_outcome_capability(
        run.capability, **aggregate_outcome(review_state="passed"),
    )
    assert host_result == result_before


def test_journal_rotation_retention_uses_sequence_when_clock_stalls_or_recedes(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(telemetry, "MAX_JOURNAL_BYTES", 1)
    monkeypatch.setattr(telemetry, "RETENTION_FILES", 3)
    timestamps = iter((20, 20, 19, 18, 17, 16))
    monkeypatch.setattr(telemetry.time, "time_ns", lambda: next(timestamps))
    observer = TelemetryObserver(tmp_path)
    run_ids = []
    for sequence in range(7):
        event = _raw(_invocation())
        event["run_id"] = f"{sequence:032x}"
        run_ids.append(event["run_id"])
        assert observer._emit(event)

    directory = tmp_path / "telemetry"
    archives = sorted(
        directory.glob("journal.*.ndjson"),
        key=lambda path: int(path.name[8:-7]),
    )
    assert [int(path.name[8:-7]) for path in archives] == [24, 25, 26]
    assert [json.loads(path.read_text())["run_id"] for path in archives] == run_ids[3:6]
    assert json.loads((directory / "journal.ndjson").read_text())["run_id"] == run_ids[6]


def test_journal_rotation_retries_a_real_destination_collision_without_overwrite(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(telemetry, "MAX_JOURNAL_BYTES", 1)
    monkeypatch.setattr(telemetry, "RETENTION_FILES", 4)
    monkeypatch.setattr(telemetry.time, "time_ns", lambda: 100)
    observer = TelemetryObserver(tmp_path)
    first = _raw(_invocation())
    first["run_id"] = "a" * 32
    second = _raw(_invocation())
    second["run_id"] = "b" * 32
    assert observer._emit(first)

    original_link = telemetry.os.link
    collision_payload = b"existing filesystem archive\n"
    collided = False

    def collide_once(
        source, destination, *, src_dir_fd=None, dst_dir_fd=None,
        follow_symlinks=True,
    ):
        nonlocal collided
        if not collided and os.fspath(destination).startswith("journal."):
            collision_fd = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=dst_dir_fd,
            )
            try:
                os.write(collision_fd, collision_payload)
                os.fsync(collision_fd)
            finally:
                os.close(collision_fd)
            collided = True
        return original_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(telemetry.os, "link", collide_once)

    assert observer._emit(second)
    directory = tmp_path / "telemetry"
    collision = directory / "journal.101.ndjson"
    archive = directory / "journal.102.ndjson"
    assert collided
    assert collision.read_bytes() == collision_payload
    assert json.loads(archive.read_text())["run_id"] == first["run_id"]
    assert json.loads((directory / "journal.ndjson").read_text())["run_id"] == second["run_id"]


def test_journal_rotation_interruption_recovers_on_next_append_without_duplicates(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(telemetry, "MAX_JOURNAL_BYTES", 1)
    monkeypatch.setattr(telemetry.time, "time_ns", lambda: 100)
    observer = TelemetryObserver(tmp_path)
    first = _raw(_invocation())
    first["run_id"] = "c" * 32
    second = _raw(_invocation())
    second["run_id"] = "d" * 32
    assert observer._emit(first)

    original_unlink = telemetry.os.unlink

    def interrupt_before_source_unlink(path, *args, dir_fd=None, **kwargs):
        if path == "journal.ndjson":
            raise OSError("simulated interruption after archive publication")
        return original_unlink(path, *args, dir_fd=dir_fd, **kwargs)

    monkeypatch.setattr(telemetry.os, "unlink", interrupt_before_source_unlink)

    assert observer._emit(second) is False
    directory = tmp_path / "telemetry"
    journal = directory / "journal.ndjson"
    archive = directory / "journal.101.ndjson"
    assert journal.read_bytes() == archive.read_bytes()
    assert json.loads(journal.read_text())["run_id"] == first["run_id"]
    assert (journal.stat().st_dev, journal.stat().st_ino) == (
        archive.stat().st_dev,
        archive.stat().st_ino,
    )
    assert journal.stat().st_nlink == 2

    monkeypatch.setattr(telemetry.os, "unlink", original_unlink)
    assert observer._emit(second)

    archives = list(directory.glob("journal.*.ndjson"))
    assert archives == [archive]
    run_ids = []
    for path in [archive, journal]:
        run_ids.extend(json.loads(line)["run_id"] for line in path.read_text().splitlines())
    assert run_ids.count(first["run_id"]) == 1
    assert run_ids.count(second["run_id"]) == 1
    assert len(run_ids) == 2
    assert journal.stat().st_nlink == 1
    assert archive.stat().st_nlink == 1
    assert (journal.stat().st_dev, journal.stat().st_ino) != (
        archive.stat().st_dev,
        archive.stat().st_ino,
    )
    assert export_aggregates(tmp_path)["invocations"][0]["count"] == 2


def test_append_reopens_after_cross_process_rotation_makes_its_fd_stale(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(telemetry, "MAX_JOURNAL_BYTES", 1)
    monkeypatch.setattr(telemetry.time, "time_ns", lambda: 200)
    observer = TelemetryObserver(tmp_path)
    first = _raw(_invocation())
    first["run_id"] = "e" * 32
    second = _raw(_invocation())
    second["run_id"] = "f" * 32
    assert observer._emit(first)

    context = multiprocessing.get_context("spawn")
    opened = context.Event()
    rotated = context.Event()
    output = context.Queue()
    process = context.Process(
        target=_process_append_after_rotation,
        args=(str(tmp_path), second, opened, rotated, output),
    )
    process.start()
    assert opened.wait(5), "append did not reach the pre-flock interleaving"
    try:
        with observer._telemetry_directory() as (_directory, directory_fd):
            observer._rotate(directory_fd)
    finally:
        rotated.set()
    process.join(5)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail("append and rotation deadlocked")
    assert process.exitcode == 0
    assert output.get(timeout=2) is True

    directory = tmp_path / "telemetry"
    paths = [*directory.glob("journal.*.ndjson"), directory / "journal.ndjson"]
    run_ids = [
        json.loads(line)["run_id"]
        for path in paths
        for line in path.read_text().splitlines()
    ]
    assert run_ids.count(first["run_id"]) == 1
    assert run_ids.count(second["run_id"]) == 1
    assert len(run_ids) == 2


@pytest.mark.parametrize("state", ["three_links", "unrelated_numeric"])
def test_ambiguous_linked_journal_states_fail_open_without_mutation(tmp_path, state):
    directory = tmp_path / "telemetry"
    directory.mkdir(mode=0o700)
    journal = directory / "journal.ndjson"
    journal.write_bytes(b"private journal sentinel\n")
    journal.chmod(0o600)
    archive = directory / "journal.10.ndjson"
    outside = tmp_path / "outside-journal"
    if state == "three_links":
        os.link(journal, archive)
        os.link(journal, outside)
    else:
        os.link(journal, outside)
        archive.write_bytes(b"unrelated private archive\n")
        archive.chmod(0o600)

    def snapshot(path):
        value = path.stat()
        return path.read_bytes(), value.st_dev, value.st_ino, value.st_nlink

    before = {path: snapshot(path) for path in (journal, archive, outside)}
    assert TelemetryObserver(tmp_path)._emit(_raw(_invocation())) is False
    assert {path: snapshot(path) for path in before} == before
    assert not (directory / ".retention.lock").exists()


def test_telemetry_io_failure_and_sensitive_invalid_cli_value_are_invariant(
    tmp_path, monkeypatch, capsys,
):
    data_dir = tmp_path / "state"
    data_dir.mkdir()
    packet = _packet()
    monkeypatch.delenv("FOUNDRY_DATA", raising=False)
    expected = codex_spawn_plan("implementer", packet, root=tmp_path)
    claude_expected = route_agent.route_tool_input(
        {"subagent_type": "foundry:implementer", "prompt": packet},
        cwd=tmp_path, environ={},
    )
    monkeypatch.setenv("FOUNDRY_DATA", str(data_dir))
    def fail(*_args, **_kwargs):
        raise RuntimeError("sensitive internal IO failure")

    monkeypatch.setattr(TelemetryObserver, "prepare_invocation", fail)
    monkeypatch.setattr(TelemetryObserver, "prepare_correlated_invocation", fail)
    observed = codex_spawn_plan("implementer", packet, root=tmp_path)
    assert observed["execution_id"] != expected["execution_id"]
    assert _without_execution_instance(observed) == _without_execution_instance(expected)
    assert route_agent.route_tool_input(
        {"subagent_type": "foundry:implementer", "prompt": packet},
        cwd=tmp_path, environ={"FOUNDRY_DATA": str(data_dir)}, correlation="host-call",
    ) == claude_expected

    sentinel = "SENSITIVE_FORGED_CAPABILITY_user@example.test"
    telemetry.main([
        "complete", "--capability", sentinel,
        "--status", "unknown", "--failure-class", "unknown",
    ])
    assert capsys.readouterr().out == '{"observed": false}\n'
    assert sentinel not in json.dumps(export_aggregates(data_dir))


@pytest.mark.parametrize("failure", ["append", "fsync"])
@pytest.mark.parametrize("facade", [False, True])
def test_failed_outcome_emission_can_retry_once(tmp_path, monkeypatch, failure, facade):
    observer = TelemetryObserver(tmp_path)
    run = observer.invocation_completed(**_invocation())
    assert run is not None
    capability_path = tmp_path / "telemetry" / "outcomes" / run._capability
    original_append = TelemetryObserver._append
    original_fsync = telemetry.os.fsync

    def unavailable(*_args):
        raise OSError("journal unavailable")

    failed_once = False

    def fail_first_sync(fd):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise OSError("journal sync uncertain")
        return original_fsync(fd)

    def attempt():
        if facade:
            return observer.run_outcome(run, **_outcome())
        return TelemetryObserver(tmp_path).run_outcome_capability(run._capability, **_outcome())

    if failure == "append":
        monkeypatch.setattr(TelemetryObserver, "_append", unavailable)
    else:
        monkeypatch.setattr(telemetry.os, "fsync", fail_first_sync)
    assert attempt() is False
    assert capability_path.exists()
    monkeypatch.setattr(TelemetryObserver, "_append", original_append)
    monkeypatch.setattr(telemetry.os, "fsync", original_fsync)
    # An unrelated event rotates a potentially already-written outcome.
    monkeypatch.setattr(telemetry, "MAX_JOURNAL_BYTES", 1)
    assert observer.invocation_completed(**_invocation()) is not None
    assert attempt() is True
    assert not capability_path.exists()
    assert attempt() is False
    assert export_aggregates(tmp_path)["outcomes"] == {"passed": 1}


@pytest.mark.parametrize("family", ["default", "private-"])
def test_recorded_scope_does_not_authorize_model_identifiers_for_export(tmp_path, family):
    observer = TelemetryObserver(tmp_path)
    assert observer.invocation_completed(**_invocation()) is not None
    journal = tmp_path / "telemetry" / "journal.ndjson"
    event = json.loads(journal.read_text())
    event["model"] = "private-customer-identifier"
    event["effort_scope"]["family"] = family
    journal.write_text(json.dumps(event) + "\n")
    assert export_aggregates(tmp_path)["invocations"] == []
    # A current model allowlist cannot repair a tampered journal seal.
    exported = export_aggregates(tmp_path, project_models=(event["model"],))
    assert exported["invocations"] == []


@pytest.mark.parametrize("recorded_family", ["default", "gpt-6"])
def test_export_requires_exact_model_even_with_a_declared_family(tmp_path, recorded_family):
    model = "gpt-6-astra"
    old_scopes = {"codex": {recorded_family: EffortScope(
        "codex", recorded_family, 3, ("careful", "deep"), {},
    )}}
    observer = TelemetryObserver(
        tmp_path, effort_scopes=old_scopes, project_models=(model,),
    )
    invocation = _invocation()
    invocation.update(model=model, effort="deep")
    assert observer.invocation_completed(**invocation) is not None
    journal = tmp_path / "telemetry" / "journal.ndjson"
    event = json.loads(journal.read_text())
    forged_model = "gpt-6-alice.smith@example.com"
    event["model"] = forged_model
    with journal.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")

    # A current family declaration must neither authorize the forged model nor
    # reinterpret the historical effort using its new vocabulary/version.
    current_scopes = {"codex": {"gpt-6": EffortScope(
        "codex", "gpt-6", 4, ("low", "medium", "high"), {},
    )}}
    exported = export_aggregates(
        tmp_path, effort_scopes=current_scopes, project_models=(model,),
    )
    assert forged_model not in json.dumps(exported)
    assert len(exported["invocations"]) == 1
    row = exported["invocations"][0]
    assert (row["model"], row["effort"], row["policy_version"], row["count"]) == (
        model, "deep", 3, 1,
    )


@pytest.mark.parametrize("sealed", [True, False])
@pytest.mark.parametrize("field", ["effort", "levels", "both"])
def test_export_rejects_pii_in_historical_effort_vocabulary(tmp_path, sealed, field):
    observer = TelemetryObserver(tmp_path)
    assert observer.invocation_completed(**_invocation()) is not None
    journal = tmp_path / "telemetry" / "journal.ndjson"
    clean_line = journal.read_text()
    event = json.loads(clean_line)
    pii = "alice.smith@example.com"
    if not sealed:
        event.pop("_journal_digest")
    if field in ("effort", "both"):
        event["effort"] = pii
    if field in ("levels", "both"):
        event["effort_scope"]["levels"].append(pii)
    journal.write_text(clean_line + json.dumps(event) + "\n")

    exported = export_aggregates(tmp_path)
    assert pii not in json.dumps(exported)
    assert len(exported["invocations"]) == 1
    assert exported["invocations"][0]["count"] == 1


def test_export_rejects_unsealed_records_without_creating_a_key(tmp_path):
    observer = TelemetryObserver(tmp_path)
    assert observer.invocation_completed(**_invocation()) is not None
    journal = tmp_path / "telemetry" / "journal.ndjson"
    event = json.loads(journal.read_text())
    event.pop("_journal_digest")
    journal.write_text(json.dumps(event) + "\n")
    key = journal.parent / ".capabilities.key"
    key.unlink()
    # An unsealed record cannot be downgraded into a supported legacy format.
    assert export_aggregates(tmp_path)["invocations"] == []
    assert not key.exists()


def test_export_imports_pre_seal_v1_record_only_through_current_vocabulary(tmp_path):
    legacy = _raw(_invocation())
    telemetry_dir = tmp_path / "telemetry"
    telemetry_dir.mkdir()
    journal = telemetry_dir / "journal.ndjson"
    journal.write_text(json.dumps(legacy) + "\n")
    journal.chmod(0o600)

    exported = export_aggregates(tmp_path)
    assert [(row["model"], row["count"]) for row in exported["invocations"]] == [
        (legacy["model"], 1),
    ]


def test_export_imports_pre_seal_v1_sanitized_unknown_model(tmp_path):
    legacy = _raw(_invocation())
    legacy["model"] = "unknown"
    telemetry_dir = tmp_path / "telemetry"
    telemetry_dir.mkdir()
    journal = telemetry_dir / "journal.ndjson"
    journal.write_text(json.dumps(legacy) + "\n")
    journal.chmod(0o600)

    exported = export_aggregates(tmp_path)
    assert [(row["model"], row["count"]) for row in exported["invocations"]] == [
        ("unknown", 1),
    ]


def test_export_rejects_a_seal_stripped_from_a_signed_record(tmp_path):
    observer = TelemetryObserver(tmp_path)
    assert observer.invocation_completed(**_invocation()) is not None
    journal = tmp_path / "telemetry" / "journal.ndjson"
    signed = json.loads(journal.read_text())
    stripped = dict(signed)
    stripped.pop("_journal_digest")
    stripped["model"] = "gpt-6-alice@example.test"
    journal.write_text(json.dumps(signed) + "\n" + json.dumps(stripped) + "\n")

    exported = export_aggregates(tmp_path)
    assert len(exported["invocations"]) == 1
    assert "alice@example.test" not in json.dumps(exported)


def test_scope_family_prefix_does_not_declare_a_telemetry_model(tmp_path):
    scopes = {"codex": {"gpt-6": EffortScope(
        "codex", "gpt-6", 1, ("low", "medium"), {},
    )}}
    event = _invocation()
    event.update(model="gpt-6-alice@example.test", effort="medium")
    observer = TelemetryObserver(tmp_path, effort_scopes=scopes)

    assert observer.invocation_completed(**event) is None
    assert not (tmp_path / "telemetry" / "journal.ndjson").exists()


def test_export_cannot_authenticate_history_without_its_key(tmp_path):
    observer = TelemetryObserver(tmp_path)
    assert observer.invocation_completed(**_invocation()) is not None
    key = tmp_path / "telemetry" / ".capabilities.key"
    key.unlink()
    assert export_aggregates(tmp_path)["invocations"] == []
    assert not key.exists()


@pytest.mark.parametrize("digest", ["é" * 64, "0" * 64, [], None])
def test_export_ignores_invalid_journal_seals(tmp_path, digest):
    assert TelemetryObserver(tmp_path).invocation_completed(**_invocation()) is not None
    journal = tmp_path / "telemetry" / "journal.ndjson"
    event = json.loads(journal.read_text())
    event["_journal_digest"] = digest
    journal.write_text(json.dumps(event) + "\n")
    assert export_aggregates(tmp_path)["invocations"] == []
