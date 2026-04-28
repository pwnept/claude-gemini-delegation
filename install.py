#!/usr/bin/env python3

import sys
import os
import shutil
import json
from pathlib import Path

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
        cmd = info["command"].split()[0]
        if shutil.which(cmd):
            discovered[key] = info
            discovered[key]["installed"] = True
        else:
            discovered[key] = info
            discovered[key]["installed"] = False
            
    return discovered

def interactive_selection(discovered):
    """Interactively select which CLIs to enable."""
    print("\n\033[1m--- CLI Selection ---\033[0m")
    selected = {}
    
    # In a non-interactive environment or for simplicity, 
    # we'll enable all that are installed by default
    # But for a real installer, we'd prompt.
    # Since we're fixing a loop, let's just use what's found.
    
    for key, info in discovered.items():
        status = "\033[92m[Found]\033[0m" if info["installed"] else "\033[93m[Not Found]\033[0m"
        print(f"{status} {info['name']}: {info['description']}")
        if info["installed"]:
            selected[key] = info
            
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
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
    print(f"\033[92m[SUCCESS] Saved configuration to {config_path}\033[0m")

def install_not_found_clis(selected):
    """Offer help for missing CLIs."""
    # Logic to suggest installation commands
    pass
