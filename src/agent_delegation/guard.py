from __future__ import annotations

import json
import os
import shlex
import sys

from .cli import ALLOW_ENV, DEPTH_ENV
from .policy import DEFAULT_POLICY


FORBIDDEN_SYNTAX = (
    ";",
    "&",
    "&&",
    "||",
    "|",
    ">",
    "<",
    "`",
    "$(",
    "(",
    ")",
    "{",
    "}",
    "$",
    "^",
    "%",
    "!",
    "\n",
    "\r",
)

FORBIDDEN_ARGUMENTS = {
    "--output",
    "--exec",
    "--exec-batch",
    "-x",
    "-X",
    "--pre",
    "--ext-diff",
    "--textconv",
    "--open-files-in-pager",
    "--paginate",
    "--show-signature",
}


def _command_from_payload(payload: dict) -> str:
    tool_call = payload.get("toolCall")
    tool_args = tool_call.get("args") if isinstance(tool_call, dict) else None
    candidates = [
        payload.get("command"),
        tool_args.get("CommandLine") if isinstance(tool_args, dict) else None,
        tool_args.get("command") if isinstance(tool_args, dict) else None,
        (payload.get("tool_input") or {}).get("command") if isinstance(payload.get("tool_input"), dict) else None,
        (payload.get("toolInput") or {}).get("command") if isinstance(payload.get("toolInput"), dict) else None,
        (payload.get("input") or {}).get("command") if isinstance(payload.get("input"), dict) else None,
    ]
    return next((str(value) for value in candidates if value), "")


def _tokens(command: str) -> list[str]:
    normalized = command
    if len(normalized) >= 2 and normalized.startswith('"') and normalized.endswith('"'):
        try:
            decoded = json.loads(normalized)
        except ValueError:
            decoded = None
        if isinstance(decoded, str):
            normalized = decoded
    if any(item in normalized for item in FORBIDDEN_SYNTAX):
        return []
    try:
        return [token.strip('"') for token in shlex.split(normalized, posix=False)]
    except ValueError:
        return []


def is_allowed(command: str, prefixes: list[list[str]]) -> tuple[bool, str]:
    tokens = _tokens(command)
    if not tokens:
        return False, "compound, redirected, or unparsable commands are denied"
    denied = {item.lower() for item in DEFAULT_POLICY["permanent_denials"]}
    if tokens[0].lower() in denied:
        return False, f"{tokens[0]} is permanently denied"
    lowered = [token.lower() for token in tokens]
    denied_prefixes = [
        [str(token).lower() for token in item]
        for item in DEFAULT_POLICY["permanent_denied_prefixes"]
    ]
    if any(lowered[: len(prefix)] == prefix for prefix in denied_prefixes):
        return False, "command is permanently denied"
    for token in lowered[1:]:
        name = token.split("=", 1)[0]
        if name in FORBIDDEN_ARGUMENTS:
            return False, f"argument {name} is denied"
    for prefix in prefixes:
        candidate = [str(token).lower() for token in prefix]
        if lowered[: len(candidate)] == candidate:
            return True, "allowed by command prefix"
    return False, "command prefix is not in the active capability set"


def main() -> int:
    try:
        depth = int(os.environ.get(DEPTH_ENV, "0") or "0")
    except ValueError:
        depth = 0
    if depth != 1:
        print("{}")
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        prefixes = json.loads(os.environ.get(ALLOW_ENV, "[]"))
    except ValueError:
        print(json.dumps({"decision": "deny", "reason": "agent-delegation guard received malformed input"}))
        return 0
    command = _command_from_payload(payload)
    allowed, reason = is_allowed(command, prefixes)
    if allowed:
        print(
            json.dumps(
                {
                    "decision": "allow",
                    "reason": reason,
                    "permissionOverrides": [f"command({command})"],
                }
            )
        )
        return 0
    print(json.dumps({"decision": "deny", "reason": f"{reason}: {command}"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
