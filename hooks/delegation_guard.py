#!/usr/bin/env python3
"""
PreToolUse hook: block high-output Bash commands that should be delegated.

Claude Code sends hook payload JSON on stdin. Return 2 to block the tool call
and print redirection guidance to stderr.
"""

import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Derive hook prefix from script location so guidance is correct whether
# the script lives in .claude/hooks/ or .Codex/hooks/.
_HOOK_PREFIX = Path(__file__).resolve().parent.parent.name + "/hooks"

PATTERNS = [
    (re.compile(r"\bnpm\s+ls\b"), "npm ls"),
    (re.compile(r"\bpip\s+(list|freeze)\b"), "pip list/freeze"),
    (re.compile(r"\bgit\s+log\b.*(--all|-n\s*[6-9]\d*|-n\s*\d{3,}|--oneline.*--all)"), "broad git log"),
    (re.compile(r"\bgrep\s+(-r|-R|--recursive)\b"), "recursive grep"),
    (re.compile(r"\bfind\s+\S+\s+-"), "find command"),
    (re.compile(r"\b(audit|scan|vuln)\b.*\.(py|js|ts|tsx|jsx|rb|go)\b", re.I), "security scan"),
    (re.compile(r"\bpip\s+install\b.*--dry"), "pip dry-run"),
]

GUIDANCE = f"""This command matches a delegation pattern. Use Gemini instead:

PowerShell:
  $prompt = & {_HOOK_PREFIX}/delegate.ps1 "<task>" "<context>"
  $prompt | py -3 {_HOOK_PREFIX}/gemini_delegate.py

Or with validation/metrics:
  {_HOOK_PREFIX}/delegate_and_log.ps1 "<task>" "<context>" 10

Add -Profile research for documentation lookup or web search.
"""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    if payload.get("tool_name") != "Bash":
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
