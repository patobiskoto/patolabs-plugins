"""Opt-in destructive smoke against an explicitly disposable DevHubTracker instance."""
import os

import pytest

from foundry.devhub_smoke import _CONFIRM, run_smoke
from foundry.trackers.devhub import DevHubTracker


pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.environ.get("DEVHUB_SMOKE_CONFIRM") != _CONFIRM,
    reason="destructive DevHub smoke not explicitly authorized",
)
def test_real_devhub_tracker_smoke():
    key = os.environ.get("DEVHUB_SMOKE_PROJECT_KEY")
    repository = os.environ.get("DEVHUB_SMOKE_REPOSITORY")
    if not key or not repository:
        pytest.skip("explicit disposable project coordinates missing")

    result = run_smoke(
        DevHubTracker(), key=key, repository=repository,
        name=os.environ.get("DEVHUB_SMOKE_PROJECT_NAME", "Foundry DevHubTracker smoke"),
    )

    assert result["state"] == "done"
    assert result["issue_visible"] is True
    assert result["adr_visible"] is True
    assert result["link_round_trip"] is True
    assert result["search_link_round_trip"] is True
    assert result["search_inverse_round_trip"] is True
    assert result["comment_round_trip"] is True
    assert result["stale_etag"] == "version_conflict"
    assert result["divergent_replay"] == "idempotency_conflict"
    assert result["concurrency"] == {"success": 1, "version_conflict": 1}
    assert result["audit_verification"] == "public_http"
    assert result["audit_receipts"] >= 7
