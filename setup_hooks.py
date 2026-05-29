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
from pathlib import Path
import platform

MARKER_BEGIN = "> [claude-gemini-delegation:begin]"
MARKER_END = "> [claude-gemini-delegation:end]"

SCRIPT_DIR = Path(__file__).parent
HOOKS_SOURCE = SCRIPT_DIR / "hooks"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
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

python "%~dp0pre_delegate.py" %*
""", encoding="utf-8")

    # PowerShell wrapper
    ps_wrapper = hooks_dir / "delegate.ps1"
    ps_wrapper.write_text("""# Wrapper script for delegation hooks
# Usage: ./delegate.ps1 <task> [context] [max_lines]

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
python "$ScriptDir/pre_delegate.py" $args
""", encoding="utf-8")

    print("[OK] Created wrapper scripts:")
    print("   - delegate (Unix)")
    print("   - delegate.bat (Windows)")
    print("   - delegate.ps1 (PowerShell)")


def copy_hook_files(hooks_dir):
    """Copy the actual hook scripts next to the wrappers."""
    hooks_to_copy = [
        "pre_delegate.py",
        "post_delegate.py",
        "analyze_metrics.py",
        "gemini_delegate.py",
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


def create_sample_claude_md(claude_dir):
    """Create or update the delegation section in CLAUDE.md.

    Uses begin/end markers so the managed section can be updated in-place
    without overwriting surrounding user content. A timestamped backup is
    created whenever an existing file is modified.
    """
    claude_md = claude_dir / "CLAUDE.md"

    managed_body = """# Claude Code Configuration

## Delegation with Hooks

Use Python hooks for cross-platform delegation:

```bash
# Using wrapper script (Unix/Mac)
PROMPT=$(./.claude/hooks/delegate "npm ls" "Investigating build slowdown")
gemini --model {model} -p "$PROMPT"

# Using Python directly (all platforms)
PROMPT=$(python .claude/hooks/pre_delegate.py "npm ls" "Investigating build slowdown")
gemini --model {model} -p "$PROMPT"

# Validate response
python .claude/hooks/post_delegate.py "$RESPONSE" 10 "dependency-analysis"
```

## Windows Users

Use the batch or PowerShell wrappers:

```powershell
# PowerShell
$prompt = & .claude/hooks/delegate.ps1 "npm ls" "Build investigation"
$prompt | python .claude/hooks/gemini_delegate.py

# Command Prompt
FOR /F "delims=" %i IN ('.claude\\hooks\\delegate.bat "npm ls" "Build investigation"') DO SET PROMPT=%i
echo %PROMPT% | python .claude\\hooks\\gemini_delegate.py
```

## Core Principles

- **KISS**: Keep it simple
- **Token efficiency**: Every token counts
- **Hook-driven automation**: Use local scripts, not Claude subagents

## Subagent Policy

Do **not** use Claude subagents for delegation work. Subagents spend Claude
tokens and defeat this configuration's token-saving purpose.

When a task needs broad search, documentation lookup, security review,
large-output command distillation, or multi-file analysis, use these hooks and
Gemini CLI. For research, documentation, and web-search tasks, run
`gemini_delegate.py --profile research` so Gemini Pro is tried before Flash.
Only use Claude subagents when the user explicitly asks for Claude subagents by
name.
""".format(model=DEFAULT_GEMINI_MODEL)

    managed_section = MARKER_BEGIN + "\n" + managed_body.strip() + "\n" + MARKER_END + "\n"

    if claude_md.exists():
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = claude_md.with_name("global_claude_md_backup_" + timestamp + ".md")
        shutil.copy2(claude_md, backup_path)
        print("[OK] Backed up existing CLAUDE.md to " + backup_path.name)

        existing = claude_md.read_text(encoding="utf-8")
        if MARKER_BEGIN in existing and MARKER_END in existing:
            before = existing[:existing.index(MARKER_BEGIN)]
            after = existing[existing.index(MARKER_END) + len(MARKER_END):]
            new_content = before + managed_section + after
            print("[OK] Updated existing delegation section in CLAUDE.md")
        else:
            new_content = existing.rstrip("\n") + "\n\n" + managed_section
            print("[OK] Appended delegation section to existing CLAUDE.md")
    else:
        new_content = managed_section
        print("[OK] Created CLAUDE.md")

    claude_md.write_text(new_content, encoding="utf-8")


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

Use delegation for token-heavy or broad read-only work:
- Commands expected to produce more than 500 lines of output
- `npm ls`, `pip list`, `pip freeze`, and verbose dependency listings
- `git log` beyond 5 commits or broad git history analysis
- Recursive searches such as `find`, `grep -r`, or repository-wide scans
- Reading or analyzing 3 or more new files
- Security audits, vulnerability scans, XSS/SQL injection/CSRF checks
- Documentation lookup or web search
- Broad codebase analysis, performance review, or inspection tasks

Claude Code wrapper:
```powershell
$prompt = & .claude/hooks/delegate.ps1 "analyze @src/ for performance issues" "Optimization task"
$prompt | python .claude/hooks/gemini_delegate.py
```

Codex wrapper:
```powershell
$prompt = & .Codex/hooks/delegate.ps1 "analyze @src/ for performance issues" "Optimization task"
$prompt | python .Codex/hooks/gemini_delegate.py
```

For documentation lookup or web search, add `--profile research` when piping
to `gemini_delegate.py`.

Keep project-specific agent instructions outside this managed section. Rerunning
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


def ensure_agents_md(project_dir, migrated_claude_content=""):
    """Ensure AGENTS.md exists without overwriting project-specific instructions."""
    agents_md = project_dir / "AGENTS.md"
    managed_section = build_agents_section()
    migrated_section = build_migrated_claude_section(migrated_claude_content)

    if agents_md.exists():
        raw_existing = agents_md.read_text(encoding="utf-8")
        existing = normalize_agents_content(raw_existing)
        working = existing

        if migrated_section and MIGRATED_CLAUDE_MARKER_BEGIN not in working:
            working = working.rstrip("\n") + "\n\n" + migrated_section

        if AGENTS_MARKER_BEGIN in existing and AGENTS_MARKER_END in existing:
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
python pre_delegate.py "npm ls" "Debugging build" 8

# Windows (same command)
python pre_delegate.py "npm ls" "Debugging build" 8
```

### Post-delegation (validate responses)

```bash
python post_delegate.py "Response text..." 10 "task-name"
```

### Analyze metrics

```bash
python analyze_metrics.py
python analyze_metrics.py --days 14  # Last 14 days
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
        print('   python pre_delegate.py "npm ls" "Test" 5')
    else:
        print("   cd " + str(claude_dir / "hooks"))
        print('./delegate "npm ls" "Test" 5')

    print("\n3. Update your workflow:")
    print("   Project installs update AGENTS.md and CLAUDE.md automatically")

    print("\n4. Run a test delegation:")
    test_cmd = 'python .claude/hooks/pre_delegate.py "git status" "Test delegation"'
    if platform.system() != 'Windows':
        test_cmd = './.claude/hooks/delegate "git status" "Test delegation"'

    if platform.system() == 'Windows':
        print("   $prompt = & " + test_cmd)
        print("   $prompt | python .claude\\hooks\\gemini_delegate.py")
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

    # Create documentation
    create_sample_claude_md(base_dir)
    if not is_user_install:
        codex_dir = base_dir.parent / ".Codex"
        create_directory_structure(codex_dir)
        create_wrapper_scripts(codex_dir / "hooks")
        copy_hook_files(codex_dir / "hooks")
        create_readme(codex_dir / "hooks")
        create_gitignore(codex_dir)
        root_claude_md = base_dir.parent / "CLAUDE.md"
        root_claude_existing = root_claude_md.read_text(encoding="utf-8") if root_claude_md.exists() else ""
        ensure_agents_md(base_dir.parent, extract_migrated_claude_content(root_claude_existing))
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
