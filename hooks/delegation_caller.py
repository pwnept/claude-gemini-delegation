#!/usr/bin/env python3
"""
Shared caller-detection and log-dir routing for delegation hooks.

Priority order for detecting the calling harness:
  1. DELEGATION_CALLER env var — set by the installer in each tool's own config.
     Update-proof: we own this variable, vendors can't rename it.
  2. Vendor env sniff — convenience fallback. Claude signature is verified;
     Codex and Antigravity patterns are best-guess and may need updating.
  3. Empty string — unknown caller; resolve_log_dir() falls back to in-repo.

Routing: each known caller maps to its harness home delegation-logs/ directory
so delegation metrics and cooldown state sit next to that tool's JSONL session logs.
Unknown callers always fall back to in-repo; they NEVER land in another tool's dir.
"""

import os
from pathlib import Path

# ── routing table ──────────────────────────────────────────────────────────────
CALLER_LOG_DIRS: dict[str, Path] = {
    "claude": Path.home() / ".claude" / "delegation-logs",
    "codex":  Path.home() / ".codex"  / "delegation-logs",
    "agy":    Path.home() / ".gemini" / "delegation-logs",
}

_VALID_CALLERS = frozenset(CALLER_LOG_DIRS)

# ── detection ──────────────────────────────────────────────────────────────────
def detect_caller() -> str:
    """Return the calling harness ID, or '' if unknown.

    Layer 1 — DELEGATION_CALLER token (we own it; set by installer in each
               tool's config; survives vendor updates).
    Layer 2 — Vendor env sniff (best-effort; may break on vendor updates):
                 Claude:  CLAUDECODE=1  OR  CLAUDE_CODE_ENTRYPOINT set  OR
                          AI_AGENT starts with 'claude-code'
                 Codex:   any CODEX_* env var  OR  AI_AGENT starts with 'codex'
                          (best-guess — update once a real Codex session is captured)
                 Antigravity/agy: any ANTIGRAVITY* or AGY_* env var
                          (best-guess — update once a real agy session is captured)
    Layer 3 — '' (unknown; caller falls back to in-repo metrics dir).
    """
    # Layer 1: token we control
    token = os.environ.get("DELEGATION_CALLER", "").strip().lower()
    if token in _VALID_CALLERS:
        return token

    # Layer 2: vendor env sniff
    ai_agent = os.environ.get("AI_AGENT", "")

    # Claude Code (verified: CLAUDECODE=1, CLAUDE_CODE_ENTRYPOINT, AI_AGENT prefix)
    if (
        os.environ.get("CLAUDECODE") == "1"
        or "CLAUDE_CODE_ENTRYPOINT" in os.environ
        or ai_agent.startswith("claude-code")
    ):
        return "claude"

    # Codex (best-guess; verify with a real Codex session)
    if ai_agent.lower().startswith("codex") or any(
        k.startswith("CODEX_") for k in os.environ
    ):
        return "codex"

    # Antigravity / agy (best-guess; verify with a real agy session)
    if any(k.startswith("ANTIGRAVITY") or k.startswith("AGY_") for k in os.environ):
        return "agy"

    # Layer 3: unknown
    return ""


# ── routing ────────────────────────────────────────────────────────────────────
def resolve_log_dir(caller: str | None, fallback: Path) -> Path:
    """Return the log/state dir for *caller*, or *fallback* if unknown.

    When *caller* is 'auto', None, or '' the function calls detect_caller()
    automatically.  When the fallback is used (caller unknown) it drops a
    README.txt in *fallback* explaining how to fix the detection so future
    sessions route correctly.
    """
    if not caller or caller == "auto":
        caller = detect_caller()

    result = CALLER_LOG_DIRS.get(caller)
    if result is not None:
        return result

    # Unknown caller — use in-repo fallback and leave a breadcrumb.
    _write_fallback_readme(fallback)
    return fallback


def _write_fallback_readme(directory: Path) -> None:
    """Idempotently write a README.txt in the fallback metrics dir."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        readme = directory / "README.txt"
        if not readme.exists():
            readme.write_text(
                "Delegation caller could not be detected automatically.\n"
                "\n"
                "Metrics and cooldown state are being written here (in-repo fallback)\n"
                "instead of your harness home (e.g. ~/.claude/delegation-logs/).\n"
                "\n"
                "To fix this, add DELEGATION_CALLER to your harness config:\n"
                "\n"
                "  Claude Code — add to .claude/settings.json:\n"
                '    { "env": { "DELEGATION_CALLER": "claude" } }\n'
                "\n"
                "  Codex — add to your Codex env config:\n"
                '    DELEGATION_CALLER=codex\n'
                "\n"
                "  Antigravity / agy — add to your agy env config:\n"
                '    DELEGATION_CALLER=agy\n'
                "\n"
                "Valid values: claude, codex, agy\n",
                encoding="utf-8",
            )
    except OSError:
        pass  # Never crash a delegation run over a README write failure
