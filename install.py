#!/usr/bin/env python3

import sys
import os
import shutil
import json
import subprocess
import datetime
from pathlib import Path

SUPPORTED_CLIS = ("gemini", "aider", "copilot", "gpt-me")
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

def check_python_version():
    """Ensure Python 3.6+ is installed."""
    if sys.version_info < (3, 6):
        print("\033[91m[ERROR] Python 3.6 or higher is required\033[0m")
        print(f"   Current version: {sys.version}")
        return False
    return True

def discover_clis():
    """Discover installed AI CLIs."""
    clis = {
        "gemini": {"name": "Gemini CLI", "command": "gemini", "description": "Google's Gemini models via CLI"},
        "aider": {"name": "Aider", "command": "aider", "description": "AI pair programming in the terminal"},
        "copilot": {"name": "GitHub Copilot CLI", "command": "gh copilot", "description": "GitHub Copilot extensions for gh"},
        "gpt-me": {"name": "gpt-me", "command": "gpt-me", "description": "A CLI to chat with LLMs and execute code"}
    }
    
    discovered = {}
    for key, info in clis.items():
        discovered[key] = dict(info)
        discovered[key]["installed"] = command_available(info["command"])
            
    return discovered


def command_available(command):
    """Return True when a CLI command is available without invoking model APIs."""
    parts = command.split()
    if not parts or not shutil.which(parts[0]):
        return False

    if len(parts) == 1:
        return True

    try:
        result = subprocess.run(
            parts + ["--help"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False

    return result.returncode == 0

def interactive_selection(discovered, enabled_cli_names=None, enable_all=False):
    """Select which CLIs to enable.

    By default, only Gemini is enabled. Additional CLIs are opt-in via setup.py
    flags so a machine with gh/aider installed does not silently change routing.
    """
    print("\n\033[1m--- CLI Selection ---\033[0m")
    selected = {}

    if enable_all:
        requested = set(SUPPORTED_CLIS)
    else:
        requested = set(enabled_cli_names or ("gemini",))

    for key, info in discovered.items():
        status = "\033[92m[Found]\033[0m" if info["installed"] else "\033[93m[Not Found]\033[0m"
        print(f"{status} {info['name']}: {info['description']}")
        if info["installed"] and key in requested:
            selected[key] = info

    missing_requested = sorted(
        key for key in requested
        if key in discovered and not discovered[key]["installed"]
    )
    for key in missing_requested:
        print(f"\033[93m[Skipped]\033[0m {discovered[key]['name']} requested but not found")

    if not selected:
        print("\033[91mNo supported CLIs found.\033[0m")
        # We'll still return an empty dict to allow the installer to continue
        
    return selected

def setup_hooks(base_dir: Path):
    """Setup directory structure."""
    dirs = [
        base_dir,
        base_dir / "hooks",
        base_dir / "metrics",
        base_dir / "logs",
        base_dir / "tasks",
        base_dir / "orchestrators"
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    return True

def generate_delegation_config(selected_clis):
    """Generate configuration based on selection."""
    return {
        "version": "1.0.0",
        "cli_configs": selected_clis,
        "preferences": {
            "default_cli": list(selected_clis.keys())[0] if selected_clis else "gemini",
            "max_tokens_per_task": 50000,
            "auto_delegate_min_lines": 500
        }
    }

def save_config(config, base_dir: Path):
    """Save config to file."""
    config_path = base_dir / "delegation_config.json"
    with open(config_path, 'w', encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    print(f"\033[92m[SUCCESS] Saved configuration to {config_path}\033[0m")

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


def ensure_root_claude_bridge(project_dir: Path):
    """Ensure project-level CLAUDE.md points only to AGENTS.md."""
    claude_md = project_dir / "CLAUDE.md"
    existing = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
    desired_content = build_root_claude_bridge(existing)

    if claude_md.exists():
        if existing == desired_content:
            print(f"\033[92m[SUCCESS] Root CLAUDE.md already includes delegation bridge\033[0m")
            return claude_md

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = claude_md.with_name(f"CLAUDE.md.bak.{timestamp}")
        shutil.copy2(claude_md, backup_path)
        print(f"\033[94m[INFO] Backed up existing root CLAUDE.md to {backup_path.name}\033[0m")

    claude_md.write_text(desired_content, encoding="utf-8")
    print(f"\033[92m[SUCCESS] Updated root CLAUDE.md delegation bridge\033[0m")
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
    return f"{AGENTS_MARKER_BEGIN}\n{body.strip()}\n{AGENTS_MARKER_END}\n"


def build_migrated_claude_section(content):
    """Return a managed section containing prior root CLAUDE.md content."""
    if not content.strip():
        return ""

    body = "## Migrated CLAUDE.md Instructions\n\n" + content.strip()
    return f"{MIGRATED_CLAUDE_MARKER_BEGIN}\n{body}\n{MIGRATED_CLAUDE_MARKER_END}\n"


def normalize_agents_content(existing):
    """Remove obsolete generated boilerplate while preserving user content."""
    normalized = existing.replace("\r\n", "\n").lstrip("\ufeff")
    if normalized.startswith(OLD_DEFAULT_AGENTS_TEXT):
        remainder = normalized[len(OLD_DEFAULT_AGENTS_TEXT):].lstrip("\n")
        if remainder:
            return "# Agent Instructions\n\n" + remainder
        return "# Agent Instructions\n"
    return normalized


def ensure_agents_md(project_dir: Path, migrated_claude_content=""):
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
            print(f"\033[92m[SUCCESS] AGENTS.md already includes delegation section\033[0m")
            return agents_md

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = agents_md.with_name(f"AGENTS.md.bak.{timestamp}")
        shutil.copy2(agents_md, backup_path)
        print(f"\033[94m[INFO] Backed up existing AGENTS.md to {backup_path.name}\033[0m")
    else:
        base_content = migrated_claude_content.strip() or "# Agent Instructions"
        new_content = base_content.rstrip("\n") + "\n\n" + managed_section

    agents_md.write_text(new_content, encoding="utf-8")
    print(f"\033[92m[SUCCESS] Updated AGENTS.md delegation section\033[0m")
    return agents_md


def install_not_found_clis(selected):
    """Offer help for missing CLIs."""
    # Logic to suggest installation commands
    pass
