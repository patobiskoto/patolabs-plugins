"""Passive, local-only Foundry invocation telemetry.

This module is deliberately separate from routing: callers supply only data they
already have after an invocation/run completes.  It never resolves a route, reads a
project, invokes a provider, or returns data which could affect a later decision.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import stat
import sys
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping
from foundry.effort_policy import is_public_identifier, scope_for


SCHEMA_VERSION = 1
EVENT_TYPES = ("invocation_completed", "run_outcome")
HOSTS = ("claude", "codex")
KINDS = ("delegated", "main")
ROLES = ("scout", "implementer", "coordinator", "reviewer", "architect")
TIERS = ("economy", "balanced", "frontier", "apex")
SCOPES = ("bounded_packet", "current_context")
CONTEXT_POLICIES = ("fresh", "inherited", "unknown")
RESOLUTION_SOURCES = ("default", "project", "user", "fallback", "escalation", "mixed")
PROVENANCE = ("observed", "estimated", "provider_reported", "unknown")
STATUSES = ("completed", "failed", "cancelled", "unknown")
FAILURE_CLASSES = ("none", "configuration", "unavailable", "timeout", "cancelled", "host", "unknown")
TEST_STATES = ("passed", "failed", "not_run", "unknown")
REVIEW_STATES = ("passed", "failed", "not_run", "unknown")
SEVERITIES = ("info", "warning", "error", "blocking")
KNOWN_MODELS = frozenset({
    "haiku-4.5", "sonnet-5", "opus-5", "fable-5",
    "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol",
})
# These are labels shipped with Foundry, rather than caller-selected identifiers.
PRICE_TABLE_IDS = ("foundry-public-v1",)
PRICE_TABLE_VERSIONS = ("v1",)
MAX_JOURNAL_BYTES = 4 * 1024 * 1024
RETENTION_FILES = 7
CAPABILITY_BYTES = 32
CAPABILITY_HEX_LENGTH = CAPABILITY_BYTES * 2
PENDING_TTL_SECONDS = 24 * 60 * 60
MAX_PENDING_CAPABILITIES = 256
MAX_CAPABILITY_RECORD_BYTES = 64 * 1024
CAPABILITY_KINDS = ("pending", "links", "outcomes")
COMPLETION_CLASSIFICATIONS = frozenset({
    ("completed", "none"),
    ("failed", "configuration"),
    ("failed", "unavailable"),
    ("failed", "timeout"),
    ("failed", "host"),
    ("failed", "unknown"),
    ("cancelled", "cancelled"),
    ("unknown", "unknown"),
})
_ISSUER = object()


class TelemetryValidationError(ValueError):
    """An event is outside the intentionally small public schema."""


def new_run_id() -> str:
    """Create an ephemeral random correlation id, with no identity-derived input."""
    return secrets.token_hex(16)


def _now_ns() -> int:
    """Wall-clock boundary kept injectable for exact expiry tests."""
    return time.time_ns()


def _enum(value: object, allowed: tuple[str, ...], name: str, *, nullable: bool = False):
    if value is None and nullable:
        return None
    if value not in allowed:
        raise TelemetryValidationError(f"{name} must be one of the controlled vocabulary")
    return value


def _metric(value: object, name: str) -> dict:
    if not isinstance(value, Mapping) or set(value) != {"value", "provenance"}:
        raise TelemetryValidationError(f"{name} must contain only value and provenance")
    provenance = _enum(value["provenance"], PROVENANCE, f"{name}.provenance")
    number = value["value"]
    if provenance == "unknown":
        if number is not None:
            raise TelemetryValidationError(f"{name}: unknown metrics must be null")
    elif type(number) is not int or number < 0:
        raise TelemetryValidationError(f"{name}.value must be a non-negative integer")
    return {"value": number, "provenance": provenance}


def unknown_metric() -> dict:
    return {"value": None, "provenance": "unknown"}


def validate_completion_classification(status: object, failure_class: object) -> tuple[str, str]:
    """Validate one truthful, controlled host-completion classification."""
    clean_status = _enum(status, STATUSES, "status")
    clean_failure = _enum(failure_class, FAILURE_CLASSES, "failure_class")
    if (clean_status, clean_failure) not in COMPLETION_CLASSIFICATIONS:
        raise TelemetryValidationError("status/failure_class combination is not controlled")
    return clean_status, clean_failure


def _estimated_cost(value: object) -> dict | None:
    name = "estimated_cost"
    if value is None:
        return None
    required = {"amount_micros", "currency", "price_table_id", "price_table_version", "source", "completeness"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise TelemetryValidationError(f"{name} has an invalid shape")
    if type(value["amount_micros"]) is not int or value["amount_micros"] < 0:
        raise TelemetryValidationError(f"{name}.amount_micros must be non-negative")
    if value["currency"] not in ("USD", "EUR"):
        raise TelemetryValidationError(f"{name}.currency is not controlled")
    if value["source"] != "estimated" or value["completeness"] != "complete":
        raise TelemetryValidationError("estimated_cost must be complete and estimated")
    if value["price_table_id"] not in PRICE_TABLE_IDS:
        raise TelemetryValidationError(f"{name}.price_table_id is not controlled")
    if value["price_table_version"] not in PRICE_TABLE_VERSIONS:
        raise TelemetryValidationError(f"{name}.price_table_version is not controlled")
    return dict(value)


def _provider_reported_cost(value: object) -> dict | None:
    if value is None:
        return None
    required = {"amount_micros", "currency", "source", "completeness"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise TelemetryValidationError("provider_reported_cost has an invalid shape")
    if type(value["amount_micros"]) is not int or value["amount_micros"] < 0:
        raise TelemetryValidationError("provider_reported_cost.amount_micros must be non-negative")
    if value["currency"] not in ("USD", "EUR"):
        raise TelemetryValidationError("provider_reported_cost.currency is not controlled")
    if value["source"] != "provider_reported" or value["completeness"] != "complete":
        raise TelemetryValidationError("provider_reported_cost must be complete and provider_reported")
    return dict(value)


def _event_base(event: Mapping[str, object], expected: str) -> dict:
    if event.get("event") != expected or event.get("schema_version") != SCHEMA_VERSION:
        raise TelemetryValidationError("unsupported telemetry event schema")
    run_id = event.get("run_id")
    if not isinstance(run_id, str) or len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
        raise TelemetryValidationError("run_id must be a random 128-bit hexadecimal value")
    return {"schema_version": SCHEMA_VERSION, "event": expected, "run_id": run_id}


def _scope_record(scope) -> dict:
    """Persist the validated scope identity needed to interpret historical effort."""
    if not is_public_identifier(scope.family):
        raise TelemetryValidationError("effort_scope family is not a public identifier")
    if not scope.levels or any(not is_public_identifier(level) for level in scope.levels):
        raise TelemetryValidationError("effort_scope levels are not public identifiers")
    return {
        "host": scope.host,
        "family": scope.family,
        "version": scope.version,
        "levels": list(scope.levels),
    }


def _recorded_scope(value: object, host: str, model: str) -> dict:
    if not isinstance(value, Mapping) or set(value) != {"host", "family", "version", "levels"}:
        raise TelemetryValidationError("effort_scope has an invalid shape")
    scope_host = value.get("host")
    family = value.get("family")
    version = value.get("version")
    levels = value.get("levels")
    if scope_host != host or not is_public_identifier(family):
        raise TelemetryValidationError("effort_scope host or family is invalid")
    if family != "default" and not model.startswith(family):
        raise TelemetryValidationError("effort_scope family does not match model")
    if type(version) is not int or version < 1:
        raise TelemetryValidationError("effort_scope version is invalid")
    if (not isinstance(levels, list) or not levels
            or any(not is_public_identifier(level) for level in levels)
            or len(set(levels)) != len(levels)):
        raise TelemetryValidationError("effort_scope levels are invalid")
    return {"host": host, "family": family, "version": version, "levels": list(levels)}


def validate_invocation_completed(
    event: Mapping[str, object], effort_scopes=None, project_models=(), *,
    allow_recorded_scope: bool = False,
) -> dict:
    allowed = {
        "schema_version", "event", "run_id", "host", "kind", "role", "tier", "model",
        "effort", "scope", "context_policy", "resolution_source", "signals", "usage",
        "duration_ms", "packet_bytes", "file_count", "estimated_cost", "provider_reported_cost",
        "status", "failure_class",
    }
    supplied_scope = event.get("effort_scope") if isinstance(event, Mapping) else None
    keys = set(event) if isinstance(event, Mapping) else set()
    if not isinstance(event, Mapping) or keys not in (allowed, allowed | {"effort_scope"}):
        raise TelemetryValidationError("invocation_completed has unknown or missing fields")
    result = _event_base(event, "invocation_completed")
    status, failure_class = validate_completion_classification(
        event["status"], event["failure_class"],
    )
    host = _enum(event["host"], HOSTS, "host")
    historical_scope = None
    if allow_recorded_scope and supplied_scope is not None and isinstance(event["model"], str):
        historical_scope = _recorded_scope(supplied_scope, host, event["model"])
    model = _telemetry_model(
        host, event["model"], effort_scopes, project_models,
        historical_scope=historical_scope,
    )
    resolved_scope = scope_for(host, model, effort_scopes)
    expected_scope = _scope_record(resolved_scope)
    if supplied_scope is None:
        recorded_scope = expected_scope
    elif allow_recorded_scope:
        recorded_scope = historical_scope
    else:
        recorded_scope = _recorded_scope(supplied_scope, host, model)
        if recorded_scope != expected_scope:
            raise TelemetryValidationError("effort_scope does not match the resolved policy")
    if event["effort"] is not None and event["effort"] not in recorded_scope["levels"]:
        raise TelemetryValidationError(
            f"effort '{event['effort']}' is not declared for ({recorded_scope['host']}, "
            f"{recorded_scope['family']}, v{recorded_scope['version']}); accepted levels: "
            f"{', '.join(recorded_scope['levels'])}"
        )
    clean_effort = (
        event["effort"]
        if allow_recorded_scope and supplied_scope is not None
        else _telemetry_effort(host, model, event["effort"], effort_scopes)
    )
    result.update({
        "host": host,
        "kind": _enum(event["kind"], KINDS, "kind"),
        "role": _enum(event["role"], ROLES, "role", nullable=True),
        "tier": _enum(event["tier"], TIERS, "tier", nullable=True),
        "model": model,
        "effort": clean_effort,
        "effort_scope": recorded_scope,
        "scope": _enum(event["scope"], SCOPES, "scope"),
        "context_policy": _enum(event["context_policy"], CONTEXT_POLICIES, "context_policy"),
        "resolution_source": _enum(event["resolution_source"], RESOLUTION_SOURCES, "resolution_source"),
        "duration_ms": _metric(event["duration_ms"], "duration_ms"),
        "packet_bytes": _metric(event["packet_bytes"], "packet_bytes"),
        "file_count": _metric(event["file_count"], "file_count"),
        "estimated_cost": _estimated_cost(event["estimated_cost"]),
        "provider_reported_cost": _provider_reported_cost(event["provider_reported_cost"]),
        "status": status,
        "failure_class": failure_class,
    })
    signals = event["signals"]
    signal_keys = {"availability_probed", "fallback_steps", "host_override_count", "escalation_floor_active"}
    if not isinstance(signals, Mapping) or set(signals) != signal_keys:
        raise TelemetryValidationError("signals has unknown or missing fields")
    if type(signals["availability_probed"]) is not bool or type(signals["escalation_floor_active"]) is not bool:
        raise TelemetryValidationError("signals booleans are invalid")
    result["signals"] = {
        "availability_probed": signals["availability_probed"],
        "fallback_steps": _metric(signals["fallback_steps"], "signals.fallback_steps"),
        "host_override_count": _metric(signals["host_override_count"], "signals.host_override_count"),
        "escalation_floor_active": signals["escalation_floor_active"],
    }
    usage = event["usage"]
    if not isinstance(usage, Mapping) or set(usage) != {"input_tokens", "output_tokens", "total_tokens"}:
        raise TelemetryValidationError("usage has unknown or missing fields")
    result["usage"] = {key: _metric(usage[key], f"usage.{key}") for key in usage}
    return result


def _telemetry_model(
    host: str, model: object, effort_scopes=None, project_models=(), *,
    historical_scope: Mapping[str, object] | None = None,
) -> str:
    if not isinstance(model, str) or not model or model != model.strip():
        raise TelemetryValidationError("model must be a non-empty canonical identifier")
    # An effort-family prefix selects a policy only; it is not a telemetry
    # identity declaration.  Persisting an arbitrary suffix would let caller
    # data enter the local journal before the export filter sees it.
    # A verified journal record remains readable after a project model is
    # retired, but only the HMAC-authenticated export path supplies this scope.
    declared_historically = isinstance(historical_scope, Mapping)
    if (model not in KNOWN_MODELS and model not in project_models
            and not declared_historically):
        raise TelemetryValidationError("model is not declared in the telemetry vocabulary")
    if not is_public_identifier(model):
        raise TelemetryValidationError("model must be a public canonical identifier")
    return model


def _telemetry_effort(
    host: object, model: object, effort: object, effort_scopes=None,
) -> str | None:
    if effort is None:
        return None
    if not isinstance(host, str) or not isinstance(model, str):
        raise TelemetryValidationError("effort scope cannot be resolved")
    scope = scope_for(host, model, effort_scopes)
    if effort not in scope.levels:
        raise TelemetryValidationError(
            f"effort '{effort}' is not declared for ({scope.host}, {scope.family}, "
            f"v{scope.version}); accepted levels: {', '.join(scope.levels)}"
        )
    if effort in scope.inadmissible:
        raise TelemetryValidationError(f"effort '{effort}' is inadmissible: {scope.inadmissible[effort]}")
    return effort


def validate_run_outcome(event: Mapping[str, object]) -> dict:
    allowed = {"schema_version", "event", "run_id", "tests", "correction_cycles", "review_state", "severities", "violations"}
    if not isinstance(event, Mapping) or set(event) != allowed:
        raise TelemetryValidationError("run_outcome has unknown or missing fields")
    result = _event_base(event, "run_outcome")
    tests = event["tests"]
    if not isinstance(tests, Mapping) or set(tests) != {"state", "passed", "failed", "skipped"}:
        raise TelemetryValidationError("tests has unknown or missing fields")
    result["tests"] = {"state": _enum(tests["state"], TEST_STATES, "tests.state")}
    result["tests"].update({key: _metric(tests[key], f"tests.{key}") for key in ("passed", "failed", "skipped")})
    result["correction_cycles"] = _metric(event["correction_cycles"], "correction_cycles")
    result["review_state"] = _enum(event["review_state"], REVIEW_STATES, "review_state")
    for name, values, vocabulary in (("severities", event["severities"], SEVERITIES), ("violations", event["violations"], SEVERITIES)):
        if not isinstance(values, Mapping) or set(values) != set(vocabulary):
            raise TelemetryValidationError(f"{name} has unknown or missing fields")
        result[name] = {key: _metric(values[key], f"{name}.{key}") for key in vocabulary}
    return result


def validate_event(
    event: Mapping[str, object], effort_scopes=None, project_models=(), *,
    allow_recorded_scope: bool = False,
) -> dict:
    if not isinstance(event, Mapping):
        raise TelemetryValidationError("telemetry event must be an object")
    if event.get("event") == "invocation_completed":
        return validate_invocation_completed(
            event, effort_scopes, project_models,
            allow_recorded_scope=allow_recorded_scope,
        )
    if event.get("event") == "run_outcome":
        return validate_run_outcome(event)
    raise TelemetryValidationError("telemetry event type is not supported")


class TelemetryRun:
    """Immutable, observer-issued one-time capability for an invocation outcome."""

    __slots__ = ("_capability", "_observer", "_sealed")

    def __init__(
        self, observer: "TelemetryObserver", capability: str, *, _issuer: object = None,
    ):
        if _issuer is not _ISSUER:
            raise TypeError("TelemetryRun capabilities are observer-issued")
        object.__setattr__(self, "_observer", observer)
        object.__setattr__(self, "_capability", capability)
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("TelemetryRun capabilities are immutable")

    @property
    def capability(self) -> str:
        """Return the bearer value for a required cross-process outcome step."""
        return self._capability


class TelemetryObserver:
    """Best-effort append-only journal enabled only by ``FOUNDRY_DATA``."""

    def __init__(
        self, data_dir: str | os.PathLike | None = None, *, effort_scopes=None,
        project_models=(),
    ):
        self.data_dir = Path(data_dir) if data_dir else None
        self.effort_scopes = effort_scopes
        self.project_models = frozenset(project_models)
        self._issued: dict[str, TelemetryRun] = {}

    @classmethod
    def from_environ(
        cls, environ: Mapping[str, str] | None = None, *, effort_scopes=None,
        project_models=(),
    ) -> "TelemetryObserver":
        environ = os.environ if environ is None else environ
        return cls(
            environ.get("FOUNDRY_DATA"), effort_scopes=effort_scopes,
            project_models=project_models,
        )

    def _emit(
        self, event: Mapping[str, object], *, allow_recorded_scope: bool = False,
    ) -> bool:
        """Validate and append an event; every IO/validation failure is fail-open."""
        if self.data_dir is None:
            return False
        try:
            clean = validate_event(
                event,
                self.effort_scopes,
                self.project_models,
                allow_recorded_scope=allow_recorded_scope,
            )
            with self._telemetry_directory() as (_directory, directory_fd):
                self._append(directory_fd, clean)
        except (OSError, TelemetryValidationError, TypeError, ValueError):
            return False
        return True

    def invocation_completed(self, **event: object) -> TelemetryRun | None:
        """Host-neutral completion façade for Claude and Codex adapters.

        Callers provide only completion data already present in their host callback;
        this method intentionally performs no inference or collection.
        """
        # The correlation value is an observer capability, never caller identity
        # material.  Silently declining a supplied run_id keeps the method fail-open.
        if "run_id" in event:
            return None
        run_id = new_run_id()
        if not self._emit({"schema_version": SCHEMA_VERSION,
                           "event": "invocation_completed", "run_id": run_id, **event}):
            return None
        return self._issue_outcome(run_id)

    def run_outcome(self, run: TelemetryRun, **event: object) -> bool:
        """Host-neutral run-outcome façade for already-known aggregate results."""
        if not isinstance(run, TelemetryRun) or run._observer is not self or "run_id" in event:
            return False
        if self._issued.get(run._capability) is not run:
            return False
        observed = self.run_outcome_capability(run._capability, **event)
        if observed:
            self._issued.pop(run._capability, None)
        return observed

    def prepare_invocation(self, **event: object) -> str | None:
        """Persist a private pending record without emitting a completion event."""
        if self.data_dir is None or "run_id" in event:
            return None
        try:
            run_id = new_run_id()
            clean = validate_invocation_completed({
                "schema_version": SCHEMA_VERSION,
                "event": "invocation_completed",
                "run_id": run_id,
                **event,
            }, self.effort_scopes, self.project_models)
            token = secrets.token_hex(CAPABILITY_BYTES)
            with self._capability_lock() as directory_fd:
                now_ns = _now_ns()
                self._cleanup_all_locked(directory_fd, now_ns)
                self._write_capability_locked(
                    directory_fd,
                    "pending",
                    token,
                    {"created_ns": now_ns, "event": clean},
                    now_ns=now_ns,
                )
            return token
        except (OSError, TelemetryValidationError, TypeError, ValueError):
            return None

    def prepare_correlated_invocation(self, correlation: str, **event: object) -> bool:
        """Bind a host callback id through a keyed, identity-free private lookup."""
        if (
            self.data_dir is None
            or not isinstance(correlation, str)
            or not correlation
            or "run_id" in event
        ):
            return False
        try:
            run_id = new_run_id()
            clean = validate_invocation_completed({
                "schema_version": SCHEMA_VERSION,
                "event": "invocation_completed",
                "run_id": run_id,
                **event,
            }, self.effort_scopes, self.project_models)
            token = secrets.token_hex(CAPABILITY_BYTES)
            with self._capability_lock() as directory_fd:
                now_ns = _now_ns()
                self._cleanup_all_locked(directory_fd, now_ns)
                key = self._correlation_key_locked(directory_fd, correlation)
                previous = self._peek_capability_locked(directory_fd, "links", key)
                self._write_capability_locked(
                    directory_fd,
                    "pending",
                    token,
                    {"created_ns": now_ns, "event": clean},
                    now_ns=now_ns,
                )
                try:
                    self._write_capability_locked(
                        directory_fd,
                        "links",
                        key,
                        {"created_ns": now_ns, "pending": token},
                        now_ns=now_ns,
                        replace=True,
                    )
                except OSError:
                    self._remove_capability_locked(directory_fd, "pending", token)
                    raise
                old_pending = previous.get("pending") if isinstance(previous, Mapping) else None
                if old_pending != token and self._valid_capability(old_pending):
                    self._remove_capability_locked(directory_fd, "pending", old_pending)
            return True
        except (OSError, TelemetryValidationError, TypeError, ValueError):
            return False

    def complete_correlated_invocation(
        self, correlation: str, *, status: str, failure_class: str,
    ) -> bool:
        """Silently observe one correlated Claude callback.

        The hook has no safe private channel through which it can hand an aggregate
        outcome bearer to the coordinator. It therefore records completion only and
        deliberately issues no outcome capability.
        """
        if self.data_dir is None or not isinstance(correlation, str) or not correlation:
            return False
        try:
            validate_completion_classification(status, failure_class)
            with self._capability_lock() as directory_fd:
                now_ns = _now_ns()
                key = self._correlation_key_locked(directory_fd, correlation)
                link = self._consume_capability_locked(
                    directory_fd, "links", key, now_ns=now_ns,
                )
                if not isinstance(link, Mapping) or not isinstance(link.get("pending"), str):
                    return False
                pending = self._consume_capability_locked(
                    directory_fd, "pending", link["pending"], now_ns=now_ns,
                )
                if not isinstance(pending, Mapping) or not isinstance(pending.get("event"), Mapping):
                    return False
                event = dict(pending["event"])
                if event.get("host") != "claude":
                    return False
                event["status"] = status
                event["failure_class"] = failure_class
                if not self._emit(event, allow_recorded_scope=True):
                    self._write_capability_locked(
                        directory_fd, "pending", link["pending"], pending, now_ns=now_ns,
                    )
                    self._write_capability_locked(
                        directory_fd, "links", key, link, now_ns=now_ns,
                    )
                    return False
        except (OSError, TelemetryValidationError, TypeError, ValueError):
            return False
        return True

    def complete_invocation(
        self, capability: str, *, status: str, failure_class: str,
        expected_host: str | None = None,
    ) -> TelemetryRun | None:
        """Consume one pending capability after a truthfully classified host return."""
        try:
            validate_completion_classification(status, failure_class)
        except (TelemetryValidationError, TypeError, ValueError):
            return None
        if self.data_dir is None or not self._valid_capability(capability):
            return None
        try:
            with self._capability_lock() as directory_fd:
                now_ns = _now_ns()
                pending = self._consume_capability_locked(
                    directory_fd, "pending", capability, now_ns=now_ns,
                )
                if not isinstance(pending, Mapping) or not isinstance(pending.get("event"), Mapping):
                    return None
                event = dict(pending["event"])
                if expected_host is not None and event.get("host") != expected_host:
                    return None
                event["status"] = status
                event["failure_class"] = failure_class
                if not self._emit(event, allow_recorded_scope=True):
                    self._write_capability_locked(
                        directory_fd, "pending", capability, pending, now_ns=now_ns,
                    )
                    return None
        except (OSError, TelemetryValidationError, TypeError, ValueError):
            return None
        return self._issue_outcome(str(event["run_id"]))

    def run_outcome_capability(self, capability: str, **event: object) -> bool:
        """Consume a persisted one-time capability at a later process boundary."""
        if "run_id" in event:
            return False
        if self.data_dir is None or not self._valid_capability(capability):
            return False
        try:
            with self._capability_lock() as directory_fd:
                now_ns = _now_ns()
                outcome = self._consume_capability_locked(
                    directory_fd, "outcomes", capability, now_ns=now_ns,
                )
                if not isinstance(outcome, Mapping) or not isinstance(outcome.get("run_id"), str):
                    return False
                observed = self._emit({
                    "schema_version": SCHEMA_VERSION,
                    "event": "run_outcome",
                    "run_id": outcome["run_id"],
                    **event,
                })
                if not observed:
                    self._write_capability_locked(
                        directory_fd, "outcomes", capability, outcome, now_ns=now_ns,
                    )
                return observed
        except (OSError, TelemetryValidationError, TypeError, ValueError):
            return False

    def _issue_outcome(self, run_id: str) -> TelemetryRun | None:
        try:
            token = secrets.token_hex(CAPABILITY_BYTES)
            with self._capability_lock() as directory_fd:
                now_ns = _now_ns()
                self._cleanup_all_locked(directory_fd, now_ns)
                self._write_capability_locked(
                    directory_fd,
                    "outcomes",
                    token,
                    {"created_ns": now_ns, "run_id": run_id},
                    now_ns=now_ns,
                )
            run = TelemetryRun(self, token, _issuer=_ISSUER)
            self._issued[token] = run
            while len(self._issued) > MAX_PENDING_CAPABILITIES:
                self._issued.pop(next(iter(self._issued)))
            return run
        except OSError:
            return None

    @staticmethod
    def _valid_capability(value: object) -> bool:
        return (
            isinstance(value, str) and len(value) == CAPABILITY_HEX_LENGTH
            and all(character in "0123456789abcdef" for character in value)
        )

    @staticmethod
    def _private_regular_entry_stat(
        directory_fd: int,
        name: str,
        fd: int,
        *,
        maximum_size: int | None = None,
        exact_size: int | None = None,
        link_count: int = 1,
    ) -> os.stat_result:
        """Validate one private entry and its still-current directory binding."""
        file_stat = os.fstat(fd)
        entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)

        def invalid(value: os.stat_result) -> bool:
            return (
                not stat.S_ISREG(value.st_mode)
                or stat.S_IMODE(value.st_mode) & 0o077 != 0
                or value.st_uid != os.geteuid()
                or value.st_nlink != link_count
                or (maximum_size is not None and value.st_size > maximum_size)
                or (exact_size is not None and value.st_size != exact_size)
            )

        if invalid(file_stat) or invalid(entry_stat):
            raise OSError("invalid private telemetry entry")
        if (entry_stat.st_dev, entry_stat.st_ino) != (file_stat.st_dev, file_stat.st_ino):
            raise OSError("unstable private telemetry entry")
        return file_stat

    @staticmethod
    def _numeric_archive_id(name: str) -> int | None:
        prefix = "journal."
        suffix = ".ndjson"
        if not name.startswith(prefix) or not name.endswith(suffix):
            return None
        value = name[len(prefix) : -len(suffix)]
        if not value or any(character not in "0123456789" for character in value):
            return None
        return int(value)

    def _archive_for_inode(
        self,
        directory_fd: int,
        fd: int,
        *,
        link_count: int,
    ) -> str:
        """Return the sole numeric archive bound to ``fd``'s private inode."""
        file_stat = os.fstat(fd)
        matches = []
        for name in os.listdir(directory_fd):
            if self._numeric_archive_id(name) is None:
                continue
            try:
                entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if (entry_stat.st_dev, entry_stat.st_ino) == (
                file_stat.st_dev,
                file_stat.st_ino,
            ):
                matches.append(name)
        if len(matches) != 1:
            raise OSError("ambiguous telemetry journal archive")
        archive_name = matches[0]
        self._private_regular_entry_stat(
            directory_fd,
            archive_name,
            fd,
            link_count=link_count,
        )
        return archive_name

    def _interrupted_rotation_archive(self, directory_fd: int, fd: int) -> str:
        """Prove the exact two-link state left after archive publication."""
        self._private_regular_entry_stat(
            directory_fd,
            "journal.ndjson",
            fd,
            link_count=2,
        )
        archive_name = self._archive_for_inode(directory_fd, fd, link_count=2)
        # Keep the active binding as the last observation before a caller may
        # finalize it, matching the publication-side unlink discipline.
        self._private_regular_entry_stat(
            directory_fd,
            "journal.ndjson",
            fd,
            link_count=2,
        )
        return archive_name

    def _is_safe_stale_rotation_fd(self, directory_fd: int, fd: int) -> bool:
        """Recognize an opened journal that rotation safely moved to an archive."""
        try:
            archive_name = self._archive_for_inode(directory_fd, fd, link_count=1)
            flags = os.O_RDONLY | os.O_NONBLOCK
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                active_fd = os.open("journal.ndjson", flags, dir_fd=directory_fd)
            except FileNotFoundError:
                active_fd = None
            if active_fd is not None:
                try:
                    active_stat = self._private_regular_entry_stat(
                        directory_fd,
                        "journal.ndjson",
                        active_fd,
                    )
                    stale_stat = os.fstat(fd)
                    if (active_stat.st_dev, active_stat.st_ino) == (
                        stale_stat.st_dev,
                        stale_stat.st_ino,
                    ):
                        return False
                finally:
                    os.close(active_fd)
            # Revalidate the archive after inspecting the active name so removal
            # or rebinding cannot turn an unrelated stale fd into a retry signal.
            self._private_regular_entry_stat(
                directory_fd,
                archive_name,
                fd,
            )
            return True
        except OSError:
            return False

    def _consume_capability(self, kind: str, token: object) -> dict | None:
        if (
            self.data_dir is None
            or kind not in CAPABILITY_KINDS
            or not self._valid_capability(token)
        ):
            return None
        try:
            with self._capability_lock() as directory_fd:
                return self._consume_capability_locked(
                    directory_fd, kind, str(token), now_ns=_now_ns(),
                )
        except (OSError, TypeError, ValueError):
            return None

    @contextmanager
    def _telemetry_directory(self) -> Iterator[tuple[Path, int]]:
        assert self.data_dir is not None
        root = self.data_dir.expanduser()
        # FOUNDRY_DATA is shared with registry/config state.  It is deliberately
        # read-only from telemetry's perspective: only its dedicated child is owned.
        directory_flags = os.O_RDONLY | os.O_NONBLOCK
        if hasattr(os, "O_DIRECTORY"):
            directory_flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        root_fd = os.open(root, directory_flags)
        telemetry_fd = None
        try:
            if not stat.S_ISDIR(os.fstat(root_fd).st_mode):
                raise OSError("telemetry root is not a directory")
            try:
                os.mkdir("telemetry", mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
            telemetry_fd = os.open("telemetry", directory_flags, dir_fd=root_fd)
            telemetry_stat = os.fstat(telemetry_fd)
            telemetry_entry = os.stat(
                "telemetry", dir_fd=root_fd, follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(telemetry_stat.st_mode)
                or not stat.S_ISDIR(telemetry_entry.st_mode)
                or telemetry_stat.st_uid != os.geteuid()
                or telemetry_entry.st_uid != os.geteuid()
                or (telemetry_entry.st_dev, telemetry_entry.st_ino)
                != (telemetry_stat.st_dev, telemetry_stat.st_ino)
            ):
                raise OSError("telemetry path is not a directory")
            os.fchmod(telemetry_fd, 0o700)
            yield root / "telemetry", telemetry_fd
        finally:
            if telemetry_fd is not None:
                os.close(telemetry_fd)
            os.close(root_fd)

    @contextmanager
    def _capability_lock(self) -> Iterator[int]:
        """Hold the one stable lock covering every capability state transition."""
        with self._telemetry_directory() as (_directory, directory_fd):
            flags = os.O_RDWR | os.O_NONBLOCK
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            lock_fd = None
            for _attempt in range(8):
                try:
                    lock_fd = os.open(
                        ".capabilities.lock", flags, dir_fd=directory_fd,
                    )
                    break
                except FileNotFoundError:
                    try:
                        lock_fd = os.open(
                            ".capabilities.lock",
                            flags | os.O_CREAT | os.O_EXCL,
                            0o600,
                            dir_fd=directory_fd,
                        )
                        break
                    except FileExistsError:
                        continue
            if lock_fd is None:
                raise OSError("capability lock initialization race")
            locked = False
            try:
                self._private_regular_entry_stat(
                    directory_fd,
                    ".capabilities.lock",
                    lock_fd,
                )
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                locked = True
                self._private_regular_entry_stat(
                    directory_fd,
                    ".capabilities.lock",
                    lock_fd,
                )
                self._cleanup_key_temporaries_locked(directory_fd)
                yield directory_fd
            finally:
                if locked:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)

    @staticmethod
    def _open_kind_locked(directory_fd: int, kind: str, *, create: bool) -> int | None:
        if kind not in CAPABILITY_KINDS:
            raise OSError("invalid capability kind")
        if create:
            try:
                os.mkdir(kind, mode=0o700, dir_fd=directory_fd)
            except FileExistsError:
                pass
        flags = os.O_RDONLY | os.O_NONBLOCK
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            kind_fd = os.open(kind, flags, dir_fd=directory_fd)
        except FileNotFoundError:
            return None
        try:
            kind_stat = os.fstat(kind_fd)
            kind_entry = os.stat(kind, dir_fd=directory_fd, follow_symlinks=False)
            if (
                not stat.S_ISDIR(kind_stat.st_mode)
                or not stat.S_ISDIR(kind_entry.st_mode)
                or kind_stat.st_uid != os.geteuid()
                or kind_entry.st_uid != os.geteuid()
                or (kind_entry.st_dev, kind_entry.st_ino)
                != (kind_stat.st_dev, kind_stat.st_ino)
            ):
                raise OSError("capability kind is not a directory")
            os.fchmod(kind_fd, 0o700)
        except OSError:
            os.close(kind_fd)
            raise
        return kind_fd

    def _read_json_file_locked(
        self,
        directory_fd: int,
        kind_fd: int,
        name: str,
        *,
        expected_capability: str,
        consume: bool = False,
    ) -> dict | None:
        flags = os.O_RDONLY | os.O_NONBLOCK
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(name, flags, dir_fd=kind_fd)
        except (FileNotFoundError, OSError):
            return None
        try:
            file_stat = TelemetryObserver._private_regular_entry_stat(
                kind_fd,
                name,
                fd,
                maximum_size=MAX_CAPABILITY_RECORD_BYTES,
            )
            chunks = []
            remaining = file_stat.st_size
            while remaining:
                chunk = os.read(fd, min(remaining, 8192))
                if not chunk:
                    return None
                chunks.append(chunk)
                remaining -= len(chunk)
            value = json.loads(b"".join(chunks))
            if not isinstance(value, dict):
                return None
            bound_capability = value.get("_capability_digest")
            expected_digest = hashlib.sha256(expected_capability.encode("ascii")).hexdigest()
            if (
                not isinstance(bound_capability, str)
                or not hmac.compare_digest(bound_capability, expected_digest)
            ):
                return None
            bound_record = value.get("_record_digest")
            unsigned = dict(value)
            unsigned.pop("_record_digest", None)
            expected_record = self._record_digest_locked(directory_fd, unsigned)
            if (
                not isinstance(bound_record, str)
                or not hmac.compare_digest(bound_record, expected_record)
            ):
                return None
            final_stat = TelemetryObserver._private_regular_entry_stat(
                kind_fd,
                name,
                fd,
                maximum_size=MAX_CAPABILITY_RECORD_BYTES,
            )
            if (
                final_stat.st_size,
                final_stat.st_mtime_ns,
                final_stat.st_ctime_ns,
            ) != (
                file_stat.st_size,
                file_stat.st_mtime_ns,
                file_stat.st_ctime_ns,
            ):
                return None
            if consume:
                os.unlink(name, dir_fd=kind_fd)
                if os.fstat(fd).st_nlink != 0:
                    return None
            record = dict(value)
            record.pop("_capability_digest")
            record.pop("_record_digest")
            return record
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        finally:
            os.close(fd)

    @staticmethod
    def _created_ns(value: object) -> int | None:
        if not isinstance(value, Mapping):
            return None
        created_ns = value.get("created_ns")
        return created_ns if type(created_ns) is int and created_ns >= 0 else None

    @staticmethod
    def _expired(created_ns: int, now_ns: int) -> bool:
        return now_ns >= created_ns + PENDING_TTL_SECONDS * 1_000_000_000

    @staticmethod
    def _unlink_locked(kind_fd: int, name: str) -> None:
        try:
            os.unlink(name, dir_fd=kind_fd)
        except FileNotFoundError:
            pass

    def _cleanup_kind_locked(
        self, directory_fd: int, kind: str, now_ns: int, *, limit: int | None = None,
    ) -> None:
        kind_fd = self._open_kind_locked(directory_fd, kind, create=False)
        if kind_fd is None:
            return
        try:
            valid = []
            for name in os.listdir(kind_fd):
                if not self._valid_capability(name):
                    self._unlink_locked(kind_fd, name)
                    continue
                value = self._read_json_file_locked(
                    directory_fd,
                    kind_fd,
                    name,
                    expected_capability=name,
                )
                created_ns = self._created_ns(value)
                if created_ns is None or self._expired(created_ns, now_ns):
                    self._unlink_locked(kind_fd, name)
                    continue
                valid.append((created_ns, name))
            maximum = MAX_PENDING_CAPABILITIES if limit is None else max(0, limit)
            for _created_ns, name in sorted(valid)[:max(0, len(valid) - maximum)]:
                self._unlink_locked(kind_fd, name)
        finally:
            os.close(kind_fd)

    def _cleanup_all_locked(self, directory_fd: int, now_ns: int) -> None:
        for kind in CAPABILITY_KINDS:
            self._cleanup_kind_locked(directory_fd, kind, now_ns)

    def _write_capability_locked(
        self,
        directory_fd: int,
        kind: str,
        token: str,
        value: Mapping[str, object],
        *,
        now_ns: int,
        replace: bool = False,
    ) -> None:
        if (
            not self._valid_capability(token)
            or self._created_ns(value) is None
            or "_capability_digest" in value
            or "_record_digest" in value
        ):
            raise OSError("invalid capability record")
        self._cleanup_kind_locked(directory_fd, kind, now_ns)
        kind_fd = self._open_kind_locked(directory_fd, kind, create=True)
        assert kind_fd is not None
        temporary = f".write-{secrets.token_hex(16)}"
        try:
            try:
                entry = os.stat(token, dir_fd=kind_fd, follow_symlinks=False)
                target_exists = bool(entry)
            except FileNotFoundError:
                target_exists = False
            if not (replace and target_exists):
                self._cleanup_kind_locked(
                    directory_fd,
                    kind,
                    now_ns,
                    limit=MAX_PENDING_CAPABILITIES - 1,
                )
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NONBLOCK
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(temporary, flags, 0o600, dir_fd=kind_fd)
            try:
                unsigned = {
                    **value,
                    "_capability_digest": hashlib.sha256(token.encode("ascii")).hexdigest(),
                }
                record = {
                    **unsigned,
                    "_record_digest": self._record_digest_locked(directory_fd, unsigned),
                }
                payload = json.dumps(record, separators=(",", ":"), sort_keys=True).encode()
                if len(payload) > MAX_CAPABILITY_RECORD_BYTES:
                    raise OSError("capability record too large")
                self._private_regular_entry_stat(
                    kind_fd,
                    temporary,
                    fd,
                    exact_size=0,
                )
                self._write_all(fd, payload)
                self._private_regular_entry_stat(
                    kind_fd,
                    temporary,
                    fd,
                    exact_size=len(payload),
                )
                os.fsync(fd)
            finally:
                os.close(fd)
            if replace:
                os.replace(
                    temporary, token, src_dir_fd=kind_fd, dst_dir_fd=kind_fd,
                )
            else:
                os.link(
                    temporary,
                    token,
                    src_dir_fd=kind_fd,
                    dst_dir_fd=kind_fd,
                    follow_symlinks=False,
                )
                self._unlink_locked(kind_fd, temporary)
            published_fd = os.open(
                token,
                os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=kind_fd,
            )
            try:
                self._private_regular_entry_stat(
                    kind_fd,
                    token,
                    published_fd,
                    exact_size=len(payload),
                )
            finally:
                os.close(published_fd)
            os.fsync(kind_fd)
        finally:
            self._unlink_locked(kind_fd, temporary)
            os.close(kind_fd)

    def _peek_capability_locked(
        self, directory_fd: int, kind: str, token: str,
    ) -> dict | None:
        if not self._valid_capability(token):
            return None
        kind_fd = self._open_kind_locked(directory_fd, kind, create=False)
        if kind_fd is None:
            return None
        try:
            return self._read_json_file_locked(
                directory_fd,
                kind_fd,
                token,
                expected_capability=token,
            )
        finally:
            os.close(kind_fd)

    def _remove_capability_locked(self, directory_fd: int, kind: str, token: object) -> None:
        if not self._valid_capability(token):
            return
        kind_fd = self._open_kind_locked(directory_fd, kind, create=False)
        if kind_fd is None:
            return
        try:
            self._unlink_locked(kind_fd, str(token))
        finally:
            os.close(kind_fd)

    def _consume_capability_locked(
        self, directory_fd: int, kind: str, token: str, *, now_ns: int,
    ) -> dict | None:
        if not self._valid_capability(token):
            return None
        kind_fd = self._open_kind_locked(directory_fd, kind, create=False)
        if kind_fd is None:
            return None
        claimed = f".consume-{secrets.token_hex(16)}"
        try:
            try:
                os.rename(token, claimed, src_dir_fd=kind_fd, dst_dir_fd=kind_fd)
            except FileNotFoundError:
                return None
            value = self._read_json_file_locked(
                directory_fd,
                kind_fd,
                claimed,
                expected_capability=token,
                consume=True,
            )
            created_ns = self._created_ns(value)
            if created_ns is None or self._expired(created_ns, now_ns):
                return None
            return value
        finally:
            self._unlink_locked(kind_fd, claimed)
            os.close(kind_fd)

    def _record_digest_locked(
        self, directory_fd: int, record: Mapping[str, object],
    ) -> str:
        secret = self._private_secret_locked(
            directory_fd, ".capabilities.key", ".capability-key-",
        )
        payload = json.dumps(record, separators=(",", ":"), sort_keys=True).encode()
        return hmac.new(secret, payload, hashlib.sha256).hexdigest()

    def _correlation_key_locked(self, directory_fd: int, correlation: str) -> str:
        secret = self._private_secret_locked(
            directory_fd, ".correlation.key", ".key-",
        )
        return hmac.new(secret, correlation.encode(), hashlib.sha256).hexdigest()

    def _private_secret_locked(
        self, directory_fd: int, name: str, temporary_prefix: str, *, create: bool = True,
    ) -> bytes:
        flags = os.O_RDONLY | os.O_NONBLOCK
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(name, flags, dir_fd=directory_fd)
        except FileNotFoundError:
            if not create:
                raise
            secret = secrets.token_bytes(32)
            temporary = f"{temporary_prefix}{secrets.token_hex(16)}"
            create_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NONBLOCK
            if hasattr(os, "O_NOFOLLOW"):
                create_flags |= os.O_NOFOLLOW
            try:
                create_fd = os.open(temporary, create_flags, 0o600, dir_fd=directory_fd)
                try:
                    self._private_regular_entry_stat(
                        directory_fd,
                        temporary,
                        create_fd,
                        exact_size=0,
                    )
                    self._write_all(create_fd, secret)
                    self._private_regular_entry_stat(
                        directory_fd,
                        temporary,
                        create_fd,
                        exact_size=32,
                    )
                    os.fsync(create_fd)
                finally:
                    os.close(create_fd)
                os.link(
                    temporary,
                    name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                os.fsync(directory_fd)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
            fd = os.open(name, flags, dir_fd=directory_fd)
        try:
            key_stat = self._private_regular_entry_stat(
                directory_fd,
                name,
                fd,
                exact_size=32,
            )
            secret = os.read(fd, 33)
            final_stat = self._private_regular_entry_stat(
                directory_fd,
                name,
                fd,
                exact_size=32,
            )
            if (
                final_stat.st_mtime_ns,
                final_stat.st_ctime_ns,
            ) != (
                key_stat.st_mtime_ns,
                key_stat.st_ctime_ns,
            ):
                raise OSError("unstable telemetry private key")
        finally:
            os.close(fd)
        if len(secret) != 32:
            raise OSError("invalid telemetry private key")
        return secret

    @staticmethod
    def _cleanup_key_temporaries_locked(directory_fd: int) -> None:
        """Remove interrupted key publications without following their entries."""
        removed = False
        for name in sorted(os.listdir(directory_fd)):
            for prefix in (".key-", ".capability-key-"):
                suffix = name.removeprefix(prefix)
                if suffix == name:
                    continue
                if (
                    len(suffix) != 32
                    or any(character not in "0123456789abcdef" for character in suffix)
                ):
                    break
                try:
                    os.unlink(name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
                else:
                    removed = True
                break
        if removed:
            os.fsync(directory_fd)

    def _cleanup_capabilities(self, _directory: Path | None = None) -> None:
        """Remove stale/excess state under the same lock as every other operation."""
        if self.data_dir is None:
            return
        with self._capability_lock() as directory_fd:
            self._cleanup_all_locked(directory_fd, _now_ns())

    def _append(self, directory_fd: int, event: dict) -> None:
        flags = os.O_RDWR | os.O_APPEND | os.O_NONBLOCK
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        payload = None
        for _append_attempt in range(8):
            fd = None
            for _open_attempt in range(8):
                try:
                    fd = os.open("journal.ndjson", flags, dir_fd=directory_fd)
                    break
                except FileNotFoundError:
                    try:
                        fd = os.open(
                            "journal.ndjson",
                            flags | os.O_CREAT | os.O_EXCL,
                            0o600,
                            dir_fd=directory_fd,
                        )
                        break
                    except FileExistsError:
                        continue
            if fd is None:
                raise OSError("journal initialization race")
            transition = None
            locked = False
            try:
                try:
                    self._private_regular_entry_stat(
                        directory_fd,
                        "journal.ndjson",
                        fd,
                    )
                except OSError as validation_error:
                    try:
                        self._interrupted_rotation_archive(directory_fd, fd)
                    except OSError:
                        if not self._is_safe_stale_rotation_fd(directory_fd, fd):
                            raise validation_error
                        transition = "retry"
                    else:
                        transition = "recover"
                if transition is None:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                    locked = True
                    try:
                        journal_stat = self._private_regular_entry_stat(
                            directory_fd,
                            "journal.ndjson",
                            fd,
                        )
                    except OSError as validation_error:
                        try:
                            self._interrupted_rotation_archive(directory_fd, fd)
                        except OSError:
                            if not self._is_safe_stale_rotation_fd(directory_fd, fd):
                                raise validation_error
                            transition = "retry"
                        else:
                            transition = "recover"
                    if transition is None and payload is None:
                        # Authenticate the complete validated record, including its
                        # historical effort declaration. Domain separation prevents
                        # a capability seal from being reused as a journal seal.
                        digest = self._record_digest_locked(
                            directory_fd, {"journal_event": event},
                        )
                        payload = (json.dumps(
                            {**event, "_journal_digest": digest},
                            separators=(",", ":"), sort_keys=True,
                        ) + "\n").encode()
                    if transition is None and (
                        self._journal_contains_event(fd, payload)
                        or self._archives_contain_event(directory_fd, payload)
                    ):
                        # A prior write may have reached the page cache before its
                        # fsync failed. Confirm the existing canonical record before
                        # allowing its pending capability to be consumed.
                        os.fsync(fd)
                        return
                    if transition is None and journal_stat.st_size >= MAX_JOURNAL_BYTES:
                        transition = "rotate"
                    elif transition is None:
                        try:
                            if journal_stat.st_size and not self._ends_with_newline(fd):
                                self._private_regular_entry_stat(
                                    directory_fd,
                                    "journal.ndjson",
                                    fd,
                                )
                                self._write_all(fd, b"\n")
                            self._private_regular_entry_stat(
                                directory_fd,
                                "journal.ndjson",
                                fd,
                            )
                            self._write_all(fd, payload)
                            self._private_regular_entry_stat(
                                directory_fd,
                                "journal.ndjson",
                                fd,
                            )
                            os.fsync(fd)
                        except OSError:
                            # Never truncate or rewrite completed records. Delimit a
                            # short fragment so a later valid event stays parseable.
                            try:
                                self._private_regular_entry_stat(
                                    directory_fd,
                                    "journal.ndjson",
                                    fd,
                                )
                                self._write_all(fd, b"\n")
                                os.fsync(fd)
                            except OSError:
                                pass
                            raise
                        return
            finally:
                try:
                    if locked:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            # A recovery or ordinary rotation always takes the retention lock
            # after the append fd has been unlocked and closed.  _rotate itself
            # uses the sole retention -> journal lock order.
            if transition in ("recover", "rotate"):
                self._rotate(directory_fd)
            if transition in ("recover", "rotate", "retry"):
                continue
            raise OSError("invalid telemetry journal transition")
        raise OSError("telemetry journal append retry limit exceeded")

    def _archives_contain_event(self, directory_fd: int, payload: bytes) -> bool:
        """Check retained records while the current journal lock prevents rotation.

        Never acquire the retention lock here: its lock order is retention then
        journal. Open archives without following links and revalidate their entry
        before acknowledging durability, just as for the active journal.
        """
        flags = os.O_RDWR | os.O_NONBLOCK
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        for name in os.listdir(directory_fd):
            if self._numeric_archive_id(name) is None:
                continue
            try:
                fd = os.open(name, flags, dir_fd=directory_fd)
            except FileNotFoundError:
                continue
            try:
                self._private_regular_entry_stat(directory_fd, name, fd)
                if self._journal_contains_event(fd, payload):
                    os.fsync(fd)
                    self._private_regular_entry_stat(directory_fd, name, fd)
                    return True
            finally:
                os.close(fd)
        return False

    @staticmethod
    def _journal_contains_event(fd: int, payload: bytes) -> bool:
        """Recognize an already-complete canonical event after an uncertain fsync."""
        target = payload.rstrip(b"\n")
        os.lseek(fd, 0, os.SEEK_SET)
        chunks = []
        while True:
            chunk = os.read(fd, 8192)
            if not chunk:
                break
            chunks.append(chunk)
        return any(
            hmac.compare_digest(line, target)
            for line in b"".join(chunks).splitlines()
        )

    @staticmethod
    def _write_all(fd: int, payload: bytes) -> None:
        offset = 0
        while offset < len(payload):
            written = os.write(fd, payload[offset:])
            if written <= 0:
                raise OSError("incomplete telemetry append")
            offset += written

    @staticmethod
    def _ends_with_newline(fd: int) -> bool:
        return os.pread(fd, 1, os.fstat(fd).st_size - 1) == b"\n"

    def _rotate(self, directory_fd: int) -> None:
        flags = os.O_RDWR | os.O_NONBLOCK
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        lock_fd = None
        for _attempt in range(8):
            try:
                lock_fd = os.open(".retention.lock", flags, dir_fd=directory_fd)
                break
            except FileNotFoundError:
                try:
                    lock_fd = os.open(
                        ".retention.lock", flags | os.O_CREAT | os.O_EXCL,
                        0o600, dir_fd=directory_fd,
                    )
                    break
                except FileExistsError:
                    continue
        if lock_fd is None:
            raise OSError("retention lock initialization race")
        locked = False
        try:
            self._private_regular_entry_stat(
                directory_fd,
                ".retention.lock",
                lock_fd,
            )
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            locked = True
            self._private_regular_entry_stat(
                directory_fd,
                ".retention.lock",
                lock_fd,
            )
            journal_fd = None
            try:
                journal_fd = os.open("journal.ndjson", flags, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            if journal_fd is not None:
                journal_locked = False
                try:
                    recovery_archive = None
                    try:
                        self._private_regular_entry_stat(
                            directory_fd,
                            "journal.ndjson",
                            journal_fd,
                        )
                    except OSError as validation_error:
                        try:
                            recovery_archive = self._interrupted_rotation_archive(
                                directory_fd,
                                journal_fd,
                            )
                        except OSError:
                            raise validation_error
                    fcntl.flock(journal_fd, fcntl.LOCK_EX)
                    journal_locked = True
                    if recovery_archive is not None:
                        confirmed_archive = self._interrupted_rotation_archive(
                            directory_fd,
                            journal_fd,
                        )
                        if confirmed_archive != recovery_archive:
                            raise OSError("unstable telemetry journal recovery")
                        os.unlink("journal.ndjson", dir_fd=directory_fd)
                        self._private_regular_entry_stat(
                            directory_fd,
                            recovery_archive,
                            journal_fd,
                        )
                        os.fsync(directory_fd)
                    else:
                        journal_stat = self._private_regular_entry_stat(
                            directory_fd,
                            "journal.ndjson",
                            journal_fd,
                        )
                    if (
                        recovery_archive is None
                        and journal_stat.st_size >= MAX_JOURNAL_BYTES
                    ):
                        observed_ns = time.time_ns()
                        archive_ids = [
                            archive_id
                            for name in os.listdir(directory_fd)
                            if (archive_id := self._numeric_archive_id(name)) is not None
                        ]
                        archive_id = max([observed_ns, *archive_ids]) + 1
                        archive_name = None
                        for _attempt in range(8):
                            candidate = f"journal.{archive_id}.ndjson"
                            try:
                                os.link(
                                    "journal.ndjson",
                                    candidate,
                                    src_dir_fd=directory_fd,
                                    dst_dir_fd=directory_fd,
                                    follow_symlinks=False,
                                )
                            except FileExistsError:
                                current_ids = [
                                    current_id
                                    for name in os.listdir(directory_fd)
                                    if (
                                        current_id := self._numeric_archive_id(name)
                                    ) is not None
                                ]
                                archive_id = max([archive_id, observed_ns, *current_ids]) + 1
                                continue
                            archive_name = candidate
                            break
                        if archive_name is None:
                            raise OSError("journal archive publication race")
                        self._private_regular_entry_stat(
                            directory_fd,
                            archive_name,
                            journal_fd,
                            link_count=2,
                        )
                        self._private_regular_entry_stat(
                            directory_fd,
                            "journal.ndjson",
                            journal_fd,
                            link_count=2,
                        )
                        os.fsync(directory_fd)
                        os.unlink("journal.ndjson", dir_fd=directory_fd)
                        self._private_regular_entry_stat(
                            directory_fd,
                            archive_name,
                            journal_fd,
                        )
                        os.fsync(directory_fd)
                finally:
                    if journal_locked:
                        fcntl.flock(journal_fd, fcntl.LOCK_UN)
                    os.close(journal_fd)
            retained = []
            for name in os.listdir(directory_fd):
                archive_id = self._numeric_archive_id(name)
                if archive_id is None:
                    continue
                entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if stat.S_ISREG(entry.st_mode):
                    retained.append((archive_id, name))
            excess = max(0, len(retained) - max(0, RETENTION_FILES))
            for _archive_id, name in sorted(retained)[:excess]:
                os.unlink(name, dir_fd=directory_fd)
            if excess:
                os.fsync(directory_fd)
        finally:
            if locked:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)


def export_aggregates(
    data_dir: str | os.PathLike, *, effort_scopes=None, project_models=(),
) -> dict:
    """Return sanitized aggregates only; malformed journal lines are ignored."""
    counts: Counter[tuple[str, ...]] = Counter()
    outcomes: Counter[str] = Counter()
    empty = {"schema_version": SCHEMA_VERSION, "invocations": [], "outcomes": {}}
    flags = os.O_RDONLY | os.O_NONBLOCK
    directory_flags = flags
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
        directory_flags |= os.O_NOFOLLOW
    root_fd = None
    directory_fd = None
    try:
        root_fd = os.open(Path(data_dir).expanduser(), directory_flags)
        directory_fd = os.open("telemetry", directory_flags, dir_fd=root_fd)
        if not stat.S_ISDIR(os.fstat(directory_fd).st_mode):
            return empty
        for name in sorted(os.listdir(directory_fd)):
            if name != "journal.ndjson" and not (
                name.startswith("journal.")
                and name.endswith(".ndjson")
                and name[8:-7].isdigit()
            ):
                continue
            journal_fd = None
            try:
                journal_fd = os.open(name, flags, dir_fd=directory_fd)
                journal_stat = TelemetryObserver._private_regular_entry_stat(
                    directory_fd,
                    name,
                    journal_fd,
                )
                file_counts: Counter[tuple[str, ...]] = Counter()
                file_outcomes: Counter[str] = Counter()
                with os.fdopen(journal_fd, encoding="utf-8") as handle:
                    journal_fd = None
                    for line in handle:
                        try:
                            raw = json.loads(line)
                            if not isinstance(raw, dict):
                                continue
                            if "_journal_digest" not in raw:
                                # Pre-seal v1 records had no persisted scope.
                                # They remain importable only through the current
                                # closed vocabulary; a stripped sealed record has
                                # effort_scope and is therefore never legacy.
                                if "effort_scope" in raw:
                                    continue
                                # ``unknown`` was the sanctioned v1 projection for
                                # an unrecognised model before journal scopes were
                                # sealed. Keep that one literal readable; it is not
                                # an open model declaration and a stripped sealed
                                # record is rejected above.
                                event = validate_event(
                                    raw,
                                    effort_scopes,
                                    frozenset(project_models) | {"unknown"},
                                )
                            else:
                                digest = raw.pop("_journal_digest")
                                if (not isinstance(digest, str) or len(digest) != 64
                                        or any(c not in "0123456789abcdef" for c in digest)):
                                    continue
                                # Export is strictly read-only: a missing or unsafe key
                                # cannot be recreated to authorize old data.
                                secret = TelemetryObserver()._private_secret_locked(
                                    directory_fd, ".capabilities.key", "", create=False,
                                )
                                payload = json.dumps(
                                    {"journal_event": raw},
                                    separators=(",", ":"), sort_keys=True,
                                ).encode()
                                expected = hmac.new(secret, payload, hashlib.sha256).hexdigest()
                                if not hmac.compare_digest(digest, expected):
                                    continue
                                event = validate_event(
                                    raw, effort_scopes, project_models,
                                    allow_recorded_scope=True,
                                )
                        except (json.JSONDecodeError, TelemetryValidationError, OSError):
                            continue
                        if event["event"] == "invocation_completed":
                            scope = event["effort_scope"]
                            # A signed, grammar-validated scope is historical
                            # evidence.  It must survive later configuration
                            # removal, and its level order is part of the scope
                            # identity so reused versions cannot be blended.
                            key = (event["host"], event["kind"], event["role"] or "unknown", event["tier"] or "unknown", event["model"], scope["family"], str(scope["version"]), ",".join(scope["levels"]), event["effort"] or "unknown", event["status"], event["failure_class"])
                            file_counts[key] += 1
                        else:
                            file_outcomes[event["review_state"]] += 1
                    final_stat = TelemetryObserver._private_regular_entry_stat(
                        directory_fd,
                        name,
                        handle.fileno(),
                    )
                    if (
                        final_stat.st_size,
                        final_stat.st_mtime_ns,
                        final_stat.st_ctime_ns,
                    ) != (
                        journal_stat.st_size,
                        journal_stat.st_mtime_ns,
                        journal_stat.st_ctime_ns,
                    ):
                        raise OSError("unstable telemetry journal")
                counts.update(file_counts)
                outcomes.update(file_outcomes)
            except (OSError, UnicodeError):
                continue
            finally:
                if journal_fd is not None:
                    os.close(journal_fd)
    except OSError:
        return empty
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
        if root_fd is not None:
            os.close(root_fd)
    return {"schema_version": SCHEMA_VERSION, "invocations": [
        {"host": key[0], "kind": key[1], "role": key[2], "tier": key[3], "model": key[4], "model_family": key[5], "policy_version": int(key[6]), "policy_levels": key[7].split(","), "effort": key[8], "status": key[9], "failure_class": key[10], "count": count}
        for key, count in sorted(counts.items())
    ], "outcomes": dict(sorted(outcomes.items()))}


def aggregate_outcome(
    *, tests_state: str = "unknown", tests_passed: int | None = None,
    tests_failed: int | None = None, tests_skipped: int | None = None,
    correction_cycles: int | None = None, review_state: str = "unknown",
    severities: Mapping[str, int | None] | None = None,
    violations: Mapping[str, int | None] | None = None,
) -> dict:
    """Build the closed aggregate outcome from values already known by a caller."""
    def metric(value: int | None) -> dict:
        return unknown_metric() if value is None else {"value": value, "provenance": "observed"}

    severities = {} if severities is None else severities
    violations = {} if violations is None else violations
    return {
        "tests": {
            "state": tests_state,
            "passed": metric(tests_passed),
            "failed": metric(tests_failed),
            "skipped": metric(tests_skipped),
        },
        "correction_cycles": metric(correction_cycles),
        "review_state": review_state,
        "severities": {name: metric(severities.get(name)) for name in SEVERITIES},
        "violations": {name: metric(violations.get(name)) for name in SEVERITIES},
    }


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("non-negative integer expected") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("non-negative integer expected")
    return parsed


class _TelemetryArgumentParser(argparse.ArgumentParser):
    """Reject malformed CLI input without reflecting caller-controlled values."""

    def error(self, _message: str) -> None:
        print("invalid telemetry arguments", file=sys.stderr)
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> None:
    parser = _TelemetryArgumentParser(
        description="Export Foundry's local sanitized telemetry aggregates",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("export", help="emit aggregates only; never raw journal entries")
    complete = sub.add_parser(
        "complete", help="consume a post-spawn capability after the invocation returns",
    )
    complete.add_argument("--capability", required=True)
    complete.add_argument("--status", choices=STATUSES, required=True)
    complete.add_argument("--failure-class", choices=FAILURE_CLASSES, required=True)
    outcome = sub.add_parser(
        "outcome", help="consume a completion capability with closed aggregate results",
    )
    outcome.add_argument("--capability", required=True)
    outcome.add_argument("--tests-state", choices=TEST_STATES, default="unknown")
    outcome.add_argument("--tests-passed", type=_non_negative_int)
    outcome.add_argument("--tests-failed", type=_non_negative_int)
    outcome.add_argument("--tests-skipped", type=_non_negative_int)
    outcome.add_argument("--correction-cycles", type=_non_negative_int)
    outcome.add_argument("--review-state", choices=REVIEW_STATES, default="unknown")
    for group in ("severity", "violation"):
        for name in SEVERITIES:
            outcome.add_argument(f"--{group}-{name}", type=_non_negative_int)
    args = parser.parse_args(argv)
    if args.command == "export":
        data_dir = os.environ.get("FOUNDRY_DATA")
        if not data_dir:
            print(json.dumps({"schema_version": SCHEMA_VERSION, "invocations": [], "outcomes": {}}))
            return
        from foundry.routing import RoutingPolicy

        policy = RoutingPolicy.load()
        print(json.dumps(export_aggregates(
            data_dir,
            effort_scopes=policy.effort_scopes,
            project_models=policy.project_models,
        ), sort_keys=True))
        return
    observer = TelemetryObserver.from_environ()
    if args.command == "complete":
        from foundry.routing_facades import codex_invocation_completed

        try:
            validate_completion_classification(args.status, args.failure_class)
        except TelemetryValidationError:
            parser.error("invalid completion classification")
        run = codex_invocation_completed(
            observer, args.capability, status=args.status, failure_class=args.failure_class,
        )
        result = {"observed": run is not None}
        if run is not None:
            result["outcome_capability"] = run.capability
        print(json.dumps(result, sort_keys=True))
        return
    payload = aggregate_outcome(
        tests_state=args.tests_state,
        tests_passed=args.tests_passed,
        tests_failed=args.tests_failed,
        tests_skipped=args.tests_skipped,
        correction_cycles=args.correction_cycles,
        review_state=args.review_state,
        severities={name: getattr(args, f"severity_{name}") for name in SEVERITIES},
        violations={name: getattr(args, f"violation_{name}") for name in SEVERITIES},
    )
    print(json.dumps({
        "observed": observer.run_outcome_capability(args.capability, **payload),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
