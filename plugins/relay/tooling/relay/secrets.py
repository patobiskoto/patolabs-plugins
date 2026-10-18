"""Secret patterns (PAT-248). A match refuses the write; the value is never returned."""
from __future__ import annotations

import re
from typing import Any

# `NAME = value` / `NAME: value`. Only the name and the separator are consumed, so the value
# is read separately and the next candidate may start inside it.
_ASSIGN = re.compile(r"(?<![A-Za-z0-9_.-])([A-Za-z0-9_.-]+)[\"']?\s*(?::=|[:=])\s*")
# A quoted value may hold spaces (a passphrase); an unquoted one stops at a space or quote.
_VALUE = re.compile(r"\"([^\"\n]{8,})\"|'([^'\n]{8,})'|[\"']?([^\s\"',;]{8,})")
# A shell reference ($VAR, ${VAR}, $(cmd ...)) names where the secret is, it is not one.
_SHELL_REF = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[^}]*\}|\(.*)")
_PATH_START = re.compile(r"[/~.]|[A-Za-z]:[\\/]")
# Name words: camelCase and UPPER runs split, separators `_`, `-`, `.` dropped.
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
# `key` is a credential word only after one of these (`api_key`, `accessKey`, `SECRETKEY`).
_KEY_QUALIFIERS = ("api", "access", "secret")
# A credential word followed by one of these names a quantity, not a value (`token_count`).
_QUANTITY_WORDS = frozenset({"count", "limit", "budget", "length", "size"})


def _name_words(name: str) -> list[str]:
    words: list[str] = []
    for w in (x.lower() for x in _WORD.findall(name)):
        if len(w) > 3 and w.endswith("key"):  # glued `APIKEY`, `secretkey`
            words += [w[:-3], "key"]
        else:
            words.append(w)
    return words


class _Assignment:
    """An assignment whose name holds a credential word, with a value of 8+ characters.

    A word counts when it ends with a stem (`PGPASSWORD`, `authToken`), or is `key` after a
    qualifier, unless the next word is a quantity. Same `search` interface as a regex.
    """

    def __init__(self, stems: tuple[str, ...], key_qualifiers: tuple[str, ...] = (),
                 path_is_safe: bool = False) -> None:
        self.stems = stems
        self.key_qualifiers = key_qualifiers
        self.path_is_safe = path_is_safe

    def _credential_name(self, name: str) -> bool:
        words = _name_words(name)
        for i, w in enumerate(words):
            hit = w.endswith(self.stems) or (
                w == "key" and i > 0 and words[i - 1].endswith(self.key_qualifiers))
            if hit and (i + 1 == len(words) or words[i + 1] not in _QUANTITY_WORDS):
                return True
        return False

    def search(self, text: str) -> bool:
        for m in _ASSIGN.finditer(text):
            v = _VALUE.match(text, m.end())
            if not v or not self._credential_name(m.group(1)):
                continue
            value = next(g for g in v.groups() if g is not None)
            if _SHELL_REF.fullmatch(value):
                continue
            if self.path_is_safe and _PATH_START.match(value):
                continue
            return True
        return False


# (name, compiled pattern or _Assignment). Order does not matter: every pattern is tried.
PATTERNS: tuple[tuple[str, re.Pattern[str] | _Assignment], ...] = (
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("github-fine-grained-token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}")),
    ("openai-style-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("linear-key", re.compile(r"\blin_(?:api|oauth)_[A-Za-z0-9]{20,}")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("pem-private-key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{5,}")),
    ("credential-assignment",
     _Assignment(("password", "passwd", "secret", "token"), key_qualifiers=_KEY_QUALIFIERS)),
    # pwd is also the shell's working directory: a path value is not a credential.
    ("credential-assignment-pwd", _Assignment(("pwd",), path_is_safe=True)),
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
