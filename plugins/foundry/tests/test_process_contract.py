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


def test_process_contract_states_r9_checklist_deferral_and_minimal_corrections():
    text = _repo_text("AGENTS.md")
    flat = " ".join(text.split())
    assert "## R9 " in text
    for point in ("1. A sentence about what a tool launches", "2. A cause is stated",
                  "3. A rule, threshold or quantity", "4. A comparison names",
                  "5. A documented procedure", "6. No proper name"):
        assert point in text
    assert 'marks "fix before merge"' in flat
    assert "a remark on a page frozen after publication" in flat
    assert "a missing or generic documentation status under R5" in flat
    assert "(c) Correction commits" in text
    assert "What only the reviewer can judge" in text
    assert "in every repository where the plugin is installed, by the maintainer's decision of 2026-10-10" in flat
    assert "every other intake write keeps its confirmation" in flat
    assert "is seen by any gate" in flat
    assert (REPOSITORY_ROOT / "CLAUDE.md").read_bytes() == (REPOSITORY_ROOT / "AGENTS.md").read_bytes()


_TOOLING = REPOSITORY_ROOT / "plugins" / "foundry" / "tooling" / "foundry"
# (file, text that must exist in it): a citation is a file plus a symbol, never a line.
_R9_CODE_CITATIONS = (
    ("campaign_runtime.py", "_MUTATING_STEPS = frozenset("),
    ("campaign_coordinator.py", "class CampaignPipeline(Protocol):"),
    ("epic_preview.py", "def preview_epic("),
    ("epic_preview.py", 'getattr(tracker, "epic_subgraph_supported", False)'),
    ("trackers/base.py", "epic_subgraph_supported: bool = False"),
    ("trackers/devhub.py", "epic_subgraph_supported = True"),
    ("campaign_runtime.py", "def _review_observation("),
    ("campaign_runtime.py", "AcceptanceProofStore(repository_identity(self.root))"),
    ("campaign_runtime.py", "_GATE_STEPS = frozenset("),
    ("routing.py", "class AcceptanceProofStore:"),
    ("routing.py", '"record-review-proof"'),
    ("routing.py", 'set(submitted) != {"outcomes", "quality"}'),
    ("routing.py", '"record-review-remarks"'),
    ("campaign_effects.py", "def step("),
    ("campaign_defer.py", "def run_deferral("),
    ("campaign_defer.py", "MAX_DEFER_TRIES = "),
    ("campaign_defer.py", "_DURABLE = re.compile("),
    ("campaign_defer.py", "DEFER_RETRY_SECONDS = "),
    ("campaign_defer.py", "MAX_RESET_WAIT_SECONDS = "),
    ("campaign_defer.py", "def reset_of("),
    ("campaign_defer.py", "_RESET_AT = re.compile("),
    ("campaign_defer.py", '_COMPANION_NAME = re.compile('),
    ("campaign_review.py", "def parse_result("),
    ("review_remarks.py", "class ReviewRemarksStore:"),
)
_PORTABILITY_CODE_CITATIONS = (
    ("trackers/youtrack.py", "def _cf_write("),
    ("trackers/linear.py", 'LinearBindingError("type_unmapped")'),
    ("trackers/ghprojects.py", "def _field_map("),
    ("trackers/ghprojects.py", 'GitHubProjectsTrackerError("project.read", f"invalid_field_options:{semantic}")'),
    ("trackers/ghprojects.py", '!= _FIELD_OPTIONS[semantic])'),
    ("trackers/youtrack.py", "class _YouTrackHTTPError("),
    ("trackers/linear.py", "class LinearQuotaExhaustedError("),
    ("trackers/linear.py", "self.reset_at_ms = reset_at_ms"),
    ("trackers/linear.py", '"transport_error"'),
    ("trackers/ghprojects.py", '"rate_limited"'),
    ("trackers/ghprojects.py", '"transport_failed"'),
    ("trackers/ghprojects.py", "def _rest_write("),
    ("trackers/ghprojects.py", "def _observe_create_candidate("),
    ("trackers/ghprojects.py", '"issue.create_reconcile", "create_effect_unknown"'),
    ("trackers/ghprojects.py", '"issue.create_reconcile", "unowned_create_candidate"'),
    ("trackers/ghprojects.py", 'self._intent_record(fingerprint, "pending")'),
    ("trackers/linear.py", '"id": str(uuid.uuid4()),'),
    ("trackers/youtrack.py", 'raw = self._req("POST", "/issues",'),
)


def _tooling_text(relative_path):
    return (_TOOLING / relative_path).read_text(encoding="utf-8")


def _assert_campaign_facts(flat):
    """What the cited symbols contain, read from the code rather than from a line."""
    runtime = _tooling_text("campaign_runtime.py")
    declaration = next(line for line in runtime.splitlines()
                       if line.startswith("_MUTATING_STEPS = frozenset("))
    steps = ("start", "open-pr", "merge", "close-epic", "sync-parent-acceptance")
    assert sorted(declaration.split("{")[1].split("}")[0].replace('"', "").split(", ")) == sorted(steps)
    for step in steps:
        assert f"`{step}`" in flat
    gates = next(line for line in runtime.splitlines()
                 if line.startswith("_GATE_STEPS = frozenset("))
    assert sorted(gates.split("{")[1].split("}")[0].replace('"', "").split(", ")) == [
        "ci", "human-gate", "review"]
    # the cadencer carries the remarks through a receipt; the proof keeps its two keys
    assert "The channel is a local receipt distinct from the review proof" in flat
    assert "still accepts exactly the keys `outcomes` and `quality`" in flat
    assert "so the merge gate never reads it" in flat
    assert "(PAT-141, PAT-ADR-0028)" in flat
    # the DevHub coordinator is not changed: it creates no issue; only the effect does
    for module in sorted(_TOOLING.glob("campaign_*.py")):
        source = module.read_text(encoding="utf-8")
        creates = "create_issue" in source or "create-issue" in source
        assert creates == (module.name == "campaign_defer.py"), module.name
    adapters = [path.name for path in sorted((_TOOLING / "trackers").glob("*.py"))
                if "epic_subgraph_supported = True" in path.read_text(encoding="utf-8")]
    assert adapters == ["devhub.py", "linear.py"]


def test_only_the_deferral_effect_contains_the_companion_epic_name():
    """Pins the sentence "that file is the only one under `tooling/` or `hooks/` that
    contains the word `Nits`" of `docs/review-rounds.md` (PAT-141)."""
    plugin = REPOSITORY_ROOT / "plugins" / "foundry"
    scanned = 0
    offenders = []
    for folder in ("tooling", "hooks"):
        for path in sorted((plugin / folder).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            scanned += 1
            if "Nits" in text:
                offenders.append(str(path.relative_to(plugin)))
    assert scanned > 50
    assert offenders == ["tooling/foundry/campaign_defer.py"]


def _r9_deferral_rule() -> str:
    """Rule R9 (b) alone: from its heading to the heading of (c)."""
    text = _repo_text("AGENTS.md")
    return " ".join(text[text.index("**(b) "):text.index("**(c) ")].split())


# PAT-139 / PAT-ADR-0018. Presence only: these tests prove that the rule is written,
# not that anyone follows it.
def test_r9_deferral_goes_to_an_unlinked_companion_epic_with_four_standing_writes():
    rule = _r9_deferral_rule()
    assert "(PAT-ADR-0018)" in rule
    assert "named `Nits` followed by the identifier of the origin Epic" in rule
    assert "no tracker link between them: no parent, no dependency, no relation" in rule
    assert "a single follow-up issue, which gathers all the deferred remarks of the origin Epic" in rule
    assert "one per batch or per theme" not in rule
    assert ("If that issue is already started or closed, a new follow-up issue is created: "
            "no remark is added to a started or closed issue") in rule
    assert ("Four writes are authorized in advance, without human confirmation, inside "
            "and outside an Epic campaign (FOUNDRY-ADR-0013, FOUNDRY-ADR-0016), in every "
            "repository where the plugin is installed") in rule
    for write in ("1. create the companion Epic, the first time;",
                  "2. create a follow-up issue in it;",
                  "3. add deferred remarks to such an issue;",
                  "4. attach to the companion Epic a follow-up issue whose creation succeeded "
                  "but whose parent link was not set, with `edit link <FOLLOW-UP-ID> "
                  "subtask-of <COMPANION-EPIC-ID>`"):
        assert write in rule
    assert "Three writes are authorized" not in rule
    assert "The coordinator reports these writes in the PR description" in rule
    assert "neither prioritized nor started without the maintainer" in rule
    # no second-level deferral
    assert ("the non-blocking remarks left by the PR of an issue that belongs to a "
            "companion Epic, `Nits` included, are corrected before the merge. On every pull "
            "request, the review that follows the correction of the non-blocking remarks of "
            "a validated round") in rule
    assert ("covers what changed since that round, not the whole diff (PAT-ADR-0022). It "
            "stays full after a blocking round, when the change since the previous round "
            "goes beyond correcting the remarks it carried, and when the reviewer asks for "
            "it with a reason") in rule
    assert "of a companion Epic was limited" not in rule
    assert ("so after such a partial review that proof relies on the previous round for "
            "the unchanged parts, and the PR description says so; no mechanical check "
            "distinguishes a partial from a full review, and the merge gate and the proof "
            "format are not modified") in rule
    # maintainer decisions of 2026-10-10 on the cases PAT-ADR-0018 leaves open
    assert ("The origin Epic of an issue is its direct parent Epic: an issue under a "
            "sub-Epic uses that sub-Epic") in rule
    assert ("PAT-ADR-0029 extends it to an issue that has no origin Epic, with `Nits` as "
            "destination, and amends the maintainer decision of 2026-10-10 that gave such "
            "an issue no deferral") in rule
    assert "An issue that has no origin Epic gets no deferral" not in rule
    assert ("That includes an issue whose direct parent is not an Epic: the chain of "
            "parents is not walked up") in rule
    assert ("Checklist (a) and correction rule (c) live in a repository's contract file, so "
            "they apply where a repository carries them; deferral (b) is applied by the "
            "Foundry skills, which ship with the plugin, so it applies wherever the plugin "
            "is installed") in rule
    assert "stays with the maintainer" not in rule
    # durable refusal and transient failure are two cases
    assert ("durable refusal (the project cannot carry the write, for example it has no "
            "`Epic` type): the coordinator makes no substitute write") in rule
    assert ("transient failure (request quota exhausted, network failure): the coordinator "
            "waits and retries the write before the merge, and the merge waits for it") in rule
    assert "It does not correct the remarks instead and does not merge first" in rule
    assert ("it first reads the tracker to see whether the write happened, and retries "
            "only if it did not") in rule
    assert ("If the adapter itself refuses the retry (it cannot replay the write safely), "
            "the case is treated as a durable refusal: the remarks of that round are "
            "corrected before the merge and then re-reviewed as stated above, and the "
            "refusal is reported in the PR description") in rule
    assert "the refused retry is the coordinator's reading of those two decisions" in rule
    assert "Both are operating choices of this contract" in rule
    assert "not decisions of PAT-ADR-0018" in rule


def test_r9_lists_every_case_that_is_corrected_before_the_merge():
    """Each case of rule (b) that sends a remark back into the PR is written in AGENTS.md."""
    rule = _r9_deferral_rule()
    # the three cases kept from before, then the three that the rule adds
    assert ("These three cases are still corrected before the merge; the rest of this "
            "rule adds two more") in rule
    assert "The first three cases are:" in rule
    assert "fully re-reviewed" not in rule
    assert "Three cases are still corrected" not in rule
    for case in ('- a remark the reviewer marks "fix before merge";',
                 "- a remark on a page frozen after publication (release note, migration "
                 "guide, a CHANGELOG section of a published version);",
                 "- a missing or generic documentation status under R5 and FOUNDRY-ADR-0018, "
                 "which is never a deferrable remark."):
        assert case in rule, case
    # the two added cases (PAT-ADR-0029 moved "no origin Epic" to the deferred side)
    assert ("(the PR of an issue of a companion Epic, `Nits` included, and a durable "
            "refusal of one of the writes)") in rule
    assert ("the non-blocking remarks left by the PR of an issue that belongs to a "
            "companion Epic, `Nits` included, are corrected before the merge. On every pull "
            "request, the review that follows the correction of the non-blocking remarks of "
            "a validated round") in rule
    assert ("durable refusal (the project cannot carry the write, for example it has no "
            "`Epic` type): the coordinator makes no substitute write: the remarks of that "
            "round are corrected before the merge and then re-reviewed as stated above") in rule


def test_r9_partial_review_on_every_pr_after_a_validated_round_full_after_a_blocking_one():
    """PAT-172 / PAT-ADR-0022: the partial review is not limited to companion Epic PRs."""
    rule = _r9_deferral_rule()
    assert ("On every pull request, the review that follows the correction of the "
            "non-blocking remarks of a validated round (every criterion `pass` and quality "
            "`mergeable`, even with remarks marked \"fix before merge\") covers what "
            "changed since that round, not the whole diff (PAT-ADR-0022)") in rule
    assert "It stays full after a blocking round" in rule
    assert "and when the reviewer asks for it with a reason" in rule
    assert ("The partial-review packet gives the reviewer the previous round's verdict "
            "and remarks, the commit range since that round, and the full diff through the "
            "shared verifier") in rule
    assert ("no mechanical check distinguishes a partial from a full review, and the "
            "merge gate and the proof format are not modified") in rule


def test_r9_defers_an_issue_with_no_origin_epic_to_nits_and_reads_the_partial_range_from_bytes():
    """PAT-283 / PAT-ADR-0029 and the byte-checked range of the partial review."""
    rule = _r9_deferral_rule()
    assert ("The remarks deferred from an issue with no origin Epic go to the companion Epic "
            "of the project, named exactly `Nits` (PAT-ADR-0029, which amends the maintainer "
            "decision of 2026-10-10 that gave such an issue no deferral); the rules of this "
            "paragraph apply to it unchanged") in rule
    assert "They apply to `Nits` as to any companion Epic (PAT-ADR-0029)" in rule
    assert "the cadencer does not yet apply PAT-ADR-0029 to it" in " ".join(
        _repo_text("AGENTS.md").split())
    assert ("`routing prepare-review --previous-head <SHA>` records the head the previous "
            "round saw") in rule
    assert ("argument to add to the `routing read-review` command of the reviewer's role contract"
            in " ".join(rule.split()))
    assert "the shared verifier supplies only" not in rule
    assert "does not say that the review is partial" not in rule


def test_r9_no_longer_states_the_campaign_special_case_anywhere_in_the_repository():
    gone = ("maintainer creates or approves", "on its own authority", "exactly two writes",
            "one follow-up issue per batch", "batch follow-up issue",
            "outside an Epic campaign, creating")
    pages = [REPOSITORY_ROOT / "AGENTS.md", REPOSITORY_ROOT / "CLAUDE.md"]
    plugin = REPOSITORY_ROOT / "plugins" / "foundry"
    for folder in ("skills", "agents", "docs"):
        pages.extend(sorted((plugin / folder).rglob("*.md")))
    assert len(pages) > 20
    for page in pages:
        flat = " ".join(page.read_text(encoding="utf-8").split())
        for phrase in gone:
            assert phrase not in flat, (page.name, phrase)


def test_r9_deferral_names_no_tracker_and_cites_supported_contract_rows():
    rule = _r9_deferral_rule()
    for name in ("YouTrack", "Linear", "GitHub", "ghprojects", "DevHub"):
        assert name not in rule
    assert "`plugins/foundry/docs/tracker-contract.md`" in rule
    contract = _repo_text("plugins/foundry/docs/tracker-contract.md")
    header = next(line for line in contract.splitlines() if line.startswith("| Core journey |"))
    assert [cell.strip() for cell in header.strip("|").split("|")][2:] == [
        "YouTrack", "Linear", "ghprojects"]
    for journey, operations in (
        ("Frame/intake/groom: create, comment", "`create_issue`, `add_comment`"),
        ("Epics/enfants/dépendances: child creation, relations",
         "`create_issue(parent=…)`, `link(depends-on|blocks|relates)`"),
    ):
        # the rule cites the row and its operations as the contract writes them
        assert f'row "{journey}" ({operations})' in rule
        row = next(line for line in contract.splitlines() if line.startswith(f"| {journey} |"))
        # the contract escapes the pipes of the operation list inside its table cell
        cells = [cell.strip() for cell in row.strip("|").replace("\\|", "|").split(" | ")]
        assert cells[1] == operations
        assert len(cells) == 5 and all(cell.startswith("supported") for cell in cells[2:])


def test_r9_states_what_is_not_mechanical_and_what_is_not_coded_yet():
    text = _repo_text("AGENTS.md")
    flat = " ".join(text[text.index("**What only the reviewer can judge.**"):].split())
    assert ("no gate checks that the companion Epic of (b) exists, that it has no tracker "
            "link with the origin Epic") in flat
    assert "**Inside an Epic campaign (PAT-141, PAT-ADR-0028).**" in flat
    assert "has no issue-creation primitive" in flat
    assert "has no effect" not in flat and "not coded yet" not in flat
    assert "does not receive the reviewer's prose" not in flat
    # every file cited there still holds the cited symbol (no line number is pinned)
    for path, symbol in _R9_CODE_CITATIONS:
        assert f"`plugins/foundry/tooling/foundry/{path}`" in flat
        assert symbol in _tooling_text(path), (path, symbol)
    _assert_campaign_facts(flat)


def test_review_rounds_page_states_the_limits_and_the_portability_of_the_three_writes():
    page = " ".join(_repo_text("plugins/foundry/docs/review-rounds.md").split())
    assert "## What is not mechanical and how a campaign carries the remarks (PAT-139, PAT-141)" in page
    assert "No gate checks that the companion Epic exists, that it has no tracker link" in page
    assert "The campaign coordinator has no issue-creation primitive" in page
    assert "the DevHub and Linear adapters set it to `True`" in page
    assert "has no effect yet" not in page and "not coded yet" not in page
    assert "the four writes are made by the `defer-remarks` effect" in page
    assert "## Portability of the three writes (PAT-139)" in page
    # PAT-144: maintainer decisions of 2026-10-11 (presence only)
    assert "so 3.2 rounds per PR is also 3.2 rounds per issue" in page
    assert ("divided by the number of origin issues is not below 3.2, the figure of the "
            "baseline") in page
    assert "authorizes four tracker writes in advance" in page
    assert "How such a partial write is repaired is not decided" not in page
    assert ("`edit link <FOLLOW-UP-ID> subtask-of <COMPANION-EPIC-ID>`, with the same "
            "arguments as the call in `create_issue`") in page
    assert "does not cover this fourth write" in page
    assert "an accepted risk (maintainer decision of 2026-10-11)" in page
    for provider in ("| YouTrack |", "| Linear |", "| `ghprojects` |"):
        assert provider in page
    assert "the provider's answer is not verified" in page
    assert "this is a durable refusal" in page
    assert "the coordinator waits and retries the write before the merge" in page
    # same maintainer decision as R9 (b): read first, retry only if the write did not happen
    assert ("it first reads the tracker to see whether the write happened, and retries "
            "only if it did not") in page
    assert "That reading is what prevents a second issue or a second comment" in page
    assert "Retrying it can then create" not in page
    assert "then treats the case as a durable refusal" in page
    for cited in ("`create_effect_unknown`", "`unowned_create_candidate`",
                  "`_observe_create_candidate`", "`uuid.uuid4()`"):
        assert cited in page
    assert "a replay with zero candidates remains unknown and refuses" in " ".join(
        _repo_text("plugins/foundry/docs/tracker-contract.md").split())
    quota = _repo_text("plugins/foundry/docs/linear-tracker.md")
    assert "## Transport errors, read retries and quota" in quota
    assert "**A write is never retried**" in quota
    for path, symbol in _R9_CODE_CITATIONS + _PORTABILITY_CODE_CITATIONS:
        assert f"`tooling/foundry/{path}`" in page
        assert symbol in _tooling_text(path), (path, symbol)
    for cited in ("`_cf_write`", "`type_unmapped`", "`_field_map`", "`invalid_field_options:type`",
                  "`_YouTrackHTTPError`", "`LinearQuotaExhaustedError`", "`reset_at`",
                  "`rate_limited`", "`transport_failed`", "`_rest_write`"):
        assert cited in page
    _assert_campaign_facts(page)


def test_edit_docstring_names_the_four_preauthorized_writes_and_the_intake_exception():
    text = " ".join(_tooling_text("edit.py").split())
    assert "the four writes that the grouped deferral rule" in text
    assert "attach to the companion Epic with `link` an issue whose parent link was not set" in text
    assert "The second group does not cover the writes of `foundry:intake`" in text
