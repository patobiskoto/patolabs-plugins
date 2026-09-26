"""PAT-53: pin the tracker contract v1 capability matrix.

This is a deterministic, framing-level pin — not the PAT-68 conformance suite. It only
guarantees that ``tracker-contract.v1.json`` stays internally consistent (closed status
vocabulary, every cited operation really exists on the ``Tracker`` ABC, every cited
provider really has an adapter module) and that the doc references the same version.
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
        if operation["id"] == "identity-and-project-resolution":
            # resolve_project is provider-agnostic registry lookup; the one real cell.
            assert cell["status"] == "supported"
            continue
        if operation["id"] == "project-provisioning":
            # Explicitly out-of-core-scope capabilities: refused, not merely unqualified.
            assert cell["status"] == "refused"
            continue
        assert cell["status"] == "to_qualify", (
            f"{operation['id']}.ghprojects must stay to_qualify (PAT-65) until qualified, "
            f"got {cell['status']!r}"
        )


def test_operations_cover_every_criterion_1_core_journey():
    contract = _load_contract()
    journeys = " ".join(op["journey"] for op in contract["operations"])
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
        assert marker in journeys, f"no operation covers the core journey marker {marker!r}"


@pytest.mark.parametrize(
    "ticket",
    ["PAT-55", "PAT-56", "PAT-69", "PAT-64", "PAT-43", "PAT-47", "PAT-59"],
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
