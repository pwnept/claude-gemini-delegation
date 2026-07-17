from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import __version__
from .caller import detect_caller
from .policy import (
    DEFAULT_POLICY,
    command_prefixes,
    ensure_global_home,
    global_home,
    load_policy,
)


DEPTH_ENV = "AGENT_DELEGATION_DEPTH"
CALLER_ENV = "AGENT_DELEGATION_CALLER"
ALLOW_ENV = "AGENT_DELEGATION_ALLOWED_PREFIXES"
WORKSPACE_ENV = "AGENT_DELEGATION_WORKSPACE"
RUN_DIR_ENV = "AGENT_DELEGATION_RUN_DIR"


class CliError(RuntimeError):
    pass


def _depth() -> int:
    raw = os.environ.get(DEPTH_ENV, "0").strip() or "0"
    try:
        return max(0, int(raw))
    except ValueError as exc:
        raise CliError(f"{DEPTH_ENV} must be an integer, got {raw!r}.") from exc


def _workspace(value: str) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(value))).resolve()
    if not path.is_dir():
        raise CliError(f"Workspace is not a directory: {path}")
    return path


def _git_enabled(workspace: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(workspace), "config", "--local", "--get", "agentDelegation.enabled"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return True
    return result.stdout.strip().lower() not in {"0", "false", "no", "off"}


def _set_git_enabled(workspace: Path, enabled: bool) -> None:
    probe = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "--git-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        raise CliError("Per-repository enable and disable requires a Git repository.")
    subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "config",
            "--local",
            "agentDelegation.enabled",
            "true" if enabled else "false",
        ],
        check=True,
    )


def _safe_part(value: str, default: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in value.strip())
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned.strip("._-") or default


def _run_directory(caller: str, workspace: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    run_id = f"{stamp}-{uuid.uuid4().hex[:8]}"
    project = _safe_part(workspace.name, "workspace")
    path = global_home() / "runs" / _safe_part(caller, "unknown") / project / run_id
    path.mkdir(parents=True, exist_ok=False)
    return path


def _native_transcripts() -> dict[str, tuple[int, int]]:
    brain = _agy_root() / "brain"
    if not brain.is_dir():
        return {}
    result: dict[str, tuple[int, int]] = {}
    for path in brain.glob("*/.system_generated/logs/*.jsonl"):
        try:
            stat = path.stat()
            result[str(path)] = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            continue
    return result


def _agy_root() -> Path:
    configured = os.environ.get("AGENT_DELEGATION_AGY_ROOT")
    if configured:
        return Path(os.path.expandvars(os.path.expanduser(configured))).resolve()
    return Path.home() / ".gemini" / "antigravity-cli"


def _agy_config_root() -> Path:
    configured = os.environ.get("AGENT_DELEGATION_AGY_CONFIG_ROOT")
    if configured:
        return Path(os.path.expandvars(os.path.expanduser(configured))).resolve()
    return Path.home() / ".gemini" / "config"


def _install_agy_hook() -> Path:
    root = _agy_config_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "hooks.json"
    hooks = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                hooks = loaded
            else:
                raise CliError(f"Cannot update agy hooks file {path}: top level must be an object")
        except ValueError as exc:
            raise CliError(f"Cannot update invalid agy hooks file {path}: {exc}") from exc
    hooks["agent-delegation-command-policy"] = {
        "enabled": True,
        "PreToolUse": [
            {
                "matcher": "Bash|run_command",
                "hooks": [
                    {
                        "type": "command",
                        "command": "agent-delegation guard",
                        "timeout": 5,
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(hooks, indent=2) + "\n", encoding="utf-8")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _archive_native_transcripts(before: dict[str, tuple[int, int]], run_dir: Path) -> list[dict]:
    archived: list[dict] = []
    after = _native_transcripts()
    for raw_path, signature in after.items():
        if before.get(raw_path) == signature:
            continue
        source = Path(raw_path)
        conversation_id = source.parents[2].name
        destination = run_dir / "native" / conversation_id / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = source.read_bytes()
        destination.write_bytes(data)
        source_hash = hashlib.sha256(data).hexdigest()
        destination_hash = _sha256(destination)
        if source_hash != destination_hash:
            destination.unlink(missing_ok=True)
            raise CliError(f"Native transcript hash verification failed: {source}")
        archived.append(
            {
                "source": str(source),
                "archive": str(destination),
                "bytes": len(data),
                "sha256": source_hash,
                "source_preserved": source.exists(),
            }
        )
    return archived


def _build_prompt(task: str, context: str, workspace: Path, prefixes: list[list[str]]) -> str:
    rendered = [" ".join(prefix) for prefix in prefixes]
    return "\n".join(
        [
            "You are a bounded delegate working for another agent.",
            "Do not invoke agent-delegation or start another delegate.",
            "Work only inside the supplied workspace and obey the active sandbox.",
            "Terminal commands are denied unless their token prefix is listed below.",
            "Allowed command prefixes:",
            *[f"- {item}" for item in rendered],
            "",
            f"Workspace: {workspace}",
            f"Context: {context or 'No additional context supplied.'}",
            "",
            "Task:",
            task,
            "",
            "Return findings with file paths and concise evidence. Do not make unrequested changes.",
        ]
    )


@contextlib.contextmanager
def _temporary_environment(values: dict[str, str]):
    old = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _invoke_runner(
    args: argparse.Namespace, prompt: str, run_dir: Path, caller: str
) -> tuple[int, str, str, str | None]:
    from . import runner

    runner_args = [
        "agent-delegation-runner",
        "--backend",
        args.backend,
        "--profile",
        args.profile,
        "--caller",
        caller if caller in {"claude", "codex", "agy"} else "auto",
        "--agent-dir",
        str(global_home()),
        "--state-file",
        str(global_home() / "state" / f"{args.backend}_model_state.json"),
        "--timeout-seconds",
        str(args.timeout),
        "--idle-timeout-seconds",
        str(args.idle_timeout),
        "--no-save",
        prompt,
    ]
    if args.model:
        runner_args[1:1] = ["--models", args.model]

    previous_argv = sys.argv
    output = io.StringIO()
    errors = io.StringIO()
    live_errors = sys.stderr

    class Tee:
        def write(self, value: str) -> int:
            live_errors.write(value)
            errors.write(value)
            return len(value)

        def flush(self) -> None:
            live_errors.flush()
            errors.flush()

    try:
        runner.LAST_MODEL_USED = None
        sys.argv = runner_args
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(Tee()):
            code = runner.main()
    finally:
        sys.argv = previous_argv
    return code, output.getvalue(), errors.getvalue(), runner.LAST_MODEL_USED


def run_command(args: argparse.Namespace) -> int:
    if _depth() >= 1:
        raise CliError("Nested delegation rejected: a delegate cannot create another delegate.")

    workspace = _workspace(args.workspace)
    if not _git_enabled(workspace):
        raise CliError(f"Delegation is disabled for {workspace}")

    caller = args.caller if args.caller != "auto" else (detect_caller() or "unknown")
    policy = load_policy()
    prefixes = command_prefixes(policy, args.allow_command)
    run_dir = _run_directory(caller, workspace)
    prompt = _build_prompt(args.task, args.context, workspace, prefixes)
    before = _native_transcripts() if args.backend == "agy" else {}
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    environment = {
        DEPTH_ENV: "1",
        CALLER_ENV: caller,
        ALLOW_ENV: json.dumps(prefixes),
        WORKSPACE_ENV: str(workspace),
        RUN_DIR_ENV: str(run_dir),
        "DELEGATION_LOG_ROOT": str(global_home()),
    }
    old_cwd = Path.cwd()
    code = 2
    response = ""
    runner_stderr = ""
    worker_model = None
    native: list[dict] = []
    run_error = None
    archive_error = None
    try:
        os.chdir(workspace)
        with _temporary_environment(environment):
            code, response, runner_stderr, worker_model = _invoke_runner(args, prompt, run_dir, caller)
    except BaseException as exc:  # noqa: BLE001 - interruptions must still produce a durable run record.
        run_error = f"{type(exc).__name__}: {exc}"
        code = 130 if isinstance(exc, KeyboardInterrupt) else 2
    finally:
        if args.backend == "agy":
            try:
                native = _archive_native_transcripts(before, run_dir)
            except Exception as exc:  # noqa: BLE001 - native state stays untouched; record copy failure.
                archive_error = f"{type(exc).__name__}: {exc}"
                code = 2
        os.chdir(old_cwd)

    exchange = run_dir / "exchange.jsonl"
    records = [
        {"type": "prompt", "text": prompt},
        {
            "type": "response",
            "text": response,
            "stderr": runner_stderr,
            "exit_code": code,
            "error": run_error,
            "native_archive_error": archive_error,
        },
    ]
    exchange.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
    manifest = {
        "schema": 1,
        "started_utc": started,
        "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "caller": caller,
        "backend": args.backend,
        "profile": args.profile,
        "worker_model": worker_model,
        "workspace": str(workspace),
        "depth": 1,
        "allowed_command_prefixes": prefixes,
        "caller_extensions": args.allow_command,
        "exit_code": code,
        "exchange_jsonl": str(exchange),
        "exchange_sha256": _sha256(exchange),
        "native_transcripts": native,
        "run_error": run_error,
        "runner_stderr": runner_stderr,
        "native_archive_error": archive_error,
        "native_state_modified_by_agent_delegation": False,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if response:
        sys.stdout.write(response)
    if run_error:
        print(f"[ERROR] Delegate failed: {run_error}", file=sys.stderr)
    if archive_error:
        print(f"[ERROR] Native transcript archive failed: {archive_error}", file=sys.stderr)
    print(f"[agent-delegation run: {run_dir}]", file=sys.stderr)
    return code


def install_command(args: argparse.Namespace) -> int:
    home = ensure_global_home(force=args.force)
    agy_hook = _install_agy_hook()
    print(f"Installed global configuration at {home}")
    print(f"Installed agy command guard in {agy_hook}")
    return 0


def toggle_command(args: argparse.Namespace, enabled: bool) -> int:
    workspace = _workspace(args.workspace)
    _set_git_enabled(workspace, enabled)
    print(f"Delegation {'enabled' if enabled else 'disabled'} for {workspace}")
    return 0


def status_command(args: argparse.Namespace) -> int:
    workspace = _workspace(args.workspace)
    payload = {
        "version": __version__,
        "home": str(global_home()),
        "workspace": str(workspace),
        "enabled": _git_enabled(workspace),
        "depth": _depth(),
        "caller": detect_caller() or "unknown",
        "policy": str(global_home() / "policy.json"),
        "agy_hook": str(_agy_config_root() / "hooks.json"),
    }
    print(json.dumps(payload, indent=2))
    return 0


def guard_command() -> int:
    from .guard import main as guard_main

    return guard_main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-delegation")
    parser.add_argument("--version", action="version", version=f"agent-delegation {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    install = sub.add_parser("install", help="Create or update global managed configuration")
    install.add_argument("--force", action="store_true", help="Refresh the managed base policy")
    install.set_defaults(handler=install_command)

    run = sub.add_parser("run", help="Run one bounded delegate")
    run.add_argument("task")
    run.add_argument("--context", default="")
    run.add_argument("--workspace", default=".")
    run.add_argument("--caller", choices=("auto", "claude", "codex", "agy"), default="auto")
    run.add_argument("--backend", choices=("agy", "gemini-cli", "gemini-api"), default="agy")
    run.add_argument("--profile", choices=("default", "research", "scout"), default="default")
    run.add_argument("--model")
    run.add_argument("--allow-command", action="append", default=[], metavar="PREFIX")
    run.add_argument("--timeout", type=int, default=600)
    run.add_argument("--idle-timeout", type=int, default=60)
    run.set_defaults(handler=run_command)

    for name, enabled in (("enable", True), ("disable", False)):
        command = sub.add_parser(name, help=f"{name.title()} delegation in one Git repository")
        command.add_argument("workspace", nargs="?", default=".")
        command.set_defaults(handler=lambda args, value=enabled: toggle_command(args, value))

    status = sub.add_parser("status", help="Show global and repository state")
    status.add_argument("workspace", nargs="?", default=".")
    status.set_defaults(handler=status_command)

    guard = sub.add_parser("guard", help=argparse.SUPPRESS)
    guard.set_defaults(handler=lambda args: guard_command())
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (CliError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
