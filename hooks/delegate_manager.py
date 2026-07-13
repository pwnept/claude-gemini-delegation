#!/usr/bin/env python3
"""
Delegate manager: async (fire-and-be-woken) and persistent steerable delegates.

One-shot async (Tier 1):
    delegate_manager.py async "<task>" [--context C] [--max-lines N] [--profile P]
        -> prints a delegate id and returns immediately; the delegation runs in a
           fully detached process. On completion it writes a condensed result
           (extracted 'Final delegation answer:' + one-line status) and a `done`
           marker; a `warn` marker appears at 75% of the hard cap.
    delegate_manager.py wait <id> [--timeout-seconds N]
        -> sacrificial waiter: blocks on the marker files in a dumb poll loop
           (no model tokens). Exit codes: 0 done, 2 warn (still running),
           3 waiter timeout, 4 unknown/dead delegate. Launch it with the
           harness's run_in_background so the waiter's exit is the wake signal.

Persistent steerable delegates (Tier 2):
    delegate_manager.py spawn [--profile P] [--model M] [--workspace D]
        -> keeps an interactive agy PTY session alive as an ID'd delegate that
           outlives the originating session. Pinned to one workspace (--add-dir).
    delegate_manager.py steer <id> "<prompt>"   (single writer at a time;
        exit 0 done, 2 host-side turn timeout, 3 client wait timeout,
        4 dead delegate, 5 another session is steering)
    delegate_manager.py read <id>               (latest condensed response)
    delegate_manager.py list [--json]           (recovery path for lost ids)
    delegate_manager.py stop <id>

Registry: ~/.gemini_delegation/delegates/<id>/record.json (atomic JSON), with
liveness via an exclusively held `alive.lock` (a dead host releases the OS lock
automatically — mirror of claude-revolver's park-record lock pattern).

Delegates are librarians, not devs: they return fact-based digests (findings,
file paths, one-line status), never code or architecture decisions.
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gemini_delegate  # noqa: E402  (sentinels, ANSI strip, log root, preambles)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Idle-GC: a persistent delegate that receives no steer for this long retires
# itself ("otherwise probably not going to be used again anyway").
IDLE_GC_SECONDS = int(os.environ.get("DELEGATION_DELEGATE_IDLE_GC", "180"))
# Generous max-age backstop for a persistent delegate.
MAX_AGE_SECONDS = int(os.environ.get("DELEGATION_DELEGATE_MAX_AGE", "7200"))
WARN_FRACTION = 0.75  # soft-threshold fraction of the hard cap
# How long steer waits for a just-spawned host before declaring it dead.
HOST_STARTUP_GRACE = float(os.environ.get("DELEGATION_HOST_STARTUP_GRACE", "15"))

_HOOKS_DIR = Path(__file__).resolve().parent


def delegates_root() -> Path:
    return gemini_delegate._delegation_log_root() / "delegates"


def delegate_dir(delegate_id: str) -> Path:
    return delegates_root() / delegate_id


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def load_record(delegate_id: str) -> dict | None:
    path = delegate_dir(delegate_id) / "record.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_record(record: dict) -> None:
    _atomic_write_json(delegate_dir(record["id"]) / "record.json", record)


class FileLock:
    """Advisory exclusive file lock (msvcrt on Windows, fcntl elsewhere).

    A crashed holder releases the OS-level lock automatically, so "can this
    lock be acquired?" doubles as the delegate-host liveness probe.
    """

    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")  # noqa: SIM115 - held for lock lifetime
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def acquire_with_retry(self, attempts: int = 10, delay: float = 0.1) -> bool:
        """Acquire, retrying briefly: a host's startup acquire can collide with
        a host_is_alive() probe (steer/wait/list poll every 0.5-1s) that holds
        the byte lock for a moment — without retry the host would be declared
        a duplicate and die on arrival."""
        for _ in range(attempts):
            if self.acquire():
                return True
            time.sleep(delay)
        return False

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_UN)
        except OSError:
            pass
        self.handle.close()
        self.handle = None


def host_is_alive(ddir: Path) -> bool:
    """True while the delegate host process holds alive.lock."""
    lock_path = ddir / "alive.lock"
    if not lock_path.exists():
        return False
    probe = FileLock(lock_path)
    if probe.acquire():
        probe.release()
        return False
    return True


def _session_id(args: argparse.Namespace) -> str:
    explicit = getattr(args, "session_id", None)
    if explicit:
        return explicit
    env = os.environ.get("DELEGATION_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID")
    if env:
        return env
    agent_dir = getattr(args, "agent_dir", None)
    if agent_dir:
        context = gemini_delegate._load_caller_session(Path(agent_dir))
        session = str(context.get("session_id", "")).strip()
        if session:
            return session
    return "unknown-session"


def _new_id(prefix: str) -> str:
    return f"{prefix}-{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _spawn_detached(argv: list[str], log_path: Path) -> int:
    """Start a fully detached child; caller does not wait. Returns the pid."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(log_path, "ab")  # noqa: SIM115 - inherited by the child
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "cwd": str(Path.cwd()),
    }
    if os.name == "nt":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(argv, **kwargs)
    log_handle.close()
    return proc.pid


def _write_marker(ddir: Path, name: str, text: str) -> None:
    try:
        (ddir / name).write_text(text + "\n", encoding="utf-8")
    except OSError:
        pass


# ── Tier 1: one-shot async ─────────────────────────────────────────────────────

def cmd_async(args: argparse.Namespace) -> int:
    delegate_id = _new_id("dg")
    ddir = delegate_dir(delegate_id)
    ddir.mkdir(parents=True, exist_ok=True)

    record = {
        "id": delegate_id,
        "mode": "oneshot",
        "status": "spawning",
        "workspace": str(Path.cwd().resolve()),
        "profile": args.profile,
        "model": args.model or "",
        "created_at": time.time(),
        "last_activity": time.time(),
        "turn_count": 0,
        "originating_session": _session_id(args),
        "last_attached_session": _session_id(args),
        "timeout_seconds": args.timeout_seconds,
        "task": args.task,
        "context": args.context,
        "max_lines": args.max_lines,
        "idle_timeout_seconds": args.idle_timeout_seconds,
        "caller": args.caller,
        "agent_dir": args.agent_dir or "",
    }
    save_record(record)

    pid = _spawn_detached(
        [sys.executable, str(_HOOKS_DIR / "delegate_manager.py"), "run-oneshot", delegate_id],
        ddir / "host.log",
    )
    record["pid"] = pid
    record["status"] = "busy"
    save_record(record)

    print(delegate_id)
    print(f"[async] Delegation running detached (pid {pid}).", file=sys.stderr)
    print(f"[async] Result: {ddir / 'result.md'}", file=sys.stderr)
    print(
        f"[async] Wake signal: run 'delegate_manager wait {delegate_id}' in a "
        "backgrounded process; its exit is the completion notification.",
        file=sys.stderr,
    )
    return 0


def cmd_run_oneshot(args: argparse.Namespace) -> int:
    """Internal: executes a one-shot delegation inside the detached process."""
    delegate_id = args.id
    ddir = delegate_dir(delegate_id)
    record = load_record(delegate_id)
    if record is None:
        return 4

    alive = FileLock(ddir / "alive.lock")
    if not alive.acquire_with_retry():
        return 4  # another process already runs this delegation
    record.update(pid=os.getpid(), status="busy", last_activity=time.time())
    save_record(record)

    timeout = int(record.get("timeout_seconds") or 600)
    done_event = threading.Event()

    def _warn_at_soft_threshold():
        if not done_event.wait(timeout * WARN_FRACTION):
            _write_marker(ddir, "warn", f"soft-threshold: {int(timeout * WARN_FRACTION)}s of {timeout}s cap elapsed")

    threading.Thread(target=_warn_at_soft_threshold, daemon=True).start()

    cmd = [
        sys.executable, str(_HOOKS_DIR / "gemini_delegate.py"),
        "--pre-format", "--context", record.get("context") or "General task",
        "--post-validate",
        "--timeout-seconds", str(timeout),
    ]
    if record.get("profile") and record["profile"] != "default":
        cmd += ["--profile", record["profile"]]
    if record.get("model"):
        cmd += ["--models", record["model"]]
    if record.get("max_lines"):
        cmd += ["--max-lines", str(record["max_lines"])]
    if record.get("idle_timeout_seconds"):
        cmd += ["--idle-timeout-seconds", str(record["idle_timeout_seconds"])]
    if record.get("caller") and record["caller"] != "auto":
        cmd += ["--caller", record["caller"]]
    if record.get("agent_dir"):
        cmd += ["--agent-dir", record["agent_dir"]]

    started = time.time()
    status = "done"
    try:
        result = subprocess.run(
            cmd,
            input=record.get("task") or "",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=record.get("workspace") or None,
            timeout=timeout + 120,  # gemini_delegate enforces its own cap; this is a backstop
            check=False,
        )
        answer = (result.stdout or "").strip()
        stderr_tail = (result.stderr or "").strip()[-2000:]
        if result.returncode != 0:
            status = "timeout" if "Timed out" in stderr_tail else "error"
        if not answer:
            answer = "(no answer produced)\n\n--- runner stderr tail ---\n" + stderr_tail
        exit_code = result.returncode
    except subprocess.TimeoutExpired as exc:
        status = "timeout"
        answer = "(hard timeout hit before the runner returned)\n" + ((exc.output or "")[-2000:])
        exit_code = 1

    elapsed = time.time() - started
    status_line = f"status: {status} (exit {exit_code}, {elapsed:.0f}s, profile {record.get('profile')})"
    (ddir / "result.md").write_text(status_line + "\n\n" + answer + "\n", encoding="utf-8")
    done_event.set()

    record.update(status=status, last_activity=time.time(), turn_count=1)
    save_record(record)
    _write_marker(ddir, "done", status_line)
    alive.release()
    return 0 if status == "done" else 1


def cmd_wait(args: argparse.Namespace) -> int:
    """Sacrificial waiter: dumb poll on marker files; exit code is the signal."""
    ddir = delegate_dir(args.id)
    if not ddir.exists():
        print(f"Unknown delegate id: {args.id}", file=sys.stderr)
        return 4
    deadline = time.time() + (args.timeout_seconds or 900)
    warn_reported = (ddir / "warn").exists()
    while time.time() < deadline:
        if (ddir / "done").exists():
            status_line = (ddir / "done").read_text(encoding="utf-8").strip()
            print(status_line)
            result = ddir / "result.md"
            if result.exists():
                print(f"result: {result}")
            return 0
        if not warn_reported and (ddir / "warn").exists():
            print((ddir / "warn").read_text(encoding="utf-8").strip())
            return 2
        record = load_record(args.id)
        past_startup = (
            record is not None
            and time.time() - record.get("created_at", 0) > HOST_STARTUP_GRACE
        )
        if record and record.get("mode") == "oneshot" and past_startup and not host_is_alive(ddir) and not (ddir / "done").exists():
            # Host died without writing its marker — report honestly, don't
            # spin. The startup grace covers the window before the detached
            # child has started Python and acquired alive.lock.
            time.sleep(2)
            if not (ddir / "done").exists() and not host_is_alive(ddir):
                print("delegate host died without a result", file=sys.stderr)
                return 4
        time.sleep(1)
    print(f"waiter timeout after {args.timeout_seconds}s (delegate may still be running)", file=sys.stderr)
    return 3


# ── Tier 2: persistent steerable delegates ─────────────────────────────────────

def cmd_spawn(args: argparse.Namespace) -> int:
    delegate_id = _new_id("dlg")
    ddir = delegate_dir(delegate_id)
    (ddir / "steer").mkdir(parents=True, exist_ok=True)

    if args.profile == "research":
        model = args.model or gemini_delegate.RESEARCH_MODELS[0]
    elif args.profile == "skim":
        model = args.model or gemini_delegate.SKIM_MODELS[0]
    else:
        model = args.model or gemini_delegate.DEFAULT_MODELS[0]

    record = {
        "id": delegate_id,
        "mode": "persistent",
        "status": "spawning",
        "workspace": str(Path(args.workspace or ".").resolve()),
        "profile": args.profile,
        "model": model,
        "created_at": time.time(),
        "last_activity": time.time(),
        "turn_count": 0,
        "originating_session": _session_id(args),
        "last_attached_session": _session_id(args),
        "idle_gc_seconds": args.idle_gc_seconds or IDLE_GC_SECONDS,
        "max_age_seconds": args.max_age_seconds or MAX_AGE_SECONDS,
    }
    save_record(record)

    pid = _spawn_detached(
        [sys.executable, str(_HOOKS_DIR / "delegate_manager.py"), "host", delegate_id],
        ddir / "host.log",
    )
    record["pid"] = pid
    save_record(record)

    print(delegate_id)
    print(f"[spawn] Persistent delegate starting (pid {pid}, model {model}).", file=sys.stderr)
    print(f"[spawn] Workspace pin: {record['workspace']}", file=sys.stderr)
    if args.task:
        args.id = delegate_id
        args.prompt = args.task
        return cmd_steer(args)
    return 0


def _turn_marker(nonce: str) -> str:
    return f"DELEGATION-ANSWER-{nonce}"


def _interactive_prompt(profile: str, nonce: str, task: str) -> str:
    """Build the full injected prompt for one interactive steer turn.

    The TUI hard-wraps and repeatedly redraws the echoed prompt, so any literal
    marker inside the instructions can land alone on a line and false-positive
    detection. Instead the model must ASSEMBLE the marker from parts — the
    joined token can never appear in the echo, so any occurrence is the answer.
    """
    persona = gemini_delegate._PROFILE_PERSONAS.get(
        profile, gemini_delegate._PROFILE_PERSONAS["default"]
    )
    fmt = (
        "IMPORTANT RESPONSE FORMAT: "
        "1. Start immediately with exactly 'working' on its own line. "
        "2. While working, output brief intermediate findings. "
        "3. When finished, output one line consisting of the words DELEGATION "
        f"and ANSWER and the code {nonce}, all three joined by single hyphens "
        "into one token with no spaces, then your complete consolidated answer "
        "on the following lines. TASK: "
    )
    return persona + fmt + task


# Lines that are TUI chrome rather than model output (repaint artifacts).
_CHROME_RE = re.compile(
    r"^[─━═]{4,}"            # box rules
    r"|^\s*>\s*$"            # empty input prompt
    r"|[⠀-⣿]"                # braille spinner frames
    r"|esc to cancel|\? for shortcuts|└ Tip:|▸ Thought for"
    r"|^\s*,?\s*\d+(\.\d+)?k? tokens\s*$"  # streaming token-count stat
    r"|^\s*(Working|Generating)\.{0,3}\s*$"
)


def _clean_answer(body: str) -> str:
    """Drop TUI chrome lines from an extracted answer and squeeze blank runs."""
    lines = [ln for ln in body.splitlines() if not _CHROME_RE.search(ln)]
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return cleaned


def _find_answer(new_output: str, marker: str) -> str | None:
    """Return the chrome-cleaned text after the model's assembled marker, or None.

    The marker is a per-turn nonce token the echo cannot contain (see
    _interactive_prompt) — but the TUI's incremental repaints can shred even
    the model's own marker across paint fragments, with whole chrome lines
    (spinner, tips, input box) landing between the pieces. Filter chrome
    FIRST so the fragments become adjacent, then match with whitespace allowed
    between every character AND each character allowed to repeat — overlapping
    repaints duplicate boundary characters (observed live: "...ANSWER-0ea84" /
    "4a" for nonce 0ea84a). The echoed instructions keep the words separated
    by 'and' and contain no hyphens, so they still cannot match. Take
    everything after the LAST match (earlier ones may be the model restating
    the marker mid-reasoning).
    """
    cleaned = _clean_answer(gemini_delegate._strip_ansi(new_output))
    pattern = re.compile("".join(f"(?:{re.escape(ch)}\\s*)+" for ch in marker))
    matches = list(pattern.finditer(cleaned))
    if not matches:
        return None
    return cleaned[matches[-1].end():].strip() or None


def _host_read_available(pty) -> str:
    chunks = []
    while True:
        chunk = pty.read(blocking=False)
        if not chunk:
            break
        chunks.append(chunk)
    return "".join(chunks)


def cmd_host(args: argparse.Namespace) -> int:
    """Internal: owns the live agy PTY session for a persistent delegate."""
    delegate_id = args.id
    ddir = delegate_dir(delegate_id)
    record = load_record(delegate_id)
    if record is None:
        return 4

    alive = FileLock(ddir / "alive.lock")
    if not alive.acquire_with_retry():
        return 4  # another host already owns this delegate

    if os.name != "nt":
        print("persistent delegates require the Windows winpty path", file=sys.stderr)
        record["status"] = "dead"
        save_record(record)
        return 2

    try:
        import winpty
    except ImportError:
        # This runs detached: mark the record dead so steer/list report a real
        # status instead of an eternal "spawning", and say how to fix it.
        print(
            "pywinpty is required for persistent delegates: py -3 -m pip install --user pywinpty",
            file=sys.stderr,
        )
        record["status"] = "dead"
        save_record(record)
        alive.release()
        return 2

    workspace = record.get("workspace") or str(Path.cwd())
    command = gemini_delegate.resolve_agy_command()
    # Interactive session: spawn INSIDE the workspace so agy enters interactive
    # mode (the one-shot runner deliberately avoids this; here it is the point).
    cmdline = subprocess.list2cmdline(["--add-dir", workspace, "--model", record.get("model") or ""])
    pty = winpty.PTY(220, 50)
    pty.spawn(command, cmdline=cmdline, cwd=workspace)

    record.update(pid=os.getpid(), status="idle", last_activity=time.time())
    save_record(record)

    created = record.get("created_at", time.time())
    idle_gc = int(record.get("idle_gc_seconds") or IDLE_GC_SECONDS)
    max_age = int(record.get("max_age_seconds") or MAX_AGE_SECONDS)
    buf = ""
    trust_confirmed = False
    served = set()

    def _update_record(**fields) -> dict:
        # Merge onto the freshest on-disk record so steer's own writes
        # (e.g. last_attached_session) are not clobbered by a stale copy.
        nonlocal record
        record = load_record(delegate_id) or record
        record.update(fields)
        save_record(record)
        return record

    def _dump_pty_log() -> None:
        # errors="replace": PTY reads can carry lone surrogates that raw utf-8
        # rejects; a failed dump must never take down the host, but say why.
        try:
            (ddir / "pty.log").write_text(
                gemini_delegate._strip_ansi(buf), encoding="utf-8", errors="replace"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[host] pty.log dump failed: {exc}", file=sys.stderr, flush=True)

    def _finish(status: str) -> int:
        _update_record(status=status, last_activity=time.time())
        _dump_pty_log()
        try:
            if pty.isalive():
                os.kill(pty.pid, signal.SIGTERM)
        except OSError:
            pass
        alive.release()
        return 0

    last_chunk_at = time.time()

    def _pump_and_confirm_trust() -> str:
        """Read available PTY output; auto-confirm agy's first-run trust dialog."""
        nonlocal buf, trust_confirmed, last_chunk_at
        chunk = _host_read_available(pty)
        if chunk:
            buf += chunk
            last_chunk_at = time.time()
            if not trust_confirmed and "Do you trust the contents" in buf:
                time.sleep(0.3)
                pty.write("\r")
                trust_confirmed = True
        return chunk

    while True:
        now = time.time()
        _pump_and_confirm_trust()
        if not pty.isalive():
            return _finish("dead")
        if (ddir / "stop.request").exists():
            return _finish("done")

        # Scan for pending steers BEFORE the GC checks so a request arriving
        # at the idle boundary is served, not dropped by a same-tick retirement.
        pending = sorted(
            p for p in (ddir / "steer").glob("*.prompt")
            if p.stem not in served and not (ddir / "steer" / f"{p.stem}.response").exists()
        )
        if not pending:
            if now - created > max_age:
                return _finish("done")
            if now - record.get("last_activity", created) > idle_gc:
                return _finish("done")

        # Injecting a prompt while the TUI is still booting/signing-in (or
        # showing the trust dialog) silently loses it. Quiet-time alone is not
        # enough — sign-in has >1s network stalls — so also require the input
        # footer ("? for shortcuts") to have been painted at least once.
        tui_ready = (
            "? for shortcuts" in gemini_delegate._strip_ansi(buf[-8000:])
            and (trust_confirmed or "Do you trust the contents" not in buf)
            and now - last_chunk_at >= 1.0
        )
        if pending and tui_ready:
            request = pending[0]
            served.add(request.stem)
            _update_record(status="busy", last_activity=time.time())
            prompt = request.read_text(encoding="utf-8").strip()
            nonce = uuid.uuid4().hex[:6]
            marker = _turn_marker(nonce)
            # A newline submits in the interactive TUI — collapse to one line.
            one_line = re.sub(
                r"\s+", " ",
                _interactive_prompt(record.get("profile", "default"), nonce, prompt),
            )
            marker_before = len(buf)
            pty.write(one_line + "\r")
            steer_timeout = time.time() + int(record.get("steer_timeout_seconds") or 600)
            answer = None
            last_output = time.time()
            last_dump = 0.0
            while time.time() < steer_timeout:
                piece = _pump_and_confirm_trust()
                if piece:
                    last_output = time.time()
                if time.time() - last_dump >= 2.0:
                    # Incremental dump so a live steer can be debugged mid-turn.
                    _dump_pty_log()
                    last_dump = time.time()
                candidate = _find_answer(buf[marker_before:], marker)
                if candidate is not None and time.time() - last_output >= 3.0:
                    # Marker seen and the TUI has fully quiesced — mid-repaint
                    # extraction shreds the answer into half-painted lines.
                    answer = candidate
                    break
                if not pty.isalive():
                    break
                if (ddir / "stop.request").exists():
                    break  # honor stop even mid-turn; outer loop finishes up
                time.sleep(0.2)
            if answer is not None:
                status_line = f"status: done (turn {record.get('turn_count', 0) + 1}, model {record.get('model')})"
                body = answer  # already chrome-cleaned by _find_answer
            else:
                status_line = "status: timeout (no final answer sentinel)"
                body = gemini_delegate._strip_ansi(buf[marker_before:]).strip()[-2000:]
            (ddir / "steer" / f"{request.stem}.response").write_text(
                status_line + "\n\n" + body + "\n", encoding="utf-8"
            )
            _dump_pty_log()
            _update_record(
                status="idle",
                last_activity=time.time(),
                turn_count=record.get("turn_count", 0) + 1,
            )
        time.sleep(0.3)


def cmd_steer(args: argparse.Namespace) -> int:
    ddir = delegate_dir(args.id)
    record = load_record(args.id)
    if record is None or record.get("mode") != "persistent":
        print(f"Unknown or non-persistent delegate: {args.id} — run 'list' to recover ids.", file=sys.stderr)
        return 4
    # Give a just-spawned host a moment to come up before declaring it dead.
    deadline = time.time() + HOST_STARTUP_GRACE
    while not host_is_alive(ddir) and time.time() < deadline:
        time.sleep(0.5)
    if not host_is_alive(ddir):
        print(f"Delegate {args.id} is dead/GC'd — re-dispatch instead of steering a corpse.", file=sys.stderr)
        record["status"] = "dead"
        save_record(record)
        return 4

    writer = FileLock(ddir / "steer.lock")
    if not writer.acquire():
        print(f"Delegate {args.id} is being steered by another session (single-writer).", file=sys.stderr)
        return 5
    try:
        client_timeout = args.timeout_seconds or 660
        record["last_attached_session"] = _session_id(args)
        # Bump last_activity so the host's idle-GC can't retire the delegate
        # in the same tick this request lands, and give the host a turn cap
        # slightly inside the client's own wait so its verdict arrives in time.
        record["last_activity"] = time.time()
        record["steer_timeout_seconds"] = max(60, client_timeout - 30)
        save_record(record)
        steer_dir = ddir / "steer"
        steer_dir.mkdir(exist_ok=True)
        index = len(list(steer_dir.glob("*.prompt"))) + 1
        stem = f"{index:04d}"
        (steer_dir / f"{stem}.prompt").write_text(args.prompt, encoding="utf-8")

        response_path = steer_dir / f"{stem}.response"
        deadline = time.time() + client_timeout
        while time.time() < deadline:
            if response_path.exists():
                response = response_path.read_text(encoding="utf-8").strip()
                print(response)
                # A host-side turn timeout is a failed turn, not a success —
                # exit 2 (soft failure) so callers don't have to parse the body.
                return 2 if response.startswith("status: timeout") else 0
            if not host_is_alive(ddir):
                print("delegate host died mid-steer", file=sys.stderr)
                return 4
            time.sleep(1)
        print("steer timed out waiting for a response; 'read' may pick it up later", file=sys.stderr)
        return 3
    finally:
        writer.release()


def cmd_read(args: argparse.Namespace) -> int:
    ddir = delegate_dir(args.id)
    record = load_record(args.id)
    if record is None:
        print(f"Unknown delegate id: {args.id}", file=sys.stderr)
        return 4
    if record.get("mode") == "oneshot":
        result = ddir / "result.md"
        if result.exists():
            print(result.read_text(encoding="utf-8").strip())
            return 0
        print("no result yet", file=sys.stderr)
        return 1
    responses = sorted((ddir / "steer").glob("*.response"))
    if not responses:
        print("no responses yet", file=sys.stderr)
        return 1
    print(responses[-1].read_text(encoding="utf-8").strip())
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    root = delegates_root()
    entries = []
    if root.exists():
        for ddir in sorted(p for p in root.iterdir() if p.is_dir()):
            record = load_record(ddir.name)
            if record is None:
                continue
            alive = host_is_alive(ddir)
            status = record.get("status", "unknown")
            if not alive and status in ("spawning", "idle", "busy"):
                # Host is gone but the record says live — report honestly and persist.
                status = "dead"
                record["status"] = "dead"
                save_record(record)
            age = int(time.time() - record.get("created_at", time.time()))
            idle = int(time.time() - record.get("last_activity", time.time()))
            # Prune: drop long-dead delegates past the max-age backstop.
            if not alive and age > int(record.get("max_age_seconds") or MAX_AGE_SECONDS):
                import shutil
                shutil.rmtree(ddir, ignore_errors=True)
                continue
            entries.append(
                {
                    "id": record["id"],
                    "mode": record.get("mode"),
                    "status": status,
                    "alive": alive,
                    "workspace": record.get("workspace"),
                    "model": record.get("model"),
                    "profile": record.get("profile"),
                    "age_seconds": age,
                    "idle_seconds": idle,
                    "turn_count": record.get("turn_count", 0),
                    "originating_session": record.get("originating_session"),
                    "last_attached_session": record.get("last_attached_session"),
                }
            )
    if args.json:
        print(json.dumps(entries, indent=2))
        return 0
    if not entries:
        print("no delegates")
        return 0
    for entry in entries:
        print(
            "{id}  {mode:<10} {status:<8} turns={turn_count:<3} age={age_seconds}s "
            "idle={idle_seconds}s model={model!r} ws={workspace}".format(**entry)
        )
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    ddir = delegate_dir(args.id)
    record = load_record(args.id)
    if record is None:
        print(f"Unknown delegate id: {args.id}", file=sys.stderr)
        return 4
    _write_marker(ddir, "stop.request", f"requested at {time.strftime('%H:%M:%S')}")
    deadline = time.time() + 15
    while host_is_alive(ddir) and time.time() < deadline:
        time.sleep(0.5)
    if host_is_alive(ddir):
        print("stop requested; host has not exited yet", file=sys.stderr)
        return 1
    record["status"] = "done" if record.get("status") != "dead" else "dead"
    save_record(record)
    print(f"stopped {args.id}")
    return 0


# ── CLI ────────────────────────────────────────────────────────────────────────

def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session-id", help="Originating/attaching session id (else env/.caller-session.json).")
    parser.add_argument("--agent-dir", help="Path to the repo's .gemini-delegation dir.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("async", help="Fire a detached one-shot delegation; prints the delegate id.")
    p.add_argument("task")
    p.add_argument("--context", default="General task")
    p.add_argument("--max-lines", type=int, default=0)
    p.add_argument("--profile", choices=("default", "research", "scout", "skim"), default="default")
    p.add_argument("--model", help="Explicit agy model override.")
    p.add_argument("--timeout-seconds", type=int, default=600)
    p.add_argument("--idle-timeout-seconds", type=int, default=0)
    p.add_argument("--caller", choices=("claude", "codex", "agy", "auto"), default="auto")
    _add_common(p)
    p.set_defaults(handler=cmd_async)

    p = sub.add_parser("run-oneshot", help=argparse.SUPPRESS)
    p.add_argument("id")
    p.set_defaults(handler=cmd_run_oneshot)

    p = sub.add_parser("wait", help="Sacrificial waiter; exits when the delegate finishes/warns/times out.")
    p.add_argument("id")
    p.add_argument("--timeout-seconds", type=int, default=900)
    p.set_defaults(handler=cmd_wait)

    p = sub.add_parser("spawn", help="Spawn a persistent steerable delegate (librarian, read-only).")
    p.add_argument("--task", help="Optional first steering prompt.")
    p.add_argument("--profile", choices=("default", "research", "scout", "skim"), default="default")
    p.add_argument("--model", help="Explicit agy model (default from profile).")
    p.add_argument("--workspace", help="Workspace to pin via --add-dir (default: cwd).")
    p.add_argument("--idle-gc-seconds", type=int, default=0)
    p.add_argument("--max-age-seconds", type=int, default=0)
    p.add_argument("--timeout-seconds", type=int, default=660)
    _add_common(p)
    p.set_defaults(handler=cmd_spawn)

    p = sub.add_parser("host", help=argparse.SUPPRESS)
    p.add_argument("id")
    p.set_defaults(handler=cmd_host)

    p = sub.add_parser("steer", help="Send a steering prompt to a live delegate (single writer).")
    p.add_argument("id")
    p.add_argument("prompt")
    p.add_argument("--timeout-seconds", type=int, default=660)
    _add_common(p)
    p.set_defaults(handler=cmd_steer)

    p = sub.add_parser("read", help="Print the latest condensed response for a delegate.")
    p.add_argument("id")
    p.set_defaults(handler=cmd_read)

    p = sub.add_parser("list", help="Enumerate delegates; prunes and reports dead/GC'd ones.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(handler=cmd_list)

    p = sub.add_parser("stop", help="Stop a delegate.")
    p.add_argument("id")
    p.set_defaults(handler=cmd_stop)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
