"""Print the release-history freeze entry of a version (PAT-166). Read-only.

Uses the release contract's own ``changelog_sections`` and ``freeze_entry``
(``tests/test_release_contract.py``), so the digests are the ones its frozen-pages
test recomputes. It writes nothing: the coordinator pastes the JSON under
``releases`` in ``tests/fixtures/release-history.json``.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "tests"))
import test_release_contract as contract  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument(
        "--date",
        help="publication date YYYY-MM-DD; default today in Europe/Paris",
    )
    args = parser.parse_args(argv)
    contract.parse_version(args.version)
    date = args.date or datetime.now(ZoneInfo("Europe/Paris")).date().isoformat()
    sections = {version: (when, text) for version, when, text in contract.changelog_sections()}
    if args.version not in sections:
        sys.exit(f"no published CHANGELOG section for {args.version}")
    changelog_date, section = sections[args.version]
    if changelog_date != date:
        sys.exit(f"CHANGELOG dates {args.version} {changelog_date}, not {date}")
    entry = contract.freeze_entry(args.version, date, section)
    print(json.dumps({args.version: entry}, indent=2))


if __name__ == "__main__":
    main()
