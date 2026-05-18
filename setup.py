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
from pathlib import Path
from typing import Dict
import stat

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
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


def copy_hook_files(dest_dir: Path):
    """Copy hook scripts to destination directory."""
    print_header("Installing Hook Scripts")
    
    hooks_to_copy = [
        "pre_delegate.py",
        "post_delegate.py",
        "analyze_metrics.py",
        "gemini_delegate.py",
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
python "%~dp0pre_delegate.py" %*
""", encoding="utf-8")
    print_success("Created Windows wrapper: delegate.bat")
    
    # PowerShell wrapper
    wrapper_ps1 = hooks_dir / "delegate.ps1"
    wrapper_ps1.write_text("""# Delegation wrapper script
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
python "$ScriptDir/pre_delegate.py" $args
""", encoding="utf-8")
    print_success("Created PowerShell wrapper: delegate.ps1")


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
    """Create enhanced CLAUDE.md with routing presets."""
    claude_md = base_dir / "CLAUDE.md"
    
    enabled_clis = config.get("cli_configs", {})
    
    if not enabled_clis:
        content = """# Claude Code Configuration

## Delegation Status

No external CLIs configured. Run `python setup.py` to enable delegation.
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
        
        content = f"""# Claude Code Configuration

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
$prompt | python .claude/hooks/gemini_delegate.py
```

**Windows (CMD):**
```cmd
FOR /F "delims=" %i IN ('.claude\\hooks\\delegate.bat "npm ls" "Build analysis"') DO SET PROMPT=%i
echo %PROMPT% | python .claude\\hooks\\gemini_delegate.py
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
$prompt | python .claude/hooks/gemini_delegate.py --profile research
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
    
    claude_md.write_text(content, encoding="utf-8")
    print_success(f"Created enhanced CLAUDE.md")


def create_usage_examples(config: Dict, base_dir: Path):
    """Create example scripts showing delegation usage."""
    print_header("Creating Usage Examples")
    
    examples_dir = base_dir / "examples"
    examples_dir.mkdir(exist_ok=True)
    
    # Basic example
    basic_example = examples_dir / "basic_delegation.sh"
    basic_example.write_text(f"""#!/bin/bash
# Basic delegation example

# 1. Generate optimized prompt
PROMPT=$(python ../.claude/hooks/pre_delegate.py \
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
# python ../.claude/hooks/post_delegate.py "$RESPONSE" 10 "build-analysis"
""", encoding="utf-8")
    
    if os.name != 'nt':
        basic_example.chmod(basic_example.stat().st_mode | stat.S_IXUSR)
    
    print_success("Created basic_delegation.sh")
    
    # Advanced example
    advanced_example = examples_dir / "security_audit.sh"
    advanced_example.write_text(f"""#!/bin/bash
# Security audit example - delegates to Gemini

# Run security scan on source files
PROMPT=$(python ../.claude/hooks/pre_delegate.py \
  "grep -r 'password' src/ && grep -r 'api.*key' src/" \
  "Pre-deployment security audit" \
  8)

echo "Running security audit..."
RESPONSE=$(gemini --model {DEFAULT_GEMINI_MODEL} -p "$PROMPT")

# Validate and save results
python ../.claude/hooks/post_delegate.py "$RESPONSE" 8 "security-audit"

echo "$RESPONSE" > security-audit-results.txt
echo "Results saved to security-audit-results.txt"
""", encoding="utf-8")
    
    if os.name != 'nt':
        advanced_example.chmod(advanced_example.stat().st_mode | stat.S_IXUSR)
    
    print_success("Created security_audit.sh")


def verify_installation(base_dir: Path, config: Dict) -> bool:
    """Verify that installation was successful."""
    print_header("Verifying Installation")
    
    checks = [
        (base_dir / "hooks" / "pre_delegate.py", "Pre-delegation hook"),
        (base_dir / "hooks" / "post_delegate.py", "Post-delegation hook"),
        (base_dir / "hooks" / "analyze_metrics.py", "Metrics analyzer"),
        (base_dir / "hooks" / "gemini_delegate.py", "Gemini fallback runner"),
        (base_dir / "delegation_config.json", "Configuration file"),
        (base_dir / "CLAUDE.md", "Claude configuration"),
    ]
    
    all_passed = True
    for path, description in checks:
        if path.exists():
            print_success(f"{description}: {path}")
        else:
            print_error(f"{description} missing: {path}")
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
        print("   python .claude\\hooks\\pre_delegate.py \"test task\" \"test context\"")
    else:
        print("   python3 .claude/hooks/pre_delegate.py \"test task\" \"test context\"")
    
    if enabled_clis:
        print(f"\n{Colors.BOLD}2. Try a delegation:{Colors.END}")
        first_cli = list(enabled_clis.keys())[0]
        first_cli_cmd = enabled_clis[first_cli]["command"]
        if os.name == "nt":
            print("   $prompt = & .claude\\hooks\\delegate.ps1 \"npm ls\" \"Test\"")
            if first_cli == "gemini":
                print("   $prompt | python .claude\\hooks\\gemini_delegate.py")
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
    print("   Check .claude/examples/ for usage examples")
    
    print(f"\n{Colors.BOLD}5. Monitor metrics:{Colors.END}")
    if os.name == "nt":
        print("   python .claude\\hooks\\analyze_metrics.py")
    else:
        print("   python3 .claude/hooks/analyze_metrics.py")


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
    args = parser.parse_args()
    
    print_header("Claude-Gemini Delegation Enhanced Setup")
    
    # Check Python version
    if not check_python_version():
        sys.exit(1)
    
    # Determine installation location
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
    
    # Generate config
    config = generate_delegation_config(configured)
    save_config(config, base_dir)
    
    # Build routing presets
    presets = build_routing_presets(config)
    
    # Create enhanced CLAUDE.md
    create_enhanced_claude_md(config, presets, base_dir)
    
    # Create usage examples
    create_usage_examples(config, base_dir)
    
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
