#!/usr/bin/env python3
"""
Run Gemini CLI with model-pool fallback.

Usage:
    python gemini_delegate.py [prompt]
    echo "prompt" | python gemini_delegate.py

The wrapper avoids separate quota probes. Instead, it treats capacity/rate-limit
failures from Gemini CLI as live signals, cools down that model, and retries the
same prompt on the next configured model pool.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


DEFAULT_MODELS = [
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
]

RESEARCH_MODELS = [
    "gemini-3.1-pro-preview",
    "gemini-2.5-pro",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
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
        if prefix_path.parent.name in (".claude", ".Codex"):
            return prefix_path.parent

    # 2. Use script's own parent dir if it is a known agent dir
    script_parent = Path(__file__).resolve().parent.parent
    if script_parent.name in (".claude", ".Codex", ".gemini-delegation"):
        return script_parent

    # 3. Search up the tree
    current = start.resolve()
    for directory in (current, *current.parents):
        for name in (".gemini-delegation", ".claude", ".Codex"):
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


def resolve_gemini_command() -> str:
    if os.name == "nt":
        return shutil.which("gemini.cmd") or shutil.which("gemini") or "gemini.cmd"
    return shutil.which("gemini") or "gemini"


import tempfile

def run_gemini(
    command: str,
    model: str,
    prompt: str,
    timeout: int,
    idle_timeout: int = 30,
) -> subprocess.CompletedProcess:
    """Run Gemini CLI with activity-aware streaming timeout.

    Kills the process if no output appears on stdout or stderr for
    idle_timeout seconds. As long as Gemini is streaming (progress lines,
    search results being compiled, etc.) the idle timer resets, so research
    tasks that take several minutes are not cut short. The hard cap is
    timeout seconds; 0 means no hard cap.
    """
    env = os.environ.copy()
    env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"

    # Use a temporary file for the prompt to avoid command line length limits
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False) as tf:
        tf.write(prompt)
        temp_prompt_file = tf.name

    try:
        proc = subprocess.Popen(
            [command, "--skip-trust", "--model", model, "-p", f"@{temp_prompt_file}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )

        stdout_chunks: list = []
        stderr_chunks: list = []
        last_activity = [time.monotonic()]

        def _drain(stream, collector):
            for chunk in iter(lambda: stream.read(256), ""):
                collector.append(chunk)
                last_activity[0] = time.monotonic()
            stream.close()

        t_out = threading.Thread(target=_drain, args=(proc.stdout, stdout_chunks), daemon=True)
        t_err = threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks), daemon=True)
        t_out.start()
        t_err.start()

        start = time.monotonic()
        kill_reason = None

        while proc.poll() is None:
            now = time.monotonic()
            if idle_timeout > 0 and (now - last_activity[0]) >= idle_timeout:
                kill_reason = "idle"
                proc.kill()
                break
            if timeout > 0 and (now - start) >= timeout:
                kill_reason = "max"
                proc.kill()
                break
            time.sleep(0.5)

        t_out.join(timeout=5)
        t_err.join(timeout=5)

        stdout = "".join(stdout_chunks)
        stderr = "".join(stderr_chunks)

        if kill_reason:
            elapsed = time.monotonic() - start
            secs = idle_timeout if kill_reason == "idle" else timeout
            raise subprocess.TimeoutExpired(
                cmd=[command, "--model", model],
                timeout=secs,
                output=stdout,
                stderr=stderr,
            )

        return subprocess.CompletedProcess(
            args=[command, "--skip-trust", "--model", model, "-p", f"@{temp_prompt_file}"],
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
        )
    finally:
        if os.path.exists(temp_prompt_file):
            try:
                os.remove(temp_prompt_file)
            except OSError:
                pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Gemini CLI with capacity-aware model fallback.")
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
        help="Cooldown to apply when Gemini reports capacity without a reset duration.",
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
        help="Override state file path. Defaults to .claude/metrics/gemini_model_state.json.",
    )
    parser.add_argument(
        "--no-state",
        action="store_true",
        help="Ignore and do not write cooldown state.",
    )
    parser.add_argument(
        "--show-model",
        action="store_true",
        help="Print the selected model to stderr on success.",
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
    state_path = Path(args.state_file) if args.state_file else claude_dir / "metrics" / "gemini_model_state.json"
    state = {"cooldowns": {}} if args.no_state else load_state(state_path)
    now = time.time()
    command = resolve_gemini_command()
    skipped = []
    last_result = None
    last_model = None

    ordered_models = [model for model in models if model_available(model, state, now)]
    ordered_models.extend(model for model in models if model not in ordered_models)

    for model in ordered_models:
        if not model_available(model, state, time.time()):
            skipped.append(model)
            continue

        print("Trying Gemini model: {0}".format(model), file=sys.stderr)
        try:
            result = run_gemini(
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
            mark_cooldown(model, state, error_text, time.time(), args.cooldown_seconds)
            continue

        last_result = result
        last_model = model
        output = result.stdout or ""
        error = result.stderr or ""
        combined = output + "\n" + error

        if result.returncode == 0:
            if args.show_model:
                print("Gemini model used: {0}".format(model), file=sys.stderr)
            if output:
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

    print("All Gemini models are currently capacity-limited or unavailable.", file=sys.stderr)
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
