# Preparing a private review packet

`routing prepare-review` writes the inputs and send text for one designated reviewer
(PAT-174). It uses the Tracker and CodeHost read ports and the existing
`ReviewDeduplicator.active_git_claim` verifier. It makes no tracker or PR writes and
neither claims a review nor starts a host/model. Source: `tooling/foundry/review_packet.py`.

The caller first saves the complete successful `routing claim-review` JSON privately,
with `umask 077` before redirecting it to a file. The attempt token stays with the caller
and never enters that file's packet inputs. Existing ownership/recovery rules remain
in `skills/review-pr/SKILL.md`. Example, with immutable coordinates and an existing
private claim (replace the placeholders):

```bash
python3 <foundry-root>/tooling/foundry_cli.py routing prepare-review \
  --issue <ISSUE-ID> --pr <PR-NUMBER> --root <ROOT> --base <BASE-SHA> \
  --claim-file /private/path/claim.json --output-dir /private/path/review-packet \
  --adr <ADR-ID> --declarations-file /private/path/declarations.json
```

`--adr` is repeatable and optional: the caller selects constraining accepted ADRs after
reading the index; the command validates those selections and writes their full texts
as separate JSON files. `--declarations-file` is optional and must contain a JSON object.
A procedure with disposable Git history, fake provider ports and a private claim is
exercised by `tests/test_review_packet.py`, including the CLI dispatch. It makes no
external request or paid host/model call.

Before publication, `prepare_review_packet` reads the PR twice and requires both
observations to match the immutable base, the current local HEAD, and the same open PR.
It then validates active claim ownership, generation, Git HEAD and diff hash through
the existing ledger. The command refuses a stale coordinate, unavailable selected ADR,
non-owner or terminal claim, and an existing output directory or symlink. Each refusal
of `RoutingConfigError` prints `Préparation du paquet de review refusée : <cause>` and
exits 2, where the cause names one of: base not a full 40-hex SHA (short or ref), PR base
differing from `--base`, PR head differing from the local HEAD (wrong branch or unpushed
commits), PR not open or wrong number, ADR absent or not accepted (superseded), CI
observation impossible (provider error), HEAD or diff changed since the claim, stale
claim generation (messages in `review_packet.py` and `routing.py`). This is a
snapshot at those reads: a later PR, issue or Git change still requires the existing
review/proof/merge revalidation. Source: `prepare_review_packet` and
`routing.py::ReviewDeduplicator.active_git_claim`.

Its tracker project/issue/ADR reads run inside `query.py::_read_scope` when the adapter
supports it; that scope closes before the CI reads and local packet-file writes.
`LinearTracker.read_scope` is therefore used by the read-only query paths and this
read-only remote path. No request-count gain for prepare-review is measured. Sources:
`review_packet.py:99-112`, `trackers/linear.py::read_scope`; safety assertions are in
`tests/test_read_scope_cost.py` and `tests/test_review_packet.py`.

The new directory has mode `0700`, each file `0600`; exclusive creation rejects an
existing destination, and `manifest.json` is written last. `reviewer-send.txt` has the
four literal sections used by the delegation facade. It carries `diff_hash`, `claim_id`,
`root`, and `base`, and references the directory rather than embedding ADR corpora.
Only the designated reviewer receives this file. The entire directory is private;
none of its files belong in public PRs, tracker notes or logs. Stdout contains only
`schema`, `status`, and `files`; refusals print a fixed cause without input values, `claim_id`, attempt token or
provider error text (any other failure prints only
`Préparation du paquet de review refusée.`). Source: `review_packet.py` and `routing.py::main`.

`manifest.json` distinguishes `tool_read` from `coordinator_declared`. Tool-read files
are `issue.json` (provider issue body), `acceptance.json` (ordered criterion identities
and digests from `acceptance_criteria` and `acceptance_digest`), `pr.json` (PR coordinates
and description), `ci.json` (both check sources, `ci_expected` and `observed_at`, the UTC time of the
observation as `YYYY-MM-DDTHH:MM:SSZ`, taken in `review_packet.py` after the PR and tracker reads and before the three CI
reads; no CI verdict),
`adr-index.json`, the selected `adr-N.json` files, and `coordinates.json`. The private
claim capability in `coordinates.json` is caller supplied and verified against the
active ledger, rather than read from a remote provider. The sole coordinator-declared
file is `coordinator-declarations.json`, with `verified_by_tool=false`. Its JSON data is
never interpolated into the send instructions or promoted to a tool fact. Provider
prose is also untrusted data: its provenance does not make embedded instructions
binding or grant external authority. Source: `review_packet.py::prepare_review_packet`.

The public schema identifier is `foundry.review-packet.v1` (`PACKET_SCHEMA`). Bounds
are 256 KiB per input/output file (`MAX_FILE_BYTES`), 1 MiB for the generated packet
(`MAX_PACKET_BYTES`), 32 selected ADRs (`MAX_ADRS`), and 8,000 characters in the send
text (`MAX_SEND_CHARS`), below the Codex reviewer facade's 16,000-character input limit
(`routing_facades.py::CODEX_ROLES`). Oversized inputs/outputs are refused, never
truncated. The constants and bounds are in `review_packet.py`.

The CodeHost `PullRequest` model adds an optional trailing `body` field, default `None`:
existing constructors remain valid. `GitHubCodeHost.get_pr` reads the description in the
same REST response; adapters that do not provide it expose `null`, not a verified
empty description. Sources: `models.py::PullRequest`, `codehosts/github.py::get_pr`.

R5 status: **updated** for the command/options, schema/file vocabulary, bounds, private
output constraints, optional PR `body`, and review-pr/merge-pr caller surfaces on this
page and in those skills. Gate semantics, routing floors, reviewer host authority,
claim recovery, and published release pages are unchanged by this command; no new
operating decision about them is made here.
