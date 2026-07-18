from __future__ import annotations

import json
import os
import shlex
from pathlib import Path


DEFAULT_POLICY = {
    "schema": 1,
    "description": "Managed read-only terminal capabilities for depth-1 delegates.",
    "agy_print_mode_enabled": False,
    "command_prefixes": [
        ["rg"],
        ["fd"],
        ["git", "status"],
        ["git", "--no-pager", "diff", "--no-ext-diff", "--no-textconv"],
        ["git", "--no-pager", "log", "--no-ext-diff", "--no-textconv", "--no-show-signature"],
        ["git", "--no-pager", "show", "--no-ext-diff", "--no-textconv", "--no-show-signature"],
        ["git", "rev-parse"],
        ["git", "ls-files"],
        ["Get-ChildItem"],
        ["Get-Content"],
        ["Select-String"],
        ["Test-Path"],
        ["Resolve-Path"],
        ["Get-Command"],
        ["where.exe"],
    ],
    "permanent_denials": [
        "agent-delegation",
        "agent-delegate",
        "gemini-delegate",
        "pwsh",
        "powershell",
        "powershell.exe",
        "cmd",
        "cmd.exe",
        "bash",
        "sh",
        "wsl",
        "rm",
        "rmdir",
        "del",
        "erase",
        "Remove-Item",
        "Move-Item",
        "Set-Content",
        "Add-Content",
        "Out-File",
        "Invoke-Expression",
        "Start-Process",
    ],
    "permanent_denied_prefixes": [
        ["git", "add"],
        ["git", "apply"],
        ["git", "branch"],
        ["git", "checkout"],
        ["git", "clean"],
        ["git", "commit"],
        ["git", "fetch"],
        ["git", "merge"],
        ["git", "mv"],
        ["git", "pull"],
        ["git", "push"],
        ["git", "rebase"],
        ["git", "reset"],
        ["git", "restore"],
        ["git", "rm"],
        ["git", "stash"],
        ["git", "switch"],
        ["git", "tag"],
        ["git", "diff"],
        ["git", "grep"],
        ["git", "log"],
        ["git", "show"],
    ],
}


def global_home() -> Path:
    configured = os.environ.get("AGENT_DELEGATION_HOME")
    if configured:
        return Path(os.path.expandvars(os.path.expanduser(configured))).resolve()
    return Path.home() / ".agent-delegation"


def ensure_global_home(*, force: bool = False) -> Path:
    home = global_home()
    (home / "runs").mkdir(parents=True, exist_ok=True)
    (home / "state").mkdir(parents=True, exist_ok=True)
    policy_path = home / "policy.json"
    if force or not policy_path.exists():
        policy_path.write_text(json.dumps(DEFAULT_POLICY, indent=2) + "\n", encoding="utf-8")
    local_path = home / "policy.local.json"
    if not local_path.exists():
        local_path.write_text(
            json.dumps({"schema": 1, "command_prefixes": []}, indent=2) + "\n",
            encoding="utf-8",
        )
    return home


def _read_policy(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def load_policy() -> dict:
    home = ensure_global_home()
    base = _read_policy(home / "policy.json") or DEFAULT_POLICY
    local = _read_policy(home / "policy.local.json")
    prefixes = list(base.get("command_prefixes", []))
    prefixes.extend(local.get("command_prefixes", []))
    return {
        "schema": 1,
        "command_prefixes": prefixes,
        "agy_print_mode_enabled": bool(
            local.get("agy_print_mode_enabled", base.get("agy_print_mode_enabled", False))
        ),
        "permanent_denials": list(DEFAULT_POLICY["permanent_denials"]),
        "permanent_denied_prefixes": list(DEFAULT_POLICY["permanent_denied_prefixes"]),
    }


def parse_prefix(value: str) -> list[str]:
    try:
        tokens = shlex.split(value, posix=False)
    except ValueError as exc:
        raise ValueError(f"Invalid command prefix {value!r}: {exc}") from exc
    tokens = [token.strip('"') for token in tokens if token.strip('"')]
    if not tokens:
        raise ValueError("Command prefix cannot be empty.")
    return tokens


def command_prefixes(policy: dict, extensions: list[str]) -> list[list[str]]:
    prefixes = []
    for raw in policy.get("command_prefixes", []):
        if isinstance(raw, list) and raw and all(isinstance(token, str) and token for token in raw):
            prefixes.append(raw)
    prefixes.extend(parse_prefix(value) for value in extensions)
    denied = {item.lower() for item in policy.get("permanent_denials", [])}
    denied_prefixes = [
        [str(token).lower() for token in item]
        for item in policy.get("permanent_denied_prefixes", [])
        if isinstance(item, list)
    ]
    for prefix in prefixes:
        if prefix[0].lower() in denied:
            raise ValueError(f"Command is permanently denied for delegates: {prefix[0]}")
        lowered = [token.lower() for token in prefix]
        if any(lowered[: len(item)] == item for item in denied_prefixes):
            raise ValueError(f"Command prefix is permanently denied for delegates: {' '.join(prefix)}")
    unique = []
    seen = set()
    for prefix in prefixes:
        key = tuple(token.lower() for token in prefix)
        if key not in seen:
            unique.append(prefix)
            seen.add(key)
    return unique
