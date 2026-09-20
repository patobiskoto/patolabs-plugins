"""Real YouTrack smoke; destructive only inside an explicitly confirmed test project."""
from __future__ import annotations

import pytest

from foundry.youtrack_smoke import SmokeDisabled, SmokeSettings, run_smoke


pytestmark = pytest.mark.integration


def test_real_youtrack_foundry_cycle():
    try:
        settings = SmokeSettings.from_env()
    except SmokeDisabled as exc:
        pytest.skip(str(exc))

    result = run_smoke(settings)

    assert result.issue_ids[0] != result.issue_ids[1]
    assert result.adr_id.startswith(f"{settings.project.key}-ADR-")
