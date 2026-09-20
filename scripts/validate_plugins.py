#!/usr/bin/env python3
"""Validate the dual Claude Code + Codex marketplace without third-party deps."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, NoReturn

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAMES = ("foundry", "ship-ios")
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
ROOT_PLACEHOLDER = "<plugin-root>"

# Minimal allowlist of intentional host-schema differences. Claude discovers
# skills and capabilities implicitly; Codex declares them in its manifest.
# Hooks are intentionally *not* compared at the manifest-field level: each host
# independently discovers the shared conventional document. Claude may declare
# an additional document, while Codex has no supported manifest hooks field.
# Do not add presentation metadata: parity covers executable semantics only.
HOST_SCHEMA_ALLOWLIST = {
    "claude": {
        "skills_path": "./skills/",
        "capabilities": ("Read", "Write"),
    },
    "codex": {},
}

CONVENTIONAL_HOOKS_PATH = "./hooks/hooks.json"

# These logical entrypoints are the stable public names that the Foundry routing hook
# accepts.  Keep their source contracts independently checked here: this validator is
# intentionally source-only and does not import the plugin tooling or invoke a host.
FOUNDRY_AGENT_CONTRACTS = {
    "lupin": (
        "Performs bounded, read-only repository exploration",
        "Explore only the question in `Goal`.",
        "strictly read-only and must not delegate",
        "`file:line` evidence",
    ),
    "eiffel": (
        "Executes one bounded Foundry implementation task",
        "Implement exactly the accepted scope in `Goal`",
        "preserve unrelated work",
        "Do not delegate, open/merge a PR, or change tracker state",
    ),
    "maigret": (
        "Reviews a branch/PR diff at blank context BEFORE merge",
        "Review in TWO STAGES",
        "Never modify the repo",
        "AC: PASS|BLOCK",
    ),
    "vauban": (
        "Resolves a durable architectural question against existing ADRs",
        "Load every cited accepted ADR",
        "Stay read-only, do not delegate",
        "recommended decision, trade-offs, rejected alternatives",
    ),
}
FOUNDRY_AGENT_COMMON_CONTRACT = (
    "FOUNDRY_ROUTED_AGENT_V1",
    "STOP",
    "PreToolUse",
)


def load(path: Path, plugin: str | None = None, runtime: str | None = None,
         category: str = "JSON") -> dict[str, Any]:
    """Load an object JSON document, wrapping malformed input diagnostically."""
    try:
        with path.open(encoding="utf-8") as f:
            document = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        if plugin is not None and runtime is not None:
            fail(plugin, runtime, category, f"invalid JSON at {path}: {exc}")
        raise
    if not isinstance(document, dict):
        if plugin is not None and runtime is not None:
            fail(plugin, runtime, category, f"must be a JSON object: {path}")
        raise TypeError(f"{path}: must be a JSON object")
    return document


def fail(plugin: str, runtime: str, category: str, detail: str) -> NoReturn:
    """Raise a stable, actionable semantic-parity failure."""
    raise AssertionError(f"{plugin}: {runtime} {category}: {detail}")


def normalise_hook_command(command: str, plugin_root: Path) -> str:
    """Make a shared hook command independent of its host root spelling."""
    for root in ("${CLAUDE_PLUGIN_ROOT}", str(plugin_root)):
        command = command.replace(f'"{root}"', ROOT_PLACEHOLDER)
        command = command.replace(f"'{root}'", ROOT_PLACEHOLDER)
        command = command.replace(root, ROOT_PLACEHOLDER)
    return command


def resolve_under_plugin(plugin: str, runtime: str, category: str,
                         plugin_root: Path, announced_path: str) -> Path:
    """Resolve an announced path and refuse lexical or symlink escapes."""
    if not isinstance(announced_path, str) or not announced_path:
        fail(plugin, runtime, category, "must be a non-empty relative path")
    root = plugin_root.resolve()
    candidate = (root / announced_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        fail(plugin, runtime, category,
             f"path escapes plugin root: {announced_path}")
    return candidate


def normalise_hooks(plugin: str, runtime: str, plugin_root: Path,
                    hooks_path: Path) -> tuple[tuple[str, str, str, str], ...]:
    """Return a sorted, host-neutral inventory of shared hook actions."""
    if not hooks_path.is_file():
        fail(plugin, runtime, "hooks path", f"missing {hooks_path}")
    document = load(hooks_path, plugin, runtime, "hooks")
    groups_by_event = document.get("hooks")
    if not isinstance(groups_by_event, dict):
        fail(plugin, runtime, "hooks", "hooks must be an object")

    actions: list[tuple[str, str, str, str]] = []
    for event, groups in groups_by_event.items():
        if not isinstance(event, str) or not isinstance(groups, list):
            fail(plugin, runtime, "hooks", "each event must map to a list of groups")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                fail(plugin, runtime, "hooks", "each group must contain a hooks list")
            matcher = group.get("matcher", "")
            if not isinstance(matcher, str):
                fail(plugin, runtime, "hooks", "matcher must be a string")
            for hook in group["hooks"]:
                if not isinstance(hook, dict):
                    fail(plugin, runtime, "hooks", "each hook must be an object")
                hook_type = hook.get("type", "")
                command = hook.get("command", "")
                if not isinstance(hook_type, str) or not isinstance(command, str):
                    fail(plugin, runtime, "hooks", "hook type and command must be strings")
                actions.append((event, matcher, hook_type,
                                normalise_hook_command(command, plugin_root)))
    return tuple(sorted(actions))


def normalise_hook_documents(
    plugin: str, runtime: str, plugin_root: Path, hooks_paths: tuple[Path, ...]
) -> tuple[tuple[str, str, str, str], ...]:
    """Aggregate the hook documents independently discovered by one host."""
    actions = [
        action
        for hooks_path in hooks_paths
        for action in normalise_hooks(plugin, runtime, plugin_root, hooks_path)
    ]
    return tuple(sorted(actions))


def hook_script_paths(actions: tuple[tuple[str, str, str, str], ...]) -> tuple[str, ...]:
    """Extract root-relative script paths from normalised shared commands."""
    paths: set[str] = set()
    pattern = re.compile(re.escape(ROOT_PLACEHOLDER) + r"/([^\s\"']+)")
    for _, _, hook_type, command in actions:
        if hook_type == "command":
            paths.update(pattern.findall(command))
    return tuple(sorted(paths))


def normalise_facade(plugin_root: Path, runtime: str) -> dict[str, Any]:
    """Build the deterministic semantic inventory exposed by one host facade."""
    plugin = plugin_root.name
    if runtime not in {"claude", "codex"}:
        raise ValueError(f"unknown runtime: {runtime}")
    host_schema = HOST_SCHEMA_ALLOWLIST[runtime]
    claude_manifest_path = plugin_root / ".claude-plugin" / "plugin.json"

    if runtime == "claude":
        manifest = load(claude_manifest_path, plugin, runtime, "manifest")
        skills_path = resolve_under_plugin(
            plugin, runtime, "skills path", plugin_root, host_schema["skills_path"]
        )
        capabilities = tuple(host_schema["capabilities"])
        conventional_hooks = resolve_under_plugin(
            plugin, runtime, "hooks path", plugin_root, CONVENTIONAL_HOOKS_PATH
        )
        hooks_paths = (conventional_hooks,) if conventional_hooks.exists() else ()
        declared_hooks = manifest.get("hooks")
        if declared_hooks is not None:
            additional_hooks = resolve_under_plugin(
                plugin, runtime, "hooks path", plugin_root, declared_hooks
            )
            if additional_hooks == conventional_hooks:
                fail(
                    plugin,
                    runtime,
                    "hooks path",
                    f"duplicate conventional hook file {declared_hooks}: "
                    f"{CONVENTIONAL_HOOKS_PATH} is loaded automatically; remove "
                    "manifest.hooks or reference only an additional hook file",
                )
            hooks_paths += (additional_hooks,)
    else:
        manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
        if not manifest_path.is_file():
            fail(plugin, runtime, "manifest path", f"missing {manifest_path}")
        manifest = load(manifest_path, plugin, runtime, "manifest")
        declared_skills = manifest.get("skills")
        skills_path = resolve_under_plugin(
            plugin, runtime, "skills path", plugin_root, declared_skills
        )
        interface = manifest.get("interface", {})
        if not isinstance(interface, dict):
            fail(plugin, runtime, "capabilities", "interface must be an object")
        raw_capabilities = interface.get("capabilities", [])
        if not isinstance(raw_capabilities, list) or not all(
            isinstance(capability, str) for capability in raw_capabilities
        ):
            fail(plugin, runtime, "capabilities", "must be a list of strings")
        duplicates = sorted({item for item in raw_capabilities if raw_capabilities.count(item) > 1})
        if duplicates:
            fail(plugin, runtime, "capabilities", f"duplicate entries: {duplicates}")
        capabilities = tuple(raw_capabilities)
        conventional_hooks = resolve_under_plugin(
            plugin, runtime, "hooks path", plugin_root, CONVENTIONAL_HOOKS_PATH
        )
        hooks_paths = (conventional_hooks,) if conventional_hooks.exists() else ()

    if not skills_path.is_dir():
        fail(plugin, runtime, "skills path", f"missing {skills_path}")
    skills = tuple(sorted(
        path.parent.name for path in skills_path.glob("*/SKILL.md") if path.is_file()
    ))
    hooks = normalise_hook_documents(plugin, runtime, plugin_root, hooks_paths)
    return {"skills": skills, "hooks": hooks, "capabilities": tuple(sorted(capabilities))}


def validate_hook_scripts(plugin: str, runtime: str, plugin_root: Path,
                          hooks: tuple[tuple[str, str, str, str], ...]) -> None:
    for hook in hooks:
        if hook[2] != "command":
            continue
        script_paths = hook_script_paths((hook,))
        if not script_paths:
            fail(plugin, runtime, "hooks path",
                 f"command must reference {ROOT_PLACEHOLDER}/...: {hook[3]}")
        for relative_path in script_paths:
            script = resolve_under_plugin(
                plugin, runtime, "hooks path", plugin_root, relative_path
            )
            if not script.is_file():
                fail(plugin, runtime, "hooks path", f"missing {relative_path}")


def validate_semantic_parity(plugin: str, claude: dict[str, Any],
                             codex: dict[str, Any]) -> None:
    """Assert that two normalised facade inventories expose the same contract."""
    for category in ("skills", "hooks", "capabilities"):
        if claude[category] != codex[category]:
            missing = sorted(set(claude[category]) - set(codex[category]))
            extra = sorted(set(codex[category]) - set(claude[category]))
            fail(plugin, "claude/codex", category,
                 f"missing={missing}; extra={extra}")


def validate_plugin_semantics(plugin_root: Path) -> None:
    """Compare the two host facades without conflating presentation metadata."""
    plugin = plugin_root.name
    claude = normalise_facade(plugin_root, "claude")
    codex = normalise_facade(plugin_root, "codex")
    for runtime, facade in (("claude", claude), ("codex", codex)):
        validate_hook_scripts(plugin, runtime, plugin_root, facade["hooks"])
    validate_semantic_parity(plugin, claude, codex)


def validate_foundry_agent_contracts(plugin_root: Path) -> None:
    """Verify Foundry's four routed identities and their semantic source contracts."""
    plugin = plugin_root.name
    agents = plugin_root / "agents"
    for identity, required_phrases in FOUNDRY_AGENT_CONTRACTS.items():
        path = agents / f"{identity}.md"
        if not path.is_file():
            fail(plugin, "agents", identity, f"missing identity file {path.name}")
        text = path.read_text(encoding="utf-8")
        match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
        if not match:
            fail(plugin, "agents", identity, "missing YAML frontmatter")
        frontmatter = match.group(1)
        if not re.search(rf"(?m)^name: {re.escape(identity)}$", frontmatter):
            fail(plugin, "agents", identity, "frontmatter name must match identity filename")
        if not re.search(r"(?m)^tools: Read$", frontmatter):
            fail(plugin, "agents", identity, "frontmatter tools must be exactly Read")
        if re.search(r"(?m)^(?:model|effort):", frontmatter):
            fail(plugin, "agents", identity, "frontmatter must keep model and effort dynamic")
        contract_text = " ".join(text.split())
        for phrase in FOUNDRY_AGENT_COMMON_CONTRACT + required_phrases:
            if phrase not in contract_text:
                fail(plugin, "agents", identity, f"missing required contract: {phrase!r}")
        if f"`foundry:{identity}`" not in contract_text:
            fail(plugin, "agents", identity, "missing namespaced identity reference")


def validate_marketplaces() -> None:
    claude = load(ROOT / ".claude-plugin" / "marketplace.json")
    codex = load(ROOT / ".agents" / "plugins" / "marketplace.json")
    assert claude["name"] == codex["name"] == "patolabs"
    assert tuple(p["name"] for p in claude["plugins"]) == PLUGIN_NAMES
    assert tuple(p["name"] for p in codex["plugins"]) == PLUGIN_NAMES

    for entry in claude["plugins"]:
        assert entry["source"] == f"./plugins/{entry['name']}"
    for entry in codex["plugins"]:
        name = entry["name"]
        assert entry["source"] == {"source": "local", "path": f"./plugins/{name}"}
        assert entry["policy"]["installation"] in {
            "NOT_AVAILABLE", "AVAILABLE", "INSTALLED_BY_DEFAULT"
        }
        assert entry["policy"]["authentication"] in {"ON_INSTALL", "ON_USE"}
        assert entry["category"]


def validate_plugins() -> None:
    for name in PLUGIN_NAMES:
        root = ROOT / "plugins" / name
        claude = load(root / ".claude-plugin" / "plugin.json")
        codex = load(root / ".codex-plugin" / "plugin.json")
        assert claude["name"] == codex["name"] == root.name
        assert claude["version"] == codex["version"]
        assert SEMVER.fullmatch(codex["version"]), f"{name}: invalid semver"
        assert codex["description"] and codex["author"]["name"]
        assert codex["skills"] == "./skills/"
        assert (root / codex["skills"]).is_dir()
        assert (root / "hooks" / "hooks.json").is_file() or name != "foundry"
        interface = codex["interface"]
        for key in (
            "displayName", "shortDescription", "longDescription",
            "developerName", "category", "capabilities", "websiteURL",
        ):
            assert interface[key], f"{name}: interface.{key} is required"
        assert len(interface.get("defaultPrompt", [])) <= 3
        assert all(len(prompt) <= 128 for prompt in interface.get("defaultPrompt", []))
        assert not any("[TODO:" in path.read_text(encoding="utf-8")
                       for path in (root / ".codex-plugin").glob("*.json"))
        validate_plugin_semantics(root)
        if name == "foundry":
            validate_foundry_agent_contracts(root)


def validate_skill_paths() -> None:
    for skill in ROOT.glob("plugins/*/skills/*/SKILL.md"):
        text = skill.read_text(encoding="utf-8")
        assert text.startswith("---\n") and "\n---\n" in text[4:], (
            f"{skill.relative_to(ROOT)}: missing YAML frontmatter"
        )
        frontmatter = text.split("---\n", 2)[1]
        keys = set(re.findall(r"^([a-z][a-z-]*):", frontmatter, re.MULTILINE))
        allowed_keys = {
            "name", "description", "license", "allowed-tools", "metadata",
            "argument-hint",
        }
        assert {"name", "description"} <= keys, (
            f"{skill.relative_to(ROOT)}: name and description are required"
        )
        assert keys <= allowed_keys, (
            f"{skill.relative_to(ROOT)}: non-portable frontmatter keys "
            f"{sorted(keys - allowed_keys)}"
        )
        expected_name = skill.parent.name
        assert re.search(rf"^name:\s*{re.escape(expected_name)}\s*$", text, re.MULTILINE), (
            f"{skill.relative_to(ROOT)}: skill name must match its directory"
        )
        hint_lines = [
            line for line in frontmatter.splitlines()
            if line.startswith("argument-hint:")
        ]
        assert len(hint_lines) <= 1, (
            f"{skill.relative_to(ROOT)}: argument-hint must appear at most once"
        )
        if hint_lines:
            raw_hint = hint_lines[0].partition(":")[2].strip()
            try:
                hint = json.loads(raw_hint)
            except json.JSONDecodeError as exc:
                raise AssertionError(
                    f"{skill.relative_to(ROOT)}: argument-hint must be a quoted "
                    "one-line string"
                ) from exc
            assert isinstance(hint, str) and hint and len(hint) <= 128, (
                f"{skill.relative_to(ROOT)}: argument-hint must be a non-empty "
                "string of at most 128 characters"
            )
            assert "\n" not in hint and "\r" not in hint, (
                f"{skill.relative_to(ROOT)}: argument-hint must fit on one line"
            )
        plugin = skill.parents[2].name
        root_placeholder = f"<{plugin}-root>"
        bash = "\n".join(re.findall(r"```bash\n(.*?)```", text, re.DOTALL))
        selector = (
            '$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s '
            f'"${{CLAUDE_PLUGIN_ROOT}}" || printf %s "{root_placeholder}")'
        )
        for line in bash.splitlines():
            if root_placeholder in line or "CLAUDE_PLUGIN_ROOT" in line:
                assert selector in line, (
                    f"{skill.relative_to(ROOT)}: plugin command must use the single "
                    "Claude-token/Codex-path selector"
                )
        assert "CLAUDE_PLUGIN_ROOT:-" not in text, (
            f"{skill.relative_to(ROOT)}: Claude replaces only the exact root token"
        )


def main() -> None:
    validate_marketplaces()
    validate_plugins()
    validate_skill_paths()
    print(f"dual-runtime catalogue OK — {len(PLUGIN_NAMES)} plugins")


if __name__ == "__main__":
    main()
