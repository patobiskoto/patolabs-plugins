#!/usr/bin/env python3
"""relay: verified, secret-free session resume state (PAT-248)."""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from relay import load, schema, secrets, verify

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
    os.chmod(path.parent, 0o700)  # mkdir's mode does not apply to an existing directory
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
    if args.draft != "-" and not Path(args.draft).is_file():
        return fail(f"draft file not found: {args.draft}")
    try:
        if args.draft == "-":
            buf = getattr(sys.stdin, "buffer", None)
            raw = buf.read().decode("utf-8") if buf is not None else sys.stdin.read()
        else:
            raw = Path(args.draft).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # The exception text may quote draft bytes: only its type is shown.
        return fail(f"the draft cannot be read as UTF-8 text ({type(exc).__name__})")
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        return fail("the draft is not valid JSON or is nested too deeply")
    # Secrets first: a refusal message must never echo a draft value or key.
    try:
        hits = secrets.find(data)
    except RecursionError:
        return fail("the draft is nested too deeply")
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
    # Second scan, on the final state: values read from git, gh and Foundry are not the draft's.
    hits = secrets.find(state)
    if hits:
        print("relay: state refused, nothing written: possible secret(s) in the verified "
              "state (values not shown):", file=sys.stderr)
        for loc, name in hits:
            print(f"  - {loc}: {name}", file=sys.stderr)
        return 3
    try:
        write_atomic(path, json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    except (OSError, UnicodeEncodeError) as exc:  # e.g. a relay/ owned by another user, a lone surrogate
        return fail(f"nothing written: the state cannot be stored ({type(exc).__name__})")
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


def well_formed(state: object) -> bool:
    """What `show` reads: `real` and `state` objects, a valid draft, complete checks."""
    if not isinstance(state, dict):
        return False
    real, s, checks = state.get("real"), state.get("state"), state.get("verification", [])
    return (isinstance(real, dict)
            and all(isinstance(real.get(k), (str, type(None))) for k in ("branch", "head"))
            and isinstance(s, dict) and not schema.validate(s)[1]
            and isinstance(checks, list)
            and all(isinstance(c, dict) and all(isinstance(c.get(k), str)
                                                for k in ("check", "status", "detail"))
                    for c in checks))


def cmd_show(args: argparse.Namespace) -> int:
    root = repo_root()
    if root is None:
        return fail("not inside a git repository")
    path = state_path(root)
    if path is None:
        return fail("cannot locate the git common directory")
    try:
        if not path.is_file():
            return fail(f"no state written yet ({path})", 1)
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return fail("the stored state is unreadable or malformed", 1)
    if not well_formed(state):
        return fail("the stored state is unreadable or malformed", 1)
    if args.json:
        print(json.dumps(state, indent=2, ensure_ascii=False))
        return 0
    print("\n".join(load.render_state(state)))
    print(render_verification(state.get("verification", [])))
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    """Present the last state re-verified now. Always exit 0 (hook path): problems are said."""
    root = repo_root()
    if root is None:
        print("relay: not inside a git repository, no resume state to load.")
        return 0
    state, message = load.read_state(state_path(root))
    print(message if state is None else load.present(state, root, args.budget))
    return 0


def budget_seconds(text: str) -> float:
    value = float(text)  # ValueError becomes an argparse error
    if not math.isfinite(value) or value < 0:
        raise ValueError(text)
    return value


def main() -> int:
    p = argparse.ArgumentParser(prog="relay_cli.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write", help="verify, scan and write the resume state")
    w.add_argument("--draft", required=True, help="JSON draft file, or - for stdin")
    w.set_defaults(fn=cmd_write)
    s = sub.add_parser("show", help="print the last written state")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_show)
    ld = sub.add_parser("load", help="print the last state, re-verified now (always exit 0)")
    ld.add_argument("--budget", type=budget_seconds, default=verify.BUDGET,
                    help="total seconds, finite and >= 0, for the re-verification (default: BUDGET)")
    ld.set_defaults(fn=cmd_load)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
