# Claude Gemini Delegation

Local-first delegation hooks for Claude Code and Codex. The backend is `agy`
(Antigravity CLI), which can route broad, high-output, or research-heavy work
away from the main Claude/Codex session while preserving a concise handoff.

## Intended Workflow

Clone this repository, inspect help, then install into a target project:

```powershell
git clone https://github.com/carlosduplar/claude-gemini-delegation.git
cd claude-gemini-delegation
.\install-delegation.ps1 help
.\install-delegation.ps1 install --target "C:\path\to\target-repo"
```

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
- Antigravity CLI `agy` installed and signed in.
- Windows only: `pywinpty` is recommended for reliable `agy.exe` output capture:

```powershell
py -3 -m pip install --user pywinpty
```

The installer itself uses only Python standard library modules. `pywinpty` is an
optional runtime dependency for Windows `agy` capture.

## Principal Features

- Local target install with no hidden project registry.
- Claude Code and Codex hook shims that share one local backend.
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
  hooks/
.claude/
  hooks/
  settings.json
.codex/
  hooks/
.agents/
  rules/delegation.md
agents/
  code-review-agent-dave/
```

`.gemini-delegation/hooks/` contains the copied implementation. `.claude/hooks/`
and `.codex/hooks/` contain lightweight shims that call the shared local backend.

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

## Expected Delegate Commands

Claude Code should use:

```powershell
& .claude/hooks/delegate_and_log.ps1 "npm ls" "Build analysis" 5
```

Codex may use:

```powershell
& .codex/hooks/delegate_and_log.ps1 "npm ls" "Build analysis" 5
```

For research-heavy work:

```powershell
& .claude/hooks/delegate_and_log.ps1 "find current deployment docs" "Research" 10 -Profile research
```

The lower-level two-step flow is:

```powershell
$prompt = & .claude/hooks/delegate.ps1 "npm ls" "Build analysis" 5
$prompt | py -3 .gemini-delegation/hooks/gemini_delegate.py
```

## When Agents Should Delegate

Claude Code and Codex should delegate:

- Commands expected to produce more than 500 lines of output.
- `npm ls`, `pip list`, `pip freeze`, and verbose dependency listings.
- `git log` beyond 5 commits or broad history analysis.
- Recursive searches and broad multi-file analysis.
- Security audits and vulnerability scans.
- Documentation lookup or web research.

When the current agent is Antigravity or `agy` itself, it should do the work
directly and must not recursively invoke `agy`.

## Global Vs Local

Use local target installs by default. They are more portable and easier for an
AI agent to audit because every managed file lives in the target repo.

Global setup is not the default and the new installer does not mutate a global
project registry. If you find an old `~/.gemini-delegation-registry.json`, see
`docs/legacy-uninstall-notes.md`.

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
