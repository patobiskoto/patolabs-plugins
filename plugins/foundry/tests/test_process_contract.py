"""FOUNDRY-122: the process contract and documentation doctrine are versioned in the
repository, readable by both hosts from an identical single source, and cover the
concrete facts this issue requires (documentation doctrine, OAuth machine binding,
current Claude model resolution) without duplicating any ADR decision.
"""
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _repo_text(relative_path):
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


def test_claude_and_agents_contracts_exist_and_are_byte_identical():
    # FOUNDRY-ADR-0005: one implementation, two host façades, never a content fork.
    claude = _repo_text("CLAUDE.md")
    agents = _repo_text("AGENTS.md")

    assert claude == agents
    assert len(claude) > 0


def test_process_contract_states_the_pr_merge_adr_scan_and_intake_rules():
    contract = _repo_text("AGENTS.md")

    for marker in (
        "foundry:open-pr",
        "foundry:merge-pr",
        "guard_bash.py",
        "query adr",
        "foundry:intake",
        "FOUNDRY-ADR-0001",
        "FOUNDRY-ADR-0002",
    ):
        assert marker in contract


def test_process_contract_states_the_adr_0018_documentation_doctrine_without_restating_it():
    contract = _repo_text("AGENTS.md")

    assert "FOUNDRY-ADR-0018" in contract
    assert "not necessary" in contract
    assert "FOUNDRY-123" in contract  # the not-yet-shipped mechanical detector/gate
    # The doctrine's own decision text (from the ADR body) must not be copied in.
    assert "Documenté = jugé documenté" not in contract


def test_process_contract_documents_oauth_machine_binding_operational_consequence():
    contract = _repo_text("AGENTS.md")

    assert "FOUNDRY-101" in contract
    assert "_CLAUDE_CHILD_ENV_ALLOWLIST" in contract
    assert "FOUNDRY-ADR-0017" in contract
    # The operational consequence, not just the fact.
    assert "same authenticated developer machine" in contract


def test_process_contract_documents_current_claude_model_resolution_not_a_closed_table():
    contract = _repo_text("AGENTS.md")

    for marker in (
        "FOUNDRY-100",
        "FOUNDRY-125",
        "claude_invocation_model",
        "RoutingConfigError",
        "_CLAUDE_MODEL_DECLARATION",
        "project's own",
    ):
        assert marker in contract
    # The AC amendment: closure is past tense only ("opened what was ... closed"),
    # never claimed as the current behaviour.
    assert "not a hardcoded tuple" in contract
    assert "fails closed" in contract


def test_maigret_project_conventions_criterion_now_resolves_to_real_files():
    maigret = _repo_text("plugins/foundry/agents/maigret.md")

    assert "AGENTS.md" in maigret and "CLAUDE.md" in maigret
    assert (REPOSITORY_ROOT / "AGENTS.md").exists()
    assert (REPOSITORY_ROOT / "CLAUDE.md").exists()
