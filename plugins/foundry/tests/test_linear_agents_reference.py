"""PAT-284: Linear's rendering of the bare `AGENTS.md#R<n>` reference in an ADR body."""

from __future__ import annotations

from pathlib import Path

import pytest

from foundry.trackers import linear as linear_module
from foundry.trackers.base import TrackerConflictError

FIXTURES = Path(__file__).parent / "fixtures"


def _source() -> str:
    return (FIXTURES / "pat284_adr0029_source.md").read_text(encoding="utf-8")


def _stored() -> str:
    return (FIXTURES / "pat284_adr0029_stored.md").read_text(encoding="utf-8")


def test_orphan_adr_body_renders_byte_identically_to_the_stored_body():
    source = _source()
    linear_module._preflight_adr_body_readback(source)
    assert linear_module._linear_markdown_readback_body(source) == _stored()
    assert _stored() in linear_module._linear_markdown_readback_bodies(source)


def test_orphan_adr_document_content_is_accepted_as_exact_readback():
    metadata = {"id": "PAT-ADR-0000", "sequence": 0, "title": "t"}
    canonical = linear_module._adr_document_content(metadata, _source())
    header = canonical.partition("\n-->\n\n")[0]
    escaped = header[len(linear_module._ADR_HEADER) :]
    stored = f"{linear_module._ADR_HEADER}{escaped}\n\\-->\n\n{_stored()}"
    assert linear_module._adr_readback_content_matches(stored, canonical)
    assert not linear_module._adr_readback_content_matches(
        stored.replace("<http://AGENTS.md#R9>", "<https://AGENTS.md#R9>"), canonical
    )


@pytest.mark.parametrize(
    ("source", "rendered"),
    [
        ("AGENTS.md#R9 (b) x", "[AGENTS.md#R9](<http://AGENTS.md#R9>) (b) x"),
        ("x AGENTS.md#R12", "x [AGENTS.md#R12](<http://AGENTS.md#R12>)"),
        ("a (AGENTS.md#R3) b", "a ([AGENTS.md#R3](<http://AGENTS.md#R3>)) b"),
        ("`AGENTS.md#R9 x`", "`AGENTS.md#R9 x`"),
    ],
)
def test_only_the_observed_reference_forms_are_rewritten(source, rendered):
    assert linear_module._linear_markdown_readback_body(source) == rendered


@pytest.mark.parametrize(
    ("source", "named"),
    [
        ("CLAUDE.md#R9 x", "CLAUDE.md"),
        ("voir (AGENTS.md#R9(b)) ici", "AGENTS.md"),
        ("voir docs/guide.md ici", "guide.md"),
        ("AGENTS.md#R9, puis", "AGENTS.md"),
        ("AGENTS.md#R9\tx", "AGENTS.md"),
        ("[voir AGENTS.md#R9 ici](#ancre)", "AGENTS.md"),
        ("[a (AGENTS.md#R9) b](#x)", "AGENTS.md"),
        ("[r]: AGENTS.md#R9", "AGENTS.md"),
        ("    AGENTS.md#R9 x", "AGENTS.md"),
    ],
)
def test_other_domain_like_references_are_refused_before_any_write(source, named):
    with pytest.raises(TrackerConflictError, match="domain-like reference") as raised:
        linear_module._preflight_adr_body_readback(source)
    assert repr(named) in str(raised.value)


def test_domain_like_reference_in_code_is_not_refused():
    linear_module._preflight_adr_body_readback("`CLAUDE.md#R9` et\n\n```\nfoo.md\n```")


def test_reference_in_a_fenced_block_is_not_rewritten():
    body = "```\nAGENTS.md#R9 x\n```"
    assert linear_module._linear_markdown_readback_body(body) == body
    linear_module._preflight_adr_body_readback(body)
