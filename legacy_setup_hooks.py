#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cross-platform setup script for Claude Code/Codex delegation to agy

This script:
1. Creates .claude/hooks directory structure
2. Makes hook scripts executable (Unix-like systems)
3. Creates sample wrapper scripts
4. Validates Python installation

Usage:
    python setup_hooks.py [user|local]

Scope:
    user     Install user-wide hooks under ~/.claude and ~/.gemini-delegation (default)
    local    Install project-local hooks under ./.claude and ./.gemini-delegation

Prefer the positional scope over old flag-style invocations. `--user` is still
accepted as a compatibility alias for `user`.
"""

import sys
import os
import stat
import shutil
import datetime
import json
import subprocess
from pathlib import Path
import platform

MARKER_BEGIN = "> [claude-gemini-delegation:begin]"
MARKER_END = "> [claude-gemini-delegation:end]"

SCRIPT_DIR = Path(__file__).parent
HOOKS_SOURCE = SCRIPT_DIR / "hooks"
CODEX_DIR_NAME = ".codex"
ROOT_CLAUDE_IMPORTS = ("@AGENTS.md",)
GEMINI_DELEGATION_DIR = ".gemini-delegation"

SHARED_HOOK_SCRIPTS = [
    "gemini_delegate.py",
    "pre_delegate.py",
    "post_delegate.py",
    "analyze_metrics.py",
    "delegation_guard.py",
    "delegation_guard.ps1",
    "delegate_and_log.ps1",
]
LEGACY_ROOT_CLAUDE_IMPORTS = ("@AGENTS.md", "@.claude/CLAUDE.md")
AGENTS_MARKER_BEGIN = "> [claude-gemini-delegation:agents-begin]"
AGENTS_MARKER_END = "> [claude-gemini-delegation:agents-end]"
MIGRATED_CLAUDE_MARKER_BEGIN = "> [claude-gemini-delegation:migrated-claude-begin]"
MIGRATED_CLAUDE_MARKER_END = "> [claude-gemini-delegation:migrated-claude-end]"
OLD_DEFAULT_AGENTS_TEXT = (
    "# Agent Instructions\n\n"
    + "Gemini"
    + " delegation is installed locally in `.claude/hooks`.\n\n"
    "The root `CLAUDE.md` also loads `.claude/CLAUDE.md`; follow that generated\n"
    "configuration for delegation presets, wrapper usage, and " + "Gemini" + " fallback\n"
    "behavior.\n"
)


def get_codex_dir(project_dir):
    """Return .codex, migrating the legacy .Codex directory casing."""
    canonical = project_dir / CODEX_DIR_NAME
    exact_children = {child.name: child for child in project_dir.iterdir()} if project_dir.exists() else {}
    legacy = exact_children.get(".Codex")
    exact_canonical = exact_children.get(CODEX_DIR_NAME)
    if legacy is not None and exact_canonical is None:
        if platform.system() == "Windows":
            temporary = project_dir / ".codex-case-migration"
            legacy.rename(temporary)
            temporary.rename(canonical)
        else:
            legacy.rename(canonical)
        print("[OK] Migrated legacy .Codex directory to .codex")
    return canonical


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def check_python_version():
    """Ensure Python 3.6+ is installed."""
    if sys.version_info < (3, 6):
        print("[ERROR] Python 3.6 or higher is required")
        print("   Current version: " + sys.version)
        sys.exit(1)

    print("[OK] Python {}.{} detected".format(sys.version_info.major, sys.version_info.minor))


def create_directory_structure(base_dir):
    """Create necessary directory structure."""
    dirs = [
        base_dir / "hooks",
        base_dir / "metrics",
    ]

    for dir_path in dirs:
        dir_path.mkdir(parents=True, exist_ok=True)
        print("[OK] Created directory: " + str(dir_path))


def make_executable(file_path):
    """Make a file executable on Unix-like systems."""
    if platform.system() != 'Windows':
        current_permissions = file_path.stat().st_mode
        file_path.chmod(current_permissions | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print("   Made executable: " + file_path.name)


def install_gemini_delegation_dir(project_dir: Path, config: dict):
    """Install the shared .gemini-delegation/ directory with all hook scripts."""
    gem_dir = project_dir / GEMINI_DELEGATION_DIR
    hooks_dir = gem_dir / "hooks"
    metrics_dir = gem_dir / "metrics"

    print("\n" + "=" * 60)
    print("Installing Shared .gemini-delegation/ Directory")
    print("=" * 60)
    hooks_dir.mkdir(parents=True, exist_ok=True)
    metrics_dir.mkdir(parents=True, exist_ok=True)

    for script in SHARED_HOOK_SCRIPTS:
        src = HOOKS_SOURCE / script
        if not src.exists():
            print("[WARNING] " + script + " not found in source, skipping")
            continue
        dest = hooks_dir / script
        shutil.copy2(src, dest)
        make_executable(dest)
        print("[OK] Installed " + script)

    _write_gemini_delegation_wrappers(hooks_dir)

    config_path = gem_dir / "delegation_config.json"
    import json as _json
    with config_path.open("w", encoding="utf-8") as f:
        _json.dump(config, f, indent=2)
    print("[OK] Saved config to " + str(config_path))


def _write_gemini_delegation_wrappers(hooks_dir: Path):
    """Write the main delegate wrapper scripts into .gemini-delegation/hooks/."""
    unix = hooks_dir / "delegate"
    unix.write_text(
        '#!/bin/bash\nSCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        'python3 "$SCRIPT_DIR/pre_delegate.py" "$@"\n',
        encoding="utf-8",
    )
    make_executable(unix)

    (hooks_dir / "delegate.bat").write_text(
        "@echo off\n"
        'set "SCRIPT_DIR=%~dp0"\n'
        'py -3 "%SCRIPT_DIR%pre_delegate.py" %*\n',
        encoding="utf-8",
    )

    (hooks_dir / "delegate.ps1").write_text(
        "$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path\n"
        "$PythonCommands = @(\n"
        "    @{ Command = 'py'; Prefix = @('-3') },\n"
        "    @{ Command = 'python3'; Prefix = @() },\n"
        "    @{ Command = 'python'; Prefix = @() }\n"
        ")\n"
        "foreach ($Python in $PythonCommands) {\n"
        "    if (-not (Get-Command $Python.Command -ErrorAction SilentlyContinue)) { continue }\n"
        "    & $Python.Command @($Python.Prefix + @('-c', 'import sys; sys.exit(0 if sys.version_info[0] >= 3 else 1)')) *> $null\n"
        "    if ($LASTEXITCODE -ne 0) { continue }\n"
        "    & $Python.Command @($Python.Prefix + @(\"$ScriptDir/pre_delegate.py\") + $args)\n"
        "    exit $LASTEXITCODE\n"
        "}\n"
        "Write-Error 'Python 3 was not found.'\n"
        "exit 127\n",
        encoding="utf-8",
    )
    print("[OK] Created .gemini-delegation wrapper scripts (delegate, delegate.bat, delegate.ps1)")


def create_env_shims(hooks_dir: Path):
    """Create thin per-environment shims that forward to .gemini-delegation/hooks/."""
    print("\n" + "=" * 60)
    print("Creating Wrapper Scripts")
    print("=" * 60)
    hooks_dir.mkdir(parents=True, exist_ok=True)

    shim = hooks_dir / "delegate"
    shim.write_text(
        '#!/bin/bash\n'
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        'PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"\n'
        'exec "$PROJECT_ROOT/.gemini-delegation/hooks/delegate" "$@"\n',
        encoding="utf-8",
    )
    make_executable(shim)
    print("[OK] Created Unix wrapper: delegate")

    (hooks_dir / "delegate.bat").write_text(
        "@echo off\n"
        'for %%I in ("%~dp0..\\..") do set "PROJECT_ROOT=%%~fI"\n'
        'call "%PROJECT_ROOT%\\.gemini-delegation\\hooks\\delegate.bat" %*\n',
        encoding="utf-8",
    )
    print("[OK] Created Windows wrapper: delegate.bat")

    (hooks_dir / "delegate.ps1").write_text(
        "$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path\n"
        '$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $ScriptDir "..\\.."))\n'
        '& (Join-Path $ProjectRoot ".gemini-delegation\\hooks\\delegate.ps1") @args\n'
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    print("[OK] Created PowerShell wrapper: delegate.ps1")

    (hooks_dir / "delegate_and_log.ps1").write_text(
        "[CmdletBinding()]\n"
        "param(\n"
        "    [Parameter(Mandatory = $true, Position = 0)][string]$Task,\n"
        "    [Parameter(Position = 1)][string]$Context = 'General task',\n"
        "    [Parameter(Position = 2)][int]$MaxLines = 0,\n"
        "    [ValidateSet('default', 'research')]\n"
        "    [string]$Profile = 'default'\n"
        ")\n"
        "$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path\n"
        '$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $ScriptDir "..\\.."))\n'
        '& (Join-Path $ProjectRoot ".gemini-delegation\\hooks\\delegate_and_log.ps1") `\n'
        "    -Task $Task -Context $Context -MaxLines $MaxLines -Profile $Profile\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    print("[OK] Created PowerShell wrapper: delegate_and_log.ps1")

    env_dir_name = hooks_dir.parent.name
    (hooks_dir / "delegation_guard.ps1").write_text(
        "$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path\n"
        '$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $ScriptDir "..\\.."))\n'
        f'$env:DELEGATION_HOOK_PREFIX = "{env_dir_name}/hooks"\n'
        "if ($MyInvocation.ExpectingInput) {\n"
        "    $InputPayload = ($input | Out-String).TrimEnd()\n"
        "} else {\n"
        "    $InputPayload = \"\"\n"
        "}\n"
        '$InputPayload | & (Join-Path $ProjectRoot ".gemini-delegation\\hooks\\delegation_guard.ps1")\n'
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    print("[OK] Created PowerShell wrapper: delegation_guard.ps1")


def create_wrapper_scripts(hooks_dir):
    """Create convenient wrapper scripts for different platforms."""

    # Unix wrapper (bash)
    unix_wrapper = hooks_dir / "delegate"
    unix_wrapper.write_text("""#!/bin/bash
# Wrapper script for delegation hooks
# Usage: ./delegate <task> [context] [max_lines]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/pre_delegate.py" "$@"
""", encoding="utf-8")
    make_executable(unix_wrapper)

    # Windows wrapper (batch)
    windows_wrapper = hooks_dir / "delegate.bat"
    windows_wrapper.write_text("""@echo off
REM Wrapper script for delegation hooks
REM Usage: delegate.bat <task> [context] [max_lines]

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -c "import sys; sys.exit(0 if sys.version_info[0] >= 3 else 1)" >nul 2>nul
  if %errorlevel%==0 (
    py -3 "%~dp0pre_delegate.py" %*
    exit /b %errorlevel%
  )
)

where python3 >nul 2>nul
if %errorlevel%==0 (
  python3 -c "import sys; sys.exit(0 if sys.version_info[0] >= 3 else 1)" >nul 2>nul
  if %errorlevel%==0 (
    python3 "%~dp0pre_delegate.py" %*
    exit /b %errorlevel%
  )
)

where python >nul 2>nul
if %errorlevel%==0 (
  python -c "import sys; sys.exit(0 if sys.version_info[0] >= 3 else 1)" >nul 2>nul
  if %errorlevel%==0 (
    python "%~dp0pre_delegate.py" %*
    exit /b %errorlevel%
  )
)

echo Python 3 was not found. Install Python 3.6+ or ensure py -3/python3 is on PATH. 1>&2
exit /b 127
""", encoding="utf-8")

    # PowerShell wrapper
    ps_wrapper = hooks_dir / "delegate.ps1"
    ps_wrapper.write_text("""# Wrapper script for delegation hooks
# Usage: ./delegate.ps1 <task> [context] [max_lines]

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonCommands = @(
    @{ Command = "py"; Prefix = @("-3") },
    @{ Command = "python3"; Prefix = @() },
    @{ Command = "python"; Prefix = @() }
)

foreach ($Python in $PythonCommands) {
    if (-not (Get-Command $Python.Command -ErrorAction SilentlyContinue)) {
        continue
    }

    & $Python.Command @($Python.Prefix + @("-c", "import sys; sys.exit(0 if sys.version_info[0] >= 3 else 1)")) *> $null
    if ($LASTEXITCODE -ne 0) {
        continue
    }

    & $Python.Command @($Python.Prefix + @("$ScriptDir/pre_delegate.py") + $args)
    exit $LASTEXITCODE
}

Write-Error "Python 3 was not found. Install Python 3.6+ or ensure py -3/python3 is on PATH."
exit 127
""", encoding="utf-8")

    print("[OK] Created wrapper scripts:")
    print("   - delegate (Unix)")
    print("   - delegate.bat (Windows)")
    print("   - delegate.ps1 (PowerShell)")


def create_claude_settings(claude_dir):
    """Merge the delegation guard PreToolUse hook into settings.json.

    Registers the guard for both Bash and PowerShell matchers so it fires
    regardless of which shell tool Claude uses. Derives the hook path from
    the Claude project directory name so the command is always correct.
    """
    settings_path = claude_dir / "settings.json"
    hook_root = claude_dir.name
    command = (
        f"pwsh -NoProfile -ExecutionPolicy Bypass -File {hook_root}/hooks/delegation_guard.ps1"
        if platform.system() == "Windows"
        else f"python3 {hook_root}/hooks/delegation_guard.py"
    )
    hook_def = {"type": "command", "command": command, "timeout": 5}

    def is_guard_hook(hook):
        if not isinstance(hook, dict):
            return False
        cmd = hook.get("command", "")
        return "delegation_guard.py" in cmd or "delegation_guard.ps1" in cmd

    settings = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            settings = {}
    if not isinstance(settings, dict):
        settings = {}

    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        settings["hooks"] = hooks

    pre_tool_use = hooks.setdefault("PreToolUse", [])
    if not isinstance(pre_tool_use, list):
        pre_tool_use = []
        hooks["PreToolUse"] = pre_tool_use

    # Remove all existing guard hooks so we can re-add them cleanly
    for entry in pre_tool_use:
        if isinstance(entry, dict) and isinstance(entry.get("hooks"), list):
            entry["hooks"] = [h for h in entry["hooks"] if not is_guard_hook(h)]

    # Ensure both Bash and PowerShell matchers have the guard
    matchers_needed = {"Bash", "PowerShell"}
    for entry in pre_tool_use:
        if isinstance(entry, dict) and entry.get("matcher") in matchers_needed:
            if isinstance(entry.get("hooks"), list):
                entry["hooks"].append(hook_def)
                matchers_needed.discard(entry["matcher"])

    for matcher in matchers_needed:
        pre_tool_use.append({"matcher": matcher, "hooks": [hook_def]})

    new_settings_text = json.dumps(settings, indent=2) + "\n"
    if settings_path.exists():
        if settings_path.read_text(encoding="utf-8") == new_settings_text:
            print("[OK] " + hook_root + "/settings.json delegation guard already up to date")
            return

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = settings_path.with_name("settings.json.bak." + timestamp)
        shutil.copy2(settings_path, backup_path)

    settings_path.write_text(new_settings_text, encoding="utf-8")
    print("[OK] Updated " + hook_root + "/settings.json delegation guard (Bash + PowerShell)")


def copy_hook_files(hooks_dir):
    """Copy the actual hook scripts next to the wrappers."""
    hooks_to_copy = [
        "pre_delegate.py",
        "post_delegate.py",
        "analyze_metrics.py",
        "gemini_delegate.py",
        "delegation_guard.py",
        "delegation_guard.ps1",
        "delegate_and_log.ps1",
    ]

    copied_count = 0
    for hook_file in hooks_to_copy:
        source = HOOKS_SOURCE / hook_file
        dest = hooks_dir / hook_file
        if not source.exists():
            print("[WARNING] " + hook_file + " not found in source, skipping")
            continue

        shutil.copy2(source, dest)
        make_executable(dest)
        copied_count += 1
        print("[OK] Installed " + hook_file)

    if copied_count != len(hooks_to_copy):
        print("[ERROR] One or more hook scripts were missing")
        sys.exit(1)


def ensure_dot_claude_bridge(claude_dir: Path) -> str:
    """Migrate .claude/CLAUDE.md content and remove the redundant bridge.

    Returns any user content that was outside the managed delegation section
    so it can be migrated into AGENTS.md.
    """
    claude_md = claude_dir / "CLAUDE.md"
    bridge = "@../AGENTS.md\n"

    if claude_md.exists():
        existing = claude_md.read_text(encoding="utf-8")
        if existing == bridge:
            claude_md.unlink()
            print("[OK] Removed redundant .claude/CLAUDE.md bridge")
            return ""

        content = existing
        if MARKER_BEGIN in content and MARKER_END in content:
            before = content[:content.index(MARKER_BEGIN)]
            after = content[content.index(MARKER_END) + len(MARKER_END):]
            content = before + after
        elif MARKER_BEGIN in content:
            content = content[:content.index(MARKER_BEGIN)]

        lines = [l for l in content.splitlines()
                 if l.strip() not in ("@../AGENTS.md", "@AGENTS.md")]
        user_content = "\n".join(lines).strip()

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = claude_md.with_name("CLAUDE.md.bak." + timestamp)
        shutil.copy2(claude_md, backup_path)
        print("[OK] Backed up .claude/CLAUDE.md to " + backup_path.name)
    else:
        user_content = ""

    if claude_md.exists():
        claude_md.unlink()
        print("[OK] Migrated and removed .claude/CLAUDE.md")
    return user_content + "\n" if user_content else ""


def build_root_claude_bridge(existing=""):
    """Return the root CLAUDE.md bridge."""
    return "@AGENTS.md\n"


def extract_migrated_claude_content(existing=""):
    """Return existing CLAUDE.md instructions after removing bridge imports."""
    retained_lines = existing.splitlines()

    while retained_lines and retained_lines[0].strip() in LEGACY_ROOT_CLAUDE_IMPORTS:
        retained_lines.pop(0)
    while retained_lines and not retained_lines[0].strip():
        retained_lines.pop(0)
    while retained_lines and not retained_lines[-1].strip():
        retained_lines.pop()

    if not retained_lines:
        return ""
    return "\n".join(retained_lines) + "\n"


def ensure_root_claude_bridge(project_dir):
    """Ensure project-level CLAUDE.md points only to AGENTS.md."""
    claude_md = project_dir / "CLAUDE.md"
    existing = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
    desired_content = build_root_claude_bridge(existing)

    if claude_md.exists():
        if existing == desired_content:
            print("[OK] Root CLAUDE.md already includes delegation bridge")
            return claude_md

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = claude_md.with_name("root_claude_md_backup_" + timestamp + ".md")
        shutil.copy2(claude_md, backup_path)
        print("[OK] Backed up existing root CLAUDE.md to " + backup_path.name)

    claude_md.write_text(desired_content, encoding="utf-8")
    print("[OK] Updated root CLAUDE.md delegation bridge")
    return claude_md


def build_agents_section():
    """Return the managed AGENTS.md delegation section."""
    body = """## Antigravity Delegation

Antigravity/Gemini delegation is installed locally in `.claude/hooks` and
`.codex/hooks`. Antigravity CLI (`agy`) is the executable path; Gemini is the
AI agent/model pool reached through agy. Do not call a direct `gemini`
executable for this workflow.

### Always Delegate

Use delegation for token-heavy or broad read-only work:
- Commands expected to produce more than 500 lines of output
- `npm ls`, `pip list`, `pip freeze`, and verbose dependency listings
- `git log` beyond 5 commits or broad git history analysis
- Recursive searches such as `find`, `grep -r`, or repository-wide scans
- Reading or analyzing 3 or more new files
- Security audits, vulnerability scans, XSS/SQL injection/CSRF checks
- Documentation lookup or web search. Use `--profile research` so a Pro model is tried before Flash.
- Broad codebase analysis, performance review, or inspection tasks

### Subagent Policy

Do **not** use Claude subagents for delegation work. Subagents spend Claude
tokens and defeat this configuration's token-saving purpose.
Only use Claude subagents when the user explicitly asks for Claude subagents by name.

When the current agent is Antigravity or agy itself, do the work directly.
Do not recursively invoke agy from inside an Antigravity agent session.

### Delegation Workflow

1. **Identify task type** - Security? Git ops? Analysis?
2. **Check presets** - Is there a matching preset?
3. **Use delegation hook** - Let the hook format the prompt
4. **Execute with agy** - Use `gemini_delegate.py`, not Claude subagents
5. **Validate response** - Check quality with the post-delegation hook

### Quick Delegation

**On Windows always use the PowerShell tool — not Bash.** Git Bash cannot
run `.ps1` scripts. When generating commands, use `PowerShell(...)` not `Bash(...)`.

**Windows (PowerShell tool):**
```powershell
# Claude Code hook
& .claude/hooks/delegate_and_log.ps1 "analyze @src/ for performance issues" "Optimization task" 10

# Codex hook
& .codex/hooks/delegate_and_log.ps1 "analyze @src/ for performance issues" "Optimization task" 10

# Research profile (Pro before Flash)
& .claude/hooks/delegate_and_log.ps1 "find docs for X" "Research task" 10 -Profile research
```

**Unix/Mac (Bash):**
```bash
PROMPT=$(./.claude/hooks/delegate "npm ls" "Build analysis")
echo "$PROMPT" | python3 .gemini-delegation/hooks/gemini_delegate.py
```

The PowerShell wrappers resolve Python 3 explicitly (`py -3`, then `python3`,
then a verified Python 3 `python`) so they do not accidentally run Python 2.

`.claude/settings.json` registers a PreToolUse Bash guard that blocks known
high-output Bash commands and returns delegation instructions.

Keep project-specific agent instructions outside this managed section. Re-running
setup updates only this block.
"""
    return AGENTS_MARKER_BEGIN + "\n" + body.strip() + "\n" + AGENTS_MARKER_END + "\n"


def build_migrated_claude_section(content):
    """Return a managed section containing prior root CLAUDE.md content."""
    if not content.strip():
        return ""

    body = "## Migrated CLAUDE.md Instructions\n\n" + content.strip()
    return MIGRATED_CLAUDE_MARKER_BEGIN + "\n" + body + "\n" + MIGRATED_CLAUDE_MARKER_END + "\n"


def normalize_agents_content(existing):
    """Remove obsolete generated boilerplate while preserving user content."""
    normalized = existing.replace("\r\n", "\n").lstrip("\ufeff")
    if normalized.startswith(OLD_DEFAULT_AGENTS_TEXT):
        remainder = normalized[len(OLD_DEFAULT_AGENTS_TEXT):].lstrip("\n")
        if remainder:
            return "# Agent Instructions\n\n" + remainder
        return "# Agent Instructions\n"
    return normalized


def _launch_claude_conflict_fix(agents_md: Path, conflict: str):
    """Try to launch the claude CLI to resolve an AGENTS.md marker conflict, then exit."""
    prompt = (
        "Fix AGENTS.md delegation markers: " + conflict + ". "
        "Begin marker: '> [claude-gemini-delegation:agents-begin]'. "
        "End marker: '> [claude-gemini-delegation:agents-end]'. "
        "Inspect " + str(agents_md) + ", fix or remove the orphaned marker "
        "so begin and end properly wrap the delegation section, then confirm."
    )
    print("[WARNING] AGENTS.md conflict: " + conflict)
    print("[INFO] Launching Claude CLI to resolve the conflict...")
    try:
        result = subprocess.run(["claude", "--print", prompt], timeout=300, check=False)
        if result.returncode == 0:
            print("[OK] Claude resolved the conflict. Re-run setup to apply changes.")
        else:
            raise OSError("claude exited " + str(result.returncode))
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("[ERROR] Could not launch Claude: " + str(exc))
        print("[ERROR] Manually fix " + str(agents_md))
        print("        Ensure begin/end markers match and wrap the delegation section.")
    raise SystemExit(1)


def ensure_agents_md(project_dir, migrated_claude_content=""):
    """Ensure AGENTS.md exists without overwriting project-specific instructions."""
    agents_md = project_dir / "AGENTS.md"
    managed_section = build_agents_section()
    migrated_section = build_migrated_claude_section(migrated_claude_content)

    if agents_md.exists():
        raw_existing = agents_md.read_text(encoding="utf-8")
        existing = normalize_agents_content(raw_existing)

        has_begin = AGENTS_MARKER_BEGIN in existing
        has_end = AGENTS_MARKER_END in existing
        if has_begin != has_end:
            conflict = (
                "begin marker found without end marker"
                if has_begin else "end marker found without begin marker"
            )
            _launch_claude_conflict_fix(agents_md, conflict)

        working = existing
        if migrated_section and MIGRATED_CLAUDE_MARKER_BEGIN not in working:
            working = working.rstrip("\n") + "\n\n" + migrated_section

        if has_begin and has_end:
            before = working[:working.index(AGENTS_MARKER_BEGIN)]
            after = working[working.index(AGENTS_MARKER_END) + len(AGENTS_MARKER_END):]
            new_content = before + managed_section + after.lstrip("\n")
        else:
            new_content = working.rstrip("\n") + "\n\n" + managed_section

        if new_content == raw_existing:
            print("[OK] AGENTS.md already includes delegation section")
            return agents_md

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = agents_md.with_name("AGENTS.md.bak." + timestamp)
        shutil.copy2(agents_md, backup_path)
        print("[OK] Backed up existing AGENTS.md to " + backup_path.name)
    else:
        base_content = migrated_claude_content.strip() or "# Agent Instructions"
        new_content = base_content.rstrip("\n") + "\n\n" + managed_section

    agents_md.write_text(new_content, encoding="utf-8")
    print("[OK] Updated AGENTS.md delegation section")
    return agents_md


def create_readme(hooks_dir):
    """Create README for hooks directory."""
    readme = hooks_dir / "README.md"

    content = """# Delegation Hooks

Cross-platform Python hooks for Claude Code/Codex -> agy delegation.

## Usage

### Pre-delegation (format prompts)

```bash
# Unix/Mac/Linux
python3 pre_delegate.py "npm ls" "Debugging build" 8

# Windows
py -3 pre_delegate.py "npm ls" "Debugging build" 8
```

### Post-delegation (validate responses)

```bash
python3 post_delegate.py "Response text..." 10 "task-name"
```

### Analyze metrics

```bash
python3 analyze_metrics.py
python3 analyze_metrics.py --days 14  # Last 14 days
```

## Wrapper Scripts

For convenience, use the wrapper scripts:

**Unix/Mac/Linux:**
```bash
./delegate "npm ls" "Build investigation"
```

**Windows (Command Prompt):**
```cmd
delegate.bat "npm ls" "Build investigation"
```

**Windows (PowerShell):**
```powershell
./delegate.ps1 "npm ls" "Build investigation"
./delegate_and_log.ps1 "npm ls" "Build investigation" 5
```

## Requirements

- Python 3.6 or higher (no additional dependencies)

## Platform Notes

All scripts use standard library only and work identically across:
- Windows (Command Prompt, PowerShell)
- macOS (Terminal, iTerm2)
- Linux (bash, zsh, fish)
"""

    readme.write_text(content, encoding="utf-8")
    print("[OK] Created README.md in hooks directory")


def create_gitignore(claude_dir):
    """Create .gitignore for metrics."""
    gitignore = claude_dir / ".gitignore"

    if gitignore.exists():
        content = gitignore.read_text()
        if "metrics/" not in content:
            gitignore.write_text(content + "\n# Delegation metrics\nmetrics/\n", encoding="utf-8")
            print("[OK] Updated .gitignore")
    else:
        gitignore.write_text("# Delegation metrics\nmetrics/\n", encoding="utf-8")
        print("[OK] Created .gitignore")


def create_antigravity_rule(project_dir):
    """Create the workspace rule used by Antigravity IDE."""
    rules_dir = project_dir / ".agents" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    rule_path = rules_dir / "delegation.md"
    rule_path.write_text(
        "# Delegation workspace rule\n\n"
        "- Read and follow the repository-root `AGENTS.md`.\n"
        "- This session is already running in Antigravity, so perform work directly.\n"
        "- Do not recursively invoke `agy` from Antigravity IDE or agy CLI.\n"
        "- `.claude/hooks` and `.codex/hooks` are entry points for Claude Code and Codex.\n",
        encoding="utf-8",
    )
    print("[OK] Created Antigravity workspace rule: " + str(rule_path))


def print_next_steps(claude_dir, is_user_install=False):
    """Print next steps for the user."""
    print("\n" + "=" * 60)
    print("Setup Complete!")
    print("=" * 60)

    print("\nInstallation Location:")
    print("   " + str(claude_dir.absolute()))

    print("\nNext Steps:")

    if is_user_install:
        print("\n1. This is a user-wide installation")
        print("   Hooks are available from your user Claude directory")
    else:
        print("\n1. This is a project-specific installation")
        print("   Hooks are only available in this project")

    print("\n2. Test the hooks:")

    if platform.system() == 'Windows':
        print("   cd " + str(claude_dir / "hooks"))
        print('   py -3 pre_delegate.py "npm ls" "Test" 5')
    else:
        print("   cd " + str(claude_dir / "hooks"))
        print('./delegate "npm ls" "Test" 5')

    print("\n3. Update your workflow:")
    print("   Project installs update AGENTS.md and CLAUDE.md automatically")

    print("\n4. Run a test delegation:")
    if platform.system() == 'Windows':
        test_cmd = '.claude\\hooks\\delegate.ps1 "git status" "Test delegation"'
    else:
        test_cmd = './.claude/hooks/delegate "git status" "Test delegation"'

    if platform.system() == 'Windows':
        print("   $prompt = & " + test_cmd)
        print("   $prompt | py -3 .gemini-delegation\\hooks\\gemini_delegate.py")
        print("   .claude\\hooks\\delegate_and_log.ps1 \"git status\" \"Test delegation\" 5")
    else:
        print("   PROMPT=$(" + test_cmd + ")")
        print('   echo "$PROMPT" | python3 .gemini-delegation/hooks/gemini_delegate.py')

    print("\nDocumentation:")
    print("   " + str(claude_dir / "hooks" / "README.md"))
    if not is_user_install:
        print("   " + str(claude_dir.parent / CODEX_DIR_NAME / "hooks" / "README.md"))

    print("\nTips:")
    print("   - Hooks work identically on Windows, Mac, and Linux")
    print("   - No dependencies needed beyond Python 3.6+")
    print("   - Metrics are auto-logged in .claude/metrics/")
    print("   - Run analyze_metrics.py weekly to optimize prompts")


def main():
    """Main setup process."""
    print("Claude/Codex -> Antigravity Delegation Hooks Setup")
    print("=" * 60)

    # Check Python version
    check_python_version()

    # Determine installation location
    raw_args = [arg for arg in sys.argv[1:] if arg]
    if "--user" in raw_args:
        raw_args.remove("--user")
        raw_args.append("user")
        print("\n[WARNING] `--user` is deprecated; use positional `user` instead.")
    if "--local" in raw_args:
        raw_args.remove("--local")
        raw_args.append("local")
        print("\n[WARNING] `--local` is deprecated; use positional `local` instead.")

    if len(raw_args) > 1 or any(arg not in ("user", "local") for arg in raw_args):
        print("\n[ERROR] Usage: python setup_hooks.py [user|local]")
        sys.exit(2)

    scope = raw_args[0] if raw_args else "user"
    is_user_install = scope == "user"
    if is_user_install:
        base_dir = Path.home() / ".claude"
        print("\nInstalling to user directory: " + str(base_dir))
    else:
        base_dir = Path.cwd() / ".claude"
        print("\nInstalling to project directory: " + str(base_dir))

    print()

    # Create directory structure
    create_directory_structure(base_dir)

    # Install shared scripts to .gemini-delegation/ (single copy for both envs)
    config = {"version": "1.0.0", "preferences": {"default_cli": "agy"}}
    install_gemini_delegation_dir(base_dir.parent, config)

    # Install per-env shims only (thin forwarders to .gemini-delegation/hooks/)
    hooks_dir = base_dir / "hooks"
    create_env_shims(hooks_dir)

    # Migrate any legacy .claude/CLAUDE.md content into AGENTS.md.
    dot_claude_migrated = ensure_dot_claude_bridge(base_dir)
    if not is_user_install:
        create_claude_settings(base_dir)
        codex_dir = get_codex_dir(base_dir.parent)
        create_directory_structure(codex_dir)
        create_env_shims(codex_dir / "hooks")
        create_readme(codex_dir / "hooks")
        create_gitignore(codex_dir)
        root_claude_md = base_dir.parent / "CLAUDE.md"
        root_claude_existing = root_claude_md.read_text(encoding="utf-8") if root_claude_md.exists() else ""
        root_migrated = extract_migrated_claude_content(root_claude_existing)
        combined_migration = (
            root_migrated.rstrip("\n") + "\n\n" + dot_claude_migrated
            if dot_claude_migrated.strip() else root_migrated
        )
        ensure_agents_md(base_dir.parent, combined_migration)
        ensure_root_claude_bridge(base_dir.parent)
        create_antigravity_rule(base_dir.parent)
    create_readme(hooks_dir)
    create_gitignore(base_dir)

    # Make Python scripts executable on Unix
    if platform.system() != 'Windows':
        for script in ['pre_delegate.py', 'post_delegate.py', 'analyze_metrics.py']:
            script_path = hooks_dir / script
            if script_path.exists():
                make_executable(script_path)

    # Print next steps
    print_next_steps(base_dir, is_user_install)


if __name__ == "__main__":
    main()
