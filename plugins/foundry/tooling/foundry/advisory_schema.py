"""Neutral vocabulary for bounded append-only advisory comments."""
SCHEMA = 'foundry.postmerge-review.v1'
PREFIX = 'Foundry postmerge advisory receipt\n'

TERMINAL_SCHEMA = 'foundry.postmerge-review.v2'
TERMINAL_PREFIX = PREFIX + 'terminal '
MAX_COMMENT_BYTES = 12294
