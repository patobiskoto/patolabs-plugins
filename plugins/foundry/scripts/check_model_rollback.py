"""PAT-17 configuration exercise; constructs plans, never invokes a native host."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "tooling"))
from foundry.routing import ReviewDeduplicator, RoutingPolicy, ROLE_DEFAULTS  # noqa: E402
from foundry.routing_facades import (  # noqa: E402
    claude_invocation_binding, claude_route_plan, codex_spawn_plan,
)

ROLES = ("scout", "implementer", "reviewer", "architect")
PACKET = "Goal:\nCheck configuration rollback.\nInputs:\nDisposable policy.\nConstraints:\nDo not delegate.\nDone when:\nReport the resolved profile."
EXAMPLES = {
    "codex": "codex-gpt-5.6-rollback.json",
    "claude": "claude-pre-promotion-rollback.json",
}


def exercise(host, role):
    """Merge one incumbent tier on a copy, construct its plan, restore exact bytes."""
    with tempfile.TemporaryDirectory(prefix="foundry-model-rollback-") as scratch:
        root = Path(scratch)
        config = root / ".foundry" / "model-routing.json"
        config.parent.mkdir()
        # Deliberately keep unrelated keys and the other host's mapping in the copy.
        other = "claude" if host == "codex" else "codex"
        seed = {"version": 1, "roles": {"coordinator": "balanced"}, "mappings": {
            other: {"balanced": {"model": "sonnet-5.5" if other == "claude" else "gpt-6.1-sol",
                                  "effort": "medium"}},
        }}
        config.write_text(json.dumps(seed, indent=2) + "\n", encoding="utf-8")
        before = config.read_bytes()
        current = RoutingPolicy.load(root).resolve(role, host)
        incumbent = json.loads((PLUGIN / "examples" / EXAMPLES[host]).read_text())
        tier = ROLE_DEFAULTS[role]
        target = incumbent["mappings"][host][tier]
        merged = json.loads(before)
        merged["mappings"].setdefault(host, {})[tier] = target
        config.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
        assert merged["roles"] == seed["roles"]
        assert merged["mappings"][other] == seed["mappings"][other]
        env = {key: value for key, value in os.environ.items() if not key.startswith(("FOUNDRY_", "CLAUDE_"))}
        # This is a configuration-only subprocess, not a Claude OAuth session.
        env["HOME"] = str(root)
        env["PYTHONPATH"] = str(PLUGIN / "tooling")
        command = [sys.executable, "-m", "foundry.routing", "resolve", role,
                   "--host", host, "--root", str(root)]
        result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, check=True)
        resolved = json.loads(result.stdout)["routes"][0]
        assert (resolved["model"], resolved["effort"]) == (target["model"], target["effort"])
        if host == "claude":
            plan = claude_route_plan(role, PACKET, root=root, environ={})
            invocation = claude_invocation_binding(
                plan["route"], "worker" if role == "implementer" else "readonly",
                plugin_root=PLUGIN, project_models=plan["claude_models"],
            )
        else:
            claim = None
            if role == "reviewer":
                # A fixture claim constructs a plan; this is not a review of product code.
                claim = {**ReviewDeduplicator("fixture/repo", root / "state").claim(
                    b"rollback configuration fixture",
                ).to_dict(), "root": str(root), "base": "a" * 40}
            invocation = codex_spawn_plan(
                role, PACKET, root=root, review_claim=claim, _observe=False,
            )["spawn"]
        config.write_bytes(before)
        assert config.read_bytes() == before
        restored = RoutingPolicy.load(root).resolve(role, host)
        assert restored == current
        # An absent original policy must be restored to absence, not to an empty file.
        config.unlink()
        absent = RoutingPolicy.load(root).resolve(role, host)
        config.write_text(json.dumps(incumbent), encoding="utf-8")
        assert RoutingPolicy.load(root).resolve(role, host).model == target["model"]
        config.unlink()
        assert not config.exists()
        assert RoutingPolicy.load(root).resolve(role, host) == absent
        return {
            "host": host, "role": role, "tier": tier,
            "current": {"model": current.model, "effort": current.effort},
            "rollback": target, "cli_resolve": "passed",
            "constructed_invocation": {key: invocation[key] for key in (
                ("profile", "agent_model", "transmitted_model", "effort_parameters")
                if host == "claude" else ("model", "reasoning_effort", "agent_type", "fork_turns")
            )},
            "unrelated_configuration_preserved": True,
            "existing_policy_restored_byte_exact": True,
            "absent_policy_restored_to_absence": True,
            "native_host_called": False, "executed_identity": None,
            "backend_availability": "unknown",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = {"schema": "foundry.pat17-config-rollback.v1", "date": datetime.now(timezone.utc).date().isoformat(),
              "source_head": subprocess.run(
                  ["git", "rev-parse", "HEAD"], cwd=PLUGIN, capture_output=True,
                  text=True, check=True,
              ).stdout.strip(),
              "evidence_kind": "configuration_only", "native_calls": 0,
              "rows": [exercise(host, role) for host in EXAMPLES for role in ROLES]}
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{len(report['rows'])} configuration rollbacks passed; native calls: 0")


if __name__ == "__main__":
    main()
