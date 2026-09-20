# Real YouTrack smoke test

The smoke test exercises Foundry's public YouTrack adapter against a disposable
project: authenticated project-scoped read, issue create/read, link, transition,
comment, and ADR create/read/status. It is destructive and therefore opt-in.
The disposable project must have Foundry's normal `State` and `Type` fields and
controlled vocabulary (as created by `foundry.setup_project`).

## Bounded body writes

YouTrack does not expose a provider compare-and-swap precondition for issue descriptions
or article content. Foundry therefore serializes its own writers with a local file lock and uses one
read-verify-write-readback sequence. A pre-write divergence refuses without writing; a
post-write divergence refuses without retrying. An exact no-op performs no write. This
does not prevent a concurrent external YouTrack client from racing between requests, so
it is deliberately weaker than provider CAS.

The public write surface keeps the snapshot explicit: `edit body <ISSUE-ID>
<expected-body.md> <updated-body.md>` and `adr edit <ADR-ID> <expected-body.md>
<updated-body.md>`. The expected file is the text the editor read before preparing the
amendment. It is passed byte-for-byte to the bounded provider operation; a stale expected
file refuses rather than overwriting newer text. Both files are read as UTF-8 with line
endings preserved.

Acceptance synchronization uses the same body primitive. The write tier first derives
the allowed replacement from the complete structured proof, then the YouTrack adapter
derives it again and requires exact equality before sending a request. Only markers
authorized by the proof may change, including valid `-`, `*`, and `+` Markdown list
markers. DevHub keeps its stronger provider CAS and durable audit receipt; callers use
the same full proof contract for either provider.

## Required environment

Set all of these in the runner environment:

| Variable | Meaning |
|---|---|
| `FOUNDRY_YOUTRACK_SMOKE=1` | Explicitly enables the network test. |
| `YOUTRACK_URL` | YouTrack base URL, for example `https://tracker.example`. |
| `YOUTRACK_TOKEN` | Permanent token authorized only for the disposable project. |
| `FOUNDRY_YOUTRACK_SMOKE_PROJECT_KEY` | Short key of the disposable project. |
| `FOUNDRY_YOUTRACK_SMOKE_PROJECT_ID` | Native YouTrack id of that same project. |
| `FOUNDRY_YOUTRACK_SMOKE_CONFIRM_TEST_PROJECT=1` | Confirms the target is intentionally disposable. |

The target is never inferred from the current repository or Foundry's project
registry. The production key `FOUNDRY` is refused before the tracker is constructed.
For exceptional, deliberate disaster-recovery testing only, a second guard exists:
`FOUNDRY_YOUTRACK_SMOKE_ALLOW_FOUNDRY=1`.

Without opt-in, pytest reports one integration skip with the activation variable in
the reason. A missing URL, token, project key/id, or confirmation also produces a
clear skip, even after opt-in. Present but invalid safety values still fail closed:
the confirmation must equal `1`, the production key remains guarded, and the native
project id and key must match on read-back before the first mutation.

## Run it

From `plugins/foundry`:

```sh
pytest -q -m integration tests/integration/test_real_youtrack_smoke.py
```

Normal local and pull-request suites may exclude it explicitly with `-m
"not integration"`; without configuration it is safe and skipped before any network
request.

## Audit and cleanup

Each issue title/body, comment, and ADR title/body contains an unpredictable marker
of the form `[foundry-smoke:<run-id>]`. Cleanup runs from `finally`, rediscovers tagged
artifacts to cover a create whose response failed, and attempts every article and
issue deletion even after an earlier deletion fails. A DELETE 404 means the artifact
is already gone and is treated as success. Other cleanup failures are summarized by
operation, HTTP status, and exception type; they do not replace an earlier smoke
failure. Tokens, authorization headers, provider response bodies, and secret values
are never included in these diagnostics.

An id returned by a create response is never trusted as a DELETE target. Before an
issue is registered and again immediately before deletion, cleanup rereads it and
requires the configured native project id/key plus the exact run marker; it deletes
the reread id. Article refs are likewise admitted and revalidated only through the
configured project's project-scoped article listing. A failed proof skips that
candidate, does not stop cleanup of other artifacts, and is reported only through the
sanitized failure summary. The functional cycle stops at that creation stage and uses
only the reread id/ref, so no later link, transition, comment, or ADR status mutation
can target an unproved create response.

The shared YouTrack HTTP client follows relative and same-origin redirects (including
equivalent explicit default ports). It refuses a redirect before the next request if
the scheme, host, or effective port changes, so the bearer token and request body
cannot cross an origin boundary.

## Public CI boundary

The public GitHub workflow never runs this mutating smoke test. Its pull-request jobs
are secret-free and use only GitHub-hosted ephemeral runners; fork pull requests create
no executable job. Run the smoke manually only from a trusted developer environment,
with the required environment above configured for a disposable YouTrack project.

Do not add the smoke token or its target configuration as public-repository Actions
secrets or variables. A missing configuration remains an explicit local pytest skip;
it is not a reason to weaken the production-project guard or to run this mutation from
public CI.
