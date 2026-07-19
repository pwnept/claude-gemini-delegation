# Agent Delegation

`agent-delegation` is a machine-global delegation tool for Claude, Codex/ChatGPT,
and Gemini/agy. It sends bounded search, research, and large-output work to an
isolated worker so the caller keeps a smaller context.

Three backends are supported: `agy` (Antigravity CLI) is the default. Three
model profiles are available: `default` (Flash cascade), `research` (Pro, agy
only), and `scout` (Flash on agy; Gemma 4 on alternate backends, ideal for
read-heavy file work).

## Safety model

- One delegation level. Every worker receives `AGENT_DELEGATION_DEPTH=1`, and
  another delegation attempt is rejected.
- agy runs with `--sandbox --mode plan`.
- Gemini CLI runs with `--sandbox --approval-mode plan`.
- Permission bypass options are never used.
- Terminal commands are denied unless their token prefix is present in the
  managed global policy or explicitly added by the caller for one run.
- Native agy transcripts and continuation state are never changed or removed.

## Install

From a source checkout:

```powershell
uv tool install --reinstall .
agent-delegation install --force
agent-delegation status
```

The managed home is `~/.agent-delegation/`. User extensions belong in
`~/.agent-delegation/policy.local.json`; setup refreshes `policy.json` without
overwriting the local file. This global managed installation is the default.
The global agy hook allows only the active delegated capability set at depth 1.
For normal agy sessions it auto-allows the same reviewed read-only command set
and forces interactive confirmation for everything else.

The agy print backend remains disabled until its permission hook passes a
reviewed live smoke that allows `rg` and denies `Get-Date`. `gemini-cli` and
`gemini-api` remain available while that validation is unresolved. Do not set
`agy_print_mode_enabled` to true merely to bypass this gate.

## Use

```powershell
agent-delegation run "map the test suite" --profile scout --workspace .
agent-delegation run "run the approved test subset" --allow-command "python -m pytest" --workspace .
agent-delegation async "map all test fixtures" --profile skim --workspace .
agent-delegation wait <delegate-id>
agent-delegation spawn --workspace . --profile scout
agent-delegation steer <delegate-id> "Now inspect the failing fixtures"
agent-delegation read <delegate-id>
agent-delegation stop <delegate-id>
```

The second example grants one exact command prefix for that run. Shells,
redirection, pipelines, compound commands, delegation commands, and destructive
file commands remain permanently denied.

`async` starts a detached one-shot job. `spawn` starts a persistent, single-writer
agy session with sandboxed plan mode, bounded lifetime, append-only PTY logging,
and marker-delimited responses. Both retain native transcript copies and a
manifest under `~/.agent-delegation/` when they finish.

Disable or enable delegation in one Git repository without adding tracked files:

```powershell
agent-delegation disable .
agent-delegation enable .
```

The setting is stored in the repository's local Git config.

## Logs

Each run creates:

```text
~/.agent-delegation/runs/<caller>/<project>/<run-id>/
  exchange.jsonl
  manifest.json
  native/<agy-conversation-id>/transcript_full.jsonl
```

Native JSONL is copied byte for byte and hash-verified after the child exits.
The original remains in agy's native store. The manifest records caller,
workspace, backend, capability additions, exit code, and hashes.

## Optional project-local customization

The old `gemini-delegate` command and per-repository installer remain as
compatibility interfaces. New setup uses the global managed installation and
does not create `.gemini-delegation` or repository hook copies. A future
project-local workflow may be used explicitly when a repository genuinely
needs custom delegation behavior.

## Legacy per-repository compatibility

User-authored content outside the markers is preserved. If only one marker is
present, the installer stops and tells you to fix the mismatched marker block.

## Claude Instruction Migration

On install, the root `CLAUDE.md` is normalized to:

```text
@AGENTS.md
```

Any previous root `CLAUDE.md` instructions are backed up and migrated into
`AGENTS.md`. If `.claude/CLAUDE.md` exists, its non-bridge content is also
migrated into `AGENTS.md`, backed up, and removed.

This keeps Claude Code, Codex, and Antigravity reading the same project-level
delegation policy.

If the target already has user-authored `AGENTS.md` content, the installer
preserves `CLAUDE.md` automatically and only refreshes the managed delegation
block. Use `--preserve-claude-md` only when you explicitly want to skip Claude
instruction migration in a repo without shared `AGENTS.md` content:

```powershell
.\install-delegation.ps1 install --target "F:\SomeRepo" --preserve-claude-md
```

## Expected Delegate Commands

Claude Code and Codex should use the hook installed in the target repo:

```powershell
# Default (Flash cascade, agy backend)
& .gemini-delegation/hooks/delegate_and_log.ps1 "npm ls" "Build analysis" 5

# Research: web search, documentation lookup, security audits
& .gemini-delegation/hooks/delegate_and_log.ps1 "find current deployment docs" "Research" 10 -Profile research

# Scout: file mapping, log parsing, dependency scanning, test discovery (read-only)
& .gemini-delegation/hooks/delegate_and_log.ps1 "list all test files under src/" "Scout" 10 -Profile scout
```

Or use the `/delegate` slash-command in Claude Code, which runs the same
`delegate_and_log.ps1` via `.claude/commands/delegate.md`.

## Delegate Transcript Logs

Each successful delegate call writes one full prompt/output `.txt` transcript
under user-home by default:

```text
~/.gemini_delegation/runs/<caller>/<project-slug>/<session-slug>_gemini_delegation/<turn-id>_<delegation-number>.txt
```

This keeps full AI-use artifacts out of target repos unless that repo chooses
to copy them into its own archive hook. Set `DELEGATION_LOG_ROOT` to override
the root directory, or `DELEGATION_DISABLE_LOGS=1` to disable transcript writes
for tests and special cases.

## Choosing A Backend

Three backends are supported. `agy` is the default, so existing installs and
the examples above keep working unchanged.

| Backend | Selected by | Needs |
|---|---|---|
| `agy` (default) | nothing, or `DELEGATION_BACKEND=agy` | Antigravity CLI installed and signed in |
| `gemini-cli` | `DELEGATION_BACKEND=gemini-cli` | `npm install -g @google/gemini-cli` + `gemini auth login` |
| `gemini-api` | `DELEGATION_BACKEND=gemini-api` | `GEMINI_API_KEY` in `$PROFILE`, no extra installs |

`gemini-api` is the preferred agy fallback. It uses stdlib `urllib` with no
additional dependencies and cascades through five models in order, each with an
independent daily/RPM quota:

```
gemini-3.5-flash → gemini-3-flash → gemini-2.5-flash →
gemini-3.1-flash-lite → gemini-2.5-flash-lite
```

Lite models are last-resort. The pipeline prints a warning when one is selected,
since output quality may be lower. They are acceptable for file search and
grounding tasks. Grounding RPD is generous (1500/day for default, Gemini 2.5,
and Gemini 2 keys).

A 429 on any model marks it cooled-down and immediately retries on the next.
Override with `--models` or `GEMINI_API_MODELS`.

`gemini-cli` is a full agent (file reads, workspace browsing). It defaults to
`gemini-3.5-flash` then cascades through four fallbacks including lite models.
Override with `--models` or `GEMINI_CLI_MODELS`.

`gemini-cli` is launched with `--sandbox --approval-mode plan`. On Windows, the
npm shim is resolved to its Node entry point so task text never passes through
`cmd.exe`. The process tree is killed immediately on the first capacity signal
to prevent the CLI's internal retry loop from wasting extra RPD.

To set up either API backend (same key, same `$PROFILE` line):

```powershell
# Once, in $PROFILE:
$env:GEMINI_API_KEY = "<key from aistudio.google.com>"

# Then per-session or in scripts:
$env:DELEGATION_BACKEND = "gemini-api"   # or "gemini-cli"
& .gemini-delegation/hooks/delegate_and_log.ps1 "npm ls" "Build analysis" 5
```

Capacity errors trigger the same cooldown/fallback behavior across all backends,
tracked in per-backend state files (`gemini_cli_model_state.json`,
`gemini_api_model_state.json`).

## Model Profiles

Three profiles are available via the `-Profile` flag:

| Profile | Models | Best for |
|---|---|---|
| `default` | Flash cascade (3.5 → 3 → 2.5 → lite) | General delegation, code tasks |
| `research` | Pro (agy only), falls back to Flash | Web search, docs, security audits |
| `scout` | Flash on agy; Gemma 4 31B → 26B on alt backends | File mapping, log parsing, dep scanning, test discovery |

The **scout profile** is for tasks that read many files but do not require code
authoring or complex reasoning. On agy (the primary backend) it uses Flash. On
the `gemini-cli` and `gemini-api` alt backends it uses Gemma 4 (1.5K RPD,
unlimited TPM). Keep code writing and architectural decisions on the main Claude
session or the `default` / `research` profiles.

Gemma 4 model IDs used by scout on alt backends (both confirmed working via gemini-cli):
- `gemma-4-31b-it`: full 31B model, primary
- `gemma-4-26b-a4b-it`: 26B MoE with 4B active params, faster fallback

Adding another backend means one `run_<backend>()` function, one
`run_<backend>_backend()` wrapper, one branch in `gemini_delegate.py:main()`,
and an entry in the `BACKENDS` tuple. `run_with_fallback` is shared by all.

## When Agents Should Delegate

Claude Code and Codex should delegate:

- Commands expected to produce more than 500 lines of output.
- `npm ls`, `pip list`, `pip freeze`, and verbose dependency listings.
- `git log` beyond 5 commits or broad history analysis.
- Recursive searches and broad multi-file analysis.
- Security audits and vulnerability scans.
- Documentation lookup or web research.
- File structure mapping, log parsing, and test coverage discovery (`-Profile scout`).

When the current agent is Antigravity or `agy` itself, it should do the work
directly and must not recursively invoke `agy`.

## Global Vs Local

Use local target installs by default. They are more portable and easier for an
AI agent to audit because every managed file lives in the target repo.

Project-local setup is not the default and the installer does not mutate a
global project registry. If you find an old `~/.gemini-delegation-registry.json`,
see `docs/legacy-uninstall-notes.md`.

## Error Handling Philosophy

The installer stops on unexpected complex errors. It tries to describe the
problem in plain text, including paths and marker names, so you can either fix
it yourself or paste the output into an AI agent.

Typical stop conditions:

- Target path does not exist and `--create-target` was not used.
- `AGENTS.md` has mismatched managed markers.
- `.claude/settings.json` is invalid JSON.
- Required source hook templates are missing.
- Offline verification cannot format a delegation prompt.

## Uninstall Behavior

Uninstall removes managed hook files and the managed `AGENTS.md` marker block.
It intentionally leaves migrated project instructions in `AGENTS.md` and leaves
root `CLAUDE.md` as `@AGENTS.md`.

Each uninstall writes a fresh report:

```text
temp/delegation-uninstall-latest.md
```

The previous latest report is deleted first. If uninstall cannot remove
something, paste that report into an AI agent or remove the listed path manually.

## Source Checkout Development

From this repository, use the source hooks directly for broad repo analysis:

The agy backend remains fail-closed unless it is launched through the validated
global policy path. The `gemini-cli` and `gemini-api` backends remain available
for direct source-checkout use.

```powershell
$env:DELEGATION_BACKEND = "gemini-cli"
& hooks/delegate_and_log.ps1 "npm ls" "Build analysis" 5
& hooks/delegate_and_log.ps1 "audit @src/ for SQL injection" "Security" 10 -Profile research
& hooks/delegate_and_log.ps1 "map all test files under src/" "Scout" 10 -Profile scout
```

## Development

Run checks before handing off changes:

```powershell
python -m unittest discover -s tests -v
python -m py_compile src\agent_delegation\*.py src\gemini_delegation\*.py
```
