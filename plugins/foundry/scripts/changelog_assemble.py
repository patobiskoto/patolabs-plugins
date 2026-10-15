"""Print the dated CHANGELOG section assembled from the fragments (PAT-210). Read-only.

Each unreleased entry is one file ``changelog.d/<ISSUE-ID>.md`` holding one Markdown list
item. The output is ``## <version> — <date>``, a blank line, then the fragments in issue
number descending order (ties broken by the prefix, ascending), each byte-identical. The
hand-written header paragraph of a release goes between the heading and the entries. It
writes nothing: the release step pastes it into ``CHANGELOG.md`` and deletes the fragments.
"""
import argparse
import datetime
import re
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
FRAGMENTS = PLUGIN / "changelog.d"
NAME = re.compile(r"^([A-Za-z]+)-(\d+)\.md$")
# The published heading shape of tests/test_release_contract.py (PUBLISHED_HEADING).
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def fragment_paths(directory=FRAGMENTS):
    found = []
    for path in Path(directory).iterdir():
        if path.name == "README.md" or path.name.startswith(".") or not path.is_file():
            continue
        match = NAME.match(path.name)
        if not match:
            raise ValueError(f"fragment name is not <ISSUE-ID>.md: {path.name}")
        found.append((-int(match.group(2)), match.group(1), path))
    return [path for _, _, path in sorted(found)]


def _check_heading(version, date):
    if not VERSION.match(version):
        raise ValueError(f"version must be X.Y.Z: {version}")
    try:
        if not DATE.match(date):
            raise ValueError
        datetime.date.fromisoformat(date)
    except ValueError:
        raise ValueError(f"date must be a valid YYYY-MM-DD: {date}") from None


def assemble(version, date, directory=FRAGMENTS):
    _check_heading(version, date)
    entries = []
    for path in fragment_paths(directory):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("- ") or not text.endswith("\n") or text.endswith("\n\n"):
            raise ValueError(f"{path.name}: must be one list item ending in one newline")
        if any(line.startswith("- ") for line in text.splitlines()[1:]):
            raise ValueError(f"{path.name}: holds more than one list item")
        entries.append(text)
    if not entries:
        raise ValueError("no changelog fragment to assemble")
    return f"## {version} — {date}\n\n" + "".join(entries)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("date", help="publication date YYYY-MM-DD (Europe/Paris)")
    parser.add_argument("--dir", default=str(FRAGMENTS), help="fragment directory")
    args = parser.parse_args(argv)
    try:
        sys.stdout.write(assemble(args.version, args.date, args.dir))
    except ValueError as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
