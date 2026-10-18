"""Draft schema (PAT-248). A malformed draft is refused with every error listed."""
from __future__ import annotations

from typing import Any

TEXT_KEYS = ("summary", "next_step")
LIST_KEYS = ("decisions", "blockers", "pitfalls", "files", "issues", "prs")
OPTIONAL_TEXT_KEYS = ("branch", "commit")
KEYS = TEXT_KEYS + LIST_KEYS + OPTIONAL_TEXT_KEYS


def validate(draft: Any) -> tuple[dict[str, Any], list[str]]:
    """Return (normalised draft, errors). Unknown keys are errors, never dropped."""
    if not isinstance(draft, dict):
        return {}, ["the draft must be a JSON object"]
    errors = [f"unknown key: {k!r}" for k in draft if k not in KEYS]
    out: dict[str, Any] = {}
    for key in TEXT_KEYS:
        v = draft.get(key)
        if not isinstance(v, str) or not v.strip():
            errors.append(f"{key}: required, non-empty string")
        else:
            out[key] = v
    for key in LIST_KEYS:
        v = draft.get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
            errors.append(f"{key}: must be a list of non-empty strings")
        else:
            out[key] = list(v)
    for key in OPTIONAL_TEXT_KEYS:
        if key in draft:
            v = draft[key]
            if not isinstance(v, str) or not v.strip():
                errors.append(f"{key}: must be a non-empty string when present")
            else:
                out[key] = v
    return out, errors
