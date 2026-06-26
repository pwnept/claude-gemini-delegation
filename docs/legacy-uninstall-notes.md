# Legacy Uninstall Notes

This project used to keep several installer surfaces alive at the same time:

- `setup.py`
- `install.py`
- `setup_hooks.py`
- global installation registry updates
- direct hook copies into `.claude/hooks` and `.codex/hooks`

The intended installer is now local-first:

```powershell
.\install-delegation.ps1 install --target "C:\path\to\repo"
```

## What The New Uninstall Removes

`uninstall --target <path>` removes managed local delegation files:

- `.gemini-delegation/`
- `.claude/commands/delegate.md`
- `.agents/rules/delegation.md`
- the managed `[claude-gemini-delegation:agents-begin]` block in `AGENTS.md`
- legacy direct-copy hook files in `.claude/hooks/` (if present from older installs)

It writes a fresh report to:

```text
temp/delegation-uninstall-latest.md
```

The previous latest report is deleted first so the file always describes the
most recent uninstall attempt.

## What It Intentionally Leaves

The uninstall does not try to reconstruct older `CLAUDE.md` layouts. During
install, project Claude instructions are migrated into `AGENTS.md`, and root
`CLAUDE.md` becomes:

```text
@AGENTS.md
```

That bridge is usually still useful after delegation is removed because it keeps
Claude Code reading the project instructions. If a project wants to leave the
bridge model, restore a backed-up `CLAUDE.md.bak.*` manually.

## Old Registry Cleanup

Older versions could write:

```text
~/.gemini-delegation-registry.json
```

The new installer does not use that registry. If it exists, it is safe to
inspect and remove manually after confirming no old workflow still depends on
`setup.py --update-all`.

## If Cleanup Fails

The installer stops on complex errors and prints descriptive output. Paste that
output and `temp/delegation-uninstall-latest.md` into an AI agent, or fix the
specific missing file, bad JSON, or mismatched marker block manually.
