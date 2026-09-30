"""Create and push one release tag on an exact merged commit, with safe replay."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        check=False,
    )


def publish_tag(repo: Path, version: str, merged_sha: str) -> str:
    """Return created, resumed, or already_pushed; never move an existing tag."""
    if re.fullmatch(r"[0-9][A-Za-z0-9._-]*", version) is None:
        raise ValueError("invalid release version")
    if re.fullmatch(r"[0-9a-f]{40}", merged_sha) is None:
        raise ValueError("exact merged commit SHA required")
    if _git(repo, "cat-file", "-t", merged_sha).stdout.strip() != "commit":
        raise ValueError("merged SHA is not a local commit; fetch origin first")
    ref = f"refs/tags/v{version}"
    local = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{}}")
    if local.returncode not in (0, 1):
        raise RuntimeError("local tag read failed")
    local_sha = local.stdout.strip() if local.returncode == 0 else None
    if local_sha is not None and local_sha != merged_sha:
        raise ValueError("local release tag points to another commit")

    remote = _git(repo, "ls-remote", "--tags", "origin", ref, f"{ref}^{{}}")
    if remote.returncode != 0:
        raise RuntimeError("remote tag read failed")
    remote_refs = {}
    for line in remote.stdout.splitlines():
        sha, separator, name = line.partition("\t")
        if separator != "\t" or name not in (ref, f"{ref}^{{}}"):
            raise RuntimeError("remote tag response invalid")
        remote_refs[name] = sha
    remote_sha = remote_refs.get(f"{ref}^{{}}") or remote_refs.get(ref)
    if remote_sha is not None:
        if remote_sha != merged_sha:
            raise ValueError("remote release tag points to another commit")
        return "already_pushed"

    if local_sha is None:
        if _git(repo, "tag", f"v{version}", merged_sha).returncode != 0:
            raise RuntimeError("local tag creation failed")
        action = "created"
    else:
        action = "resumed"
    if _git(repo, "push", "origin", f"{ref}:{ref}").returncode != 0:
        raise RuntimeError("single release tag push failed; inspect remote before replay")
    return action


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("version")
    parser.add_argument("merged_sha")
    parser.add_argument("--repo", default=".")
    args = parser.parse_args()
    try:
        action = publish_tag(Path(args.repo), args.version, args.merged_sha)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"Release tag refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"tag": f"v{args.version}", "merged_sha": args.merged_sha,
                      "action": action}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
