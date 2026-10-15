- PAT-63: the R1 guard (`hooks/guard_bash.py`) now names the real cause of a fail-closed
  denial. A malformed global registry (invalid JSON or encoding, non-object root, invalid
  `canonical_repo`, and now a provider or an entry that is not an object, which was silently
  treated as unregistered) raises `registry.RegistryFileError` (a `ValueError` subclass from
  `registry.load()`), and the denial says "registre global Foundry" with the registry path
  instead of "binding tracker Foundry invalide"; a broken repo binding keeps the old wording.
  Observed: the guard path never reads `config.env`/`$FOUNDRY_CONFIG`, so a non-UTF-8 config
  file changes nothing (pinned by a test); an unreadable registry (OSError) still fails open.
  Behaviour change: `registry.load()` now refuses a non-object provider or entry for every
  caller. Documentation status (R5): updated the `guard_bash.py` docstring, the hooks
  paragraph of `README.md` and AGENTS.md/CLAUDE.md R1 (identical); no CLI verb, option,
  configuration key or public constant changed.
