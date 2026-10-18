"""Versioned navigation file: how to reach the screen to prove on iOS (PAT-255).

JSON, schema `witness-navigation.v1`, kept in the app's repository:
  {"schema": "witness-navigation.v1", "screen": "<expected screen name>",
   "mechanism": "deeplink", "url": "<scheme>://..."}
  {"schema": "witness-navigation.v1", "screen": "<expected screen name>",
   "mechanism": "uitest", "test": "<UITarget>/<Class>/<test>", "attachment": "<name filter, optional>"}
The screen name is recorded, never verified: witness cannot read the screen.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SCHEMA = "witness-navigation.v1"
MECHANISMS = ("deeplink", "uitest")
MAX_BYTES = 64 * 1024
_TEST_ID = re.compile(r"^[A-Za-z0-9_.+-]+(/[A-Za-z0-9_.()+-]+){0,2}$")
_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://\S*$")


class NavigationError(Exception):
    pass


def _text(data: dict, key: str, required: bool = True) -> str | None:
    value = data.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise NavigationError(f"navigation file: {key!r} must be a non-empty string")
    return value


def load(path: Path) -> dict:
    """Validated navigation: schema, mechanism, screen, url|test, attachment, sha256 (of the file bytes)."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise NavigationError(f"navigation file cannot be read ({type(exc).__name__})") from None
    if len(raw) > MAX_BYTES:
        raise NavigationError(f"navigation file is larger than {MAX_BYTES} bytes")
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise NavigationError("navigation file is not valid UTF-8 JSON") from None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise NavigationError(f"navigation file: schema must be {SCHEMA!r}")
    mechanism = data.get("mechanism")
    if mechanism not in MECHANISMS:
        raise NavigationError(f"navigation file: mechanism must be one of {', '.join(MECHANISMS)}")
    nav = {"schema": SCHEMA, "mechanism": mechanism, "screen": _text(data, "screen"),
           "sha256": hashlib.sha256(raw).hexdigest()}
    if mechanism == "deeplink":
        url = _text(data, "url")
        if not _URL.match(url):
            raise NavigationError("navigation file: 'url' must be an absolute URL with a scheme and no whitespace")
        nav["url"] = url
    else:
        test = _text(data, "test")
        if not _TEST_ID.match(test):
            raise NavigationError("navigation file: 'test' must be Target[/Class[/test]]")
        nav["test"] = test
        nav["attachment"] = _text(data, "attachment", required=False)
    return nav


def identity(nav: dict | None) -> str:
    """What two proofs must share to be comparable: mechanism and file content hash."""
    return "none" if not nav else f"ios:{nav['mechanism']}:{nav['sha256']}"
