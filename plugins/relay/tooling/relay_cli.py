#!/usr/bin/env python3
"""relay: verified, secret-free session resume state (PAT-248)."""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from relay import schema, secrets, verify

STATE_DIR = "relay"
STATE_FILE = "state.json"


def repo_root() -> Path | None:
    rc, out = verify.run(["git", "rev-parse", "--show-toplevel"], Path.cwd())
    return Path(out.strip()) if rc == 0 and out.strip() else None


def state_path(root: Path) -> Path | None:
    """<git-common-dir>/relay/state.json: inside .git, so never tracked, shared by worktrees."""
    rc, out = verify.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], root)
    if rc != 0 or not out.strip():
        return None
    return Path(out.strip()) / STATE_DIR / STATE_FILE


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".state-")  # created 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def fail(msg: str, code: int = 2) -> int:
    print(f"relay: {msg}", file=sys.stderr)
    return code


def cmd_write(args: argparse.Namespace) -> int:
    root = repo_root()
    if root is None:
        return fail("not inside a git repository")
    path = state_path(root)
    if path is None:
        return fail("cannot locate the git common directory")
    if args.draft == "-":
        raw = sys.stdin.read()
    elif Path(args.draft).is_file():
        raw = Path(args.draft).read_text(encoding="utf-8")
    else:
        return fail(f"draft file not found: {args.draft}")
    try:
        data = json.loads(raw)
    except ValueError:
        return fail("the draft is not valid JSON")
    # Secrets first: a refusal message must never echo a draft value or key.
    hits = secrets.find(data)
    if hits:
        print("relay: draft refused, nothing written: possible secret(s) found "
              "(values not shown); remove them and retry:", file=sys.stderr)
        for loc, name in hits:
            print(f"  - {loc}: {name}", file=sys.stderr)
        return 3
    draft, errors = schema.validate(data)
    if errors:
        print("relay: draft refused, nothing written:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 2
    real, checks = verify.verify(draft, root)
    state = {
        "written_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "real": real,
        "verification": checks,
        "state": draft,
    }
    write_atomic(path, json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    print(f"relay: state written to {path} (mode 600)")
    print(render_verification(checks))
    return 0


def render_verification(checks: list[dict]) -> str:
    bad = [c for c in checks if c["status"] != "ok"]
    div = sum(c["status"] == "divergence" for c in bad)
    unk = sum(c["status"] == "unknown" for c in bad)
    lines = [f"verification: {len(checks) - len(bad)} ok, {div} divergence(s), {unk} unknown"]
    lines += [f"  [{c['status']}] {c['check']}: {c['detail']}" for c in bad]
    return "\n".join(lines)


def cmd_show(args: argparse.Namespace) -> int:
    root = repo_root()
    path = state_path(root) if root else None
    if path is None:
        return fail("not inside a git repository")
    if not path.is_file():
        return fail(f"no state written yet ({path})", 1)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        s = state["state"]
    except (ValueError, KeyError, TypeError):
        return fail("the stored state is unreadable or malformed", 1)
    if args.json:
        print(json.dumps(state, indent=2, ensure_ascii=False))
        return 0
    print(f"written {state.get('written_at')} on {state['real'].get('branch')} "
          f"@ {str(state['real'].get('head'))[:12]}")
    print(f"summary: {s['summary']}")
    for title, key in (("decisions", "decisions"), ("blockers", "blockers"),
                       ("pitfalls", "pitfalls"), ("files", "files"),
                       ("issues", "issues"), ("prs", "prs")):
        if s.get(key):
            print(f"{title}:")
            print("\n".join(f"  - {x}" for x in s[key]))
    print(f"next step: {s['next_step']}")
    print(render_verification(state.get("verification", [])))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="relay_cli.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write", help="verify, scan and write the resume state")
    w.add_argument("--draft", required=True, help="JSON draft file, or - for stdin")
    w.set_defaults(fn=cmd_write)
    s = sub.add_parser("show", help="print the last written state")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_show)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
