# claude-gemini-delegation

## Editing rules

**All changes must follow this order:**

1. Edit the source file in this repository (`hooks/`, `src/gemini_delegation/`, etc.)
2. Run the installer against every target project that needs the update:
   ```powershell
   pwsh -NoProfile -ExecutionPolicy Bypass -File install-delegation.ps1 update --target "F:\EDFN"
   ```
3. Never edit files inside a target project's `.gemini-delegation/` directly — they will be overwritten on the next update.

## Repository layout

| Path | Purpose |
|---|---|
| `hooks/` | Source hook scripts copied verbatim to `<target>/.gemini-delegation/hooks/` |
| `src/gemini_delegation/installer.py` | Installer logic — controls which files are copied, how settings are written, and how codex/claude configs are managed |
| `install-delegation.ps1` | Entry point for install / update / uninstall |
| `agents/` | Bundled agent definitions copied to `<target>/.gemini-delegation/agents/` |

## Installer contract

- `--version` output must include the git commit hash
- `install` is first-time only — fails if delegation is already present
- `update` is the refresh command — runs uninstall then install; safe to run repeatedly
- `uninstall` removes only managed files and markers; never touches user content
- Global configs (`~/.claude/settings.json`) must never be broken or duplicated by an install
- A manifest at `.gemini-delegation/manifest.json` tracks every owned file outside `.gemini-delegation/`; uninstall reads it so relocated files are cleaned up automatically
