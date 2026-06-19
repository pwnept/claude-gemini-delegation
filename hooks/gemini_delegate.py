#!/usr/bin/env python3
"""
Run agy (Antigravity) CLI with model-pool fallback.

Usage:
    python gemini_delegate.py [prompt]
    echo "prompt" | python gemini_delegate.py

The wrapper avoids separate quota probes. Instead, it treats capacity/rate-limit
failures as live signals, cools down that model, and retries the same prompt on
the next configured model pool.
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdin, "reconfigure"):
    # PowerShell prepends a UTF-8 BOM when piping a string to a native
    # process; utf-8-sig strips it so a leading U+FEFF doesn't pollute the prompt.
    sys.stdin.reconfigure(encoding="utf-8-sig", errors="replace")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


DEFAULT_MODELS = [
    "Gemini 3.5 Flash (Medium)",
    "Gemini 3.5 Flash (Low)",
    "Gemini 3.5 Flash (High)",
]

RESEARCH_MODELS = [
    "Gemini 3.1 Pro (High)",
    "Gemini 3.1 Pro (Low)",
    "Gemini 3.5 Flash (Medium)",
]

CAPACITY_PATTERNS = (
    "exhausted your capacity",
    "no capacity available",
    "too many requests",
    "ratelimitexceeded",
    "rate limit",
    "status 429",
)


def find_agent_dir(start: Path) -> Path:
    # 1. Respect DELEGATION_HOOK_PREFIX env var set by per-env shim
    hook_prefix = os.environ.get("DELEGATION_HOOK_PREFIX")
    if hook_prefix:
        prefix_path = Path(hook_prefix)
        if prefix_path.parent.name in (".claude", ".codex", ".Codex"):
            return prefix_path.parent

    # 2. Use script's own parent dir if it is a known agent dir
    script_parent = Path(__file__).resolve().parent.parent
    if script_parent.name in (".claude", ".codex", ".Codex", ".gemini-delegation"):
        return script_parent

    # 3. Search up the tree
    current = start.resolve()
    for directory in (current, *current.parents):
        for name in (".gemini-delegation", ".claude", ".codex", ".Codex"):
            candidate = directory / name
            if candidate.exists():
                return candidate

    return current / ".gemini-delegation"


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"cooldowns": {}}

    try:
        with path.open("r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        return {"cooldowns": {}}

    if not isinstance(state, dict):
        return {"cooldowns": {}}

    state.setdefault("cooldowns", {})
    return state


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def parse_duration_seconds(text: str) -> int:
    match = re.search(r"reset after\s+(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hour|hours)", text, re.I)
    if not match:
        return 0

    value = int(match.group(1))
    unit = match.group(2).lower()
    if unit.startswith("h"):
        return value * 3600
    if unit.startswith("m"):
        return value * 60
    return value


def capacity_limited(text: str) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in CAPACITY_PATTERNS)


def model_available(model: str, state: dict, now: float) -> bool:
    cooldown_until = state.get("cooldowns", {}).get(model, 0)
    try:
        cooldown_until = float(cooldown_until)
    except (TypeError, ValueError):
        return True
    return cooldown_until <= now


def mark_cooldown(model: str, state: dict, error_text: str, now: float, fallback_seconds: int) -> int:
    reset_seconds = parse_duration_seconds(error_text)
    cooldown_seconds = max(reset_seconds, fallback_seconds)
    state.setdefault("cooldowns", {})[model] = now + cooldown_seconds
    return cooldown_seconds


def resolve_agy_command() -> str:
    if os.name == "nt":
        return shutil.which("agy.exe") or shutil.which("agy") or "agy.exe"
    return shutil.which("agy") or "agy"


_ANSI_RE = re.compile(
    # OSC and DCS must come before the 2-char catch-all or ] gets consumed early
    r"\x1b(?:"
    r"\][^\x07\x1b]*(?:\x07|\x1b\\)"   # OSC: ESC ] ... BEL or ST
    r"|\[[0-?]*[ -/]*[@-~]"             # CSI: ESC [ params final
    r"|[@-Z\\-_]"                        # 2-char Fe/Fp/Fs sequences
    r")"
    r"|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"  # bare control chars
)


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")


def run_agy(
    command: str,
    model: str,
    prompt: str,
    timeout: int,
    idle_timeout: int = 30,
) -> subprocess.CompletedProcess:
    """Run agy and return captured output.

    On Windows, agy writes to CONOUT$ instead of redirected stdout, so use a
    ConPTY via pywinpty. On macOS and Linux, normal subprocess capture works.
    """
    import tempfile
    neutral_cwd = tempfile.gettempdir()
    workspace_dir = str(Path.cwd().resolve())
    agy_args = ["--add-dir", workspace_dir, "--model", model, "-p", prompt]

    if os.name != "nt":
        return subprocess.run(
            [command, *agy_args],
            cwd=neutral_cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout if timeout > 0 else None,
            check=False,
        )

    try:
        import winpty
    except ImportError:
        raise RuntimeError(
            "pywinpty is required to capture agy output: pip3 install pywinpty"
        )

    # Run from a neutral temp dir — agy detects git workspaces and enters
    # interactive mode when run from a project directory, ignoring -p.
    # argv[0] is NOT prepended: the original working form used bare flags.
    cmdline = subprocess.list2cmdline(agy_args)
    pty = winpty.PTY(220, 50)
    pty.spawn(command, cmdline=cmdline, cwd=neutral_cwd)

    buf = ""
    last_activity = time.monotonic()
    start = time.monotonic()
    kill_reason = None
    trust_confirmed = False

    while True:
        now = time.monotonic()
        chunk = pty.read(blocking=False)
        if chunk:
            buf += chunk
            last_activity = now
            # agy prompts "Do you trust…" on first run in a directory;
            # the cursor starts on "Yes, I trust this folder" so Enter confirms.
            if not trust_confirmed and "Do you trust the contents" in buf:
                time.sleep(0.3)
                pty.write("\r")
                trust_confirmed = True
        elif not pty.isalive():
            # drain any remaining PTY buffer
            while True:
                tail = pty.read(blocking=False)
                if not tail:
                    break
                buf += tail
            break
        else:
            if idle_timeout > 0 and (now - last_activity) >= idle_timeout:
                kill_reason = "idle"
                os.kill(pty.pid, signal.SIGTERM)
                break
            if timeout > 0 and (now - start) >= timeout:
                kill_reason = "max"
                os.kill(pty.pid, signal.SIGTERM)
                break
            time.sleep(0.2)

    stdout = _strip_ansi(buf)

    if kill_reason:
        secs = idle_timeout if kill_reason == "idle" else timeout
        raise subprocess.TimeoutExpired(
            cmd=[command, "--add-dir", workspace_dir, "--model", model],
            timeout=secs,
            output=stdout,
            stderr="",
        )

    return subprocess.CompletedProcess(
        args=[command, "--add-dir", workspace_dir, "--model", model, "-p", "..."],
        returncode=0,
        stdout=stdout,
        stderr="",
    )


def _try_save_response(output: str, model: str) -> None:
    """Save response to temp/ in cwd if that directory exists."""
    temp_dir = Path.cwd() / "temp"
    if not temp_dir.is_dir():
        return
    ts = time.strftime("%Y%m%d-%H%M%S")
    out_path = temp_dir / f"agy-{ts}.md"
    try:
        with out_path.open("w", encoding="utf-8") as f:
            f.write(f"<!-- model: {model} -->\n")
            f.write(output)
        print(f"[Saved to: {out_path}]", file=sys.stderr)
    except OSError:
        pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run agy CLI with capacity-aware model fallback.")
    parser.add_argument("prompt", nargs="?", help="Prompt to send. If omitted, stdin is used.")
    parser.add_argument(
        "--models",
        help="Comma-separated model fallback order.",
    )
    parser.add_argument(
        "--profile",
        choices=("default", "research"),
        default="default",
        help="Model order profile. Research uses Pro before Flash.",
    )
    parser.add_argument(
        "--cooldown-seconds",
        type=int,
        default=300,
        help="Cooldown to apply when agy reports capacity without a reset duration.",
    )
    parser.add_argument(
        "--idle-timeout-seconds",
        type=int,
        default=60,
        help="Kill model if no output for this many seconds (default 60). 0 disables.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=600,
        help="Hard cap per-model in seconds (default 600). 0 disables.",
    )
    parser.add_argument(
        "--state-file",
        help="Override state file path. Defaults to .claude/metrics/agy_model_state.json.",
    )
    parser.add_argument(
        "--no-state",
        action="store_true",
        help="Ignore and do not write cooldown state.",
    )
    parser.add_argument(
        "--show-model",
        action="store_true",
        help="Print the selected agy model to stderr on success.",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Do not save response to temp/ even if the directory exists.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    
    # Increase idle timeout for research profile if not explicitly overridden
    if args.profile == "research" and args.idle_timeout_seconds == 60:
        args.idle_timeout_seconds = 120
    
    prompt = args.prompt if args.prompt is not None else sys.stdin.read()
    prompt = prompt.strip()
    if not prompt:
        print("No prompt provided.", file=sys.stderr)
        return 2

    model_order = args.models
    if model_order is None:
        model_order = ",".join(RESEARCH_MODELS if args.profile == "research" else DEFAULT_MODELS)

    models = [model.strip() for model in model_order.split(",") if model.strip()]
    if not models:
        print("No models configured.", file=sys.stderr)
        return 2

    claude_dir = find_agent_dir(Path.cwd())
    state_path = Path(args.state_file) if args.state_file else claude_dir / "metrics" / "agy_model_state.json"
    state = {"cooldowns": {}} if args.no_state else load_state(state_path)
    now = time.time()
    command = resolve_agy_command()
    skipped = []
    last_result = None
    last_model = None

    ordered_models = [model for model in models if model_available(model, state, now)]
    ordered_models.extend(model for model in models if model not in ordered_models)

    for model in ordered_models:
        if not model_available(model, state, time.time()):
            skipped.append(model)
            continue

        print("Trying agy model: {0}".format(model), file=sys.stderr)
        try:
            result = run_agy(
                command, model, prompt,
                timeout=args.timeout_seconds,
                idle_timeout=args.idle_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            last_model = model
            last_result = None
            idle = args.idle_timeout_seconds
            hard = args.timeout_seconds
            error_text = "Timed out (idle>{0}s or >{1}s total)".format(idle, hard)
            print("{0}: {1}".format(model, error_text), file=sys.stderr)

            captured = ((exc.output or "") + "\n" + (exc.stderr or "")).strip()
            if captured:
                snippet = captured[-2000:]
                print("--- last output before kill ---", file=sys.stderr)
                print(snippet, file=sys.stderr)
                print("--- end captured output ---", file=sys.stderr)
                if capacity_limited(captured):
                    error_text = captured

            mark_cooldown(model, state, error_text, time.time(), args.cooldown_seconds)
            continue

        last_result = result
        last_model = model
        output = result.stdout or ""
        error = result.stderr or ""
        combined = output + "\n" + error

        if result.returncode == 0:
            if args.show_model:
                print("agy model used: {0}".format(model), file=sys.stderr)
            if output:
                if not args.no_save:
                    _try_save_response(output, model)
                sys.stdout.write(output)
            if not args.no_state:
                state.setdefault("cooldowns", {}).pop(model, None)
                save_state(state_path, state)
            return 0

        if capacity_limited(combined):
            cooldown = mark_cooldown(model, state, combined, time.time(), args.cooldown_seconds)
            print(
                "{0} is capacity-limited; cooling down for {1}s and trying next model.".format(model, cooldown),
                file=sys.stderr,
            )
            continue

        if output:
            sys.stdout.write(output)
        if error:
            sys.stderr.write(error)
        if not args.no_state:
            save_state(state_path, state)
        return result.returncode

    if not args.no_state:
        save_state(state_path, state)

    print("All agy models are currently capacity-limited or unavailable.", file=sys.stderr)
    if skipped:
        print("Skipped due to cooldown: {0}".format(", ".join(skipped)), file=sys.stderr)
    if last_result is not None:
        if last_result.stdout:
            sys.stdout.write(last_result.stdout)
        if last_result.stderr:
            sys.stderr.write(last_result.stderr)
        return last_result.returncode or 1

    if last_model:
        print("Last attempted model: {0}".format(last_model), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
