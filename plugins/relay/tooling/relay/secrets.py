"""Secret patterns (PAT-248). A match refuses the write; the value is never returned."""
from __future__ import annotations

import re
from typing import Any

# (name, compiled pattern). Order does not matter: every pattern is tried.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("github-fine-grained-token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}")),
    ("openai-style-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("linear-key", re.compile(r"\blin_(?:api|oauth)_[A-Za-z0-9]{20,}")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("pem-private-key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{5,}")),
    (
        "credential-assignment",
        re.compile(
            r"(?i)(?<![A-Za-z0-9])(?:[A-Za-z0-9]+[_-])*"
            r"(?:password|passwd|secret|token|api[_-]?key|access[_-]?key)(?:_[A-Za-z0-9_]*)?"
            r"[\"']?\s*[:=]\s*[\"']?[^\s\"',;]{8,}"
        ),
    ),
    # pwd is also the shell's working directory: a path value is not a credential.
    (
        "credential-assignment-pwd",
        re.compile(
            r"(?i)(?<![A-Za-z0-9])(?:[A-Za-z0-9]+[_-])*pwd(?:_[A-Za-z0-9_]*)?"
            r"[\"']?\s*[:=]\s*[\"']?(?![/~.]|[A-Za-z]:[\\/])[^\s\"',;]{8,}"
        ),
    ),
    ("authorization-bearer",
     re.compile(r"(?i)\bauthorization\s*[:=]\s*[\"']?bearer\s+[A-Za-z0-9._~+/=-]{8,}")),
    ("url-credentials", re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")),
    ("youtrack-permanent-token", re.compile(r"\bperm:[A-Za-z0-9+/=._-]{16,}")),
)


def find(value: Any, path: str = "$") -> list[tuple[str, str]]:
    """Return (location, pattern name) for every string in `value` matching a pattern.

    Object keys are scanned too. The matched text is never part of the result.
    """
    hits: list[tuple[str, str]] = []
    if isinstance(value, str):
        for name, pattern in PATTERNS:
            if pattern.search(value):
                hits.append((path, name))
    elif isinstance(value, dict):
        for key, item in value.items():
            # Only plain identifiers appear in a location: a key is never echoed otherwise.
            key_hits = find(key, f"{path}.<key>")
            name = key if key.isidentifier() and len(key) <= 40 and not key_hits else "<key>"
            hits += key_hits
            hits += find(item, f"{path}.{name}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            hits += find(item, f"{path}[{i}]")
    return hits
