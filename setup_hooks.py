#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cross-platform setup script for Claude-Gemini delegation hooks

This script:
1. Creates .claude/hooks directory structure
2. Makes hook scripts executable (Unix-like systems)
3. Creates sample wrapper scripts
4. Validates Python installation

Usage:
    python setup-hooks.py [--user]

Options:
    --user    Install in user's home directory (~/.claude)
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
DEFAULT_GEMINI_MODEL = "gemini-3-flash-preview"
ROOT_CLAUDE_IMPORTS = ("@AGENTS.md",)
LEGACY_ROOT_CLAUDE_IMPORTS = ("@AGENTS.md", "@.claude/CLAUDE.md")
AGENTS_MARKER_BEGIN = "> [claude-gemini-delegation:agents-begin]"
AGENTS_MARKER_END = "> [claude-gemini-delegation:agents-end]"
MIGRATED_CLAUDE_MARKER_BEGIN = "> [claude-gemini-delegation:migrated-claude-begin]"
MIGRATED_CLAUDE_MARKER_END = "> [claude-gemini-delegation:migrated-claude-end]"
OLD_DEFAULT_AGENTS_TEXT = """# Agent Instructions

Gemini delegation is installed locally in `.claude/hooks`.

The root `CLAUDE.md` also loads `.claude/CLAUDE.md`; follow that generated
configuration for delegation presets, wrapper usage, and Gemini fallback
behavior.
"""

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

    Works for both .claude/ and .Codex/ — derives the hook path from
    the directory name so the command is always correct.
    """
    settings_path = claude_dir / "settings.json"
    hook_root = claude_dir.name  # ".claude" or ".Codex"
    command = (
        f"powershell -NoProfile -ExecutionPolicy Bypass -File {hook_root}/hooks/delegation_guard.ps1"
        if platform.system() == "Windows"
        else f"python3 {hook_root}/hooks/delegation_guard.py"
    )
    guard_entry = {
        "matcher": "Bash",
        "hooks": [
            {
                "type": "command",
                "command": command,
                "timeout": 5,
            }
        ],
    }

    def is_delegation_guard_hook(hook):
        if not isinstance(hook, dict):
            return False
        command_text = hook.get("command", "")
        return "delegation_guard.py" in command_text or "delegation_guard.ps1" in command_text

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

    guard_count = 0
    desired_guard_present = False
    for entry in pre_tool_use:
        if not isinstance(entry, dict):
            continue
        for hook in entry.get("hooks", []):
            if is_delegation_guard_hook(hook):
                guard_count += 1
                desired_guard_present = (
                    desired_guard_present
                    or entry.get("matcher") == "Bash"
                    and hook == guard_entry["hooks"][0]
                )

    if guard_count == 1 and desired_guard_present:
        print("[OK] CLAUDE settings already include delegation guard")
        return

    for entry in pre_tool_use:
        if not isinstance(entry, dict):
            continue
        hooks_list = entry.get("hooks", [])
        if isinstance(hooks_list, list):
            entry["hooks"] = [
                hook for hook in hooks_list
                if not is_delegation_guard_hook(hook)
            ]

    bash_entry = None
    for entry in pre_tool_use:
        if isinstance(entry, dict) and entry.get("matcher") == "Bash" and isinstance(entry.get("hooks"), list):
            bash_entry = entry
            break

    if bash_entry is None:
        bash_entry = {"matcher": "Bash", "hooks": []}
        pre_tool_use.append(bash_entry)

    if settings_path.exists():
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = settings_path.with_name("settings.json.bak." + timestamp)
        shutil.copy2(settings_path, backup_path)
        print("[OK] Backed up existing settings.json to " + backup_path.name)

    bash_entry["hooks"].append(guard_entry["hooks"][0])
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print("[OK] Updated .claude/settings.json delegation guard")


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
    """Replace .claude/CLAUDE.md with a bridge to ../AGENTS.md.

    Returns any user content that was outside the managed delegation section
    so it can be migrated into AGENTS.md.
    """
    claude_md = claude_dir / "CLAUDE.md"
    bridge = "@../AGENTS.md\n"

    if claude_md.exists():
        existing = claude_md.read_text(encoding="utf-8")
        if existing == bridge:
            print("[OK] .claude/CLAUDE.md is already a bridge to AGENTS.md")
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

    claude_md.write_text(bridge, encoding="utf-8")
    print("[OK] .claude/CLAUDE.md is now a bridge to AGENTS.md")
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
    body = """## Gemini Delegation

Gemini delegation is installed locally in `.claude/hooks` and `.Codex/hooks`.

### Always Delegate

Use delegation for token-heavy or broad read-only work:
- Commands expected to produce more than 500 lines of output
- `npm ls`, `pip list`, `pip freeze`, and verbose dependency listings
- `git log` beyond 5 commits or broad git history analysis
- Recursive searches such as `find`, `grep -r`, or repository-wide scans
- Reading or analyzing 3 or more new files
- Security audits, vulnerability scans, XSS/SQL injection/CSRF checks
- Documentation lookup or web search. Use `--profile research` so Gemini Pro is tried before Flash.
- Broad codebase analysis, performance review, or inspection tasks

### Subagent Policy

Do **not** use Claude subagents for delegation work. Subagents spend Claude
tokens and defeat this configuration's token-saving purpose.
Only use Claude subagents when the user explicitly asks for Claude subagents by name.

### Delegation Workflow

1. **Identify task type** - Security? Git ops? Analysis?
2. **Check presets** - Is there a matching preset?
3. **Use delegation hook** - Let the hook format the prompt
4. **Execute with Gemini CLI** - Use `gemini_delegate.py`, not Claude subagents
5. **Validate response** - Check quality with the post-delegation hook

### Quick Delegation

**Windows (PowerShell):**
```powershell
# Claude Code hook
.claude/hooks/delegate_and_log.ps1 "analyze @src/ for performance issues" "Optimization task" 10

# Codex hook
.Codex/hooks/delegate_and_log.ps1 "analyze @src/ for performance issues" "Optimization task" 10

# Research profile (Gemini Pro before Flash)
.claude/hooks/delegate_and_log.ps1 "find docs for X" "Research task" 10 -Profile research
```

**Unix/Mac:**
```bash
PROMPT=$(./.claude/hooks/delegate "npm ls" "Build analysis")
gemini --model """ + DEFAULT_GEMINI_MODEL + """ -p "$PROMPT"
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

Cross-platform Python hooks for Claude Code -> Gemini delegation.

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


def print_next_steps(claude_dir, is_user_install):
    """Print next steps for the user."""
    print("\n" + "=" * 60)
    print("Setup Complete!")
    print("=" * 60)

    print("\nInstallation Location:")
    print("   " + str(claude_dir.absolute()))

    print("\nNext Steps:")

    if is_user_install:
        print("\n1. This is a user-wide installation")
        print("   Hooks will be available for all your projects")
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
        print("   $prompt | py -3 .claude\\hooks\\gemini_delegate.py")
        print("   .claude\\hooks\\delegate_and_log.ps1 \"git status\" \"Test delegation\" 5")
    else:
        print("   PROMPT=$(" + test_cmd + ")")
        print("   gemini --model " + DEFAULT_GEMINI_MODEL + ' -p "$PROMPT"')

    print("\nDocumentation:")
    print("   " + str(claude_dir / "hooks" / "README.md"))
    if not is_user_install:
        print("   " + str(claude_dir.parent / ".Codex" / "hooks" / "README.md"))

    print("\nTips:")
    print("   - Hooks work identically on Windows, Mac, and Linux")
    print("   - No dependencies needed beyond Python 3.6+")
    print("   - Metrics are auto-logged in .claude/metrics/")
    print("   - Run analyze_metrics.py weekly to optimize prompts")


def main():
    """Main setup process."""
    print("Claude-Gemini Delegation Hooks Setup")
    print("=" * 60)

    # Check Python version
    check_python_version()

    # Determine installation location
    is_user_install = '--user' in sys.argv

    if is_user_install:
        base_dir = Path.home() / ".claude"
        print("\nInstalling to user directory: " + str(base_dir))
    else:
        base_dir = Path.cwd() / ".claude"
        print("\nInstalling to project directory: " + str(base_dir))

    print()

    # Create directory structure
    create_directory_structure(base_dir)

    # Create wrapper scripts
    hooks_dir = base_dir / "hooks"
    create_wrapper_scripts(hooks_dir)
    copy_hook_files(hooks_dir)

    # Make .claude/CLAUDE.md a bridge; migrate its content to AGENTS.md
    dot_claude_migrated = ensure_dot_claude_bridge(base_dir)
    if not is_user_install:
        create_claude_settings(base_dir)
        codex_dir = base_dir.parent / ".Codex"
        create_directory_structure(codex_dir)
        create_wrapper_scripts(codex_dir / "hooks")
        copy_hook_files(codex_dir / "hooks")
        create_claude_settings(codex_dir)
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
