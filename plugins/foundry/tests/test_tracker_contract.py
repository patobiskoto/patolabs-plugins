"""PAT-53: pin the tracker contract v1 capability matrix.

This is a deterministic, framing-level pin — not the PAT-68 conformance suite. It
guarantees that ``tracker-contract.v1.json`` stays internally consistent (closed status
vocabulary, every cited operation really exists on the ``Tracker`` ABC, every cited
provider really has an adapter module) and that the doc references the same version.
Beyond schema consistency, it also pins the AC-1 core/non-core asymmetry (no core
operation cell may be ``refused``; no non-core cell may be a blocking ``gap``), the
owner ticket on every blocking core cell, the cells gated on the pending no-CAS ADR,
and a handful of cheap, code-tied assertions (capability flags, missing method
overrides, and the implemented/refused ghprojects boundaries). Only those
few cells are tied to code; the other cells' evidence is checked by review, not here.
"""
import importlib
import inspect
import json
import re
from pathlib import Path

import pytest


DOCS = Path(__file__).resolve().parents[1] / "docs"
CONTRACT_PATH = DOCS / "tracker-contract.v1.json"
DOC_PATH = DOCS / "tracker-contract.md"

_STATUS_VOCABULARY = {"supported", "gap", "refused", "to_qualify"}
_GAP_TICKET_RE = re.compile(r"^PAT-\d+$")
_BLOCKING_STATUSES = {"gap", "to_qualify"}


def _load_contract():
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def test_contract_json_is_versioned_and_names_its_doc():
    contract = _load_contract()
    assert contract["contract"] == "foundry.tracker-contract.v1"
    assert contract["version"] == 3
    assert contract["doc"] == "plugins/foundry/docs/tracker-contract.md"


def test_doc_references_the_same_contract_version():
    doc = DOC_PATH.read_text(encoding="utf-8")
    assert "tracker-contract.v1.json" in doc
    assert "version: 3" in doc
    assert "contract **v1**" in doc


def test_status_vocabulary_is_closed_and_declared():
    contract = _load_contract()
    declared = set(contract["status_vocabulary"])
    assert declared == _STATUS_VOCABULARY


def test_status_vocabulary_documents_core_blocking_semantics():
    """Vocabulary text must say gap/to_qualify block core V1 exit, refused never does."""
    contract = _load_contract()
    vocab = contract["status_vocabulary"]
    assert "core" in vocab["gap"] and "block" in vocab["gap"]
    assert "core" in vocab["to_qualify"] and "block" in vocab["to_qualify"]
    assert "non-core" in vocab["refused"] or "never" in vocab["refused"]


def test_every_operation_declares_a_core_flag():
    contract = _load_contract()
    for operation in contract["operations"]:
        assert isinstance(operation.get("core"), bool), (
            f"{operation['id']} is missing a boolean 'core' flag"
        )


def test_no_core_operation_cell_is_refused():
    """AC-1: a core operation's cell may never be 'refused' — that would silently
    exempt a core journey from V1's exit criterion. Unsupported core capability is
    always 'gap' or 'to_qualify', both of which block V1 for that provider."""
    contract = _load_contract()
    for operation in contract["operations"]:
        if not operation["core"]:
            continue
        for provider, cell in operation["cells"].items():
            assert cell["status"] != "refused", (
                f"{operation['id']}.{provider} is a CORE operation and cannot be "
                f"'refused' — an unsupported core capability is a 'gap' or "
                f"'to_qualify', never a deliberate optional exclusion"
            )


def test_non_core_operation_cell_is_never_a_gap():
    """A non-core (optional/excluded) operation cannot carry a blocking 'gap' cell —
    nothing optional is a 'must close before V1' item by definition."""
    contract = _load_contract()
    for operation in contract["operations"]:
        if operation["core"]:
            continue
        for provider, cell in operation["cells"].items():
            assert cell["status"] != "gap", (
                f"{operation['id']}.{provider} is not core and must not carry a "
                f"blocking 'gap' status"
            )


def test_every_cell_status_is_in_the_closed_vocabulary():
    contract = _load_contract()
    for operation in contract["operations"]:
        for provider, cell in operation["cells"].items():
            assert cell["status"] in _STATUS_VOCABULARY, (
                f"{operation['id']}.{provider} uses an undeclared status {cell['status']!r}"
            )
            assert isinstance(cell.get("evidence"), str) and cell["evidence"], (
                f"{operation['id']}.{provider} is missing a code-evidence citation"
            )


def test_every_gap_cell_names_a_pat_ticket():
    contract = _load_contract()
    for operation in contract["operations"]:
        for provider, cell in operation["cells"].items():
            if cell["status"] != "gap":
                continue
            ticket = cell.get("ticket")
            assert isinstance(ticket, str) and _GAP_TICKET_RE.match(ticket), (
                f"{operation['id']}.{provider} is a gap without a well-formed PAT-<n> ticket"
            )


def test_every_blocking_core_cell_names_an_owner_ticket():
    """AC-1: every core cell that blocks V1 exit (``gap`` or ``to_qualify``) traces to
    the ticket that owns closing it, or to PAT-65 where only qualification is known."""
    contract = _load_contract()
    for operation in contract["operations"]:
        if not operation["core"]:
            continue
        for provider, cell in operation["cells"].items():
            if cell["status"] not in _BLOCKING_STATUSES:
                continue
            ticket = cell.get("ticket")
            assert isinstance(ticket, str) and _GAP_TICKET_RE.match(ticket), (
                f"{operation['id']}.{provider} blocks V1 ({cell['status']}) without a "
                f"well-formed PAT-<n> owner ticket"
            )


def test_blocked_by_adr_references_a_declared_pending_adr():
    contract = _load_contract()
    pending = {adr["id"] for adr in contract["pending_adrs"]}
    for adr in contract["pending_adrs"]:
        assert adr["status"] == "to_create_and_accept"
        assert adr["proposed_in"] == "PAT-53"
    for operation in contract["operations"]:
        for provider, cell in operation["cells"].items():
            if "blocked_by_adr" not in cell:
                continue
            assert cell["blocked_by_adr"] in pending, (
                f"{operation['id']}.{provider} cites an undeclared pending ADR"
            )
            assert operation["core"] and cell["status"] == "gap", (
                f"{operation['id']}.{provider} is gated on an ADR but is not a core gap"
            )


def test_no_pending_adr_remains_after_pat_adr_0006_acceptance():
    assert _load_contract()["pending_adrs"] == []


def test_doc_records_the_accepted_adr_without_a_local_draft_path():
    doc = DOC_PATH.read_text(encoding="utf-8")
    assert "Garanties d'écriture sans CAS pour les trackers V1" in " ".join(doc.split())
    assert "pat53-adr-draft" not in doc
    assert "/tmp/" not in doc


def test_every_provider_listed_has_an_adapter_module():
    contract = _load_contract()
    # tests/test_tracker_contract.py -> tests -> foundry -> plugins -> repo root
    repo_root = Path(__file__).resolve().parents[3]
    for name, provider in contract["providers"].items():
        module_path = repo_root / provider["module"]
        assert module_path.is_file(), f"{name} declares a module that does not exist: {module_path}"
        module = importlib.import_module(f"foundry.trackers.{name}")
        cls = getattr(module, provider["class"])
        from foundry.trackers.base import Tracker

        assert issubclass(cls, Tracker)


def test_every_operation_maps_to_a_real_tracker_abc_member():
    """Every cited op name must resolve to a real Tracker attribute/method name.

    ``tracker_abc`` entries may carry an argument annotation (e.g.
    ``"search(query=...)"`` or ``"link(depends-on|blocks|relates)"``); only the bare
    callable name before ``(`` is checked against the ABC.
    """
    from foundry.trackers.base import Tracker

    contract = _load_contract()
    for operation in contract["operations"]:
        for raw in operation["tracker_abc"]:
            name = raw.split("(", 1)[0].strip()
            assert hasattr(Tracker, name), (
                f"{operation['id']} cites {raw!r}, but Tracker has no member {name!r}"
            )
            assert inspect.isfunction(getattr(Tracker, name)) or callable(
                getattr(Tracker, name)
            )


def test_ghprojects_cells_match_delivered_reads_writes_adrs_and_lifecycle():
    """The qualified private-project lifecycle is supported; later tranches remain owned."""
    contract = _load_contract()
    for operation in contract["operations"]:
        cell = operation["cells"].get("ghprojects")
        if cell is None:
            continue
        if operation["id"] == "full-administrative-provisioning":
            # Explicitly out-of-core-scope capability: refused, not merely unqualified.
            assert cell["status"] == "refused"
            continue
        if operation["id"] in {
            "identity-and-project-resolution", "repository-bootstrap", "backlog-read",
            "frame-intake-groom-create", "frame-intake-groom-evolve-existing",
            "mid-flight-comment", "epics-children-creation",
            "epics-children-reparent-existing", "dependencies-relates-blocks",
            "release-and-changelog-scope", "adr-read", "adr-create",
            "adr-status-evolution", "adr-supersession-and-issue-linking",
            "issue-lifecycle-transitions", "acceptance-criteria-sync",
            "epic-closure", "tombstone-archived-source-after-switch",
            "cross-tracker-adr-import", "cross-tracker-live-work-copy",
        }:
            assert cell["status"] == "supported"
            continue
        if operation["id"] in {
            "native-free-text-search", "acceptance-override-receipt",
        }:
            assert cell["status"] == "refused"
            continue
        assert cell["status"] == "to_qualify", (
            f"{operation['id']}.ghprojects must stay to_qualify under its owner ticket, "
            f"got {cell['status']!r}"
        )


def test_pat54_identity_resolution_is_supported_for_all_three_providers():
    contract = _load_contract()
    op = next(
        o for o in contract["operations"]
        if o["id"] == "identity-and-project-resolution"
    )
    assert op["core"] is True
    assert {cell["status"] for cell in op["cells"].values()} == {"supported"}


def test_repository_bootstrap_is_core_and_full_provisioning_is_not():
    """Criterion 4 excludes full administrative provisioning, not the minimal
    create-or-recover bootstrap PAT-54 owns on all three trackers."""
    contract = _load_contract()
    by_id = {o["id"]: o for o in contract["operations"]}
    bootstrap = by_id["repository-bootstrap"]
    assert bootstrap["core"] is True
    assert {c["status"] for c in bootstrap["cells"].values()} == {"supported"}
    provisioning = by_id["full-administrative-provisioning"]
    assert provisioning["core"] is False
    assert provisioning["cells"]["linear"]["status"] == "refused"
    assert provisioning["cells"]["ghprojects"]["status"] == "refused"
    assert "project-provisioning" not in by_id


def test_youtrack_provisioning_ignores_canonical_repository():
    """Full administrative provisioning remains separate from PAT-54 bootstrap."""
    from foundry.trackers.base import Tracker
    from foundry.trackers.linear import LinearTracker
    from foundry.trackers.youtrack import YouTrackTracker

    assert YouTrackTracker.project_provisioning_supported is True
    assert YouTrackTracker.project_provisioning_requires_repository is False
    assert LinearTracker.provision_project is Tracker.provision_project


def test_operations_cover_every_criterion_1_core_journey():
    """Every criterion-1 core journey marker must be covered by at least one
    operation that is itself marked core — a marker appearing only on a non-core
    operation would not actually satisfy criterion 1."""
    contract = _load_contract()
    core_journeys = " ".join(
        op["journey"] for op in contract["operations"] if op["core"]
    )
    for marker in (
        "contexte/backlog",
        "frame/intake/groom",
        "epics/enfants/dépendances",
        "ADR",
        "start/resume/review/merge",
        "état et AC",
        "clôture d'epic",
        "release/changelog",
        "PAT-64",
    ):
        assert marker in core_journeys, (
            f"no CORE operation covers the core journey marker {marker!r}"
        )


def test_linear_acceptance_projection_matches_the_supported_cell():
    from foundry.trackers.linear import LinearTracker

    assert LinearTracker.acceptance_sync_supported is False
    assert LinearTracker.acceptance_proof_projection_supported is True


def test_youtrack_epic_closure_flag_matches_the_gap_cell():
    from foundry.trackers.youtrack import YouTrackTracker

    assert YouTrackTracker.epic_closure_supported is False


def test_youtrack_migration_port_is_separate_from_historical_import_port():
    """PAT-64 uses the provider-neutral migration port, without claiming that
    YouTrack implements the older Linear-specific historical import surface."""
    from foundry.trackers.youtrack import YouTrackTracker

    assert "import_adr" not in YouTrackTracker.__dict__
    assert "import_adr_batch" not in YouTrackTracker.__dict__
    for method_name in (
        "migration_export_adrs",
        "migration_find_issue",
        "migration_import_issue",
        "migration_link_issue",
        "migration_find_adr",
        "migration_import_adr",
    ):
        assert method_name in YouTrackTracker.__dict__


def test_pat64_required_adapter_ports_are_concrete_on_all_three_providers():
    from foundry.trackers.ghprojects import GitHubProjectsTracker
    from foundry.trackers.linear import LinearTracker
    from foundry.trackers.youtrack import YouTrackTracker

    required = {
        "migration_preflight",
        "migration_export_adrs",
        "migration_find_issue",
        "migration_import_issue",
        "migration_link_issue",
        "migration_find_adr",
        "migration_import_adr",
    }
    for adapter in (YouTrackTracker, LinearTracker, GitHubProjectsTracker):
        assert required.issubset(adapter.__dict__)


def test_ghprojects_pat66_pat58_writes_and_pat67_lifecycle_are_real():
    """Delivered Issue, ADR and lifecycle ports are concrete."""
    from foundry.trackers.ghprojects import GitHubProjectsTracker

    assert "search" in GitHubProjectsTracker.__dict__
    assert "get_issue" in GitHubProjectsTracker.__dict__
    for method_name in ("create_issue", "update_fields", "update_body", "link", "add_comment"):
        assert method_name in GitHubProjectsTracker.__dict__
    assert "set_state" in GitHubProjectsTracker.__dict__
    assert "project_acceptance_proof" in GitHubProjectsTracker.__dict__
    for method_name in ("list_adrs", "create_adr", "set_adr_status"):
        assert method_name in GitHubProjectsTracker.__dict__
    assert GitHubProjectsTracker.bounded_transition_proofs is True
    assert GitHubProjectsTracker.bounded_state_transitions is True
    assert GitHubProjectsTracker.append_only_lifecycle_supported is True
    assert GitHubProjectsTracker.acceptance_proof_projection_supported is True


@pytest.mark.parametrize("operation_id", [
    "tombstone-archived-source-after-switch",
    "cross-tracker-adr-import",
    "cross-tracker-live-work-copy",
])
def test_pat64_contract_rows_are_supported_for_all_three_providers(operation_id):
    rows = {operation["id"]: operation for operation in _load_contract()["operations"]}
    cells = rows[operation_id]["cells"]

    assert set(cells) == {"youtrack", "linear", "ghprojects"}
    assert {cell["status"] for cell in cells.values()} == {"supported"}
    assert all("ticket" not in cell for cell in cells.values())


def test_youtrack_archived_target_tombstone_is_supported_with_native_preflight_evidence():
    rows = {operation["id"]: operation for operation in _load_contract()["operations"]}
    cell = rows["tombstone-archived-source-after-switch"]["cells"]["youtrack"]

    assert cell["status"] == "supported"
    assert "ticket" not in cell
    assert "project(id,shortName)" in cell["evidence"]
    assert "exact native id or exact key" in cell["evidence"]
    assert "test_youtrack_smoke.py" in cell["evidence"]


def test_non_core_to_qualify_cells_name_an_owner_and_must_resolve_before_v1():
    contract = _load_contract()
    assert "must resolve to 'supported' or to 'refused'" in contract["status_vocabulary"]["to_qualify"]
    for operation in contract["operations"]:
        if operation["core"]:
            continue
        for provider, cell in operation["cells"].items():
            if cell["status"] == "to_qualify":
                assert re.fullmatch(r"PAT-\d+", cell.get("ticket", "")), (operation["id"], provider)


def test_switch_rows_separate_adr_import_from_live_work_copy():
    rows = {operation["id"]: operation for operation in _load_contract()["operations"]}
    adr = rows["cross-tracker-adr-import"]
    assert set(adr["tracker_abc"]) == {
        "migration_export_adrs", "migration_find_adr", "migration_prepare_adr",
        "migration_qualify_adrs", "migration_import_adrs",
    }
    assert {cell["status"] for cell in adr["cells"].values()} == {"supported"}
    live = rows["cross-tracker-live-work-copy"]
    assert live["core"] is True
    assert set(live["tracker_abc"]) == {
        "migration_preflight", "migration_attribute_exceptions",
        "migration_find_issue", "migration_import_issue", "migration_link_issue",
    }
    assert {cell["status"] for cell in live["cells"].values()} == {"supported"}
    assert "cross-tracker-live-work-and-adr-copy" not in rows


def test_pending_adr_names_a_creation_owner():
    for adr in _load_contract()["pending_adrs"]:
        assert adr.get("creation_owner")
