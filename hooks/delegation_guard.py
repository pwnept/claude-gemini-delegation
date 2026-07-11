#!/usr/bin/env python3
"""
PreToolUse hook: block high-output Bash commands that should be delegated.

Claude Code sends hook payload JSON on stdin. Return 2 to block the tool call
and print redirection guidance to stderr.
"""

import json
import os
import re
import sys
from pathlib import Path

if hasattr(sys.stdin, "reconfigure"):
    # PowerShell prepends a UTF-8 BOM when piping a string to a native
    # process; utf-8-sig strips it so json.load() doesn't choke on it.
    sys.stdin.reconfigure(encoding="utf-8-sig", errors="replace")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Derive hook paths from the script location. In this source repository the
# scripts live directly under hooks/; target installs use environment shims.
_script_hooks_dir = Path(__file__).resolve().parent
_script_parent_name = _script_hooks_dir.parent.name
_default_prefix = (
    "hooks"
    if _script_parent_name not in (".claude", ".codex", ".Codex", ".gemini-delegation")
    else _script_parent_name + "/hooks"
)
_HOOK_PREFIX = os.environ.get("DELEGATION_HOOK_PREFIX") or _default_prefix
_RUNNER_PATH = (
    _HOOK_PREFIX + "/gemini_delegate.py"
    if _HOOK_PREFIX in ("hooks", ".gemini-delegation/hooks")
    else ".gemini-delegation/hooks/gemini_delegate.py"
)

PATTERNS = [
    (re.compile(r"\bnpm\s+ls\b"), "npm ls"),
    (re.compile(r"\bpip\s+(list|freeze)\b"), "pip list/freeze"),
    (re.compile(r"\bgit\s+log\b.*(--all|-n\s*[6-9]\d*|-n\s*\d{3,}|--oneline.*--all)"), "broad git log"),
    (re.compile(r"\bgrep\s+(-r|-R|--recursive)\b"), "recursive grep"),
    (re.compile(r"\bfind\s+\S+\s+-"), "find command"),
    (re.compile(r"\b(audit|scan|vuln)\b.*\.(py|js|ts|tsx|jsx|rb|go)\b", re.I), "security scan"),
    (re.compile(r"\bpip\s+install\b.*--dry"), "pip dry-run"),
]

GUIDANCE = f"""This command matches a delegation pattern. Use agy instead.

IMPORTANT: Use the PowerShell tool — NOT the Bash tool. Bash routes to Git Bash on Windows and cannot run .ps1 scripts.

PowerShell tool:
  $prompt = & {_HOOK_PREFIX}/delegate.ps1 "<task>" "<context>"
  $prompt | py -3 {_RUNNER_PATH}

Or with validation/metrics (PowerShell tool):
  & {_HOOK_PREFIX}/delegate_and_log.ps1 "<task>" "<context>" 10

Add -Profile research for documentation lookup or web search.
"""


def _update_caller_session(payload: dict) -> None:
    """Persist the active turn id into .caller-session.json before delegation runs.

    Claude supplies prompt_id; Codex supplies turn_id at the top level.
    delegation_guard runs as a PreToolUse hook before every tool call, so by the
    time delegate_and_log.ps1 executes, the file already has the current turn id.
    We merge rather than overwrite so session_id written by archive-sync is kept.
    """
    # Codex: turn_id is a top-level field. Claude: prompt_id. Prefer whichever is set.
    prompt_id = payload.get("turn_id") or payload.get("prompt_id", "")
    if not prompt_id:
        return
    session_file = _script_hooks_dir.parent / ".caller-session.json"
    try:
        existing = json.loads(session_file.read_text(encoding="utf-8")) if session_file.exists() else {}
    except (OSError, ValueError):
        existing = {}
    existing["turn_id"] = prompt_id
    if payload.get("session_id"):
        existing["session_id"] = payload["session_id"]
    try:
        session_file.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    _update_caller_session(payload)

    if payload.get("tool_name") not in ("Bash", "PowerShell", "shell", "run_command"):
        return 0

    command = payload.get("tool_input", {}).get("command", "")
    if not command:
        return 0

    for pattern, label in PATTERNS:
        if pattern.search(command):
            print(f"[delegation_guard] Blocked: matches '{label}' delegation rule.", file=sys.stderr)
            print(GUIDANCE, file=sys.stderr)
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
