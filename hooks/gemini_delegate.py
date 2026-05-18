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
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


DEFAULT_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
]

RESEARCH_MODELS = [
    "gemini-2.5-pro",
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


def find_claude_dir(start: Path) -> Path:
    current = start.resolve()
    candidate = current / ".claude"
    if candidate.exists():
        return candidate

    for parent in current.parents:
        candidate = parent / ".claude"
        if candidate.exists():
            return candidate

    return current / ".claude"


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


def run_gemini(command: str, model: str, prompt: str, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        [command, "--model", model, "-p", prompt],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout if timeout > 0 else None,
        check=False,
    )


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
        "--timeout-seconds",
        type=int,
        default=0,
        help="Per-model subprocess timeout. 0 means no wrapper timeout.",
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

    claude_dir = find_claude_dir(Path.cwd())
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
            result = run_gemini(command, model, prompt, args.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            last_model = model
            last_result = None
            error_text = "Timed out after {0}s".format(args.timeout_seconds)
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
