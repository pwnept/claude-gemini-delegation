# Repository Instructions

- Run `python -m unittest discover -s tests -v` after changing Python or hook code.
- Run `python -m py_compile install.py setup.py setup_hooks.py` plus the Python
  files under `hooks/` before handing off installer changes.
- Preserve user-authored instructions outside the managed markers below.

## Source-checkout delegation

This repository contains the hook templates themselves. Before installing into
the checkout, use the source scripts directly:

```powershell
# Full pipeline (recommended)
& hooks/delegate_and_log.ps1 "npm ls" "Build analysis" 5

# Two-step: format then run
$prompt = & hooks/delegate.ps1 "npm ls" "Build analysis" 5
$prompt | python3 hooks/gemini_delegate.py

# Research profile for analysis or docs
& hooks/delegate_and_log.ps1 "audit @src/ for SQL injection" "Security" 10 -Profile research
```

Installed target projects use `.claude/hooks` for Claude Code and
`.codex/hooks` for Codex.

> [claude-gemini-delegation:agents-begin]
## Antigravity Delegation

The delegation backend is `agy` (Antigravity CLI).

### Always delegate from Claude Code or Codex

- Commands expected to produce more than 500 lines of output
- `npm ls`, `pip list`, `pip freeze`, and verbose dependency listings
- `git log` beyond 5 commits or broad git history analysis
- Recursive searches and broad multi-file analysis
- Security audits and vulnerability scans
- Documentation lookup or web research

Use `hooks/gemini_delegate.py --profile research` for research-heavy work.
Validate delegated output before acting on it.

When the current agent is Antigravity or agy itself, do the work directly.
Do not recursively invoke agy from inside an Antigravity agent session.

### Claude Code subagents

Do not use broad or exploratory Claude subagents for work that matches the
delegation rules. Use agy instead unless the user explicitly requests a Claude
subagent.
> [claude-gemini-delegation:agents-end]
