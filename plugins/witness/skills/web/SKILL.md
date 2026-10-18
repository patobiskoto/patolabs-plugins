---
name: web
description: >
  Proves a local web page change: opens a localhost URL in a headless Chromium-family browser with a fresh
  temporary profile, takes a screenshot at a fixed viewport and writes a dated report. USE WHEN the user asks
  to screenshot or prove a local web app change, or invokes /witness:web (Claude Code) or $witness:web (Codex).
argument-hint: "--project DIR --url http://localhost:PORT/PATH [--viewport 1280x800] [--label before|after]"
allowed-tools: Bash(python3:*)
---

# witness:web

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches the
agent. Codex exposes no plugin-root variable: derive `<witness-root>` from this skill's
absolute path by removing `/skills/web/SKILL.md`.

1. Serve the app yourself on `localhost` or `127.0.0.1` (other hosts are refused). The project directory must
   be in a linked git worktree or outside any repository.
2. A Chromium-family browser (Google Chrome, Chromium, Brave, Edge) must be installed; `WITNESS_BROWSER` names
   its executable otherwise. Do not use a browser tool of your agent host: this command line is the same under
   Claude Code and Codex, which is the point.
3. For a before/after proof, run it twice (`--label before`, `--label after`) with the same URL path, viewport
   and browser, then `compare`.

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<witness-root>")/tooling/witness_cli.py" web --project <DIR> --url http://localhost:<PORT>/<PATH> --label before
```

4. Report the printed outcome and the two report paths as they are. The page may need more time than the fixed
   `--virtual-time-budget` to settle: look at the screenshot. Never state a token cost: the report says it is
   unknown.
