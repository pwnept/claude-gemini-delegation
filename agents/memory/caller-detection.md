---
name: caller-detection
description: How delegation hooks auto-detect the calling harness and route logs/state
metadata:
  type: project
---

## The problem

Each harness (Claude Code, Codex, Antigravity/agy) has its own home directory where it
stores JSONL session logs. Delegation metrics and cooldown state should sit next to those
logs so archival scripts can find them. But if the model passes the caller ID as a flag
(`-Caller claude`), it can hallucinate the wrong value and corrupt another tool's directory.

## Solution: layered auto-detection, no model input

The calling harness is detected by `hooks/delegation_caller.py::detect_caller()` in this
priority order:

1. **`DELEGATION_CALLER` env token** — set by the installer in each tool's own config.
   We own this variable; vendors cannot rename it. Survives tool updates indefinitely.
   - Claude Code: written to `.claude/settings.json` `"env"` block by `create_claude_settings()`.
   - Codex/agy: must be set manually (see README "Caller detection" section).

2. **Vendor env sniff** — convenience fallback for tools not yet configured with the token:
   - Claude:  `CLAUDECODE=1` OR `CLAUDE_CODE_ENTRYPOINT` set OR `AI_AGENT` starts with `claude-code`
     *(verified from a live Claude Code 2.1.195 session on 2026-06-28)*
   - Codex:   any `CODEX_*` env var OR `AI_AGENT` starts with `codex` *(best-guess — update
     once a real Codex session env is captured)*
   - agy:     any `ANTIGRAVITY*` or `AGY_*` env var *(best-guess — update once captured)*

   Vendor signatures are NOT update-stable. The `AI_AGENT` value embeds a version string
   (`claude-code_2-1-195_agent`) so only the prefix is matched. A vendor rename will silently
   fall through to the in-repo fallback, not misroute.

3. **In-repo fallback** — when caller is unknown, logs go to `.gemini-delegation/metrics/`
   inside the repo. A `README.txt` is written there (idempotent) explaining how to set
   `DELEGATION_CALLER`. The fallback **never routes to another tool's dir** — misrouting is
   architecturally impossible.

## Routing table

| Caller  | Log dir                      |
|---------|------------------------------|
| claude  | `~/.claude/delegation-logs/` |
| codex   | `~/.codex/delegation-logs/`  |
| agy     | `~/.gemini/delegation-logs/` |
| unknown | `.gemini-delegation/metrics/` (in-repo) + README |

## Call instruction (all harnesses)

The AGENTS.md managed block and the `/delegate` command use one identical line for all
harnesses — no `-Caller` flag:

```powershell
& .gemini-delegation/hooks/delegate_and_log.ps1 "<task>" "<context>" 10
```

The `-Caller` param still exists as an optional override for scripts that know their context.

## Path-discovery optimization

`delegate_and_log.ps1` passes `--agent-dir <.gemini-delegation dir>` to both Python hooks,
so they skip the per-call `cwd` tree-walk. The walk remains as a no-arg fallback.
