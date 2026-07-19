from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

from .cli import ALLOW_ENV, DEPTH_ENV, WORKSPACE_ENV
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
    ",",
    "\n",
    "\r",
)

FORBIDDEN_ARGUMENTS = {
    "--output",
    "--exec",
    "--exec-batch",
    "--pre",
    "--ext-diff",
    "--textconv",
    "--open-files-in-pager",
    "--paginate",
    "--show-signature",
    "--follow",
    "-followsymlink",
    "--follow-symlink",
    "--search-zip",
    "--hostname-bin",
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


def _path_argument(token: str) -> str:
    lowered = token.lower()
    for name in ("-path:", "-literalpath:"):
        if lowered.startswith(name):
            return token[len(name):]
    if token.startswith("-") and "=" in token:
        return token.split("=", 1)[1]
    return token


_RG_VALUE_OPTIONS = {
    "-A", "-B", "-C", "-e", "-g", "-m", "-t", "-T",
    "--after-context", "--before-context", "--context", "--encoding",
    "--engine", "--glob", "--max-columns", "--max-count",
    "--max-depth", "--max-filesize", "--path-separator", "--pre-glob",
    "--regexp", "--replace", "--sort", "--sortr", "--threads",
    "--type", "--type-add", "--type-not",
}
_RG_FILE_OPTIONS = {"-f", "--file", "--ignore-file"}


def _rg_path_operands(tokens: list[str]) -> list[str]:
    paths = []
    explicit_pattern = False
    pattern_seen = False
    files_mode = False
    index = 1
    while index < len(tokens):
        token = tokens[index]
        name = token.split("=", 1)[0].split(":", 1)[0]
        if token == "--":
            remaining = tokens[index + 1:]
            if not explicit_pattern and not files_mode and not pattern_seen and remaining:
                pattern_seen = True
                remaining = remaining[1:]
            paths.extend(remaining)
            break
        if token.startswith("-"):
            if name in _RG_FILE_OPTIONS or (len(token) > 2 and token.startswith("-f")):
                if name in {"-f", "--file"} or token.startswith("-f"):
                    explicit_pattern = True
                if "=" in token:
                    paths.append(token.split("=", 1)[1])
                elif len(token) > 2 and token.startswith("-f"):
                    paths.append(token[2:])
                elif index + 1 < len(tokens):
                    paths.append(tokens[index + 1])
                    index += 1
                index += 1
                continue
            attached_short_value = (
                len(token) > 2 and token[:2] in {"-A", "-B", "-C", "-e", "-g", "-m", "-t", "-T"}
            )
            if name in {"-e", "--regexp"} or (len(token) > 2 and token.startswith("-e")):
                explicit_pattern = True
            if name == "--files":
                files_mode = True
            if name in _RG_VALUE_OPTIONS and "=" not in token and not attached_short_value:
                index += 2
                continue
            index += 1
            continue
        if files_mode or explicit_pattern or pattern_seen:
            paths.append(token)
        else:
            pattern_seen = True
        index += 1
    return paths


_FD_PATH_OPTIONS = {"--base-directory", "--search-path"}
_FD_FILE_OPTIONS = {"--ignore-file"}
_FD_VALUE_OPTIONS = {
    "-d", "-e", "-E", "-j", "-t",
    "--and", "--batch-size", "--changed-before", "--changed-within",
    "--color", "--exclude", "--extension", "--format", "--max-depth",
    "--max-results", "--min-depth", "--owner", "--path-separator",
    "--size", "--type", "--threads",
}


def _fd_path_operands(tokens: list[str]) -> list[str]:
    paths = []
    pattern_seen = False
    index = 1
    while index < len(tokens):
        token = tokens[index]
        name = token.split("=", 1)[0]
        if token == "--":
            remaining = tokens[index + 1:]
            if not pattern_seen and remaining:
                pattern_seen = True
                remaining = remaining[1:]
            paths.extend(remaining)
            break
        if token.startswith("-"):
            if name in _FD_PATH_OPTIONS | _FD_FILE_OPTIONS:
                if "=" in token:
                    paths.append(token.split("=", 1)[1])
                elif index + 1 < len(tokens):
                    paths.append(tokens[index + 1])
                    index += 1
            elif name in _FD_VALUE_OPTIONS and "=" not in token:
                index += 1
            index += 1
            continue
        if pattern_seen:
            paths.append(token)
        else:
            pattern_seen = True
        index += 1
    return paths


def _select_string_path_operands(tokens: list[str]) -> list[str]:
    paths = []
    positionals = []
    explicit_pattern = False
    index = 1
    while index < len(tokens):
        token = tokens[index]
        lowered = token.lower()
        name = lowered.split("=", 1)[0].split(":", 1)[0]
        path_parameter = any(
            parameter.startswith(name) for parameter in ("-path", "-literalpath")
        )
        pattern_parameter = "-pattern".startswith(name)
        if path_parameter:
            if ":" in token:
                paths.append(token.split(":", 1)[1])
            elif "=" in token:
                paths.append(token.split("=", 1)[1])
            elif index + 1 < len(tokens):
                paths.append(tokens[index + 1])
                index += 1
        elif pattern_parameter:
            explicit_pattern = True
            if ":" not in token and "=" not in token and index + 1 < len(tokens):
                index += 1
        elif not token.startswith("-"):
            positionals.append(token)
        index += 1
    paths.extend(positionals if explicit_pattern else positionals[1:])
    return paths


def _path_operands(tokens: list[str]) -> list[str]:
    command = tokens[0].lower()
    if command == "rg":
        return _rg_path_operands(tokens)
    if command == "fd":
        return _fd_path_operands(tokens)
    if command == "select-string":
        return _select_string_path_operands(tokens)
    return tokens[1:]


def _cluster_denial(command: str, token: str) -> str | None:
    if not token.startswith("-") or token.startswith("--") or len(token) < 2:
        return None
    if len(token) > 2:
        if command == "rg" and token[:2] in {
            "-A", "-B", "-C", "-e", "-f", "-g", "-m", "-t", "-T",
        }:
            return None
        if command == "fd" and token[:2] in {"-d", "-e", "-E", "-j", "-S", "-t"}:
            return None
    cluster = token[1:]
    if command in {"rg", "fd"} and "L" in cluster:
        return "follow-symlink short option is denied"
    if command == "rg" and any(flag in cluster for flag in ("z", "Z")):
        return "rg external decompressor option is denied"
    if command == "fd" and any(flag in cluster for flag in ("x", "X")):
        return "fd execution short option is denied"
    return None


def _outside_workspace(tokens: list[str], workspace: str) -> str | None:
    root = Path(workspace).resolve()
    for token in _path_operands(tokens):
        candidate = _path_argument(token).strip().strip("\"'")
        if not candidate or candidate.startswith("-"):
            continue
        if any(character in candidate for character in "*?[]"):
            return candidate
        lowered = candidate.lower()
        if lowered.startswith(("~", "\\\\", "//")):
            return candidate
        if ":" in candidate and not (
            len(candidate) >= 3 and candidate[1] == ":" and candidate[2] in {"\\", "/"}
        ):
            return candidate
        path = Path(candidate)
        looks_like_path = (
            path.is_absolute()
            or "/" in candidate
            or "\\" in candidate
            or candidate.startswith(".")
            or (root / path).exists()
        )
        if not looks_like_path:
            continue
        try:
            resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
            resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            return candidate
    return None


def is_allowed(
    command: str,
    prefixes: list[list[str]],
    workspace: str | None = None,
) -> tuple[bool, str]:
    tokens = _tokens(command)
    if not tokens:
        return False, "compound, redirected, or unparsable commands are denied"
    denied = {item.lower() for item in DEFAULT_POLICY["permanent_denials"]}
    if tokens[0].lower() in denied:
        return False, f"{tokens[0]} is permanently denied"
    lowered = [token.lower() for token in tokens]
    command = tokens[0].lower()
    denied_prefixes = [
        [str(token).lower() for token in item]
        for item in DEFAULT_POLICY["permanent_denied_prefixes"]
    ]
    if any(lowered[: len(prefix)] == prefix for prefix in denied_prefixes):
        return False, "command is permanently denied"
    for raw_token, token in zip(tokens[1:], lowered[1:]):
        cluster_reason = _cluster_denial(command, raw_token)
        if cluster_reason:
            return False, cluster_reason
        name = token.split("=", 1)[0].split(":", 1)[0]
        if command == "get-childitem" and "-followsymlink".startswith(name):
            return False, "PowerShell follow-symlink parameter is denied"
        if name in FORBIDDEN_ARGUMENTS:
            return False, f"argument {name} is denied"
    if workspace:
        outside = _outside_workspace(tokens, workspace)
        if outside:
            return False, f"path escapes delegated workspace: {outside}"
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
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        print(json.dumps({"decision": "deny", "reason": "agent-delegation guard received malformed input"}))
        return 0
    command = _command_from_payload(payload)
    if depth != 1:
        workspace_paths = payload.get("workspacePaths")
        workspace = (
            str(workspace_paths[0])
            if (
                isinstance(workspace_paths, list)
                and workspace_paths
                and isinstance(workspace_paths[0], str)
                and workspace_paths[0].strip()
            )
            else None
        )
        if workspace is None:
            print(
                json.dumps(
                    {
                        "decision": "force_ask",
                        "reason": "normal agy command requires confirmation: workspace is missing",
                    }
                )
            )
            return 0
        allowed, reason = is_allowed(command, DEFAULT_POLICY["command_prefixes"], workspace)
        if allowed:
            print(
                json.dumps(
                    {
                        "decision": "allow",
                        "reason": f"normal agy read-only policy: {reason}",
                        "permissionOverrides": [f"command({command})"],
                    }
                )
            )
        else:
            print(
                json.dumps(
                    {
                        "decision": "force_ask",
                        "reason": f"normal agy command requires confirmation: {reason}",
                    }
                )
            )
        return 0
    try:
        prefixes = json.loads(os.environ.get(ALLOW_ENV, "[]"))
    except ValueError:
        print(json.dumps({"decision": "deny", "reason": "agent-delegation guard received malformed capabilities"}))
        return 0
    workspace = os.environ.get(WORKSPACE_ENV)
    if not workspace:
        allowed, reason = False, "delegated workspace is missing"
    else:
        allowed, reason = is_allowed(command, prefixes, workspace)
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
