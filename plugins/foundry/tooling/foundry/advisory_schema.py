"""Neutral vocabulary for bounded append-only advisory comments."""
SCHEMA = 'foundry.postmerge-review.v1'
PREFIX = 'Foundry postmerge advisory receipt\n'

TERMINAL_SCHEMA = 'foundry.postmerge-review.v2'
TERMINAL_PREFIX = PREFIX + 'terminal '
MAX_COMMENT_BYTES = 12294
# Share of the room left after the empty terminal receipt that is kept back as margin (PAT-203).
REPORT_MARGIN_PERCENT = 25
