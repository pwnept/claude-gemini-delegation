import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


GITHUB_REPO = "pwnept/claude-gemini-delegation"
_UPDATE_CHECK_INTERVAL = 86400  # seconds

AGENTS_MARKER_BEGIN = "> [claude-gemini-delegation:agents-begin]"
AGENTS_MARKER_END = "> [claude-gemini-delegation:agents-end]"
MANAGED_FILES = (
    ".gemini-delegation/hooks/delegation_caller.py",
    ".gemini-delegation/hooks/pre_delegate.py",
    ".gemini-delegation/hooks/gemini_delegate.py",
    ".gemini-delegation/hooks/post_delegate.py",
    ".gemini-delegation/hooks/analyze_metrics.py",
    ".gemini-delegation/hooks/delegation_guard.py",
    ".gemini-delegation/hooks/delegation_guard.ps1",
    ".gemini-delegation/hooks/delegate.ps1",
    ".gemini-delegation/hooks/delegate_and_log.ps1",
    ".gemini-delegation/hooks/delegate.bat",
    ".gemini-delegation/hooks/delegate",
    ".gemini-delegation/src/agent_delegation/__init__.py",
    ".gemini-delegation/src/agent_delegation/policy.py",
    ".gemini-delegation/delegation_config.json",
    ".agents/rules/delegation.md",
    ".claude/commands/delegate.md",
)
HOOK_FILES = (
    "delegation_caller.py",
    "pre_delegate.py",
    "gemini_delegate.py",
    "post_delegate.py",
    "analyze_metrics.py",
    "delegation_guard.py",
    "delegation_guard.ps1",
    "delegate.ps1",
    "delegate_and_log.ps1",
    "delegate.bat",
    "delegate",
)
RUNTIME_FILES = ("__init__.py", "policy.py")


class InstallError(RuntimeError):
    """Expected installer error with user-actionable guidance."""


def _parse_version(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.lstrip("v").split("."))
    except ValueError:
        return (0,)


def check_for_update(config_path: Path) -> None:
    """Query GitHub releases and print a hint if a newer version is available.

    Reads/writes last_update_check in delegation_config.json.
    Silently skips on any network or parse error.
    """
    import urllib.request

    try:
        config: dict = {}
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass

        now = datetime.now(tz=timezone.utc)
        last_check = config.get("last_update_check")
        if last_check:
            try:
                last_dt = datetime.fromisoformat(last_check)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                if (now - last_dt).total_seconds() < _UPDATE_CHECK_INTERVAL:
                    return
            except ValueError:
                pass

        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        req = urllib.request.Request(url, headers={"User-Agent": "gemini-delegation-updater"})
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            data = json.loads(resp.read().decode())

        tag = data.get("tag_name", "")
        if not tag:
            return

        from . import __version__
        config["last_update_check"] = now.isoformat(timespec="seconds")
        config["latest_release"] = tag
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

        if _parse_version(tag) > _parse_version(__version__):
            print(f"[UPDATE] New release available: {tag} (current: {__version__})")
            print(f"[UPDATE] pip install --upgrade git+https://github.com/{GITHUB_REPO}.git")
    except Exception:  # noqa: BLE001
        pass


def source_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _backup(path: Path) -> Path:
    backup_path = path.with_name(f"{path.name}.bak.{_timestamp()}")
    shutil.copy2(path, backup_path)
    return backup_path


def _write_if_changed(path: Path, content: str, *, backup_existing: bool = True) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    if path.exists() and backup_existing:
        _backup(path)
    path.write_text(content, encoding="utf-8")
    return True


def resolve_target(target_dir: str, *, create: bool = False) -> Path:
    target = Path(os.path.expandvars(os.path.expanduser(target_dir))).resolve()
    if target.exists() and not target.is_dir():
        raise InstallError(f"Target exists but is not a directory: {target}")
    if not target.exists():
        if not create:
            raise InstallError(
                "Target directory does not exist: {0}\n"
                "Create it yourself or rerun with --create-target.".format(target)
            )
        target.mkdir(parents=True)
    if target.parent == target:
        raise InstallError("Refusing to install into a filesystem root: {0}".format(target))
    return target


def get_codex_dir(project_dir: Path) -> Path:
    exact_children = {child.name: child for child in project_dir.iterdir()} if project_dir.exists() else {}
    canonical = project_dir / ".codex"
    legacy = exact_children.get(".Codex")
    exact_canonical = exact_children.get(".codex")
    if legacy is not None and exact_canonical is None:
        temporary = project_dir / f".Codex.migrating.{_timestamp()}"
        legacy.rename(temporary)
        temporary.rename(canonical)
        print(f"[OK] Migrated legacy Codex directory casing: {legacy} -> {canonical}")
    return canonical


def agents_section() -> str:
    return f"""{AGENTS_MARKER_BEGIN}
## Delegation

Delegate to `agy` for: security audits, web/doc search, reading 3+ new files,
recursive repo scans, or expected output > 500 lines. The hook auto-detects the
calling harness - use the same command regardless of which agent is running:

```powershell
& .gemini-delegation/hooks/delegate_and_log.ps1 "<task>" "<context>" 10
& .gemini-delegation/hooks/delegate_and_log.ps1 "<task>" "<context>" 10 -Profile research
& .gemini-delegation/hooks/delegate_and_log.ps1 "<task>" "<context>" 10 -Profile scout
```

Scout (Gemma 4, 1.5K RPD): file mapping, log parsing, dep scanning, test discovery - read only.
Fallbacks: `DELEGATION_BACKEND=gemini-api` (needs `GEMINI_API_KEY`) or `DELEGATION_BACKEND=gemini-cli` (npm).
When running as Antigravity or `agy`, do the work directly - do not recursively invoke `agy`.
{AGENTS_MARKER_END}
"""


def antigravity_rule() -> str:
    return """# Delegation Rule

Follow the repository `AGENTS.md` delegation section.

When the current agent is Antigravity or `agy` itself, do the work directly.
Do not recursively invoke `agy` from inside an Antigravity agent session.
"""


def _strip_claude_bridges(text: str) -> str:
    lines = []
    for line in _normalize_text(text).splitlines():
        if line.strip() in {"@AGENTS.md", "@.claude/CLAUDE.md", "@../AGENTS.md"}:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def migrate_claude_instructions(project_dir: Path) -> str:
    migrated = []
    agents_md = project_dir / "AGENTS.md"
    existing_agents = _normalize_text(agents_md.read_text(encoding="utf-8")) if agents_md.exists() else ""

    # If AGENTS.md already has user-authored content beyond our managed delegation block,
    # skip migrating CLAUDE.md into it. Dumping CLAUDE.md content into a pre-existing
    # AGENTS.md would mix instructions that may be intentionally per-agent (e.g. a repo
    # where Claude and Codex see different rules). The delegation block is still added by
    # ensure_agents_md; only the CLAUDE.md content dump is suppressed.
    if agents_md.exists():
        try:
            without_our_block = replace_managed_section(existing_agents, "").strip()
        except InstallError:
            without_our_block = existing_agents.strip()
        root_claude = project_dir / "CLAUDE.md"
        root_claude_text = _normalize_text(root_claude.read_text(encoding="utf-8")).strip() if root_claude.exists() else ""
        if without_our_block and root_claude_text == "@AGENTS.md":
            return ""
        if without_our_block:
            print("[OK] Preserved CLAUDE.md because AGENTS.md already has custom content.")
            return ""

    root_claude = project_dir / "CLAUDE.md"
    if root_claude.exists():
        content = root_claude.read_text(encoding="utf-8")
        body = _strip_claude_bridges(content)
        if body and body not in existing_agents:
            migrated.append("## Migrated CLAUDE.md Instructions\n\n" + body)
        if _normalize_text(content).strip() != "@AGENTS.md":
            _backup(root_claude)
            root_claude.write_text("@AGENTS.md\n", encoding="utf-8")
            print(f"[OK] Replaced {root_claude} with @AGENTS.md bridge")
    else:
        root_claude.write_text("@AGENTS.md\n", encoding="utf-8")
        print(f"[OK] Created {root_claude}")

    dot_claude = project_dir / ".claude" / "CLAUDE.md"
    if dot_claude.exists():
        content = dot_claude.read_text(encoding="utf-8")
        body = _strip_claude_bridges(content)
        current_agents = existing_agents + "\n\n" + "\n\n".join(migrated)
        if body and body not in current_agents:
            migrated.append("## Migrated .claude/CLAUDE.md Instructions\n\n" + body)
        _backup(dot_claude)
        dot_claude.unlink()
        print(f"[OK] Migrated and removed {dot_claude}")
    return "\n\n".join(migrated).strip()


def replace_managed_section(existing: str, section: str) -> str:
    has_begin = AGENTS_MARKER_BEGIN in existing
    has_end = AGENTS_MARKER_END in existing
    if has_begin != has_end:
        raise InstallError(
            "AGENTS.md has mismatched delegation markers.\n"
            f"Expected both {AGENTS_MARKER_BEGIN!r} and {AGENTS_MARKER_END!r}.\n"
            "Fix the marker block manually or paste this error into an AI agent."
        )
    if has_begin:
        before = existing[: existing.index(AGENTS_MARKER_BEGIN)].rstrip()
        after = existing[existing.index(AGENTS_MARKER_END) + len(AGENTS_MARKER_END) :].lstrip()
        parts = [part for part in (before, section.strip(), after) if part]
        return "\n\n".join(parts).rstrip() + "\n"
    return existing.rstrip() + "\n\n" + section.strip() + "\n"


def ensure_agents_md(project_dir: Path, migrated_text: str = "") -> Path:
    agents_md = project_dir / "AGENTS.md"
    section = agents_section()
    if agents_md.exists():
        existing = _normalize_text(agents_md.read_text(encoding="utf-8"))
        without_section = replace_managed_section(existing, section)
        if migrated_text and migrated_text not in without_section:
            without_section = without_section.rstrip() + "\n\n" + migrated_text.rstrip() + "\n"
        _write_if_changed(agents_md, without_section)
        print(f"[OK] Updated {agents_md}")
    else:
        prefix = "# Agent Instructions\n"
        body = prefix + "\n" + section.strip() + "\n"
        if migrated_text:
            body += "\n" + migrated_text.rstrip() + "\n"
        agents_md.write_text(body, encoding="utf-8")
        print(f"[OK] Created {agents_md}")
    return agents_md


def copy_shared_hooks(project_dir: Path) -> Path:
    source_hooks = source_root() / "hooks"
    if not source_hooks.is_dir():
        raise InstallError(
            f"Cannot find source hook templates at {source_hooks}.\n"
            "Run install-delegation.ps1 from a complete source checkout."
        )
    dest_hooks = project_dir / ".gemini-delegation" / "hooks"
    dest_hooks.mkdir(parents=True, exist_ok=True)
    for name in HOOK_FILES:
        source = source_hooks / name
        if not source.is_file():
            raise InstallError(f"Required hook template is missing: {source}")
        shutil.copy2(source, dest_hooks / name)
    source_runtime = source_root() / "src" / "agent_delegation"
    dest_runtime = project_dir / ".gemini-delegation" / "src" / "agent_delegation"
    dest_runtime.mkdir(parents=True, exist_ok=True)
    for name in RUNTIME_FILES:
        source = source_runtime / name
        if not source.is_file():
            raise InstallError(f"Required managed runtime file is missing: {source}")
        shutil.copy2(source, dest_runtime / name)
    try:
        (dest_hooks / "delegate").chmod(0o755)
    except OSError:
        pass
    config_path = project_dir / ".gemini-delegation" / "delegation_config.json"
    existing_config: dict = {}
    if config_path.exists():
        try:
            existing_config = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    config = {
        "backend": "agy",
        "installed_by": "claude-gemini-delegation",
        "installed_at": existing_config.get("installed_at", datetime.now().isoformat(timespec="seconds")),
        "managed_markers": [AGENTS_MARKER_BEGIN, AGENTS_MARKER_END],
    }
    _write_if_changed(config_path, json.dumps(config, indent=2) + "\n", backup_existing=False)
    print(f"[OK] Copied shared hooks to {dest_hooks}")
    return dest_hooks


_CLAUDE_COMMAND_DELEGATE = """\
Delegate the following task to the Gemini backend using the in-repo delegation
script. Run from the repository root and report the full output:

```powershell
& .gemini-delegation/hooks/delegate_and_log.ps1 "$ARGUMENTS" "/delegate" 10
```

Profile guide:
- (default) general code tasks and broad output
- `-Profile research` web search, docs, security audits
- `-Profile scout` file mapping, log parsing, dep scanning, test discovery (Gemma 4, 1.5K RPD)
"""


def create_claude_command(claude_dir: Path) -> None:
    """Write the /delegate convenience command for Claude Code."""
    commands_dir = claude_dir / "commands"
    commands_dir.mkdir(parents=True, exist_ok=True)
    dest = commands_dir / "delegate.md"
    _write_if_changed(dest, _CLAUDE_COMMAND_DELEGATE, backup_existing=False)
    print(f"[OK] Created {dest}")


def install_claude_command(project_dir: Path) -> None:
    """Write the /delegate Claude command and ensure .claude/hooks/ dir exists."""
    claude_dir = project_dir / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / "hooks").mkdir(exist_ok=True)
    create_claude_command(claude_dir)


def create_claude_settings(claude_dir: Path) -> Path:
    claude_dir.mkdir(parents=True, exist_ok=True)
    settings_path = claude_dir / "settings.json"
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InstallError(
                f"Cannot parse {settings_path}: {exc}\n"
                "Fix the JSON manually or paste this error into an AI agent."
            ) from exc
    else:
        settings = {}

    removed_archive_hooks = remove_stale_archive_jsonl_hooks(settings)
    if removed_archive_hooks:
        print(f"[OK] Removed {removed_archive_hooks} stale archive-jsonl hook(s) from {settings_path}")

    # Inject DELEGATION_CALLER so the hook auto-routes logs to ~/.claude/delegation-logs/
    # without needing an explicit -Caller argument (update-proof: we own this var).
    settings.setdefault("env", {})["DELEGATION_CALLER"] = "claude"

    hooks = settings.setdefault("hooks", {})
    pre_tool_use = hooks.setdefault("PreToolUse", [])
    def _is_guard(hook: dict) -> bool:
        if "delegation_guard" in hook.get("command", ""):
            return True
        return any("delegation_guard" in str(a) for a in (hook.get("args") or []))

    cleaned = []
    for entry in pre_tool_use:
        entry_hooks = [h for h in entry.get("hooks", []) if not _is_guard(h)]
        if entry_hooks:
            copied = dict(entry)
            copied["hooks"] = entry_hooks
            cleaned.append(copied)

    guard_command = "pwsh -NoProfile -ExecutionPolicy Bypass -File .gemini-delegation/hooks/delegation_guard.ps1"
    for matcher in ("Bash", "PowerShell"):
        cleaned.append(
            {
                "matcher": matcher,
                "hooks": [
                    {
                        "type": "command",
                        "command": guard_command,
                        "timeout": 5,
                    }
                ],
            }
        )
    hooks["PreToolUse"] = cleaned
    _write_if_changed(settings_path, json.dumps(settings, indent=2) + "\n")
    print(f"[OK] Updated {settings_path}")
    return settings_path


def remove_stale_archive_jsonl_hooks(settings: dict) -> int:
    """Remove old absolute user-home archive-jsonl hooks from Claude settings.

    These hooks were generated by an earlier non-portable setup and point at a
    user-global path such as C:\\Users\\User\\.claude\\hooks\\archive-jsonl.ps1.
    Preserve unrelated hooks and empty hook events.
    """
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return 0

    removed = 0
    for event_name, entries in list(hooks.items()):
        if not isinstance(entries, list):
            continue

        cleaned_entries = []
        for entry in entries:
            if not isinstance(entry, dict):
                cleaned_entries.append(entry)
                continue

            entry_hooks = entry.get("hooks")
            if not isinstance(entry_hooks, list):
                cleaned_entries.append(entry)
                continue

            kept_hooks = []
            for hook in entry_hooks:
                if _is_stale_archive_jsonl_hook(hook):
                    removed += 1
                    continue
                kept_hooks.append(hook)

            if kept_hooks:
                copied = dict(entry)
                copied["hooks"] = kept_hooks
                cleaned_entries.append(copied)

        if cleaned_entries:
            hooks[event_name] = cleaned_entries
        else:
            hooks.pop(event_name, None)

    if not hooks:
        settings.pop("hooks", None)
    return removed


def _is_stale_archive_jsonl_hook(hook: object) -> bool:
    if not isinstance(hook, dict):
        return False

    parts = [str(hook.get("command", ""))]
    args = hook.get("args")
    if isinstance(args, list):
        parts.extend(str(arg) for arg in args)

    text = " ".join(parts).replace("/", "\\").lower()
    if "archive-jsonl.ps1" not in text:
        return False

    return (
        ":\\users\\" in text
        or "\\.claude\\hooks\\archive-jsonl.ps1" in text
        or "~\\.claude\\hooks\\archive-jsonl.ps1" in text
    )


def revert_claude_settings(claude_dir: Path) -> bool:
    """Strip delegation_guard PreToolUse entries and DELEGATION_CALLER env token
    from .claude/settings.json.

    Returns True if the file was changed.  Does not touch any other keys.
    """
    settings_path = claude_dir / "settings.json"
    if not settings_path.exists():
        return False
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False

    changed_env = False
    env_block = settings.get("env", {})
    if env_block.get("DELEGATION_CALLER") == "claude":
        del env_block["DELEGATION_CALLER"]
        if not env_block:
            settings.pop("env", None)
        else:
            settings["env"] = env_block
        changed_env = True

    hooks = settings.get("hooks", {})
    pre_tool_use = hooks.get("PreToolUse", [])
    if not pre_tool_use:
        if changed_env:
            _write_if_changed(settings_path, json.dumps(settings, indent=2) + "\n")
            print(f"[OK] Removed DELEGATION_CALLER from {settings_path}")
        return changed_env

    def _is_delegation_guard(hook: dict) -> bool:
        if "delegation_guard" in hook.get("command", ""):
            return True
        # Old-style: command=powershell.exe, guard path in args list
        return any("delegation_guard" in str(arg) for arg in (hook.get("args") or []))

    cleaned = []
    for entry in pre_tool_use:
        entry_hooks = [
            hook
            for hook in entry.get("hooks", [])
            if not _is_delegation_guard(hook)
        ]
        if entry_hooks:
            copied = dict(entry)
            copied["hooks"] = entry_hooks
            cleaned.append(copied)

    if cleaned == pre_tool_use and not changed_env:
        return False  # nothing to do

    if cleaned:
        hooks["PreToolUse"] = cleaned
    else:
        hooks.pop("PreToolUse", None)

    if not hooks:
        settings.pop("hooks", None)
    else:
        settings["hooks"] = hooks

    changed = _write_if_changed(settings_path, json.dumps(settings, indent=2) + "\n")
    if changed:
        print(f"[OK] Removed delegation hooks and env token from {settings_path}")
    return changed or changed_env


def create_codex_hooks(project_dir: Path) -> Path | None:
    """Add PreToolUse delegation_guard hook to .codex/hooks.json.

    Only runs when .codex/ already exists - never creates it from scratch.
    Returns the path written, or None if .codex/ is absent.
    """
    codex_dir = get_codex_dir(project_dir)
    if not codex_dir.exists():
        return None

    hooks_path = codex_dir / "hooks.json"
    if hooks_path.exists():
        try:
            doc = json.loads(hooks_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InstallError(
                f"Cannot parse {hooks_path}: {exc}\n"
                "Fix the JSON manually or paste this error into an AI agent."
            ) from exc
    else:
        doc = {"hooks": {}}

    hooks = doc.setdefault("hooks", {})
    pre_tool_use = hooks.get("PreToolUse", [])

    def _is_guard(hook: dict) -> bool:
        return "delegation_guard" in hook.get("command", "")

    cleaned = []
    for entry in pre_tool_use:
        entry_hooks = [h for h in entry.get("hooks", []) if not _is_guard(h)]
        if entry_hooks:
            copied = dict(entry)
            copied["hooks"] = entry_hooks
            cleaned.append(copied)

    guard_command = "pwsh -NoProfile -ExecutionPolicy Bypass -File .gemini-delegation/hooks/delegation_guard.ps1"
    for matcher in ("Bash", "apply_patch"):
        cleaned.append(
            {
                "matcher": matcher,
                "hooks": [{"type": "command", "command": guard_command, "timeout": 5}],
            }
        )
    hooks["PreToolUse"] = cleaned
    _write_if_changed(hooks_path, json.dumps(doc, indent=2) + "\n")
    print(f"[OK] Updated {hooks_path}")
    return hooks_path


def revert_codex_hooks(project_dir: Path) -> bool:
    """Strip delegation_guard PreToolUse entries from .codex/hooks.json.

    Returns True if the file was changed. Does not touch any other keys.
    """
    codex_dir = get_codex_dir(project_dir)
    hooks_path = codex_dir / "hooks.json"
    if not hooks_path.exists():
        return False
    try:
        doc = json.loads(hooks_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False

    pre_tool_use = doc.get("hooks", {}).get("PreToolUse", [])
    if not pre_tool_use:
        return False

    def _is_guard(hook: dict) -> bool:
        return "delegation_guard" in hook.get("command", "")

    cleaned = []
    for entry in pre_tool_use:
        entry_hooks = [h for h in entry.get("hooks", []) if not _is_guard(h)]
        if entry_hooks:
            copied = dict(entry)
            copied["hooks"] = entry_hooks
            cleaned.append(copied)

    if cleaned == pre_tool_use:
        return False

    if cleaned:
        doc["hooks"]["PreToolUse"] = cleaned
    else:
        doc["hooks"].pop("PreToolUse", None)

    changed = _write_if_changed(hooks_path, json.dumps(doc, indent=2) + "\n")
    if changed:
        print(f"[OK] Removed delegation guard from {hooks_path}")
    return changed


def create_antigravity_rule(project_dir: Path) -> Path:
    rule_path = project_dir / ".agents" / "rules" / "delegation.md"
    _write_if_changed(rule_path, antigravity_rule(), backup_existing=False)
    print(f"[OK] Updated {rule_path}")
    return rule_path


def install_agents(project_dir: Path) -> None:
    source_agents = source_root() / "agents"
    if not source_agents.is_dir():
        return
    dest_agents = project_dir / "agents"
    for source_dir in source_agents.iterdir():
        if not source_dir.is_dir():
            continue
        dest_dir = dest_agents / source_dir.name
        dest_dir.mkdir(parents=True, exist_ok=True)
        for source in source_dir.iterdir():
            if source.is_file():
                shutil.copy2(source, dest_dir / source.name)
        print(f"[OK] Installed bundled agent: {dest_dir}")


def verify_install(target_dir: str, *, preserve_claude_md: bool = False) -> int:
    project_dir = resolve_target(target_dir)
    missing = [relative for relative in MANAGED_FILES if not (project_dir / relative).exists()]
    if missing:
        raise InstallError("Delegation install is incomplete. Missing:\n- " + "\n- ".join(missing))

    claude_md = project_dir / "CLAUDE.md"
    if not preserve_claude_md:
        claude_bridge = claude_md.read_text(encoding="utf-8").strip()
        if claude_bridge != "@AGENTS.md" and not _can_preserve_custom_claude_md(project_dir):
            raise InstallError("CLAUDE.md must contain exactly @AGENTS.md after migration.")
    else:
        if claude_md.exists() and claude_md.stat().st_size > 0:
            first_line = claude_md.read_text(encoding="utf-8").splitlines()[0].strip()
            if first_line != "@AGENTS.md":
                raise InstallError(
                    "With --preserve-claude-md, CLAUDE.md must begin with @AGENTS.md on line 1.\n"
                    f"Found: {first_line!r}"
                )
    agents_text = (project_dir / "AGENTS.md").read_text(encoding="utf-8")
    if AGENTS_MARKER_BEGIN not in agents_text or AGENTS_MARKER_END not in agents_text:
        raise InstallError("AGENTS.md is missing the managed delegation marker block.")

    probe = subprocess.run(
        [
            sys.executable,
            str(project_dir / ".gemini-delegation" / "hooks" / "pre_delegate.py"),
            "npm ls",
            "Delegation verify smoke test",
            "5",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    if probe.returncode != 0 or "Delegation verify smoke test" not in probe.stdout:
        raise InstallError(
            "Offline prompt-format smoke test failed.\n"
            f"Exit code: {probe.returncode}\n"
            f"stdout:\n{probe.stdout}\n"
            f"stderr:\n{probe.stderr}"
        )
    runtime_root = project_dir / ".gemini-delegation" / "src"
    runtime_probe = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys;sys.path.insert(0,sys.argv[1]);import agent_delegation.policy",
            str(runtime_root),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    if runtime_probe.returncode != 0:
        raise InstallError(
            "Bundled managed runtime import smoke test failed.\n"
            f"Exit code: {runtime_probe.returncode}\n"
            f"stderr:\n{runtime_probe.stderr}"
        )
    print(f"[OK] Verified delegation install in {project_dir}")
    check_for_update(project_dir / ".gemini-delegation" / "delegation_config.json")
    return 0


def _can_preserve_custom_claude_md(project_dir: Path) -> bool:
    agents_md = project_dir / "AGENTS.md"
    claude_md = project_dir / "CLAUDE.md"
    if not agents_md.exists() or not claude_md.exists():
        return False

    try:
        agents_text = _normalize_text(agents_md.read_text(encoding="utf-8"))
        custom_agents_text = replace_managed_section(agents_text, "").strip()
    except InstallError:
        return False

    return bool(custom_agents_text and _normalize_text(claude_md.read_text(encoding="utf-8")).strip())


def install_hooks(
    scope: str = "local",
    target_dir: str = ".",
    create_target: bool = False,
    preserve_claude_md: bool = False,
    no_update: bool = False,
) -> int:
    if scope != "local":
        raise InstallError(
            "Global project mutation is intentionally unsupported.\n"
            "Install locally into each target repo with: install --target <path>."
        )
    project_dir = resolve_target(target_dir, create=create_target)
    if no_update and (project_dir / ".gemini-delegation" / "delegation_config.json").exists():
        raise InstallError(
            "Delegation is already installed in this repo.\n"
            "Remove --no-update to refresh/update the existing install, "
            "or run uninstall first for a clean re-install."
        )
    if preserve_claude_md:
        print("[INFO] --preserve-claude-md: skipping CLAUDE.md migration.")
        migrated = ""
    else:
        migrated = migrate_claude_instructions(project_dir)
    ensure_agents_md(project_dir, migrated)
    copy_shared_hooks(project_dir)
    install_claude_command(project_dir)
    create_claude_settings(project_dir / ".claude")
    create_codex_hooks(project_dir)
    create_antigravity_rule(project_dir)
    install_agents(project_dir)
    verify_install(str(project_dir), preserve_claude_md=preserve_claude_md)
    print(f"[SUCCESS] Installed local agy delegation into {project_dir}")
    return 0


def _remove_path(path: Path, removed: list[str], failures: list[str]) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path)
            removed.append(str(path))
        elif path.exists():
            path.unlink()
            removed.append(str(path))
    except OSError as exc:
        failures.append(f"{path}: {exc}")


def remove_agents_md_section(project_dir: Path) -> bool:
    agents_md = project_dir / "AGENTS.md"
    if not agents_md.exists():
        return False
    content = _normalize_text(agents_md.read_text(encoding="utf-8"))
    if AGENTS_MARKER_BEGIN not in content and AGENTS_MARKER_END not in content:
        return False
    new_content = replace_managed_section(content, "").strip() + "\n"
    _write_if_changed(agents_md, new_content)
    return True


def write_uninstall_report(project_dir: Path, removed: list[str], failures: list[str]) -> Path:
    temp_dir = project_dir / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    latest = temp_dir / "delegation-uninstall-latest.md"
    if latest.exists():
        latest.unlink()
    report = [
        "# Delegation Uninstall Report",
        "",
        f"Target: `{project_dir}`",
        f"Generated: `{datetime.now().isoformat(timespec='seconds')}`",
        "",
        "## Removed",
        "",
    ]
    report.extend(f"- `{item}`" for item in removed) if removed else report.append("- Nothing removed")
    report.extend(["", "## Manual Follow-up", ""])
    report.append("- `CLAUDE.md` is left as `@AGENTS.md` if migration already happened.")
    report.append("- User-authored instructions migrated into `AGENTS.md` are intentionally preserved.")
    report.append("- If failures are listed below, paste this report into an AI agent and ask it to finish cleanup.")
    report.extend(["", "## Failures", ""])
    report.extend(f"- {item}" for item in failures) if failures else report.append("- None")
    latest.write_text("\n".join(report) + "\n", encoding="utf-8")
    return latest


def uninstall_hooks(scope: str = "local", target_dir: str = ".") -> int:
    if scope != "local":
        raise InstallError("Global uninstall is unsupported; uninstall each target repo by path.")
    project_dir = resolve_target(target_dir)
    removed: list[str] = []
    failures: list[str] = []

    for relative in (
        # shared implementation tree (current)
        ".gemini-delegation",
        # claude command (current)
        ".claude/commands/delegate.md",
        # antigravity rule (current)
        ".agents/rules/delegation.md",
        # shims from the pre-collapse layout (safe no-op if absent)
        ".claude/hooks/delegate.ps1",
        ".claude/hooks/delegate_and_log.ps1",
        ".claude/hooks/delegation_guard.ps1",
        ".claude/hooks/delegate.bat",
        ".claude/hooks/delegate",
        # legacy direct-copies (old-style; safe no-op if absent)
        ".claude/hooks/gemini_delegate.py",
        ".claude/hooks/pre_delegate.py",
        ".claude/hooks/post_delegate.py",
        ".claude/hooks/analyze_metrics.py",
        ".claude/hooks/delegation_guard.py",
        # codex shims from pre-collapse layout
        ".codex/hooks/delegate.ps1",
        ".codex/hooks/delegate_and_log.ps1",
        ".codex/hooks/delegation_guard.ps1",
        ".codex/hooks/delegate.bat",
        ".codex/hooks/delegate",
        # codex legacy direct-copies
        ".codex/hooks/gemini_delegate.py",
        ".codex/hooks/pre_delegate.py",
        ".codex/hooks/post_delegate.py",
        ".codex/hooks/analyze_metrics.py",
        ".codex/hooks/delegation_guard.py",
    ):
        _remove_path(project_dir / relative, removed, failures)

    try:
        if revert_claude_settings(project_dir / ".claude"):
            removed.append(str(project_dir / ".claude/settings.json (delegation_guard hooks removed)"))
    except (OSError, InstallError) as exc:
        failures.append(f".claude/settings.json revert: {exc}")

    try:
        if revert_codex_hooks(project_dir):
            removed.append(str(project_dir / ".codex/hooks.json (delegation_guard hooks removed)"))
    except (OSError, InstallError) as exc:
        failures.append(f".codex/hooks.json revert: {exc}")

    try:
        if remove_agents_md_section(project_dir):
            removed.append(str(project_dir / "AGENTS.md managed delegation section"))
    except (OSError, InstallError) as exc:
        failures.append(f"AGENTS.md managed section: {exc}")

    report = write_uninstall_report(project_dir, removed, failures)
    print(f"[OK] Wrote uninstall report: {report}")
    if failures:
        raise InstallError("Uninstall completed with failures. See report: {0}".format(report))
    print(f"[SUCCESS] Removed local delegation files from {project_dir}")
    return 0
