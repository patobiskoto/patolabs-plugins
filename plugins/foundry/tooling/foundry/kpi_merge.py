"""PLP-343: publish the KPI records of an issue once its merge succeeded (PLP-ADR-0063).

Called by ``issue.merge`` after the merge and the ``done`` transition, in the ``issue merge`` process: never
from a hook or a gate. A campaign runs ``issue merge`` (``campaign_effects.py``, ``campaign_runtime.py``), so
publication also happens at campaign merges. It reuses the export, the log discovery and the publisher as they are
(``kpi_export.build``, ``kpi_sessions.discover``, ``kpi_publish.publish``) and adds no logic of its own to them.

It is never a gate: ``publish_after_merge`` catches every failure, including an unexpected one, and returns
one line. Nothing else is printed, so only that line reaches the agent context; it holds counts and fixed
words, never a path, a URL, a token or a server message.
"""
from __future__ import annotations

import json

from foundry import kpi_export, kpi_publish, kpi_sessions, registry

CATCH_UP = "run `routing kpi-export` then `routing kpi-publish` to catch up"


def summary(report: dict) -> str:
    totals = report["totals"]
    revised = report["revised"]
    return (f"kpi: created={totals['created'] - revised} revised={revised} replayed={totals['replayed']} "
            f"refused={totals['refused']} not_sent={totals['unsent']}")


def publish_after_merge(tracker, issue_id: str, repository: str, *, root=None) -> tuple[str | None, bool]:
    """(line to show, is_warning). ``(None, False)`` for a tracker without the capability: nothing is said."""
    if not getattr(tracker, "kpi_publication_supported", False):
        return None, False
    try:
        project = str(issue_id).split("-", 1)[0]
        try:
            base, token = kpi_publish.endpoint()  # first: with no URL or token, no log is read
        except SystemExit:  # config.require / require_public: DEVHUB_URL or the token is not configured
            raise kpi_publish.PublishError(f"DEVHUB_URL or {kpi_publish.TOKEN_KEY} not configured") from None
        claude, codex = kpi_sessions.discover(
            [issue_id], repository, roots=kpi_sessions.repository_roots(root))
        document = json.loads(kpi_export.build(
            [issue_id], repository, claude_sessions=claude, codex_sessions=codex))
        kpi_publish.validate_document(document, project)
        report = kpi_publish.publish(
            document, project, url=base, token=token,
            ledger=kpi_publish.PublishedLedger(registry.data_dir(), base, project))
        line = summary(report)
        if report["totals"]["refused"] or report["totals"]["unsent"]:
            return f"{line}; {CATCH_UP}", True
        return line, False
    except kpi_publish.PublishError as exc:
        reason = str(exc)
    except SystemExit:  # raised by discover, build or the ledger: unknown whether they can
        reason = "unexpected SystemExit"
    except Exception as exc:  # never a gate: the merge is already done
        reason = f"unexpected {type(exc).__name__}"
    return f"kpi: publication skipped ({reason}); {CATCH_UP}", True
