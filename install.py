#!/usr/bin/env python3

import sys
import os
import shutil
import json
import subprocess
from pathlib import Path

SUPPORTED_CLIS = ("gemini", "aider", "copilot", "gpt-me")

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

def install_not_found_clis(selected):
    """Offer help for missing CLIs."""
    # Logic to suggest installation commands
    pass
