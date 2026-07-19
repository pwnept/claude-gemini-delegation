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
import csv
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from .policy import secure_gemini_environment

if hasattr(sys.stdin, "reconfigure"):
    # PowerShell prepends a UTF-8 BOM when piping a string to a native
    # process; utf-8-sig strips it so a leading U+FEFF doesn't pollute the prompt.
    sys.stdin.reconfigure(encoding="utf-8-sig", errors="replace")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# All Gemini models share a single quota pool in agy; no auto-fallback.
# On capacity, print a --models hint so the caller can retry explicitly.
# Three-model preset: skim -> Flash (Low), default/scout -> Flash (High),
# research -> Pro (High). Profiles differ by system-prompt persona too
# (see _PROFILE_PREAMBLES).
DEFAULT_MODELS = ["Gemini 3.5 Flash (High)"]
RESEARCH_MODELS = ["Gemini 3.1 Pro (High)"]
SKIM_MODELS = ["Gemini 3.5 Flash (Low)"]

# Keep the full known-model list for --models overrides and prefix validation.
KNOWN_AGY_MODELS = [
    "Gemini 3.5 Flash (Medium)",
    "Gemini 3.5 Flash (High)",
    "Gemini 3.5 Flash (Low)",
    "Gemini 3.1 Pro (Low)",
    "Gemini 3.1 Pro (High)",
    "Claude Sonnet 4.6 (Thinking)",
    "Claude Opus 4.6 (Thinking)",
    "GPT-OSS 120B (Medium)",
]

# Backend: Gemini CLI (npm install -g @google/gemini-cli).
# Auth via GEMINI_API_KEY in $PROFILE (free aistudio.google.com key).
# Model IDs must be accepted by the CLI's --model flag (not all API model IDs
# are in the CLI's built-in list). Test with fresh-quota models first.
DEFAULT_CLI_MODELS = [
    "gemini-3.5-flash",       # primary; 20 RPD limit - 429s cascade to next
    "gemini-3-flash-preview", # mandatory fallback, same tier
    "gemini-2.5-flash",       # independent quota
    "gemini-3.1-flash-lite",  # 500 RPD limit - largest headroom, lite warning fires here
    "gemini-2.5-flash-lite",  # 20 RPD limit - lite warning fires here
]
RESEARCH_CLI_MODELS = DEFAULT_CLI_MODELS

# Backend: direct Gemini REST API via urllib (no npm, no agy - stdlib only).
# Auth via GEMINI_API_KEY. Each model has an independent daily/RPM quota,
# so a 5-model cascade absorbs a lot of 429s before the pipeline gives up.
# Lite models are last-resort: acceptable for file search / grounding tasks
# (grounding RPD is generous: 1500 for default, Gemini 2.5, and Gemini 2).
DEFAULT_API_MODELS = [
    "gemini-3.5-flash",
    "gemini-3-flash",
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
]
RESEARCH_API_MODELS = DEFAULT_API_MODELS

# Scout profile: Gemma 4 models for read-heavy, low-reasoning tasks.
# 31B full model for quality; 26B MoE (4B active params) as faster fallback.
# Both have 1.5K RPD and unlimited TPM - absorbs codebase scoping, log parsing,
# dependency scanning, and test-case discovery without touching Flash quota.
SCOUT_CLI_MODELS = ["gemma-4-31b-it", "gemma-4-26b-a4b-it"]
SCOUT_API_MODELS = ["gemma-4-31b-it", "gemma-4-26b-a4b-it"]

# Lite models print a warning so the caller can decide whether to trust the output.
LITE_MODELS = {"gemini-3.1-flash-lite", "gemini-2.5-flash-lite"}

# Adding another backend means: one constant block like the above,
# one run_<backend>() + run_<backend>_backend() pair, one branch in main(),
# and a new entry in this tuple.
BACKENDS = ("agy", "gemini-cli", "gemini-api")
AGY_CONFIG_ENV = "AGENT_DELEGATION_AGY_CONFIG_ROOT"
LAST_MODEL_USED: str | None = None

# Caller detection and log-dir routing live in delegation_caller.py (shared
# with post_delegate.py and analyze_metrics.py) to avoid dict duplication.
# detect_caller() auto-detects the harness from DELEGATION_CALLER env token
# (set by installer) with a vendor-env-sniff fallback.
from .caller import detect_caller, resolve_log_dir  # noqa: E402

# Injected into every agy prompt. Keeps the idle watchdog alive via progress
# updates and gives a clean extraction point for the final answer.
_AGY_RESPONSE_FORMAT = (
    "IMPORTANT RESPONSE FORMAT:\n"
    "1. Start immediately with exactly 'working' on its own line: "
    "this confirms receipt and resets the idle watchdog.\n"
    "2. While working, output intermediate findings as you progress: "
    "each update resets the idle watchdog.\n"
    "3. End with exactly 'Final delegation answer:' on its own line, "
    "followed by your complete consolidated answer.\n\n"
)

# Per-profile persona lines. Profiles differ by system prompt, not just model:
# the delegate is a librarian returning fact-based digests, never a developer.
_PROFILE_PERSONAS = {
    "skim": (
        "ROLE: You are a high-speed haystack scanner. The task is an ultra-broad "
        "search (grep-at-scale, 'does this string appear anywhere', firehose log "
        "triage). Do NOT reason deeply. Return a terse digest: matches found, "
        "file paths / locations, counts. No prose, no analysis.\n\n"
    ),
    "research": (
        "ROLE: You are a deep research librarian. Search the web and docs, "
        "synthesize findings into a fact-based digest with citations and short "
        "conclusions. Report facts and locations. Do not write code or make "
        "architecture decisions.\n\n"
    ),
    "default": (
        "ROLE: You are a codebase librarian. Traverse files, map where things "
        "live, answer 'where does X live / who calls Y' style questions. Return "
        "a fact-based digest: findings, file paths, counts, short answer. Do not "
        "write code or make design decisions.\n\n"
    ),
}
_PROFILE_PERSONAS["scout"] = _PROFILE_PERSONAS["default"]


def _agy_preamble(profile: str) -> str:
    return _PROFILE_PERSONAS.get(profile, _PROFILE_PERSONAS["default"]) + _AGY_RESPONSE_FORMAT


def default_agy_models(profile: str) -> list:
    """Default agy model order for a profile, the ONE profile->model mapping.

    scout has no agy Gemma models, so it shares the default Flash tier.
    """
    if profile == "research":
        return RESEARCH_MODELS
    if profile == "skim":
        return SKIM_MODELS
    return DEFAULT_MODELS


_WORKING_SENTINEL = "working"
_FINAL_ANSWER_MARKER = "Final delegation answer:"

CAPACITY_PATTERNS = (
    "exhausted your capacity",
    "no capacity available",
    "too many requests",
    "ratelimitexceeded",
    "rate limit",
    "status 429",
    "resource_exhausted",
    "service unavailable",    # gemini-cli 503 text
    "unavailable",            # direct API: "503 UNAVAILABLE: ..." status field
    "currently experiencing", # gemini-cli "high demand" body text
    "terminalquotaerror",     # gemini-cli daily quota class
    "exhausted your daily",   # gemini-cli daily quota message
    "retrying with backoff",  # gemini-cli WebSearchToolInvocation retry; kill before 5× backoff loop
    "exceeded your current quota",  # gemini-cli search quota exhaustion message
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


def parse_model_order(text: str) -> list[str]:
    """Parse agy model fallback names, preserving spaces in quoted names."""
    text = (text or "").strip()
    if not text:
        return []

    if text in KNOWN_AGY_MODELS:
        return [text]

    if any(sep in text for sep in (",", ";", "\n")):
        normalized = re.sub(r"[;\n]+", ",", text)
        try:
            return [
                model.strip()
                for model in next(csv.reader([normalized], skipinitialspace=True))
                if model.strip()
            ]
        except csv.Error:
            return [part.strip() for part in normalized.split(",") if part.strip()]

    try:
        parts = shlex.split(text)
    except ValueError:
        return [text]

    if len(parts) <= 1:
        return parts

    # A single unquoted agy model contains spaces. If it is not clearly a
    # shell-style list of quoted models, preserve it as one model name.
    if '"' not in text and "'" not in text:
        return [text]
    return parts


def model_name_errors(models: list[str]) -> list[str]:
    """Return helpful errors for agy model names missing required qualifiers."""
    errors = []
    for model in models:
        if model in KNOWN_AGY_MODELS:
            continue
        matches = [
            known for known in KNOWN_AGY_MODELS
            if known.startswith(model + " ")
        ]
        if matches:
            errors.append(
                "{0!r} is incomplete. agy requires the full model name, e.g. {1}.".format(
                    model,
                    ", ".join(repr(match) for match in matches),
                )
            )
    return errors


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


def _actual_agy_config_root() -> Path:
    return (Path.home() / ".gemini" / "config").resolve()


def _actual_agy_settings_path() -> Path:
    return (Path.home() / ".gemini" / "antigravity-cli" / "settings.json").resolve()


def managed_agy_config_args() -> list[str]:
    """Validate the managed global agy command gate before launch."""
    raw = os.environ.get(AGY_CONFIG_ENV, "").strip()
    root = _actual_agy_config_root()
    if raw:
        requested = Path(os.path.expandvars(os.path.expanduser(raw))).resolve()
        if requested != root:
            raise RuntimeError(f"{AGY_CONFIG_ENV} cannot override agy's actual config root: {root}")
    hooks_path = root / "hooks.json"
    settings_path = _actual_agy_settings_path()
    try:
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        hook = hooks["agent-delegation-command-policy"]
        allowed = settings["permissions"]["allow"]
        expected_command = settings["agentDelegation"]["guardCommand"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"Delegated agy config is incomplete at {root}: {exc}") from exc
    if not isinstance(hook, dict) or hook.get("enabled") is not True:
        raise RuntimeError(f"Delegated agy command guard is not enabled in {hooks_path}")
    try:
        if not isinstance(expected_command, str) or not expected_command:
            raise TypeError("guardCommand must be a nonempty string")
        groups = hook["PreToolUse"]
        valid_guard = any(
            isinstance(group, dict)
            and group.get("matcher") == "run_command"
            and any(
                isinstance(handler, dict)
                and handler.get("type") == "command"
                and handler.get("command") == expected_command
                and handler.get("timeout") == 5
                for handler in group.get("hooks", [])
            )
            for group in groups
        )
    except (KeyError, TypeError) as exc:
        raise RuntimeError(f"Delegated agy command guard is incomplete in {hooks_path}") from exc
    if not valid_guard:
        raise RuntimeError(f"Delegated agy command guard does not match the trusted runtime in {hooks_path}")
    if not isinstance(allowed, list) or "command(*)" not in allowed:
        raise RuntimeError(f"Delegated agy headless permission is missing in {settings_path}")
    return []


def resolve_backend(args: argparse.Namespace) -> str:
    """Pick the delegation backend: --backend flag > DELEGATION_BACKEND env > agy."""
    backend = args.backend or os.environ.get("DELEGATION_BACKEND") or "agy"
    return backend.strip().lower()


def _gemini_cli_argv_prefix() -> list[str]:
    """Resolve gemini-cli without passing untrusted text through cmd.exe."""
    command = shutil.which("gemini")
    if not command:
        raise RuntimeError("gemini-cli is not installed or is not on PATH")
    path = Path(command)
    if os.name != "nt" or path.suffix.lower() not in {".cmd", ".bat"}:
        return [command]

    node = shutil.which("node")
    candidates = (
        path.parent / "node_modules" / "@google" / "gemini-cli" / "bundle" / "gemini.js",
        path.parent / "node_modules" / "@google" / "gemini-cli" / "dist" / "index.js",
    )
    script = next((candidate for candidate in candidates if candidate.is_file()), None)
    if not node or script is None:
        raise RuntimeError(
            "Cannot resolve the gemini-cli Node entry point safely. Reinstall @google/gemini-cli."
        )
    return [node, str(script)]


def run_gemini_cli(model: str, prompt: str, timeout: int) -> subprocess.CompletedProcess:
    """Run gemini-cli non-interactively, killing it immediately on capacity errors.

    Streams stderr line-by-line in a background thread. The moment a capacity
    pattern appears (429, 503, quota exhausted) the process tree is killed so
    gemini-cli's own internal retry loop never fires a second API request.
    On Windows, the npm shim is resolved to its Node entry point so prompts are
    never parsed by cmd.exe. taskkill /F /T still terminates the process tree.
    """
    import threading

    cmd = [
        *_gemini_cli_argv_prefix(),
        "--model",
        model,
        "--prompt",
        prompt,
        "--sandbox",
        "--approval-mode",
        "plan",
    ]

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )

    stdout_buf = []
    stderr_buf = []

    def _kill_tree():
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
            )
        else:
            proc.kill()

    def read_stdout():
        stdout_buf.append(proc.stdout.read())

    def watch_stderr():
        for line in proc.stderr:
            stderr_buf.append(line)
            if capacity_limited(line):
                _kill_tree()
                proc.stderr.read()  # drain so the pipe does not block
                return
        rest = proc.stderr.read()
        if rest:
            stderr_buf.append(rest)

    t_out = threading.Thread(target=read_stdout, daemon=True)
    t_err = threading.Thread(target=watch_stderr, daemon=True)
    t_out.start()
    t_err.start()

    try:
        proc.wait(timeout=timeout if timeout > 0 else None)
    except subprocess.TimeoutExpired:
        _kill_tree()
        proc.wait()

    t_out.join(timeout=5)
    t_err.join(timeout=2)

    return subprocess.CompletedProcess(
        args=cmd,
        returncode=proc.returncode,
        stdout="".join(filter(None, stdout_buf)),
        stderr="".join(stderr_buf),
    )


def _extract_gemini_text(data: dict) -> str:
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return ""


def call_gemini_api(model: str, prompt: str, timeout: int) -> subprocess.CompletedProcess:
    """POST to the Gemini REST API using only stdlib urllib.

    Returns a CompletedProcess-shaped value so run_with_fallback can handle it
    the same way as every other backend. 429 error bodies contain
    "RESOURCE_EXHAUSTED" which CAPACITY_PATTERNS already matches.
    """
    import urllib.error
    import urllib.request

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return subprocess.CompletedProcess(
            args=["gemini-api", model],
            returncode=1,
            stdout="",
            stderr="GEMINI_API_KEY not set. Add to $PROFILE: $env:GEMINI_API_KEY = 'your-key'",
        )

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        + model
        + ":generateContent?key="
        + api_key
    )
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout if timeout > 0 else None) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = _extract_gemini_text(data)
        return subprocess.CompletedProcess(
            args=["gemini-api", model],
            returncode=0 if text else 1,
            stdout=text,
            stderr="" if text else "Empty response from API",
        )
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            err = json.loads(raw.decode("utf-8"))
            msg = err.get("error", {}).get("message", raw.decode("utf-8", errors="replace"))
            status = err.get("error", {}).get("status", "")
            stderr = "{0} {1}: {2}".format(exc.code, status, msg)
        except (ValueError, KeyError):
            stderr = "{0}: {1}".format(exc.code, raw.decode("utf-8", errors="replace"))
        return subprocess.CompletedProcess(
            args=["gemini-api", model], returncode=1, stdout="", stderr=stderr
        )
    except Exception as exc:
        return subprocess.CompletedProcess(
            args=["gemini-api", model], returncode=1, stdout="", stderr=str(exc)
        )


def run_api_backend(prompt: str, args: argparse.Namespace, claude_dir: Path) -> int:
    """Delegate via direct Gemini REST API (stdlib urllib, no extra installs)."""
    model_order = args.models or os.environ.get("GEMINI_API_MODELS")
    if model_order is None:
        if args.profile == "research":
            model_order = ",".join(RESEARCH_API_MODELS)
        elif args.profile in ("scout", "skim"):
            # skim has no dedicated API tier; scout models are the matching
            # cheap/low-reasoning choice, never the default tier.
            model_order = ",".join(SCOUT_API_MODELS)
        else:
            model_order = ",".join(DEFAULT_API_MODELS)
    models = parse_model_order(model_order)
    if not models:
        print("No models configured.", file=sys.stderr)
        return 2

    state_path = (
        Path(args.state_file) if args.state_file
        else resolve_log_dir(args.caller, claude_dir / "metrics") / "gemini_api_model_state.json"
    )

    def attempt(model: str) -> subprocess.CompletedProcess:
        if model in LITE_MODELS:
            print(
                "[gemini-api] Lite model {0} in use - suitable for file search / grounding "
                "tasks (grounding RPD is generous). Flag results for review if used for "
                "reasoning or code generation.".format(model),
                file=sys.stderr,
            )
        return call_gemini_api(model, prompt, timeout=args.timeout_seconds)

    def _on_success(output: str, model: str) -> None:
        _save_delegation_transcript(prompt, output, model, claude_dir, args, "gemini-api")

    return run_with_fallback(models, state_path, args, attempt, on_success=_on_success)


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


# agy prompts this on first run in a directory; the cursor starts on
# "Yes, I trust this folder" so a bare Enter confirms.
TRUST_PROMPT = "Do you trust the contents"


def confirm_trust_prompt(pty, buf: str, already_confirmed: bool) -> bool:
    """Auto-confirm agy's first-run trust dialog once it appears in `buf`.

    Returns the new confirmed state. Single source of truth for the dialog
    text and the settle-then-Enter keystroke, shared by the one-shot runner
    and the persistent delegate host.
    """
    if already_confirmed or TRUST_PROMPT not in buf:
        return already_confirmed
    time.sleep(0.3)  # let the dialog finish painting before answering
    pty.write("\r")
    return True


def _extract_final_answer(output: str) -> str:
    """Return the section after 'Final delegation answer:' if present, else full output."""
    lowered = output.lower()
    idx = lowered.rfind(_FINAL_ANSWER_MARKER.lower())
    if idx == -1:
        return output
    return output[idx + len(_FINAL_ANSWER_MARKER):].lstrip("\n").strip()


def _save_timing_log(transcript_path: Path, timing: dict, model: str) -> None:
    """Write a sibling _timing.txt next to the transcript for later review."""
    timing_path = transcript_path.parent / (transcript_path.stem + "_timing.txt")
    now_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    start = timing.get("start", 0.0)

    def _delta(key: str) -> str:
        val = timing.get(key)
        return f"{val - start:.2f}s" if val is not None else "n/a"

    lines = [
        "timing_log_version: 1",
        f"timestamp_utc: {now_utc}",
        f"model: {model}",
        f"total_seconds: {timing.get('end', start) - start:.2f}",
        f"first_chunk_seconds: {_delta('first_chunk_at')}",
        f"sentinel_seconds: {_delta('sentinel_at')}",
        f"final_answer_seconds: {_delta('final_answer_at')}",
        f"kill_reason: {timing.get('kill_reason') or 'none'}",
        f"idle_timeout_seconds: {timing.get('idle_timeout', 'n/a')}",
        f"first_response_seconds: {timing.get('first_response_seconds', 'n/a')}",
        f"transcript: {transcript_path.name}",
    ]
    try:
        timing_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass  # timing log is best-effort


def run_agy(
    command: str,
    model: str,
    prompt: str,
    timeout: int,
    idle_timeout: int = 30,
    first_response_seconds: int = 45,
    timing: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run agy and return captured output.

    On Windows, agy writes to CONOUT$ instead of redirected stdout, so use a
    ConPTY via pywinpty. On macOS and Linux, normal subprocess capture works.
    """
    import tempfile
    neutral_cwd = tempfile.gettempdir()
    workspace_dir = str(Path.cwd().resolve())
    agy_args = managed_agy_config_args() + [
        "--add-dir",
        workspace_dir,
        "--model",
        model,
        "--mode",
        "plan",
        "--sandbox",
        "-p",
        prompt,
    ]

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
            "pywinpty is required to capture agy output: py -3 -m pip install --user pywinpty"
        )

    # Run from a neutral temp dir - agy detects git workspaces and enters
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
    sentinel_seen = False  # True once model outputs "working"
    final_answer_seen = False

    if timing is not None:
        timing.update({
            "start": start, "idle_timeout": idle_timeout,
            "first_response_seconds": first_response_seconds,
        })

    while True:
        now = time.monotonic()
        chunk = pty.read(blocking=False)
        if chunk:
            buf += chunk
            last_activity = now
            if timing is not None and "first_chunk_at" not in timing:
                timing["first_chunk_at"] = now
            trust_confirmed = confirm_trust_prompt(pty, buf, trust_confirmed)
            # Kill immediately when a capacity/429 signal appears. agy will go
            # idle after printing it and the idle timeout would waste 60-90s.
            if capacity_limited(buf):
                kill_reason = "capacity"
                os.kill(pty.pid, signal.SIGTERM)
                break
            stripped = _strip_ansi(buf).lower()
            if not sentinel_seen and _WORKING_SENTINEL in stripped:
                sentinel_seen = True
                if timing is not None:
                    timing["sentinel_at"] = now
            if not final_answer_seen and _FINAL_ANSWER_MARKER.lower() in stripped:
                final_answer_seen = True
                if timing is not None:
                    timing["final_answer_at"] = now
        elif not pty.isalive():
            # drain any remaining PTY buffer
            while True:
                tail = pty.read(blocking=False)
                if not tail:
                    break
                buf += tail
            break
        else:
            # Two-phase idle: short window until model sends "working", then full idle_timeout.
            # Each progress update from the model resets last_activity, keeping long tasks alive.
            current_idle = idle_timeout if sentinel_seen else first_response_seconds
            if current_idle > 0 and (now - last_activity) >= current_idle:
                kill_reason = "idle" if sentinel_seen else "first-response"
                os.kill(pty.pid, signal.SIGTERM)
                break
            if timeout > 0 and (now - start) >= timeout:
                kill_reason = "max"
                os.kill(pty.pid, signal.SIGTERM)
                break
            time.sleep(0.2)

    stdout = _strip_ansi(buf)

    if timing is not None:
        timing["end"] = time.monotonic()
        timing["kill_reason"] = kill_reason

    if kill_reason:
        if kill_reason == "idle":
            secs = idle_timeout
        elif kill_reason == "first-response":
            secs = first_response_seconds
        else:
            secs = timeout
        raise subprocess.TimeoutExpired(
            cmd=[command, "--add-dir", workspace_dir, "--model", model],
            timeout=secs,
            output=stdout,
            stderr="capacity-limited" if kill_reason == "capacity" else "",
        )

    exit_status = pty.get_exitstatus()
    if exit_status is None:
        exit_status = 1

    return subprocess.CompletedProcess(
        args=[command, "--add-dir", workspace_dir, "--model", model, "-p", "..."],
        returncode=exit_status,
        stdout=stdout,
        stderr="",
    )


def _safe_path_part(value: object, default: str = "unknown") -> str:
    text = str(value or "").strip()
    if not text:
        text = default
    text = re.sub(r'[\\/:*?"<>|\s]+', "-", text)
    text = re.sub(r"-+", "-", text).strip("-._")
    return text or default


def _project_slug(project_root: Path) -> str:
    try:
        resolved = project_root.resolve()
    except OSError:
        resolved = project_root

    parts = [part for part in resolved.parts if part not in ("\\", "/")]
    if parts and parts[0].endswith(":"):
        parts[0] = parts[0][:-1]
    cleaned = [_safe_path_part(part) for part in parts if _safe_path_part(part)]
    return "--".join(cleaned) or _safe_path_part(resolved.name, "project")


def _load_caller_session(agent_dir: Path) -> dict:
    session_file = agent_dir / ".caller-session.json"
    if not session_file.exists():
        return {}
    try:
        data = json.loads(session_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _caller_name(args: argparse.Namespace, context: dict) -> str:
    if args.caller and args.caller != "auto":
        return args.caller
    context_agent = str(context.get("agent", "")).strip().lower()
    if context_agent:
        return context_agent
    detected = detect_caller()
    return detected or "unknown"


def _session_slug(context: dict) -> str:
    transcript_path = str(context.get("transcript_path", "")).strip()
    if transcript_path:
        return _safe_path_part(Path(transcript_path).stem, "unknown-session")
    return _safe_path_part(context.get("session_id"), "unknown-session")


def _turn_slug(context: dict) -> str:
    return _safe_path_part(
        context.get("turn_id") or os.environ.get("DELEGATION_TURN_ID"),
        "turn-unknown",
    )


def _delegation_log_root() -> Path:
    configured = os.environ.get("DELEGATION_LOG_ROOT")
    if configured:
        return Path(os.path.expandvars(os.path.expanduser(configured)))
    return Path.home() / ".gemini_delegation"


def _write_numbered_delegation_log(log_dir: Path, turn_slug: str, content: str) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    for number in range(1, 10000):
        candidate = log_dir / f"{turn_slug}_{number:04d}.txt"
        try:
            with candidate.open("x", encoding="utf-8") as handle:
                handle.write(content)
            return candidate
        except FileExistsError:
            continue
    raise OSError(f"Could not allocate delegation log file in {log_dir}")


def _save_delegation_transcript(
    prompt: str,
    output: str,
    model: str,
    agent_dir: Path,
    args: argparse.Namespace,
    backend: str,
) -> Path | None:
    """Write prompt+response as one user-home .txt file per delegation call."""
    if os.environ.get("DELEGATION_DISABLE_LOGS", "").strip().lower() in {"1", "true", "yes", "on"}:
        return None

    try:
        repo_root = agent_dir.parent
        context = _load_caller_session(agent_dir)
        caller = _safe_path_part(_caller_name(args, context))
        project_slug = _project_slug(repo_root)
        session_slug = _session_slug(context)
        turn_slug = _turn_slug(context)
        log_dir = (
            _delegation_log_root()
            / "runs"
            / caller
            / project_slug
            / f"{session_slug}_gemini_delegation"
        )
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        content = "\n".join(
            [
                "delegation_log_version: 1",
                f"timestamp_utc: {timestamp}",
                f"caller: {caller}",
                f"project_path: {repo_root}",
                f"project_slug: {project_slug}",
                f"session_id: {context.get('session_id', 'unknown-session')}",
                f"session_slug: {session_slug}",
                f"turn_id: {context.get('turn_id') or os.environ.get('DELEGATION_TURN_ID') or 'turn-unknown'}",
                f"backend: {backend}",
                f"profile: {args.profile}",
                f"model: {model}",
                "exit_status: 0",
                "",
                "=== PROMPT ===",
                prompt,
                "",
                "=== OUTPUT ===",
                output,
            ]
        )
        dest = _write_numbered_delegation_log(log_dir, turn_slug, content)
        print(f"[Saved to: {dest}]", file=sys.stderr)
        return dest
    except OSError as exc:
        print(f"[WARN] Could not save delegation transcript: {exc}", file=sys.stderr)
        return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run agy CLI with capacity-aware model fallback.")
    parser.add_argument("prompt", nargs="?", help="Prompt to send. If omitted, stdin is used.")
    parser.add_argument(
        "--models",
        help=(
            "Model fallback order. Accepts comma-separated names or shell-style "
            "quoted names, e.g. --models '\"Gemini 3.1 Pro (Low)\",\"Gemini 3.5 Flash (Medium)\"'."
        ),
    )
    parser.add_argument(
        "--profile",
        choices=("default", "research", "scout", "skim"),
        default="default",
        help="Model order profile. Research uses Pro (High); skim uses Flash (Low) for "
             "ultra-broad haystack searches; scout uses Flash on agy (Gemma 4 on gemini-cli/api).",
    )
    parser.add_argument(
        "--pre-format",
        action="store_true",
        help="Treat the input as a raw task and format it via pre_delegate before sending "
             "(replaces a separate pre_delegate.py invocation).",
    )
    parser.add_argument(
        "--context",
        default="General task",
        help="Task context used by --pre-format.",
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        default=0,
        help="Response line budget used by --pre-format/--post-validate. 0 = auto.",
    )
    parser.add_argument(
        "--post-validate",
        action="store_true",
        help="Validate the response and log metrics after a successful delegation "
             "(replaces a separate post_delegate.py invocation). Warnings go to stderr.",
    )
    parser.add_argument(
        "--backend",
        choices=BACKENDS,
        default=None,
        help="Delegation backend. Defaults to $DELEGATION_BACKEND or 'agy'.",
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
        help="Override state file path. Defaults to .gemini-delegation/metrics/agy_model_state.json.",
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
    parser.add_argument(
        "--caller",
        choices=("claude", "codex", "agy", "auto"),
        default="auto",
        help="Calling harness. State and metrics files go to its home delegation-logs/ dir. "
             "Defaults to 'auto' which calls detect_caller() from delegation_caller.py.",
    )
    parser.add_argument(
        "--agent-dir",
        help="Path to the .gemini-delegation (or .claude/.codex) dir for this repo. "
             "When provided, skips the cwd up-walk in find_agent_dir().",
    )
    return parser.parse_args()


def _post_validate(response: str, args: argparse.Namespace) -> None:
    """Inline post_delegate validation + metrics; stdout stays the pure response."""
    try:
        import contextlib

        try:
            from . import post_delegate
        except ImportError:
            import post_delegate
        agent_dir = Path(args.agent_dir) if args.agent_dir else find_agent_dir(Path.cwd())
        metrics_dir = resolve_log_dir(args.caller, agent_dir / "metrics")
        max_lines = getattr(args, "max_lines", 0) or 10
        with contextlib.redirect_stdout(sys.stderr):
            _, warnings = post_delegate.validate_response(response, max_lines)
            for warning in warnings:
                print(warning)
        label = getattr(args, "_task_label", None) or getattr(args, "context", "unknown")
        post_delegate.log_metrics(
            label,
            post_delegate.count_lines(response),
            post_delegate.estimate_tokens(response),
            metrics_dir,
        )
    except Exception as exc:  # noqa: BLE001 - validation is best-effort, never fail the run
        print(f"[WARN] post-validate failed: {exc}", file=sys.stderr)


def run_with_fallback(models: list, state_path: Path, args: argparse.Namespace, attempt, *, on_success=None) -> int:
    """Try `models` in order via `attempt(model)`, applying capacity cooldowns.

    `attempt(model)` returns a subprocess.CompletedProcess-shaped result and
    may raise subprocess.TimeoutExpired. Shared by every backend (agy,
    gemini-api, ...) so they all get the same fallback/cooldown/state-file
    behavior for free.

    `on_success(output, model)` is called once on the first successful response
    (respects --no-save).
    """
    global LAST_MODEL_USED
    LAST_MODEL_USED = None
    state = {"cooldowns": {}} if args.no_state else load_state(state_path)
    now = time.time()
    skipped = []
    last_result = None
    last_model = None

    ordered_models = [model for model in models if model_available(model, state, now)]
    ordered_models.extend(model for model in models if model not in ordered_models)

    for model in ordered_models:
        if not model_available(model, state, time.time()):
            skipped.append(model)
            continue

        print("Trying model: {0}".format(model), file=sys.stderr)
        LAST_MODEL_USED = model
        try:
            result = attempt(model)
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

        # Exit 9 on Windows = Node.js libuv cleanup crash after response is captured.
        # Treat as success if stdout has content and stderr has no capacity signal.
        node_crash = (result.returncode == 9 and output and not capacity_limited(combined))
        if (result.returncode == 0 or node_crash) and output:
            if args.show_model:
                print("Model used: {0}".format(model), file=sys.stderr)
            if not args.no_save and on_success:
                on_success(output, model)
            if getattr(args, "post_validate", False):
                _post_validate(output, args)
            sys.stdout.write(output)
            if not args.no_state:
                state.setdefault("cooldowns", {}).pop(model, None)
                save_state(state_path, state)
            return 0

        if capacity_limited(combined):
            cooldown = mark_cooldown(model, state, combined, time.time(), args.cooldown_seconds)
            print(
                "{0} is capacity-limited (cooldown {1}s). "
                "Retry with: --models \"Gemini 3.1 Pro (High)\"".format(model, cooldown),
                file=sys.stderr,
            )
            continue

        if output:
            # Failed run that still produced output: quota was spent and the
            # caller sees the text, so it must land in the metrics too.
            if getattr(args, "post_validate", False):
                _post_validate(output, args)
            sys.stdout.write(output)
        if error:
            sys.stderr.write(error)
        if not args.no_state:
            save_state(state_path, state)
        return result.returncode

    if not args.no_state:
        save_state(state_path, state)

    print("All models are currently capacity-limited or unavailable.", file=sys.stderr)
    print("Retry with a different model: --models \"Gemini 3.1 Pro (High)\"", file=sys.stderr)
    if skipped:
        print("Skipped due to cooldown: {0}".format(", ".join(skipped)), file=sys.stderr)
    if last_result is not None:
        if last_result.stdout:
            if getattr(args, "post_validate", False):
                _post_validate(last_result.stdout, args)
            sys.stdout.write(last_result.stdout)
        if last_result.stderr:
            sys.stderr.write(last_result.stderr)
        return last_result.returncode or 1

    if last_model:
        print("Last attempted model: {0}".format(last_model), file=sys.stderr)
    return 1


def run_cli_backend(prompt: str, args: argparse.Namespace, claude_dir: Path) -> int:
    """Delegate via gemini-cli (npm install -g @google/gemini-cli)."""
    model_order = args.models or os.environ.get("GEMINI_CLI_MODELS")
    if model_order is None:
        if args.profile == "research":
            model_order = ",".join(RESEARCH_CLI_MODELS)
        elif args.profile in ("scout", "skim"):
            # skim has no dedicated CLI tier; the scout models are the
            # matching cheap/low-reasoning choice, never the default tier.
            model_order = ",".join(SCOUT_CLI_MODELS)
        else:
            model_order = ",".join(DEFAULT_CLI_MODELS)

    models = parse_model_order(model_order)
    if not models:
        print("No models configured.", file=sys.stderr)
        return 2

    state_path = (
        Path(args.state_file) if args.state_file
        else resolve_log_dir(args.caller, claude_dir / "metrics") / "gemini_cli_model_state.json"
    )

    def attempt(model: str) -> subprocess.CompletedProcess:
        if model in LITE_MODELS:
            print(
                "[gemini-cli] Lite model {0} in use - suitable for file search / grounding "
                "tasks (grounding RPD is generous). Flag results for review if used for "
                "reasoning or code generation.".format(model),
                file=sys.stderr,
            )
        return run_gemini_cli(model, prompt, timeout=args.timeout_seconds)

    def _on_success(output: str, model: str) -> None:
        _save_delegation_transcript(prompt, output, model, claude_dir, args, "gemini-cli")

    return run_with_fallback(models, state_path, args, attempt, on_success=_on_success)


def main() -> int:
    try:
        current_depth = int(os.environ.get("AGENT_DELEGATION_DEPTH", "0") or "0")
    except ValueError:
        current_depth = 0
    if current_depth >= 1:
        print("Nested delegation rejected: a delegate cannot create another delegate.", file=sys.stderr)
        return 2

    previous_depth = os.environ.get("AGENT_DELEGATION_DEPTH")
    os.environ["AGENT_DELEGATION_DEPTH"] = "1"
    try:
        return _main()
    finally:
        if previous_depth is None:
            os.environ.pop("AGENT_DELEGATION_DEPTH", None)
        else:
            os.environ["AGENT_DELEGATION_DEPTH"] = previous_depth


def _main() -> int:

    args = parse_args()

    backend = resolve_backend(args)
    if backend not in BACKENDS:
        print(
            "Unknown backend {0!r}. Choose one of: {1}".format(backend, ", ".join(BACKENDS)),
            file=sys.stderr,
        )
        return 2

    if backend == "agy" and os.environ.get("AGENT_DELEGATION_AGY_VALIDATED") != "1":
        print(
            "agy delegation is disabled until its permission hook passes the reviewed live smoke.",
            file=sys.stderr,
        )
        return 2

    if backend in {"agy", "gemini-cli"}:
        os.environ.update(secure_gemini_environment())

    if backend == "gemini-cli":
        print(
            "[alt-backend: gemini-cli] Primary backend is agy; gemini-cli is in use (npm required).",
            file=sys.stderr,
        )
    elif backend == "gemini-api":
        print(
            "[alt-backend: gemini-api] Primary backend is agy; gemini-api is in use "
            "(direct REST, no grounding or CLI features; needs GEMINI_API_KEY).",
            file=sys.stderr,
        )

    # Increase idle timeout for research profile if not explicitly overridden
    if args.profile == "research" and args.idle_timeout_seconds == 60:
        args.idle_timeout_seconds = 120
    if args.profile in ("scout", "skim") and args.idle_timeout_seconds == 60:
        args.idle_timeout_seconds = 45  # Flash responds fast; long idle means it's stuck

    prompt = args.prompt if args.prompt is not None else sys.stdin.read()
    prompt = prompt.strip()
    if not prompt:
        print("No prompt provided.", file=sys.stderr)
        return 2

    if args.pre_format:
        # Fold the pre_delegate step into this process (one Python spawn per call).
        from . import pre_delegate
        task = pre_delegate.expand_paths(prompt)
        task_type = pre_delegate.detect_task_type(task)
        max_lines = args.max_lines or pre_delegate.estimate_compression(task)
        args.max_lines = max_lines
        args._task_label = task
        prompt = pre_delegate.build_prompt(task_type, task, args.context, max_lines)

    claude_dir = Path(args.agent_dir) if args.agent_dir else find_agent_dir(Path.cwd())

    if backend == "gemini-cli":
        return run_cli_backend(prompt, args, claude_dir)

    if backend == "gemini-api":
        return run_api_backend(prompt, args, claude_dir)

    # backend == "agy"
    model_order = args.models
    if model_order is None:
        model_order = ",".join(default_agy_models(args.profile))

    models = parse_model_order(model_order)
    if not models:
        print("No models configured.", file=sys.stderr)
        return 2

    errors = model_name_errors(models)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 2

    state_path = (
        Path(args.state_file) if args.state_file
        else resolve_log_dir(args.caller, claude_dir / "metrics") / "agy_model_state.json"
    )
    command = resolve_agy_command()

    augmented_prompt = _agy_preamble(args.profile) + prompt
    timing: dict = {}

    def attempt(model: str) -> subprocess.CompletedProcess:
        timing.clear()
        result = run_agy(
            command, model, augmented_prompt,
            timeout=args.timeout_seconds,
            idle_timeout=args.idle_timeout_seconds,
            first_response_seconds=45,
            timing=timing,
        )
        if result.returncode == 0 and result.stdout:
            filtered = _extract_final_answer(result.stdout)
            result = subprocess.CompletedProcess(
                args=result.args,
                returncode=result.returncode,
                stdout=filtered,
                stderr=result.stderr,
            )
        return result

    def _on_success(output: str, model: str) -> None:
        dest = _save_delegation_transcript(prompt, output, model, claude_dir, args, "agy")
        if dest and timing:
            _save_timing_log(dest, timing, model)

    return run_with_fallback(models, state_path, args, attempt, on_success=_on_success)


if __name__ == "__main__":
    sys.exit(main())
