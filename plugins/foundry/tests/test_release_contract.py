"""Release contract — declared once, checked generically, frozen after publication.

FOUNDRY-136. This file used to pin the shipped version literally (down to a test
function name) and to accumulate one block of literal content assertions per
release. It now separates three concerns:

* **The current version** is declared in exactly one place this test consumes —
  ``.claude-plugin/plugin.json``, the version authority of FOUNDRY-ADR-0005. The
  Codex manifest and the top published CHANGELOG section must agree with it. A
  bump therefore touches the two manifests and the CHANGELOG, never a literal
  assertion here and never a function name.
* **The release apparatus** required by that version is derived from semver:
  a patch requires a CHANGELOG entry plus both manifest bumps, and neither a
  migration guide nor a release report; a minor or major keeps the full
  apparatus with today's guarantees.
* **Published releases** are frozen, not re-asserted. Their exact content was
  asserted once, at publication; ``tests/fixtures/release-history.json`` pins the
  CHANGELOG section and every release document byte-for-byte, so the contract
  grows by one data entry per release instead of ~100 lines of literal
  assertions. A missing entry fails closed and prints the block to paste.

Documents that describe the *live* production contract — ``docs/model-routing.md``
and ``docs/local-scout.md`` — are not release evidence and stay asserted literally
below, independently of any version.
"""
import hashlib
import json
import re
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
RELEASE_HISTORY = Path(__file__).resolve().parent / "fixtures" / "release-history.json"

CLAUDE_MANIFEST = ".claude-plugin/plugin.json"
CODEX_MANIFEST = ".codex-plugin/plugin.json"
CLAUDE_CATALOGUE = ".claude-plugin/marketplace.json"
CODEX_CATALOGUE = ".agents/plugins/marketplace.json"

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
PUBLISHED_HEADING = re.compile(r"^## (\d+\.\d+\.\d+) — (\d{4}-\d{2}-\d{2})$", re.M)
ANY_HEADING = re.compile(r"^## ", re.M)

# Requirement tokens of the release contract.
CHANGELOG_ENTRY = "changelog-entry"
BOTH_MANIFEST_BUMPS = "both-manifest-bumps"
RELEASE_REPORT = "release-report"
MIGRATION_GUIDE = "migration-guide"


def _text(relative_path):
    return (PLUGIN_ROOT / relative_path).read_text(encoding="utf-8")


def _json(relative_path):
    return json.loads(_text(relative_path))


def _repository_json(relative_path):
    return json.loads(
        (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8"),
    )


def _flat(text):
    return " ".join(text.split())


def _digest(relative_path):
    return hashlib.sha256((PLUGIN_ROOT / relative_path).read_bytes()).hexdigest()


def parse_version(version):
    """Return the semver triple, refusing any build/pre-release suffix."""
    match = SEMVER.fullmatch(version)
    assert match, f"not a plain semver version: {version!r}"
    return tuple(int(part) for part in match.groups())


def release_kind(version):
    major, minor, patch = parse_version(version)
    if patch:
        return "patch"
    if minor:
        return "minor"
    return "major"


def release_requirements(version):
    """What publishing ``version`` requires. A patch is deliberately cheaper."""
    required = {CHANGELOG_ENTRY, BOTH_MANIFEST_BUMPS}
    if release_kind(version) != "patch":
        required |= {RELEASE_REPORT, MIGRATION_GUIDE}
    return frozenset(required)


def changelog_sections():
    """Ordered ``(version, date, section)`` triples of the published CHANGELOG."""
    changelog = _text("CHANGELOG.md")
    starts = [match.start() for match in ANY_HEADING.finditer(changelog)]
    starts.append(len(changelog))
    sections = []
    for match in PUBLISHED_HEADING.finditer(changelog):
        end = next(start for start in starts if start > match.start())
        sections.append(
            (
                match.group(1),
                match.group(2),
                changelog[match.start():end].strip("\n"),
            ),
        )
    return sections


def release_documents(version):
    """Release evidence carrying this version in its name."""
    return sorted(
        path.relative_to(PLUGIN_ROOT).as_posix()
        for path in (PLUGIN_ROOT / "docs").glob(f"*{version}*.md")
    )


def freeze_entry(version, date, section):
    """The fixture block that freezes a published release, byte-for-byte."""
    return {
        "date": date,
        "changelog_sha256": hashlib.sha256(section.encode("utf-8")).hexdigest(),
        "documents": {
            document: _digest(document) for document in release_documents(version)
        },
    }


# The current version is read, never spelled: one declaration, consumed here.
CURRENT_VERSION = _json(CLAUDE_MANIFEST)["version"]


def test_current_version_is_declared_once_and_both_manifests_carry_it():
    claude = _json(CLAUDE_MANIFEST)
    codex = _json(CODEX_MANIFEST)

    assert claude["version"] == codex["version"] == CURRENT_VERSION
    assert "+" not in claude["version"]
    assert "+" not in codex["version"]
    parse_version(CURRENT_VERSION)

    published = changelog_sections()
    assert published, "the CHANGELOG publishes no version"
    assert published[0][0] == CURRENT_VERSION, (
        "the manifests and the top published CHANGELOG section disagree: "
        f"{CURRENT_VERSION} vs {published[0][0]}"
    )
    changelog = _text("CHANGELOG.md")
    assert changelog.startswith("# Changelog\n\n## Unreleased\n")
    assert changelog.index("## Unreleased") < changelog.index(
        f"## {CURRENT_VERSION} — ",
    )


def test_catalogues_stay_versionless_with_supported_source_pointer_schemas():
    claude_catalogue = _repository_json(CLAUDE_CATALOGUE)
    codex_catalogue = _repository_json(CODEX_CATALOGUE)
    claude_foundry = next(
        plugin for plugin in claude_catalogue["plugins"]
        if plugin["name"] == "foundry"
    )
    codex_foundry = next(
        plugin for plugin in codex_catalogue["plugins"]
        if plugin["name"] == "foundry"
    )

    assert claude_foundry["source"] == "./plugins/foundry"
    assert codex_foundry["source"] == {
        "source": "local",
        "path": "./plugins/foundry",
    }
    assert set(claude_foundry) == {"name", "source", "description"}
    assert set(codex_foundry) == {"name", "source", "policy", "category"}
    assert "version" not in claude_foundry
    assert "version" not in codex_foundry
    for catalogue in (claude_catalogue, codex_catalogue):
        assert "version" not in catalogue
        for plugin in catalogue["plugins"]:
            assert "version" not in plugin


def test_changelog_publishes_unique_strictly_descending_versions():
    published = changelog_sections()
    versions = [version for version, _, _ in published]
    dates = [date for _, date, _ in published]

    assert len(set(versions)) == len(versions), "duplicate CHANGELOG version"
    parsed = [parse_version(version) for version in versions]
    assert parsed == sorted(parsed, reverse=True), (
        f"CHANGELOG versions are not strictly descending: {versions}"
    )
    assert dates == sorted(dates, reverse=True), (
        f"CHANGELOG dates are not descending: {dates}"
    )
    for version, _, section in published:
        assert section.startswith(f"## {version} — ")
        body = section.split("\n", 1)[1].strip()
        assert body, f"empty CHANGELOG section for {version}"


@pytest.mark.historical_fixture(
    reason="requires unsanitized historical release documents omitted from this snapshot",
)
def test_published_release_sections_and_documents_stay_frozen():
    frozen = json.loads(RELEASE_HISTORY.read_text(encoding="utf-8"))["releases"]
    published = changelog_sections()

    missing = [
        version for version, _, _ in published if version not in frozen
    ]
    if missing:
        blocks = {
            version: freeze_entry(version, date, section)
            for version, date, section in published
            if version in missing
        }
        pytest.fail(
            "tests/fixtures/release-history.json does not freeze "
            f"{', '.join(missing)}. Paste into `releases`:\n"
            + json.dumps(blocks, indent=2, ensure_ascii=False),
        )
    assert set(frozen) == {version for version, _, _ in published}, (
        "release-history.json freezes a version the CHANGELOG does not publish: "
        f"{sorted(set(frozen) - {version for version, _, _ in published})}"
    )

    for version, date, section in published:
        entry = frozen[version]
        assert entry["date"] == date
        assert entry["changelog_sha256"] == hashlib.sha256(
            section.encode("utf-8"),
        ).hexdigest(), (
            f"the published {version} CHANGELOG section was rewritten; published "
            "history is immutable"
        )
        for document in release_documents(version):
            assert document in entry["documents"], (
                f"{document} is unfrozen {version} release evidence"
            )
        for document, expected in entry["documents"].items():
            assert (PLUGIN_ROOT / document).is_file(), (
                f"frozen {version} evidence disappeared: {document}"
            )
            assert _digest(document) == expected, (
                f"frozen {version} evidence was rewritten: {document}"
            )


def test_release_requirements_separate_a_patch_from_a_minor():
    minimum = {CHANGELOG_ENTRY, BOTH_MANIFEST_BUMPS}
    full = minimum | {RELEASE_REPORT, MIGRATION_GUIDE}

    assert release_kind("0.8.1") == "patch"
    assert release_kind("0.9.0") == "minor"
    assert release_kind("1.0.0") == "major"

    for patch in ("0.8.1", "0.8.12", "1.2.3"):
        assert release_requirements(patch) == minimum
        assert MIGRATION_GUIDE not in release_requirements(patch)
        assert RELEASE_REPORT not in release_requirements(patch)
    for feature in ("0.9.0", "1.0.0", "2.4.0"):
        assert release_requirements(feature) == full

    for rejected in ("0.8.0+1", "0.8", "0.8.0-rc1", "v0.8.0"):
        with pytest.raises(AssertionError):
            parse_version(rejected)


def test_current_release_satisfies_the_requirements_of_its_kind():
    required = release_requirements(CURRENT_VERSION)

    assert CHANGELOG_ENTRY in required
    sections = {version: section for version, _, section in changelog_sections()}
    assert CURRENT_VERSION in sections

    assert BOTH_MANIFEST_BUMPS in required
    assert _json(CLAUDE_MANIFEST)["version"] == CURRENT_VERSION
    assert _json(CODEX_MANIFEST)["version"] == CURRENT_VERSION

    readme = _flat(_text("README.md"))
    report_path = f"docs/release-{CURRENT_VERSION}.md"
    migration_path = f"docs/migration-{CURRENT_VERSION}.md"

    if RELEASE_REPORT in required:
        assert (PLUGIN_ROOT / report_path).is_file(), (
            f"a {release_kind(CURRENT_VERSION)} release requires {report_path}"
        )
        report = _flat(_text(report_path))
        assert CURRENT_VERSION in report
        assert report_path in readme
        for host in ("Claude Code", "Codex"):
            assert host in report, (
                f"{report_path} must cover both hosts (FOUNDRY-ADR-0005)"
            )

    if MIGRATION_GUIDE in required:
        assert (PLUGIN_ROOT / migration_path).is_file(), (
            f"a {release_kind(CURRENT_VERSION)} release requires {migration_path}"
        )
        migration = _flat(_text(migration_path))
        assert CURRENT_VERSION in migration
        assert migration_path in readme
        for command in (
            "/plugin marketplace update patolabs",
            "/plugin update foundry@patolabs",
            "/reload-plugins",
            "/foundry:configure",
            "/foundry:doctor",
            "codex plugin marketplace upgrade patolabs",
            "codex plugin remove foundry@patolabs",
            "codex plugin add foundry@patolabs",
            "$foundry:configure",
            "$foundry:doctor",
        ):
            assert command in migration, (
                f"{migration_path} lacks the dual-host step `{command}`"
            )
        assert "~/.config/foundry" in migration
        assert "verify" in migration.lower()


def test_routing_documentation_keeps_dimensions_floors_and_static_resolver():
    routing = _flat(_text("docs/model-routing.md"))

    for contract in (
        "`economy`",
        "`balanced`",
        "`frontier`",
        "`apex`",
        "`model` selects provider capability",
        "`reasoning_effort`",
        "`context_policy`",
        "reviewer minimum",
        "architect minimum",
        "frontier/high",
        "`reviewer` has a `frontier` floor and `architect` an `apex` floor",
        "At most two actual tier increases are allowed per issue",
        "does not add an adaptive resolver",
        "Sonnet 5 / medium",
        "GPT-5.6 Terra / medium",
    ):
        assert contract in routing


def test_local_scout_documentation_keeps_untrusted_opt_in_boundary():
    local = _flat(_text("docs/local-scout.md"))

    for contract in (
        "désactivé par défaut",
        "http://127.0.0.1:<port>",
        "n'upload aucun payload",
        "non privilégié",
        "Mode diff",
        "Mode code",
        "`diff`, `logs` et `tests`",
        "cloud_economy",
        "LOCAL_SCOUT_STALE_INPUT",
        "Le doctor affiche",
        "10 s",
        "120 s",
    ):
        assert contract in local
