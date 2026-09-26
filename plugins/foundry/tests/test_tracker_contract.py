"""PAT-53: pin the tracker contract v1 capability matrix.

This is a deterministic, framing-level pin — not the PAT-68 conformance suite. It
guarantees that ``tracker-contract.v1.json`` stays internally consistent (closed status
vocabulary, every cited operation really exists on the ``Tracker`` ABC, every cited
provider really has an adapter module) and that the doc references the same version.
Beyond schema consistency, it also pins the AC-1 core/non-core asymmetry (no core
operation cell may be ``refused``; no non-core cell may be a blocking ``gap``), the
owner ticket on every blocking core cell, the cells gated on the pending no-CAS ADR,
and a handful of cheap, code-tied assertions (capability flags, missing method
overrides, the ghprojects stub actually raising ``NotImplementedError``) so a status
cell cannot drift away from the adapter code it claims to describe without failing.
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
    assert contract["version"] == 1
    assert contract["doc"] == "plugins/foundry/docs/tracker-contract.md"


def test_doc_references_the_same_contract_version():
    doc = DOC_PATH.read_text(encoding="utf-8")
    assert "tracker-contract.v1.json" in doc
    assert "version: 1" in doc
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


@pytest.mark.parametrize(
    ("operation_id", "provider", "ticket"),
    [
        ("frame-intake-groom-evolve-existing", "linear", "PAT-55"),
        ("epics-children-reparent-existing", "linear", "PAT-55"),
        ("acceptance-criteria-sync", "linear", "PAT-56"),
        ("epic-closure", "youtrack", "PAT-69"),
        ("epic-closure", "linear", "PAT-69"),
    ],
)
def test_durable_invariant_gaps_are_blocked_by_the_no_cas_adr(
    operation_id, provider, ticket,
):
    """Criterion 3: closing these gaps reverses linear.py's no-replacement invariant or
    the atomic epic-closure invariant, so each is gated on the one consolidated ADR."""
    contract = _load_contract()
    operation = next(o for o in contract["operations"] if o["id"] == operation_id)
    cell = operation["cells"][provider]
    assert cell["status"] == "gap"
    assert cell["ticket"] == ticket
    assert cell["blocked_by_adr"] == "no-cas-write-guarantees-v1"


def test_doc_does_not_point_at_an_untracked_adr_draft():
    """The pending ADR is referenced by title only, never by an out-of-repo path."""
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


def test_ghprojects_stub_cells_are_to_qualify_not_guessed():
    """PAT-65 owns real GitHub Projects v2 qualification; PAT-53 must not guess it."""
    contract = _load_contract()
    for operation in contract["operations"]:
        cell = operation["cells"].get("ghprojects")
        if cell is None:
            continue
        if operation["id"] == "full-administrative-provisioning":
            # Explicitly out-of-core-scope capability: refused, not merely unqualified.
            assert cell["status"] == "refused"
            continue
        assert cell["status"] == "to_qualify", (
            f"{operation['id']}.ghprojects must stay to_qualify (PAT-65) until qualified, "
            f"got {cell['status']!r}"
        )


def test_youtrack_identity_resolution_is_a_pat54_gap():
    """YouTrack's project resolution is basename/PROJECT_REPO-keyed, not
    canonical-identity based; it must not be pinned 'supported'."""
    contract = _load_contract()
    op = next(
        o for o in contract["operations"]
        if o["id"] == "identity-and-project-resolution"
    )
    assert op["core"] is True
    assert op["cells"]["youtrack"]["status"] == "gap"
    assert op["cells"]["youtrack"]["ticket"] == "PAT-54"
    assert op["cells"]["linear"]["status"] == "supported"


def test_repository_bootstrap_is_core_and_full_provisioning_is_not():
    """Criterion 4 excludes full administrative provisioning, not the minimal
    create-or-recover bootstrap PAT-54 owns on all three trackers."""
    contract = _load_contract()
    by_id = {o["id"]: o for o in contract["operations"]}
    bootstrap = by_id["repository-bootstrap"]
    assert bootstrap["core"] is True
    assert {p: c["status"] for p, c in bootstrap["cells"].items()} == {
        "youtrack": "gap", "linear": "gap", "ghprojects": "to_qualify",
    }
    assert {c["ticket"] for c in bootstrap["cells"].values()} == {"PAT-54"}
    provisioning = by_id["full-administrative-provisioning"]
    assert provisioning["core"] is False
    assert provisioning["cells"]["linear"]["status"] == "refused"
    assert provisioning["cells"]["ghprojects"]["status"] == "refused"
    assert "project-provisioning" not in by_id


def test_youtrack_provisioning_ignores_canonical_repository():
    """Grounds the youtrack repository-bootstrap gap: provisioning is not bound to
    canonical identity, and Linear inherits the base refusal."""
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


def test_linear_acceptance_sync_flag_matches_the_gap_cell():
    from foundry.trackers.linear import LinearTracker

    assert LinearTracker.acceptance_sync_supported is False


def test_youtrack_epic_closure_flag_matches_the_gap_cell():
    from foundry.trackers.youtrack import YouTrackTracker

    assert YouTrackTracker.epic_closure_supported is False


def test_youtrack_has_no_import_adr_override():
    """PAT-64: YouTrack cannot yet be an import TARGET; it inherits the base
    refusal rather than overriding import_adr/import_adr_batch."""
    from foundry.trackers.youtrack import YouTrackTracker

    assert "import_adr" not in YouTrackTracker.__dict__
    assert "import_adr_batch" not in YouTrackTracker.__dict__


def test_ghprojects_core_methods_are_unimplemented_stubs():
    """The stub adapter must actually raise NotImplementedError for every
    core method, grounding the json/'md 'to_qualify' cells in live code, not prose."""
    from foundry.trackers.ghprojects import GitHubProjectsTracker

    tracker = GitHubProjectsTracker()
    calls = {
        "search": (None,),
        "get_issue": (None,),
        "create_issue": (None, None, None),
        "update_fields": (None, None),
        "set_state": (None, None),
        "link": (None, None, None),
        "add_comment": (None, None),
        "list_adrs": (None,),
        "create_adr": (None, None, None),
        "set_adr_status": (None, None),
    }
    for method_name, args in calls.items():
        method = getattr(tracker, method_name)
        with pytest.raises(NotImplementedError):
            method(*args)


@pytest.mark.parametrize(
    "ticket",
    ["PAT-54", "PAT-55", "PAT-56", "PAT-69", "PAT-64", "PAT-43", "PAT-47", "PAT-59"],
)
def test_expected_gap_tickets_are_actually_cited(ticket):
    contract = _load_contract()
    cited = {
        cell["ticket"]
        for operation in contract["operations"]
        for cell in operation["cells"].values()
        if cell.get("status") == "gap"
    }
    assert ticket in cited
