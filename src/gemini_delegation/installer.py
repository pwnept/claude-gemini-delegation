import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


AGENTS_MARKER_BEGIN = "> [claude-gemini-delegation:agents-begin]"
AGENTS_MARKER_END = "> [claude-gemini-delegation:agents-end]"
MANAGED_FILES = (
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
    ".gemini-delegation/delegation_config.json",
    ".claude/hooks/delegate.ps1",
    ".claude/hooks/delegate_and_log.ps1",
    ".claude/hooks/delegation_guard.ps1",
    ".claude/hooks/delegate.bat",
    ".claude/hooks/delegate",
    ".claude/settings.json",
    ".codex/hooks/delegate.ps1",
    ".codex/hooks/delegate_and_log.ps1",
    ".codex/hooks/delegation_guard.ps1",
    ".codex/hooks/delegate.bat",
    ".codex/hooks/delegate",
    ".agents/rules/delegation.md",
    ".claude/commands/delegate.md",
)
HOOK_FILES = (
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


class InstallError(RuntimeError):
    """Expected installer error with user-actionable guidance."""


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
recursive repo scans, or expected output > 500 lines. Run from the repo root:

```powershell
& .claude/hooks/delegate_and_log.ps1 "<task>" "<context>" 10
& .claude/hooks/delegate_and_log.ps1 "<task>" "<context>" 10 -Profile research
```

Set `DELEGATION_BACKEND=gemini-api` with a `GEMINI_API_KEY` from
https://aistudio.google.com/apikey to delegate without an agy install.

When running as Antigravity or `agy`, do the work directly — do not recursively invoke `agy`.
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
    try:
        (dest_hooks / "delegate").chmod(0o755)
    except OSError:
        pass
    config = {
        "backend": "agy",
        "installed_by": "claude-gemini-delegation",
        "installed_at": datetime.now().isoformat(timespec="seconds"),
        "managed_markers": [AGENTS_MARKER_BEGIN, AGENTS_MARKER_END],
    }
    (project_dir / ".gemini-delegation" / "delegation_config.json").write_text(
        json.dumps(config, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[OK] Copied shared hooks to {dest_hooks}")
    return dest_hooks


_CLAUDE_COMMAND_DELEGATE = """\
Delegate the following task to the agy (Antigravity) Gemini backend using the
in-repo delegation script. Run from the repository root and report the full output:

```powershell
& .claude/hooks/delegate_and_log.ps1 "$ARGUMENTS" "/delegate" 10
```

Add `-Profile research` when the task involves web search, documentation
lookup, security audits, or reading many files.
"""


def create_claude_command(claude_dir: Path) -> None:
    """Write the /delegate convenience command for Claude Code."""
    commands_dir = claude_dir / "commands"
    commands_dir.mkdir(parents=True, exist_ok=True)
    dest = commands_dir / "delegate.md"
    _write_if_changed(dest, _CLAUDE_COMMAND_DELEGATE, backup_existing=False)
    print(f"[OK] Created {dest}")


def _proxy_ps1(script_name: str) -> str:
    return f"""[CmdletBinding()]
param(
    [Parameter(ValueFromPipeline = $true)]
    [AllowNull()]
    [object]$InputObject,

    [Parameter(ValueFromRemainingArguments = $true)]
    [object[]]$RemainingArgs
)
begin {{
    $PipelineInput = @()
}}
process {{
    if ($null -ne $InputObject) {{
        $PipelineInput += $InputObject
    }}
}}
end {{
    $Shared = Join-Path $PSScriptRoot "..\\..\\.gemini-delegation\\hooks\\{script_name}"
    if (-not (Test-Path -LiteralPath $Shared)) {{
        throw "Missing shared delegation hook: $Shared. Run install-delegation.ps1 install --target <repo>."
    }}

    if ($PipelineInput.Count -gt 0) {{
        $PipelineInput | & $Shared @RemainingArgs
    }} else {{
        & $Shared @RemainingArgs
    }}
    exit $LASTEXITCODE
}}
"""


def _proxy_bat(script_name: str) -> str:
    return f"""@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0{script_name}.ps1" %*
exit /b %ERRORLEVEL%
"""


def _proxy_sh(script_name: str) -> str:
    return f"""#!/bin/sh
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec "$SCRIPT_DIR/../../.gemini-delegation/hooks/{script_name}" "$@"
"""


def create_tool_shims(project_dir: Path) -> None:
    for tool_dir in (project_dir / ".claude" / "hooks", get_codex_dir(project_dir) / "hooks"):
        tool_dir.mkdir(parents=True, exist_ok=True)
        for name in ("delegate", "delegate_and_log", "delegation_guard"):
            _write_if_changed(tool_dir / f"{name}.ps1", _proxy_ps1(f"{name}.ps1"), backup_existing=False)
        _write_if_changed(tool_dir / "delegate.bat", _proxy_bat("delegate"), backup_existing=False)
        _write_if_changed(tool_dir / "delegate", _proxy_sh("delegate"), backup_existing=False)
        try:
            (tool_dir / "delegate").chmod(0o755)
        except OSError:
            pass
        print(f"[OK] Created {tool_dir} shims")
    create_claude_command(project_dir / ".claude")


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

    hooks = settings.setdefault("hooks", {})
    pre_tool_use = hooks.setdefault("PreToolUse", [])
    cleaned = []
    for entry in pre_tool_use:
        entry_hooks = [
            hook
            for hook in entry.get("hooks", [])
            if "delegation_guard" not in hook.get("command", "")
        ]
        if entry_hooks:
            copied = dict(entry)
            copied["hooks"] = entry_hooks
            cleaned.append(copied)

    guard_command = "pwsh -NoProfile -ExecutionPolicy Bypass -File .claude/hooks/delegation_guard.ps1"
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


def revert_claude_settings(claude_dir: Path) -> bool:
    """Strip delegation_guard PreToolUse entries from .claude/settings.json.

    Returns True if the file was changed.  Does not touch any other keys.
    """
    settings_path = claude_dir / "settings.json"
    if not settings_path.exists():
        return False
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False

    hooks = settings.get("hooks", {})
    pre_tool_use = hooks.get("PreToolUse", [])
    if not pre_tool_use:
        return False

    cleaned = []
    for entry in pre_tool_use:
        entry_hooks = [
            hook
            for hook in entry.get("hooks", [])
            if "delegation_guard" not in hook.get("command", "")
        ]
        if entry_hooks:
            copied = dict(entry)
            copied["hooks"] = entry_hooks
            cleaned.append(copied)

    if cleaned == pre_tool_use:
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
        print(f"[OK] Removed delegation_guard hooks from {settings_path}")
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
        if claude_bridge != "@AGENTS.md":
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
    print(f"[OK] Verified delegation install in {project_dir}")
    return 0


def install_hooks(
    scope: str = "local",
    target_dir: str = ".",
    create_target: bool = False,
    preserve_claude_md: bool = False,
) -> int:
    if scope != "local":
        raise InstallError(
            "Global project mutation is intentionally unsupported.\n"
            "Install locally into each target repo with: install --target <path>."
        )
    project_dir = resolve_target(target_dir, create=create_target)
    if preserve_claude_md:
        print("[INFO] --preserve-claude-md: skipping CLAUDE.md migration.")
        migrated = ""
    else:
        migrated = migrate_claude_instructions(project_dir)
    ensure_agents_md(project_dir, migrated)
    copy_shared_hooks(project_dir)
    create_tool_shims(project_dir)
    create_claude_settings(project_dir / ".claude")
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
        # shared implementation tree
        ".gemini-delegation",
        # claude shims (new-style)
        ".claude/hooks/delegate.ps1",
        ".claude/hooks/delegate_and_log.ps1",
        ".claude/hooks/delegation_guard.ps1",
        ".claude/hooks/delegate.bat",
        ".claude/hooks/delegate",
        # claude legacy direct-copies (old-style; safe no-op if absent)
        ".claude/hooks/gemini_delegate.py",
        ".claude/hooks/pre_delegate.py",
        ".claude/hooks/post_delegate.py",
        ".claude/hooks/analyze_metrics.py",
        ".claude/hooks/delegation_guard.py",
        # claude command
        ".claude/commands/delegate.md",
        # codex shims (new-style)
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
        # antigravity rule
        ".agents/rules/delegation.md",
    ):
        _remove_path(project_dir / relative, removed, failures)

    try:
        if revert_claude_settings(project_dir / ".claude"):
            removed.append(str(project_dir / ".claude/settings.json (delegation_guard hooks removed)"))
    except (OSError, InstallError) as exc:
        failures.append(f".claude/settings.json revert: {exc}")

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
