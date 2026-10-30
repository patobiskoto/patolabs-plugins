"""Print the GitHub Release body of a version (PLP-29). Read-only.

The body is the published ``## <version> — <date>`` section of ``CHANGELOG.md``, read with
the release contract's own ``changelog_sections`` (``tests/test_release_contract.py``). A
body over ``GITHUB_BODY_LIMIT`` characters is cut at a line boundary and ends with a link to
the CHANGELOG at the tag ``foundry-v<version>``. A version with no section, or an empty one,
is refused with a non-zero exit and nothing printed. Without ``--output`` the body goes to
standard output; with it, to that one file. Nothing else is written; the release step passes
the file to ``gh release create --notes-file``.
"""
import argparse
import re
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "tests"))
import test_release_contract as contract  # noqa: E402

# Documented GitHub limit on the body of a Release (characters).
GITHUB_BODY_LIMIT = 125000
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def release_body(version, repo, limit=GITHUB_BODY_LIMIT):
    """Return the Release body, or raise ``ValueError`` when the section is absent or empty."""
    contract.parse_version(version)
    sections = {v: text for v, _date, text in contract.changelog_sections()}
    if version not in sections:
        raise ValueError(f"no published CHANGELOG section for {version}")
    section = sections[version]
    _heading, _, entries = section.partition("\n")
    if not entries.strip():
        raise ValueError(f"the CHANGELOG section of {version} is empty")
    if len(section) + 1 <= limit:
        return section + "\n"
    link = f"https://github.com/{repo}/blob/foundry-v{version}/plugins/foundry/CHANGELOG.md"
    tail = f"\n\n_Truncated: the full section is in the [CHANGELOG]({link})._\n"
    kept, size = [], 0
    for line in section.split("\n"):
        if size + len(line) + 1 + len(tail) > limit:
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join(kept).rstrip("\n") + tail


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--repo", required=True, help="OWNER/REPO, for the truncation link")
    parser.add_argument("--output", help="write the body to this file instead of stdout")
    args = parser.parse_args(argv)
    if not REPO.fullmatch(args.repo):
        sys.exit(f"--repo must be OWNER/REPO: {args.repo}")
    try:
        body = release_body(args.version, args.repo)
    except ValueError as error:
        sys.exit(str(error))
    if args.output:
        Path(args.output).write_text(body, encoding="utf-8")
    else:
        sys.stdout.write(body)


if __name__ == "__main__":
    main()
