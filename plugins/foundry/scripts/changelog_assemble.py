"""Print the dated CHANGELOG section assembled from the fragments (PAT-210). Read-only.

Each unreleased entry is one file ``changelog.d/<ISSUE-ID>.md`` holding one Markdown list
item. The output is ``## <version> — <date>``, a blank line, then the fragments in issue
number descending order (ties broken by the prefix, ascending), each byte-identical. The
hand-written header paragraph of a release goes between the heading and the entries. It
writes nothing: the release step pastes it into ``CHANGELOG.md`` and deletes the fragments.
"""
import argparse
import re
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
FRAGMENTS = PLUGIN / "changelog.d"
NAME = re.compile(r"^([A-Za-z]+)-(\d+)\.md$")


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


def assemble(version, date, directory=FRAGMENTS):
    entries = []
    for path in fragment_paths(directory):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("- ") or not text.endswith("\n") or text.endswith("\n\n"):
            raise ValueError(f"{path.name}: must be one list item ending in one newline")
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
