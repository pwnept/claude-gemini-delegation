# Claude Gemini Delegation

Local-first delegation hooks for Claude Code and Codex. Routes broad, high-output,
or research-heavy work to a Gemini backend so the main session stays lean.

Three backends are supported — `agy` (Antigravity CLI) is the default. Three
model profiles are available: `default` (Flash cascade), `research` (Pro, agy
only), and `scout` (Flash on agy; Gemma 4 on alt backends — ideal for read-heavy file work).

## Intended Workflow

Clone this repository, inspect help, then install into a target project:

```powershell
git clone https://github.com/carlosduplar/claude-gemini-delegation.git
cd claude-gemini-delegation
.\install-delegation.ps1 help
.\install-delegation.ps1 install --target "C:\path\to\target-repo"
```

Re-running `install` on an already-installed repo is safe — it refreshes hook files and
updates the AGENTS.md delegation block without touching user-authored content. Pass
`--no-update` to error instead if you want to guard against accidental re-installs.

Verify an existing target install:

```powershell
.\install-delegation.ps1 verify --target "C:\path\to\target-repo"
```

Uninstall managed delegation files:

```powershell
.\install-delegation.ps1 uninstall --target "C:\path\to\target-repo"
```

The default install is local to the target repo. That is intentional: if the
target repo is cloned on a new computer, the project still contains its
delegation instructions and hook files. External executables such as Python and
`agy` still need to exist on that computer.

## Requirements

- PowerShell 7+ preferred for the installer and wrappers.
- Python 3.8+.
- **`agy` backend (default):** Antigravity CLI `agy` installed and signed in.
  Windows: `pywinpty` is recommended for reliable output capture:
  ```powershell
  py -3 -m pip install --user pywinpty
  ```
- **`gemini-cli` backend:** Node.js 18+ and `npm install -g @google/gemini-cli`.
  Authenticate once interactively with `gemini auth login` (OAuth, persists
  across sessions). A `GEMINI_API_KEY` in `$PROFILE` also works for non-browser
  environments.
- **`gemini-api` backend:** A free API key from
  https://aistudio.google.com/apikey added to `$PROFILE`:
  ```powershell
  $env:GEMINI_API_KEY = "your-key"
  ```
  No extra installs — uses Python stdlib `urllib` only.

The installer itself uses only Python standard library modules.

## Principal Features

- Local target install with no hidden project registry.
- `AGENTS.md` managed delegation section with stable bracketed markers.
- Root `CLAUDE.md` migration to `@AGENTS.md`.
- `.claude/CLAUDE.md` migration into `AGENTS.md` when present.
- Claude Code PreToolUse guard for high-output commands.
- Antigravity workspace rule to prevent recursive `agy` delegation.
- Offline install verification that does not call remote models.
- Uninstall report written to `temp/delegation-uninstall-latest.md`.

## Installed Target Scope

The installer creates or updates these paths in the target project:

```text
AGENTS.md
CLAUDE.md
.gemini-delegation/
  delegation_config.json
  manifest.json             <- tracks owned files outside this dir
  hooks/                    <- all hook implementations live here
  agents/                   <- bundled agent workflows (dave, archive, memory)
.claude/
  agents/
    dave.md                 <- Dave sub-agent, discoverable by Claude Code
  commands/delegate.md      <- /delegate slash-command
  settings.json             <- PreToolUse guard wired here
.agents/
  rules/delegation.md       <- Antigravity workspace rule
```

## Managed Markers

Only content between these exact `AGENTS.md` markers is managed by the installer:

```text
> [claude-gemini-delegation:agents-begin]
...
> [claude-gemini-delegation:agents-end]
```

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
| `gemini-api` | `DELEGATION_BACKEND=gemini-api` | `GEMINI_API_KEY` in `$PROFILE` — no extra installs |

`gemini-api` is the preferred agy fallback. It uses stdlib `urllib` with no
additional dependencies and cascades through five models in order, each with an
independent daily/RPM quota:

```
gemini-3.5-flash → gemini-3-flash → gemini-2.5-flash →
gemini-3.1-flash-lite → gemini-2.5-flash-lite
```

Lite models are last-resort. The pipeline prints a warning when one is selected,
since output quality may be lower. They are acceptable for file search and
grounding tasks — grounding RPD is generous (1500/day for default, Gemini 2.5,
and Gemini 2 keys).

A 429 on any model marks it cooled-down and immediately retries on the next.
Override with `--models` or `GEMINI_API_MODELS`.

`gemini-cli` is a full agent (file reads, workspace browsing). It defaults to
`gemini-3.5-flash` then cascades through four fallbacks including lite models.
Override with `--models` or `GEMINI_CLI_MODELS`.

On Windows, `gemini-cli` is launched with `--skip-trust` and `--yolo` so it
runs headless without blocking on interactive prompts. The process tree is killed
immediately on the first capacity signal to prevent the CLI's internal retry loop
from wasting extra RPD.

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
- `gemma-4-31b-it` — full 31B model, primary
- `gemma-4-26b-a4b-it` — 26B MoE with 4B active params, faster fallback

Adding another backend means one `run_<backend>()` function, one
`run_<backend>_backend()` wrapper, one branch in `gemini_delegate.py:main()`,
and an entry in the `BACKENDS` tuple — `run_with_fallback` is shared by all.

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

Global setup is not the default and the installer does not mutate a global
project registry. If you find an old `~/.gemini-delegation-registry.json`,
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

```powershell
& hooks/delegate_and_log.ps1 "npm ls" "Build analysis" 5
& hooks/delegate_and_log.ps1 "audit @src/ for SQL injection" "Security" 10 -Profile research
& hooks/delegate_and_log.ps1 "map all test files under src/" "Scout" 10 -Profile scout
```

Run checks before handing off changes:

```powershell
python -m unittest discover -s tests -v
$files = @(Get-ChildItem src/gemini_delegation -Filter *.py) + @(Get-ChildItem hooks -Filter *.py)
python -m py_compile @($files.FullName)
```

## Repository Layout

```text
install-delegation.ps1       PowerShell entry point
src/gemini_delegation/       Python installer and CLI package
hooks/                       Source hook templates copied into targets
agents/                      Optional bundled agent workflows
tests/                       Unit tests
docs/legacy-uninstall-notes.md
```
