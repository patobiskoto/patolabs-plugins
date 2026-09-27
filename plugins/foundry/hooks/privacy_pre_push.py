#!/usr/bin/env python3
"""Inspect new Git objects, without interpreting or changing Foundry proofs."""
from __future__ import annotations

import re
import subprocess
import sys

FORBIDDEN_EMAILS = (b"bpdiv@pm.me", b"baptiste.prealpato@pm.me")


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], capture_output=True, check=True).stdout


def is_zero(value: str) -> bool:
    return bool(value) and set(value) == {"0"}


def violations(raw: bytes) -> list[str]:
    headers, _, message = raw.partition(b"\n\n")
    found = []
    for role in (b"author", b"committer"):
        for line in headers.splitlines():
            if line.startswith(role + b" "):
                match = re.search(rb"<([^<>]*)> -?[0-9]+ [+-][0-9]{4}$", line)
                if not match:
                    raise ValueError("unreadable commit identity")
                if match[1] in FORBIDDEN_EMAILS:
                    found.append(role.decode())
    if any(email in message for email in FORBIDDEN_EMAILS):
        found.append("message/trailers")
    return found


def check_push(remote: str, lines: list[str]) -> None:
    tips = []
    for line in lines:
        fields = line.split()
        if len(fields) != 4:
            raise ValueError("malformed pre-push input")
        _, local_sha, remote_ref, _ = fields
        if not is_zero(local_sha):
            tips.append(local_sha)
    if not tips:
        return
    # Destination refs are authoritative; stale local tracking refs are not.
    # Missing objects fail closed: fetch the destination and retry.
    advertised = git("ls-remote", "--heads", "--tags", "--", remote).decode().splitlines()
    exclusions = set()
    for row in advertised:
        sha, _ = row.split()
        kind = git("cat-file", "-t", sha).strip()
        if kind in {b"commit", b"tag"}:
            peeled = git("rev-parse", sha + "^{}").decode().strip()
            if git("cat-file", "-t", peeled).strip() == b"commit":
                exclusions.add(peeled)
    commits = set()
    for tip in tips:
        kind = git("cat-file", "-t", tip).strip()
        if kind == b"tag" and any(email in git("cat-file", "tag", tip) for email in FORBIDDEN_EMAILS):
            raise ValueError(f"retired identity in annotated tag {tip}")
        peeled = git("rev-parse", tip + "^{}").decode().strip()
        if git("cat-file", "-t", peeled).strip() != b"commit":
            continue
        args = ["rev-list", peeled]
        if exclusions:
            args += ["--not", *sorted(exclusions)]
        commits.update(git(*args).decode().splitlines())
    for sha in sorted(commits):
        fields = violations(git("cat-file", "commit", sha))
        if fields:
            # Do not echo private addresses or messages into logs.
            raise ValueError(f"retired identity in commit {sha} ({', '.join(fields)})")


def main() -> int:
    try:
        if len(sys.argv) != 3:
            raise ValueError("pre-push requires remote name and destination")
        lines = sys.stdin.read().splitlines()
        for line in lines:
            fields = line.split()
            if len(fields) == 4 and fields[2] in {"refs/heads/main", "refs/heads/master"}:
                raise ValueError("direct default-branch push refused; use Foundry PR workflow")
        check_push(sys.argv[2], lines)
    except (ValueError, subprocess.CalledProcessError, OSError) as exc:
        detail = str(exc) if isinstance(exc, ValueError) else "Git inspection failed; fetch destination and retry"
        print(f"Foundry pre-push: {detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
