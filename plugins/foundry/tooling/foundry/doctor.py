"""Health-check the wiring (read-only): config, routing, tracker, and code-host.

CLI: python3 -m foundry.doctor
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess

import foundry
from foundry import config, local_scout, registry, write
from foundry.escalation import EscalationStore
from foundry.routing import (
    HOSTS,
    ROLE_DEFAULTS,
    RoutingConfigError,
    RoutingUnavailableError,
    host_override_warnings,
)
from foundry.routing_facades import (
    _load_claude_policy,
    claude_available_models,
    codex_available_models,
    detect_claude_overrides,
    detect_codex_overrides,
)


def check(label, ok, detail=""):
    print(f"  {'🟢' if ok else '🔴'} {label}" + (f" — {detail}" if detail else ""))
    return ok


def local_hook_diagnostic(root=None, *, runner=subprocess.run):
    """Inspect the effective local Git hook path without changing Git config."""
    repository = os.fspath(root if root is not None else os.getcwd())
    command = ["git", "-C", repository, "config", "--get", "core.hooksPath"]
    try:
        result = runner(command, capture_output=True, text=True, check=False)
    except (FileNotFoundError, OSError):
        return {
            "ok": False,
            "status": "error",
            "detail": "git indisponible ou lecture de core.hooksPath impossible",
            "command": command,
        }

    stdout = result.stdout or ""
    # git config writes one line terminator.  Preserve every character that is
    # part of the configured value: surrounding spaces and tabs make a hook
    # path different, so they must not be normalized away.
    if stdout.endswith("\r\n"):
        value = stdout[:-2]
    elif stdout.endswith("\n"):
        value = stdout[:-1]
    else:
        value = stdout

    if result.returncode not in (0, 1) or (result.returncode == 1 and value):
        return {
            "ok": False,
            "status": "error",
            "detail": "lecture de core.hooksPath impossible",
            "command": command,
        }
    if result.returncode == 0 and value == ".githooks":
        return {
            "ok": True,
            "status": "healthy",
            "detail": "core.hooksPath=.githooks",
            "command": command,
        }
    if result.returncode == 0 and value:
        detail = f"core.hooksPath={value!r}, attendu '.githooks'"
    else:
        detail = "core.hooksPath absent, vide ou illisible"
    return {
        "ok": False,
        "status": "warning",
        "detail": detail,
        "command": command,
    }


def _print_local_hook(payload):
    if payload["status"] == "healthy":
        check("Hook local .githooks/pre-push", True, payload["detail"])
    elif payload["status"] == "warning":
        print(
            "  🟠 Hook local .githooks/pre-push"
            f" — {payload['detail']} ; active-le avec : git config core.hooksPath .githooks"
        )
    else:
        check("Hook local .githooks/pre-push", False, payload["detail"])
    print(
        "  🟠 Garde-fou local — fail-open opérationnel ; "
        "il ne constitue pas une frontière de sécurité",
    )


def local_scout_diagnostic(root=None, *, environ=None, transport_probe=None):
    """Diagnose optional local-scout configuration without completing a model.

    ``configured`` means a valid enabled policy was resolved. ``available`` and
    ``unavailable`` are emitted only when a caller supplies a bounded, transport-only
    probe. This pure helper makes no connection without one; the doctor CLI supplies
    the TCP-only probe explicitly.
    """
    try:
        settings = local_scout.load_local_scout_settings(root, environ=environ)
    except local_scout.LocalScoutError as exc:
        return {"status": "invalid policy", "ok": False, "detail": exc.code}
    if not settings.enabled:
        return {"status": "disabled", "ok": True, "detail": "opt-in local scout disabled"}
    if transport_probe is None:
        return {"status": "configured", "ok": True, "detail": "configuration valid; transport not probed"}
    try:
        available = bool(transport_probe(settings.endpoint, settings.limits.connection_timeout_seconds))
    except Exception:
        available = False
    return {
        "status": "available" if available else "unavailable",
        "ok": available,
        "detail": "bounded transport probe only",
    }


def local_scout_transport_probe(endpoint, timeout):
    """Bounded TCP reachability only: no HTTP bytes, completion or runtime control."""
    with socket.create_connection((endpoint.host, endpoint.port), timeout=timeout):
        return True


def _print_local_scout(payload):
    check(f"Local scout · {payload['status']}", payload["ok"], payload["detail"])


def _provider_transport_preflight() -> tuple[str, str | None]:
    """Resolve only public provider coordinates before any broad config read."""
    tracker_name = config.tracker_name()
    if tracker_name == "devhub":
        from foundry.trackers.devhub import validate_base_url

        devhub_url = config.require_public("DEVHUB_URL").rstrip("/")
        validate_base_url(devhub_url)
        return tracker_name, devhub_url
    if tracker_name not in {"youtrack", "ghprojects", "linear"}:
        raise SystemExit(f"Tracker inconnu : {tracker_name}")
    return tracker_name, None


def routing_diagnostics(root=None, *, issue=None, environ=None, codex_profile=None):
    """Return the shared routing diagnosis using host observations only.

    Deliberately excludes raw host values: façades provide override *names* and
    availability sets only.  This function has no tracker dependency.
    """
    environ = os.environ if environ is None else environ
    observations = {}
    for host, override_detector, availability_detector, detector_input in (
        ("claude", detect_claude_overrides, claude_available_models, environ),
        ("codex", detect_codex_overrides, codex_available_models, codex_profile),
    ):
        overrides = list(override_detector(detector_input))
        try:
            # Availability is the sole host-specific fallback observation.
            availability = availability_detector(environ)
            error = None
        except RoutingConfigError as exc:
            availability = None
            error = str(exc)
        observations[host] = {
            "overrides": overrides, "available_models": availability,
            "observation_error": error,
        }
    hosts = {
        host: {"overrides": item["overrides"], "availability_probed": item["available_models"] is not None, "routes": []}
        for host, item in observations.items()
    }

    def override_warnings(host):
        return [
            {"code": warning.code, "message": warning.message}
            for warning in host_override_warnings(host, observations[host]["overrides"])
        ]

    try:
        # Doctor must not attest a Claude route that the host facade will
        # reject for missing translation or AgentDefinition profile.
        policy = _load_claude_policy(root)
    except RoutingConfigError as exc:
        # Preserve the full host/role matrix even when project policy cannot be
        # resolved; defaults must not become a silent bypass for invalid JSON.
        for host in HOSTS:
            for role in ROLE_DEFAULTS:
                hosts[host]["routes"].append({
                    "role": role, "ok": False, "code": "ROUTING_CONFIG_INVALID",
                    "detail": str(exc),
                    "warnings": override_warnings(host),
                })
        return {
            "config": {"valid": False, "detail": str(exc)},
            "hosts": hosts,
            **_issue_diagnostic(root, issue),
        }

    for host in HOSTS:
        observation = observations[host]
        if observation["observation_error"]:
            warnings = override_warnings(host)
            for role in ROLE_DEFAULTS:
                hosts[host]["routes"].append({
                    "role": role, "ok": False, "code": "HOST_OBSERVATION_INVALID",
                    "detail": observation["observation_error"],
                    "warnings": warnings,
                })
            continue
        for role in ROLE_DEFAULTS:
            try:
                route = policy.resolve(
                    role, host,
                    available_models=observation["available_models"],
                    active_host_overrides=observation["overrides"],
                ).to_dict()
                hosts[host]["routes"].append({"ok": True, **route})
            except RoutingUnavailableError as exc:
                hosts[host]["routes"].append({
                    "role": role, "ok": False, "code": "ROUTING_UNAVAILABLE",
                    "detail": str(exc),
                    "warnings": override_warnings(host),
                })
            except RoutingConfigError as exc:
                hosts[host]["routes"].append({
                    "role": role, "ok": False, "code": "ROUTING_INVALID",
                    "detail": str(exc),
                    "warnings": override_warnings(host),
                })
    return {
        "config": {
            "valid": True,
            "path": str(policy.config_path),
            "source": "project" if policy.has_project_config else "defaults",
        },
        "hosts": hosts,
        **_issue_diagnostic(root, issue),
    }


def _issue_diagnostic(root, issue):
    if issue is None:
        return {}
    status = EscalationStore.for_root(root).status(issue)
    return {"issue": {
        "issue_id": status["issue_id"],
        "halted": status["halted"],
        "halt_generation": status["halt_generation"],
        "total_escalations": status["total_escalations"],
    }}


def _print_routing(payload):
    config_status = payload["config"]
    check(
        "Routage model-aware · .foundry/model-routing.json",
        config_status["valid"],
        config_status.get("detail") or config_status.get("source", ""),
    )
    for host, value in payload["hosts"].items():
        warnings = []
        seen_warnings = set()
        for route in value["routes"]:
            for warning in route.get("warnings", []):
                identity = (warning["code"], warning["message"])
                if identity not in seen_warnings:
                    seen_warnings.add(identity)
                    warnings.append(warning)
        policy_neutralized = any(
            warning["code"] == "HOST_OVERRIDE_NEUTRALIZES_POLICY"
            for warning in warnings
        )
        overrides = ", ".join(value["overrides"]) or "aucun"
        probe = "sondée" if value["availability_probed"] else "non sondée"
        check(
            f"  {host} · overrides connus={overrides}",
            not policy_neutralized,
            f"disponibilité {probe}",
        )
        for route in value["routes"]:
            if route["ok"]:
                floor = route.get("gate_floor") or "—"
                effort_floor = route.get("gate_effort_floor") or "—"
                check(
                    f"    {route['role']}", True,
                    f"{route['selected_tier']} → {route['model']} ({route['effort']}) · floors tier={floor}, effort={effort_floor}",
                )
            else:
                check(f"    {route['role']} · {route['code']}", False, route["detail"])
        for warning in warnings:
            print(f"  🟠 {warning['code']} — {warning['message']}")
    if "issue" in payload:
        issue = payload["issue"]
        state = "arrêtée" if issue["halted"] else "active"
        check(
            f"Escalade {issue['issue_id']}", not issue["halted"],
            f"{state} · génération={issue['halt_generation']} · escalades={issue['total_escalations']}",
        )
    check(
        "Hooks de routage", True,
        "fail-open opérationnel ; ils ne constituent pas une frontière de sécurité",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Health-check Foundry wiring without mutations")
    parser.add_argument("--issue", help="optional issue id: inspect escalation state read-only")
    parser.add_argument(
        "--profile-model-active", action="store_true",
        help="Codex effective profile reports a model override (presence only)",
    )
    parser.add_argument(
        "--profile-effort-active", action="store_true",
        help="Codex effective profile reports a reasoning-effort override (presence only)",
    )
    args = parser.parse_args(argv)
    print("🩺 Foundry doctor\n")

    # Public provider discovery and DevHub transport validation are the first
    # configuration reads. In particular this precedes local-scout's compatible
    # bulk config fallback and every provider credential accessor.
    try:
        tracker_name, _validated_devhub_url = _provider_transport_preflight()
    except (SystemExit, ValueError) as e:
        check("Config", False, str(e))
        return

    # This local read comes before routing and network integrations.
    _print_local_hook(local_hook_diagnostic())
    _print_local_scout(local_scout_diagnostic(transport_probe=local_scout_transport_probe))

    # Kept before integrations: routing is locally calculable even when a tracker is down.
    # The CLI accepts presence bits, never a profile value.  This preserves the
    # façade's name-only observation contract and prevents value disclosure.
    codex_profile = {}
    if args.profile_model_active:
        codex_profile["model"] = True
    if args.profile_effort_active:
        codex_profile["model_reasoning_effort"] = True
    _print_routing(routing_diagnostics(issue=args.issue, codex_profile=codex_profile))

    # provider-specific config, selected without exposing credential values
    try:
        if tracker_name == "youtrack":
            config.require("YOUTRACK_URL")
            config.require("YOUTRACK_TOKEN")
            endpoint = "endpoint configured"
        elif tracker_name == "devhub":
            config.require("DEVHUB_TRACKER_TOKEN")
            config.require("DEVHUB_TRACKER_PROOF_SECRET")
            endpoint = "endpoint configured"
        elif tracker_name == "ghprojects":
            endpoint = "provider stub"
        elif tracker_name == "linear":
            config.require("LINEAR_API_TOKEN")
            endpoint = "official GraphQL endpoint"
        else:
            raise SystemExit(f"Tracker inconnu : {tracker_name}")
        check("Config", True, f"tracker={tracker_name} codehost={config.codehost_name()} · {endpoint}")
    except (SystemExit, ValueError) as e:
        check("Config", False, str(e))
        return

    # tracker auth + registered projects
    try:
        tr = foundry.tracker()
        reg = registry.load().get(tr.name, {})
        projects = {}
        for repo, entry in reg.items():
            projects.setdefault((entry["key"], entry["id"]), []).append((repo, entry))
        check(
            f"Tracker {tr.name}", True,
            f"{len(reg)} binding(s) repo · {len(projects)} projet(s) distinct(s)",
        )
        for (key, project_id), aliases in projects.items():
            repos = ", ".join(repo for repo, _entry in aliases)
            e = aliases[0][1]
            try:
                from foundry.models import Project
                extra = {k: v for k, v in e.items() if k not in ("key", "id")}
                n = len(tr.search(Project(key=key, id=project_id, extra=extra)))
                check(f"  {key} ({repos})", True, f"{n} issues")
            except Exception as ex:
                check(f"  {key} ({repos})", False, str(ex)[:70])
    except Exception as e:
        check("Tracker", False, str(e)[:80])

    # code-host
    try:
        ch = foundry.codehost()
        repo = ch.resolve_repo()
        check(f"Code-host {ch.name}", True, f"repo courant → {repo}")
    except Exception as e:
        check("Code-host / repo courant", False, str(e)[:80])

    # current repo → tracker project
    try:
        tr = foundry.tracker()
        base = registry.repo_basename()
        p = write.mutation_project(tr) if getattr(
            tr, "requires_mutation_binding", False
        ) else tr.resolve_project(base)
        check("Repo courant → projet", True, f"{base} → {p.key}")
    except (SystemExit, Exception) as e:
        check("Repo courant → projet", False, str(e)[:90])


if __name__ == "__main__":
    main()
