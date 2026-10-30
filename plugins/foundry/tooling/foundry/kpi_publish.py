"""PLP-325: send the batches of a `foundry-kpi-export.v1` document to DevHub (PLP-ADR-0061).

The publisher is distinct from the pure reader `kpi_export.py` (FOUNDRY-ADR-0015): it is reached only by
the explicit CLI verb `routing kpi-publish` and, since PLP-343 (PLP-ADR-0063), by the step that follows a
successful `issue merge` (`kpi_merge.py`), never by a hook or a gate. `issue merge` also runs in a campaign
(`campaign_effects.py`, `campaign_runtime.py`), so publication also happens at campaign merges. Only
`kpi_merge.summary` reads its report, to print one line. It never changes routing, delivery or tracker state.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

EXPORT_SCHEMA = "foundry-kpi-export.v1"
CONTRACT = "devhub-observations.v2"
REPORT_SCHEMA = "foundry-kpi-publish.v1"
TOKEN_KEY = "DEVHUB_METRICS_TOKEN"
MAX_BATCH_RECORDS = 100          # OBSERVATION_BATCH_MAX_RECORDS, DevHub observation-contract.ts
MAX_BATCH_BYTES = 1 << 20        # OBSERVATION_BATCH_MAX_BYTES, docs/TRACKER_API.md of DevHub
MAX_DOCUMENT_BYTES = 64 << 20
MAX_RESPONSE_BYTES = 1 << 20
_PROJECT = re.compile(r"[A-Za-z][A-Za-z0-9]{0,31}\Z")
_CODE = re.compile(r"[a-z0-9_]{1,80}\Z")
_STATUSES = {"created", "replayed", "error"}
LEDGER_SCHEMA = "foundry-kpi-published.v1"
_LEDGER_KEYS = ("record_id", "revision", "supersedes_id", "fingerprint", "started_at", "observed_at")


class PublishedLedger:
    """What was published, per chain, kept locally (PLP-342, PLP-ADR-0062).

    One JSON file per (DevHub URL, project) under ``<state_dir>/kpi-publish/``. An entry holds the
    record id, revision, supersedes id, source fingerprint and two times of the record last
    ``created`` or ``replayed`` for the chain ``kind|issue|attempt_id``: opaque ids and numbers
    only, no secret, no path, no token, no content. An unreadable file counts as empty: the next
    publication then sends revision 1 again, which DevHub answers ``replayed`` for an unchanged
    record (same record id, same body) and refuses for a record that was revised before the loss.
    """

    def __init__(self, state_dir, url: str, project: str):
        name = hashlib.sha256(f"{url}\n{project}".encode("utf-8")).hexdigest()[:32]
        self.path = Path(state_dir) / "kpi-publish" / f"{name}.json"
        self.chains: dict[str, dict] = {}
        self.dirty = False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            chains = data["chains"] if data.get("schema") == LEDGER_SCHEMA else {}
            self.chains = {k: v for k, v in chains.items() if isinstance(k, str) and isinstance(v, dict)
                           and set(v) == set(_LEDGER_KEYS) and isinstance(v["fingerprint"], str)
                           and isinstance(v["record_id"], str) and isinstance(v["revision"], int)
                           and isinstance(v["started_at"], int) and isinstance(v["observed_at"], int)}
        except (OSError, ValueError, KeyError, AttributeError, TypeError):
            self.chains = {}

    @staticmethod
    def chain_key(record: dict) -> str | None:
        source, anchor = record.get("source"), record.get("anchor")
        if (not isinstance(source, dict) or not isinstance(anchor, dict)
                or not isinstance(source.get("fingerprint"), str)
                or not all(isinstance(record.get(k), str) for k in ("kind", "attempt_id"))
                or not isinstance(anchor.get("issue_id"), str)
                or not all(isinstance(record.get(k), int) for k in ("started_at", "observed_at"))):
            return None
        return f"{record['kind']}|{anchor['issue_id']}|{record['attempt_id']}"

    def revise(self, record: dict) -> dict:
        """The record to send: unchanged for a new chain, else the revision this ledger implies."""
        key = self.chain_key(record)
        entry = self.chains.get(key) if key else None
        if entry is None:
            return record
        if entry["fingerprint"] == record["source"]["fingerprint"]:
            # Same source as the last published record: send the body that was published.
            return {**record, "revision": entry["revision"], "supersedes_id": entry["supersedes_id"],
                    "observed_at": entry["observed_at"]}
        return {**record, "revision": entry["revision"] + 1, "supersedes_id": entry["record_id"],
                "observed_at": max(record["observed_at"], entry["observed_at"])}

    def note(self, record: dict) -> None:
        """Remember a record DevHub answered ``created`` or ``replayed``."""
        key = self.chain_key(record)
        if key is None or not isinstance(record.get("revision"), int):
            return
        entry = {"record_id": record["record_id"], "revision": record["revision"],
                 "supersedes_id": record.get("supersedes_id"), "fingerprint": record["source"]["fingerprint"],
                 "started_at": record["started_at"], "observed_at": record["observed_at"]}
        if self.chains.get(key) != entry:
            self.chains[key] = entry
            self.dirty = True

    def save(self) -> None:
        """Atomic replace, mode 0600."""
        if not self.dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=".kpi-published-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"schema": LEDGER_SCHEMA, "chains": self.chains}, handle, sort_keys=True,
                          separators=(",", ":"))
                handle.write("\n")
            os.replace(temporary, self.path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        self.dirty = False


class PublishError(Exception):
    """A refusal before any request: nothing was written."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def load_document(path: str, project: str) -> dict:
    """Read and validate the document and the project binding; no network."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        raise PublishError("export file unreadable") from None
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise PublishError("export file too large")
    try:
        document = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError):
        raise PublishError("export file is not valid JSON") from None
    return validate_document(document, project)


def validate_document(document, project: str) -> dict:
    """Validate a parsed export document and its project binding; no network."""
    if not _PROJECT.fullmatch(project):
        raise PublishError("invalid project key")
    if not isinstance(document, dict) or document.get("export_schema") != EXPORT_SCHEMA:
        raise PublishError(f"export file is not a {EXPORT_SCHEMA} document")
    batches = document.get("batches")
    if not isinstance(batches, list):
        raise PublishError("export file has no batches")
    prefix = f"{project}-"
    for batch in batches:
        records = batch.get("records") if isinstance(batch, dict) else None
        if (not isinstance(batch, dict) or batch.get("schema_version") != CONTRACT
                or not isinstance(records, list) or not 1 <= len(records) <= MAX_BATCH_RECORDS):
            raise PublishError("malformed batch")
        for record in records:
            anchor = record.get("anchor") if isinstance(record, dict) else None
            issue = anchor.get("issue_id") if isinstance(anchor, dict) else None
            if not isinstance(record.get("record_id") if isinstance(record, dict) else None, str):
                raise PublishError("record without record_id")
            if not isinstance(issue, str) or not issue.startswith(prefix):
                raise PublishError(f"a record is anchored outside project {project}")
    return document


def _post(url: str, project: str, token: str, body: bytes, timeout: float) -> dict:
    """One POST; returns {"results": [...]} or {"error": <controlled code>}. The token is never echoed."""
    request = urllib.request.Request(
        f"{url}/api/economics/v2/projects/{urllib.parse.quote(project, safe='')}/observations:batch",
        data=body, method="POST",
    )
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/json")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            if response.headers.get("X-DevHub-Contract") != CONTRACT:
                return {"error": "contract_mismatch"}
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        code = "provider_error"
        try:
            candidate = json.loads(error.read(16_384).decode("utf-8"))["error"]["code"]
            if isinstance(candidate, str) and _CODE.fullmatch(candidate):
                code = candidate
        except Exception:
            pass
        return {"error": f"http_{error.code}_{code}"}
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
        return {"error": "tracker_unavailable"}
    if len(raw) > MAX_RESPONSE_BYTES:
        return {"error": "response_too_large"}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return {"error": "invalid_response"}
    return {"payload": payload}


def publish(document: dict, project: str, *, url: str, token: str, timeout: float = 10.0,
            ledger: PublishedLedger | None = None) -> dict:
    """Send each batch once, in order, and report per record. No retry: a republication is idempotent.

    With a ``ledger`` (PLP-342) a record whose chain was already published with another source
    fingerprint is sent as the next revision (``revision`` + 1, ``supersedes_id`` the previous
    ``record_id``); the ledger is updated from the per-record ``created``/``replayed`` answers only,
    after each batch. Without one the batches are sent as they are."""
    totals = {"created": 0, "replayed": 0, "refused": 0, "unsent": 0}
    revised = 0  # PLP-343: records answered ``created`` that supersede a published record
    batch_reports, refusals = [], []
    for index, batch in enumerate(document["batches"]):
        records = [ledger.revise(r) for r in batch["records"]] if ledger else batch["records"]
        batch = {**batch, "records": records}
        body = json.dumps(batch, ensure_ascii=False, separators=(",", ":")).encode()
        report = {"index": index, "records": len(records)}
        if len(body) > MAX_BATCH_BYTES:
            outcome = {"error": "batch_too_large"}
        else:
            outcome = _post(url, project, token, body, timeout)
        results = None
        if "payload" in outcome:
            payload = outcome["payload"]
            results = payload.get("results") if isinstance(payload, dict) else None
            if (not isinstance(payload, dict) or payload.get("schema_version") != CONTRACT
                    or not isinstance(results, list) or len(results) != len(records)
                    or any(not isinstance(r, dict) or r.get("status") not in _STATUSES
                           or r.get("record_id") != rec["record_id"]
                           for r, rec in zip(results, records))):
                outcome, results = {"error": "invalid_response"}, None
        if results is None:
            report["error"] = outcome["error"]
            totals["unsent"] += len(records)
        else:
            for result, sent in zip(results, records):
                if result["status"] == "error":
                    code = result.get("code")
                    code = code if isinstance(code, str) and _CODE.fullmatch(code) else "unknown"
                    totals["refused"] += 1
                    refusals.append({"record_id": result["record_id"], "code": code})
                else:
                    totals[result["status"]] += 1
                    revised += result["status"] == "created" and bool(sent.get("supersedes_id"))
                    if ledger:
                        ledger.note(sent)
        if ledger:
            try:
                ledger.save()
            except OSError:
                report["ledger_error"] = "published ledger not written"
        batch_reports.append(report)
    return {"schema": REPORT_SCHEMA, "project": project, "totals": totals,
            "revised": revised, "batches": batch_reports, "refusals": refusals}


def _adapter_class(name: str):
    if name == "devhub":
        from foundry.trackers.devhub import DevHubTracker
        return DevHubTracker
    if name == "linear":
        from foundry.trackers.linear import LinearTracker
        return LinearTracker
    if name == "ghprojects":
        from foundry.trackers.ghprojects import GitHubProjectsTracker
        return GitHubProjectsTracker
    if name == "youtrack":
        from foundry.trackers.youtrack import YouTrackTracker
        return YouTrackTracker
    return None


def endpoint(url: str | None = None) -> tuple[str, str]:
    """(validated DevHub base URL, bearer token) from the config; PublishError or SystemExit when unusable."""
    from foundry import config
    from foundry.trackers import devhub

    base = (url or config.require_public("DEVHUB_URL")).rstrip("/")
    try:
        devhub.validate_base_url(base)
    except ValueError as exc:
        raise PublishError(str(exc)) from None
    token = config.require(TOKEN_KEY)
    if not token.isascii() or any(ord(c) <= 0x20 or ord(c) == 0x7f for c in token):
        raise PublishError(f"{TOKEN_KEY} is not a valid bearer token")
    return base, token


def run(path: str, project: str, *, url: str | None = None, root: str | None = None) -> int:
    """CLI body: refuse without a write unless the active adapter declares the capability. Exit status only."""
    import foundry
    from foundry import registry

    try:
        name, _binding = foundry._effective_tracker_selection(root)
        adapter = _adapter_class(name)
        if not getattr(adapter, "kpi_publication_supported", False):
            raise PublishError(f"tracker '{name}' does not declare KPI publication; nothing sent")
        document = load_document(path, project)
        base, token = endpoint(url)
    except PublishError as exc:
        print(f"kpi-publish refused: {exc}", file=sys.stderr)
        return 2
    except SystemExit as exc:  # config.require/require_public: missing URL or token, nothing sent
        print(f"kpi-publish refused: {exc}", file=sys.stderr)
        return 2
    report = publish(document, project, url=base, token=token,
                     ledger=PublishedLedger(registry.data_dir(), base, project))
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    totals = report["totals"]
    return 1 if totals["refused"] or totals["unsent"] else 0
