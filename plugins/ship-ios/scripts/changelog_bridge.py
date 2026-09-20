#!/usr/bin/env python3
"""Fetch the locale-neutral changelog for a milestone from Foundry, if present.

Deliberately THIN: it only pulls FACTS (the `query changelog` payload) and prints them.
It does NOT write release notes — turning facts into user-facing copy, per locale, is a
language task the ship-ios:release skill does, not a script. Foundry is an OPTIONAL
input: no Foundry, no problem — the skill takes a changelog file instead.

Usage:
  changelog_bridge.py <MILESTONE> [--foundry-cli /path/to/foundry_cli.py]

Exit codes: 0 ok · 3 Foundry not found (caller should fall back to a file).
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _marker_path(home: Path) -> Path:
    # Cross-plugin discovery must not use PLUGIN_DATA/CLAUDE_PLUGIN_DATA: each plugin
    # gets a private directory, so ship-ios cannot read Foundry's marker there.
    return home / ".config/foundry/install.json"


def _marked_cli(home: Path) -> str | None:
    marker = _marker_path(home)
    try:
        value = json.loads(marker.read_text(encoding="utf-8")).get("foundry_cli")
    except (OSError, ValueError, TypeError):
        return None
    return value if value and os.path.isfile(value) else None


def _find_cli(explicit: str | None, *, home: Path | None = None,
              plugin_root: Path | None = None) -> str | None:
    """Find Foundry across dev checkout, Claude cache, Codex cache, or PATH."""
    home = home or Path.home()
    plugin_root = plugin_root or Path(__file__).resolve().parents[1]
    direct = [
        explicit,
        os.environ.get("FOUNDRY_CLI"),
        _marked_cli(home),
        str(plugin_root.parent / "foundry/tooling/foundry_cli.py"),
        str(home / ".claude/plugins/marketplaces/patolabs/plugins/foundry/tooling/foundry_cli.py"),
        shutil.which("foundry_cli.py"),
    ]
    cache_patterns = (
        home / ".claude/plugins/cache/patolabs/foundry/*/tooling/foundry_cli.py",
        home / ".codex/plugins/cache/patolabs/foundry/*/tooling/foundry_cli.py",
    )
    cached = [Path(path) for pattern in cache_patterns for path in glob.glob(str(pattern))]
    cached.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    seen = set()
    for guess in [*direct, *(str(path) for path in cached)]:
        if guess and guess not in seen and os.path.isfile(guess):
            return guess
        seen.add(guess)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("milestone")
    ap.add_argument("--foundry-cli")
    args = ap.parse_args()

    cli = _find_cli(args.foundry_cli)
    if not cli:
        print("Foundry introuvable — passe un fichier changelog au skill à la place.",
              file=sys.stderr)
        return 3

    r = subprocess.run(["python3", cli, "query", "changelog", args.milestone],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr or r.stdout, file=sys.stderr)
        return r.returncode
    # validate it's the shape we expect, then re-emit verbatim (facts, untouched)
    data = json.loads(r.stdout)
    print(json.dumps({"milestone": data.get("milestone"),
                      "count": data.get("count", 0),
                      "groups": data.get("groups", {})},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
