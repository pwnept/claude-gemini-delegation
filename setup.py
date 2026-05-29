#!/usr/bin/env python3
"""
Enhanced installer for Claude-Gemini Delegation
- Auto-detects installed CLIs
- Interactive CLI selection
- Copies hook files automatically
- Generates custom routing rules
- Creates wrapper scripts

Usage:
    python install-enhanced.py
"""

import os
import sys
import json
import shutil
import argparse
import datetime
from pathlib import Path
from typing import Dict
import stat

MARKER_BEGIN = "> [claude-gemini-delegation:begin]"
MARKER_END = "> [claude-gemini-delegation:end]"

# Import the basic installer classes
from pathlib import Path

class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    END = '\033[0m'
    BOLD = '\033[1m'

def print_header(text: str):
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'=' * 60}")
    print(f"  {text}")
    print(f"{'=' * 60}{Colors.END}\n")

def print_success(text: str):
    print(f"{Colors.GREEN}[SUCCESS] {text}{Colors.END}")

def print_error(text: str):
    print(f"{Colors.RED}[ERROR] {text}{Colors.END}")

def print_warning(text: str):
    print(f"{Colors.YELLOW}[WARNING] {text}{Colors.END}")

def print_info(text: str):
    print(f"{Colors.BLUE}[INFO] {text}{Colors.END}")

# Assume hook templates are in same directory as installer
SCRIPT_DIR = Path(__file__).parent
HOOKS_SOURCE = SCRIPT_DIR / "hooks"
DEFAULT_GEMINI_MODEL = "gemini-3-flash-preview"
ROOT_CLAUDE_IMPORTS = ("@AGENTS.md",)


def copy_hook_files(dest_dir: Path):
    """Copy hook scripts to destination directory."""
    print_header("Installing Hook Scripts")
    
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
        dest = dest_dir / hook_file
        
        if not source.exists():
            print_warning(f"{hook_file} not found in source, skipping")
            continue
        
        shutil.copy2(source, dest)
        
        # Make executable on Unix
        if os.name != 'nt':
            dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        
        print_success(f"Installed {hook_file}")
        copied_count += 1
    
    if copied_count == 0:
        print_error("No hook files found to copy")
        print_info("Make sure hook scripts are in the 'hooks/' directory")
        return False
    
    return True


def create_wrapper_scripts(hooks_dir: Path, platform: str = None):
    """Create platform-specific wrapper scripts."""
    if platform is None:
        platform = "windows" if os.name == 'nt' else "unix"
    
    print_header("Creating Wrapper Scripts")
    
    # Bash wrapper. Create it on every platform so projects copied between
    # Windows, WSL, macOS, and Linux keep a complete hook set.
    wrapper = hooks_dir / "delegate"
    wrapper.write_text("""#!/bin/bash
# Delegation wrapper script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/pre_delegate.py" "$@"
""", encoding="utf-8")
    if platform == "unix":
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print_success("Created Unix wrapper: delegate")
    
    # Windows batch
    wrapper_bat = hooks_dir / "delegate.bat"
    wrapper_bat.write_text("""@echo off
REM Delegation wrapper script
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
    print_success("Created Windows wrapper: delegate.bat")
    
    # PowerShell wrapper
    wrapper_ps1 = hooks_dir / "delegate.ps1"
    wrapper_ps1.write_text("""# Delegation wrapper script
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
    print_success("Created PowerShell wrapper: delegate.ps1")


def create_claude_settings(base_dir: Path):
    """Merge the delegation guard PreToolUse hook into .claude/settings.json."""
    settings_path = base_dir / "settings.json"
    command = (
        "powershell -NoProfile -ExecutionPolicy Bypass -File .claude/hooks/delegation_guard.ps1"
        if os.name == "nt"
        else "python3 .claude/hooks/delegation_guard.py"
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

    def is_delegation_guard_hook(hook: Dict) -> bool:
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
        print_success("CLAUDE settings already include delegation guard")
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
        backup_path = settings_path.with_name(f"settings.json.bak.{timestamp}")
        shutil.copy2(settings_path, backup_path)
        print_info(f"Backed up existing settings.json to {backup_path.name}")

    bash_entry["hooks"].append(guard_entry["hooks"][0])
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print_success("Updated .claude/settings.json delegation guard")


def build_routing_presets(config: Dict) -> Dict:
    """Build smart routing presets based on enabled CLIs."""
    presets = {}
    
    cli_configs = config.get("cli_configs", {})
    
    # Security tasks
    if "gemini" in cli_configs:
        presets["security_audit"] = {
            "cli": "gemini",
            "pattern": "(security|vulnerability|audit|xss|sql injection|csrf)",
            "template": "shell"
        }
    
    # Git operations
    if "aider" in cli_configs:
        presets["git_operations"] = {
            "cli": "aider",
            "pattern": "(git|commit|branch|merge|rebase)",
            "template": "shell"
        }
    
    # Web search
    if "gemini" in cli_configs:
        presets["web_search"] = {
            "cli": "gemini",
            "pattern": "(search|documentation|lookup|find.*docs)",
            "template": "docs"
        }
    
    # Code analysis
    if "gemini" in cli_configs:
        presets["code_analysis"] = {
            "cli": "gemini",
            "pattern": "(analyze|review|inspect).*code",
            "template": "analyze"
        }
    
    return presets


def create_enhanced_claude_md(config: Dict, presets: Dict, base_dir: Path):
    """Create or update CLAUDE.md with routing presets.

    Uses begin/end markers so the managed delegation section can be updated
    in-place without overwriting surrounding content added by the user.
    A timestamped backup is created whenever an existing file is modified.
    """
    claude_md = base_dir / "CLAUDE.md"

    enabled_clis = config.get("cli_configs", {})

    if not enabled_clis:
        managed_body = """# Claude Code Configuration

## Delegation Status

No external CLIs configured. Run `python3 setup.py` (or `py -3 setup.py` on Windows) to enable delegation.
"""
    else:
        cli_list = "\n".join([
            f"- **{v['name']}**: {v['description']}"
            for v in enabled_clis.values()
        ])

        preset_list = "\n".join([
            f"- **{name}**: Routes to `{p['cli']}`\n  Pattern: `{p['pattern']}`"
            for name, p in presets.items()
        ])

        git_operations_example = ""
        if "git_operations" in presets:
            git_operations_example = """
### Git Operations
```bash
# Auto-routes to Aider (if enabled)
PROMPT=$(./.claude/hooks/delegate "git log --oneline --since=1.week" "Finding bug introduction")
aider -p "$PROMPT"
```
"""

        managed_body = f"""# Claude Code Configuration

## Enabled Delegation CLIs

{cli_list}

## Delegation Presets

{preset_list}

## Quick Delegation

Use the wrapper scripts for easy delegation:

**Unix/Mac:**
```bash
PROMPT=$(./.claude/hooks/delegate "npm ls" "Build analysis")
gemini --model {DEFAULT_GEMINI_MODEL} -p "$PROMPT"
```

**Windows (PowerShell):**
```powershell
$prompt = & .claude/hooks/delegate.ps1 "npm ls" "Build analysis"
$prompt | py -3 .claude/hooks/gemini_delegate.py

# Or run the full prompt -> Gemini -> validation/metrics pipeline:
.claude/hooks/delegate_and_log.ps1 "npm ls" "Build analysis" 5
```

**Windows (CMD):**
```cmd
FOR /F "delims=" %i IN ('.claude\\hooks\\delegate.bat "npm ls" "Build analysis"') DO SET PROMPT=%i
echo %PROMPT% | py -3 .claude\\hooks\\gemini_delegate.py
```

## Subagent Policy

Do **not** use Claude subagents for delegation work. Subagents spend Claude
tokens and defeat this configuration's token-saving purpose.

When a task matches any delegation preset, banned operation, or large-output
condition, use the local hooks and Gemini CLI instead of spawning a Claude
subagent. Only use Claude subagents when the user explicitly asks for Claude
subagents by name.

## Always Delegate To Gemini

- Commands expected to produce more than 500 lines of output
- `npm ls`, `pip list`, `pip freeze`, and verbose dependency listings
- `git log` beyond 5 commits or broad git history analysis
- Recursive searches such as `find`, `grep -r`, or repository-wide scans
- Reading or analyzing 3 or more new files
- Security audits, vulnerability scans, XSS/SQL injection/CSRF checks
- Documentation lookup or web search. Use `gemini_delegate.py --profile research` so Gemini Pro is tried before Flash.
- Broad codebase analysis, performance review, or inspection tasks

## Delegation Workflow

1. **Identify task type** - Security? Git ops? Analysis?
2. **Check presets** - Is there a matching preset?
3. **Use delegation hook** - Let the hook format the prompt
4. **Execute with Gemini CLI** - Use `gemini_delegate.py`, not Claude subagents
5. **Validate response** - Check quality with the post-delegation hook

`.claude/settings.json` registers a PreToolUse Bash guard that blocks known
high-output commands and returns delegation instructions. The guard is a
backstop; still delegate proactively when the task matches these rules.

## Routing Examples

### Security Audit
```bash
# Auto-routes to Gemini (if enabled)
PROMPT=$(./.claude/hooks/delegate "scan auth.py for vulnerabilities" "Pre-deploy security check")
gemini --model {DEFAULT_GEMINI_MODEL} -p "$PROMPT"
```

{git_operations_example}
### Code Analysis
```bash
# Routes based on configured preference
PROMPT=$(./.claude/hooks/delegate "analyze @src/ for performance issues" "Optimization task")
gemini --model {DEFAULT_GEMINI_MODEL} -p "$PROMPT"
```

### Research / Documentation / Web Search
```powershell
$prompt = & .claude/hooks/delegate.ps1 "find current docs for deployment limits" "Research task"
$prompt | py -3 .claude/hooks/gemini_delegate.py --profile research

# Or:
.claude/hooks/delegate_and_log.ps1 "find current docs for deployment limits" "Research task" 10 -Profile research
```

## Weekly Maintenance

```bash
# Analyze delegation metrics
python3 .claude/hooks/analyze_metrics.py

# Review routing effectiveness
# Update presets if needed
```

## Configuration

To reconfigure delegation preferences, run the setup wizard again manually.

This will let you enable/disable CLIs.
"""

    managed_section = f"{MARKER_BEGIN}\n{managed_body.strip()}\n{MARKER_END}\n"

    if claude_md.exists():
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = claude_md.with_name(f"CLAUDE.md.bak.{timestamp}")
        shutil.copy2(claude_md, backup_path)
        print_info(f"Backed up existing CLAUDE.md to {backup_path.name}")

        existing = claude_md.read_text(encoding="utf-8")
        if MARKER_BEGIN in existing and MARKER_END in existing:
            before = existing[:existing.index(MARKER_BEGIN)]
            after = existing[existing.index(MARKER_END) + len(MARKER_END):].lstrip("\n")
            new_content = before + managed_section + after
            print_info("Updated existing delegation section in CLAUDE.md")
        else:
            new_content = existing.rstrip("\n") + "\n\n" + managed_section
            print_info("Appended delegation section to existing CLAUDE.md")
    else:
        new_content = managed_section

    claude_md.write_text(new_content, encoding="utf-8")
    print_success("Updated CLAUDE.md")


def _build_claude_md_body(config: Dict, presets: Dict) -> str:
    """Return the managed delegation body for AGENTS.md, built from live config."""
    enabled_clis = config.get("cli_configs", {})

    if not enabled_clis:
        return (
            "## Gemini Delegation\n\n"
            "No external CLIs configured. Run `python3 setup.py` to enable delegation."
        )

    cli_list = "\n".join(
        f"- **{v['name']}**: {v['description']}" for v in enabled_clis.values()
    )
    preset_list = "\n".join(
        f"- **{name}**: Routes to `{p['cli']}`\n  Pattern: `{p['pattern']}`"
        for name, p in presets.items()
    )

    git_ops_example = ""
    if "git_operations" in presets:
        git_ops_example = (
            "\n### Git Operations\n"
            "```bash\n"
            'PROMPT=$(./.claude/hooks/delegate "git log --oneline --since=1.week" "Finding bug introduction")\n'
            'aider -p "$PROMPT"\n'
            "```\n"
        )

    return f"""## Gemini Delegation

Gemini delegation is installed locally in `.claude/hooks` and `.Codex/hooks`.

### Enabled CLIs

{cli_list}

### Delegation Presets

{preset_list}

### Always Delegate

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
.claude/hooks/delegate_and_log.ps1 "analyze @src/ for performance issues" "Optimization task" 10
.claude/hooks/delegate_and_log.ps1 "find current docs for X" "Research task" 10 -Profile research
```

**Unix/Mac:**
```bash
PROMPT=$(./.claude/hooks/delegate "npm ls" "Build analysis")
gemini --model {DEFAULT_GEMINI_MODEL} -p "$PROMPT"
```
{git_ops_example}
`.claude/settings.json` registers a PreToolUse Bash guard that blocks known
high-output commands and returns delegation instructions. Keep project-specific
agent instructions outside this managed section. Re-running setup updates only
this block."""


def create_usage_examples(config: Dict, base_dir: Path):
    """Create example scripts showing delegation usage."""
    hook_root = base_dir.name
    print_header(f"Creating Usage Examples in {hook_root}")
    
    examples_dir = base_dir / "examples"
    examples_dir.mkdir(exist_ok=True)
    
    # Basic example
    basic_example = examples_dir / "basic_delegation.sh"
    basic_example.write_text(f"""#!/bin/bash
# Basic delegation example

# 1. Generate optimized prompt
PROMPT=$(python3 ../{hook_root}/hooks/pre_delegate.py \
  "npm ls --depth=0" \
  "Investigating build performance")

echo "Generated prompt:"
echo "$PROMPT"
echo ""

# 2. Execute with Gemini (or other CLI)
echo "Executing with Gemini..."
# gemini --model {DEFAULT_GEMINI_MODEL} -p "$PROMPT"

# 3. Validate response (after execution)
# RESPONSE=$(gemini --model {DEFAULT_GEMINI_MODEL} -p "$PROMPT")
# python3 ../{hook_root}/hooks/post_delegate.py "$RESPONSE" 10 "build-analysis"
""", encoding="utf-8")
    
    if os.name != 'nt':
        basic_example.chmod(basic_example.stat().st_mode | stat.S_IXUSR)
    
    print_success("Created basic_delegation.sh")
    
    # Advanced example
    advanced_example = examples_dir / "security_audit.sh"
    advanced_example.write_text(f"""#!/bin/bash
# Security audit example - delegates to Gemini

# Run security scan on source files
PROMPT=$(python3 ../{hook_root}/hooks/pre_delegate.py \
  "grep -r 'password' src/ && grep -r 'api.*key' src/" \
  "Pre-deployment security audit" \
  8)

echo "Running security audit..."
RESPONSE=$(gemini --model {DEFAULT_GEMINI_MODEL} -p "$PROMPT")

# Validate and save results
python3 ../{hook_root}/hooks/post_delegate.py "$RESPONSE" 8 "security-audit"

echo "$RESPONSE" > security-audit-results.txt
echo "Results saved to security-audit-results.txt"
""", encoding="utf-8")
    
    if os.name != 'nt':
        advanced_example.chmod(advanced_example.stat().st_mode | stat.S_IXUSR)
    
    print_success("Created security_audit.sh")


def verify_installation(base_dir: Path, config: Dict) -> bool:
    """Verify that installation was successful."""
    print_header("Verifying Installation")
    codex_dir = base_dir.parent / ".Codex"
    agents_md = base_dir.parent / "AGENTS.md"
    root_claude_md = base_dir.parent / "CLAUDE.md"
    
    checks = [
        (base_dir / "hooks" / "pre_delegate.py", "Pre-delegation hook"),
        (base_dir / "hooks" / "post_delegate.py", "Post-delegation hook"),
        (base_dir / "hooks" / "analyze_metrics.py", "Metrics analyzer"),
        (base_dir / "hooks" / "gemini_delegate.py", "Gemini fallback runner"),
        (base_dir / "hooks" / "delegation_guard.py", "Delegation guard"),
        (base_dir / "hooks" / "delegation_guard.ps1", "Delegation guard PowerShell launcher"),
        (base_dir / "hooks" / "delegate_and_log.ps1", "Full PowerShell delegation pipeline"),
        (base_dir / "delegation_config.json", "Configuration file"),
        (base_dir / "CLAUDE.md", "Claude configuration"),
        (base_dir / "settings.json", "Claude settings delegation guard"),
        (codex_dir / "hooks" / "pre_delegate.py", "Codex pre-delegation hook"),
        (codex_dir / "hooks" / "post_delegate.py", "Codex post-delegation hook"),
        (codex_dir / "hooks" / "analyze_metrics.py", "Codex metrics analyzer"),
        (codex_dir / "hooks" / "gemini_delegate.py", "Codex Gemini fallback runner"),
        (codex_dir / "hooks" / "delegation_guard.py", "Codex delegation guard"),
        (codex_dir / "hooks" / "delegation_guard.ps1", "Codex delegation guard PowerShell launcher"),
        (codex_dir / "hooks" / "delegate_and_log.ps1", "Codex full PowerShell delegation pipeline"),
        (codex_dir / "delegation_config.json", "Codex configuration file"),
        (agents_md, "AGENTS.md delegation instructions"),
        (root_claude_md, "Root CLAUDE.md bridge"),
    ]
    
    all_passed = True
    for path, description in checks:
        if path.exists():
            print_success(f"{description}: {path}")
        else:
            print_error(f"{description} missing: {path}")
            all_passed = False

    if root_claude_md.exists():
        root_lines = root_claude_md.read_text(encoding="utf-8").splitlines()
        if root_lines == list(ROOT_CLAUDE_IMPORTS):
            print_success("Root CLAUDE.md content: @AGENTS.md")
        else:
            print_error("Root CLAUDE.md is not exactly @AGENTS.md")
            all_passed = False
    
    # Check if at least one CLI is enabled
    enabled_clis = [k for k, v in config.get("cli_configs", {}).items()]
    if enabled_clis:
        print_success(f"Enabled CLIs: {', '.join(enabled_clis)}")
    else:
        print_warning("No CLIs enabled - delegation will be limited")
    
    return all_passed


def show_next_steps(config: Dict):
    """Show user what to do next."""
    print_header("Next Steps")
    
    enabled_clis = config.get("cli_configs", {})
    
    print(f"{Colors.BOLD}1. Test the hooks:{Colors.END}")
    if os.name == "nt":
        print("   py -3 .claude\\hooks\\pre_delegate.py \"test task\" \"test context\"")
    else:
        print("   python3 .claude/hooks/pre_delegate.py \"test task\" \"test context\"")
    
    if enabled_clis:
        print(f"\n{Colors.BOLD}2. Try a delegation:{Colors.END}")
        first_cli = list(enabled_clis.keys())[0]
        first_cli_cmd = enabled_clis[first_cli]["command"]
        if os.name == "nt":
            print("   $prompt = & .claude\\hooks\\delegate.ps1 \"npm ls\" \"Test\"")
            if first_cli == "gemini":
                print("   $prompt | py -3 .claude\\hooks\\gemini_delegate.py")
                print("   .claude\\hooks\\delegate_and_log.ps1 \"npm ls\" \"Test\" 5")
            else:
                print(f"   # Send $prompt to {first_cli_cmd} using that CLI's prompt option")
        else:
            print(f"   PROMPT=$(./.claude/hooks/delegate \"npm ls\" \"Test\")")
            if first_cli == "gemini":
                print(f"   {first_cli_cmd} --model {DEFAULT_GEMINI_MODEL} -p \"$PROMPT\"")
            else:
                print(f"   # Send \"$PROMPT\" to {first_cli_cmd} using that CLI's prompt option")
    
    print(f"\n{Colors.BOLD}3. Restart Claude Code{Colors.END}")
    print("   Your delegation configuration will be active")
    
    print(f"\n{Colors.BOLD}4. Review examples:{Colors.END}")
    print("   Check .claude/examples/ and .Codex/examples/ for usage examples")
    
    print(f"\n{Colors.BOLD}5. Monitor metrics:{Colors.END}")
    if os.name == "nt":
        print("   py -3 .claude\\hooks\\analyze_metrics.py")
        print("   py -3 .Codex\\hooks\\analyze_metrics.py")
    else:
        print("   python3 .claude/hooks/analyze_metrics.py")
        print("   python3 .Codex/hooks/analyze_metrics.py")


def main():
    """Enhanced installation flow."""
    # Import from basic installer
    from install import (
        check_python_version,
        discover_clis,
        interactive_selection,
        setup_hooks,
        generate_delegation_config,
        save_config,
        extract_migrated_claude_content,
        ensure_agents_md,
        ensure_root_claude_bridge,
        ensure_dot_claude_bridge,
        install_not_found_clis
    )

    parser = argparse.ArgumentParser(
        description="Install Claude-Gemini delegation hooks and routing rules."
    )
    parser.add_argument(
        "--enable-cli",
        action="append",
        choices=("gemini", "aider", "copilot", "gpt-me"),
        dest="enabled_clis",
        help="Enable an additional delegation CLI. Repeat for multiple CLIs. Default: gemini only.",
    )
    parser.add_argument(
        "--all-clis",
        action="store_true",
        help="Enable every supported CLI that is installed.",
    )
    parser.add_argument(
        "--target",
        type=str,
        default=None,
        help="Target directory for installation (default: current working directory)",
    )
    args = parser.parse_args()
    
    print_header("Claude-Gemini Delegation Enhanced Setup")
    
    # Check Python version
    if not check_python_version():
        sys.exit(1)
    
    # Determine installation location
    if args.target:
        base_dir = Path(args.target) / ".claude"
    else:
        base_dir = Path.cwd() / ".claude"
    print_info(f"Installing to: {base_dir.absolute()}")
    
    # Discover CLIs
    discovered = discover_clis()
    
    # CLI selection. Default is Gemini only; other CLIs require explicit flags.
    enabled_cli_names = ["gemini"] + (args.enabled_clis or [])
    configured = interactive_selection(
        discovered,
        enabled_cli_names=enabled_cli_names,
        enable_all=args.all_clis,
    )
    
    # Setup hooks directory
    setup_hooks(base_dir)
    
    # Copy hook files
    hooks_dir = base_dir / "hooks"
    if HOOKS_SOURCE.exists():
        if not copy_hook_files(hooks_dir):
            print_error("Hook installation failed - check that hooks/ directory exists")
            sys.exit(1)
    else:
        print_warning(f"Hooks source directory not found: {HOOKS_SOURCE}")
        print_info("Hook files should be in: hooks/")
    
    # Create wrappers
    create_wrapper_scripts(hooks_dir)
    create_claude_settings(base_dir)
    
    # Generate config
    config = generate_delegation_config(configured)
    save_config(config, base_dir)
    
    # Build routing presets
    presets = build_routing_presets(config)
    
    # Make .claude/CLAUDE.md a bridge; migrate its content to AGENTS.md
    dot_claude_migrated = ensure_dot_claude_bridge(base_dir)
    root_claude_md = base_dir.parent / "CLAUDE.md"
    root_claude_existing = root_claude_md.read_text(encoding="utf-8") if root_claude_md.exists() else ""
    root_migrated = extract_migrated_claude_content(root_claude_existing)
    combined_migration = (
        root_migrated.rstrip("\n") + "\n\n" + dot_claude_migrated
        if dot_claude_migrated.strip() else root_migrated
    )
    ensure_agents_md(
        base_dir.parent,
        combined_migration,
        managed_body=_build_claude_md_body(config, presets),
    )
    ensure_root_claude_bridge(base_dir.parent)
    
    # Create usage examples
    create_usage_examples(config, base_dir)

    # Install a Codex hook mirror for workflows that expect .Codex/hooks.
    codex_dir = base_dir.parent / ".Codex"
    setup_hooks(codex_dir)
    if HOOKS_SOURCE.exists():
        if not copy_hook_files(codex_dir / "hooks"):
            print_error("Codex hook installation failed - check that hooks/ directory exists")
            sys.exit(1)
    create_wrapper_scripts(codex_dir / "hooks")
    save_config(config, codex_dir)
    create_usage_examples(config, codex_dir)
    
    # Verify installation
    if not verify_installation(base_dir, config):
        print_error("Installation verification failed")
        sys.exit(1)
    
    # Show next steps
    show_next_steps(config)
    
    # Offer to install missing CLIs
    install_not_found_clis(configured)
    
    print_header("Installation Complete!")
    print(f"\n{Colors.GREEN}{Colors.BOLD}Your delegation setup is ready!{Colors.END}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}Installation cancelled.{Colors.END}")
        sys.exit(0)
    except Exception as e:
        print_error(f"Installation failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
