#!/usr/bin/env python3
"""Generate shipped Claude pin profiles without invoking a host or touching a cache."""
from pathlib import Path

from foundry.routing_facades import claude_pin_profile_documents


def main():
    root = Path(__file__).resolve().parents[1]
    for name, text in claude_pin_profile_documents(root).items():
        (root / "agents" / f"{name}.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
