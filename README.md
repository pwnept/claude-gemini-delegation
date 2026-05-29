# Claude Code + Gemini Delegation

**Preserve 50-70% of your Claude Code token quota** by delegating high-cost operations to Gemini CLI.

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.6+-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](#)

## The Problem

Claude Code Pro users hit a hard wall:
- **~19,000 tokens per 5-hour window**
- **Token quota exhausted = can't work until reset**
- Most developers run out in 2-3 hours on complex projects

**Common scenario:** You spend 2,000 tokens reading `npm ls` output, leaving only 17,000 for actual coding. By afternoon, you're blocked.

## The Solution

Delegate token-heavy operations to Gemini CLI (generous free tier) while Claude handles high-value reasoning.

**Simple math:**
- Reading 2,000 lines yourself: **2,000 tokens**
- Delegating to Gemini: **~150 tokens**
- **Savings: 92% per operation**

---

## Quick Start

### Prerequisites

- **Python 3.6+** (no additional packages required)
- **Claude Code** installed
- **Gemini CLI:** `npm install -g @google/gemini-cli`

### Installation

First, clone the repository to your machine:
```bash
git clone https://github.com/carlosduplar/claude-gemini-delegation.git
cd claude-gemini-delegation
```

#### Option A: Windows Interactive Installer

From this repository, run the PowerShell installer. It requests elevation,
asks for the project directory when one is not provided, installs or updates
all Claude and Codex delegation files, and verifies the result:

```powershell
.\install-delegation.ps1
```

You can also pass the project directory directly:

```powershell
.\install-delegation.ps1 -ProjectDir "C:\path\to\your\project"
```

#### Option B: Cross-Platform Target Install

From this repository, install into any project with `--target`:

```bash
python3 setup.py --target /path/to/your/project
```

On Windows without the PowerShell helper:

```powershell
py -3 setup.py --target "C:\path\to\your\project"
```

To install into the current working directory, run:

```bash
python3 setup.py
```

The default installer enables only Gemini CLI, even if other supported CLIs are installed. Enable extra CLIs explicitly when you want them:
```bash
python3 setup.py --enable-cli aider
python3 setup.py --enable-cli copilot
python3 setup.py --all-clis
```

On Windows, generated examples use `gemini_delegate.py`, which calls
`gemini.cmd` and falls back across the stable Gemini 2.5 Flash, Flash Lite,
and Pro model pools when Gemini reports capacity or 429 errors. Windows
wrappers resolve Python 3 explicitly (`py -3`, then `python3`, then a verified
Python 3 `python`) so machines with Python 2 on `PATH` do not silently break.

#### Option C: Repeat for Another Project

Run the installer again with a different target. It updates managed sections
in place and backs up existing `AGENTS.md`, `CLAUDE.md`, and `.claude/CLAUDE.md`
before changing them.

```powershell
.\install-delegation.ps1 -ProjectDir "C:\path\to\another\project"
```

```bash
python3 setup.py --target /path/to/another/project
```

**Important: Always restart Claude Code after changing configuration files.**

---

## What Gets Installed

### Interactive Installer (`setup.py`)

The installer configures:

1. **Root CLAUDE.md** - Contains only `@AGENTS.md`
2. **AGENTS.md** - Preserved project rules plus the managed delegation section
3. **.claude/CLAUDE.md** - Generated Claude Code delegation reference
4. **.claude/hooks** - Claude Code wrapper hooks
5. **.claude/settings.json** - Claude Code PreToolUse guard for known high-output Bash commands
6. **.Codex/hooks** - Codex wrapper hooks
7. **delegation_config.json** - Enabled CLI configuration in both hook roots

### Optional: Delegation Hooks

If you want automated prompt formatting:

```bash
python3 setup_hooks.py

# Installs cross-platform hooks to .claude/hooks/ and .Codex/hooks/:
# - pre_delegate.py (prompt formatter)
# - post_delegate.py (response validator)
# - analyze_metrics.py (usage analyzer)
# - gemini_delegate.py (Gemini model fallback runner)
# - delegate_and_log.ps1 (PowerShell full pipeline with metrics)
# - delegation_guard.py / .ps1 (Claude Code PreToolUse guard)
```

**Note:** The generated instructions are the main routing control. The Claude
Code PreToolUse guard is a backstop for obvious high-output Bash commands; it
does not replace proactive delegation for broad analysis, docs lookup, or
multi-file reading.

---

## How It Works

### Delegation Rules

Your `AGENTS.md` configures strict rules for when Claude or Codex MUST delegate:

**Forbidden for delegation:**
- Do not use Claude subagents for token-heavy delegation work
- Do not replace Gemini delegation with Claude Task/subagent calls
- Use Claude subagents only when the user explicitly asks for Claude subagents

**BANNED Operations (Always Delegate):**
- Commands producing >500 lines of output
- `npm ls`, `pip list`, `git log` (>5 commits)
- `find`, `grep -r` (recursive searches)
- Reading 3+ new files for analysis
- Security audits and scans

**Decision Tree:**
```
1. >500 lines of output? -> DELEGATE
2. 3+ new files to read? -> DELEGATE
3. Banned command? -> DELEGATE
4. Security/audit task? -> DELEGATE
5. Already in context? -> Handle directly
```

### Example: Before vs After

**WITHOUT Delegation:**
```
User: "Check npm dependencies"
Claude: Let me read npm ls output...
[Reads 1,847 lines = 2,000 tokens]
Total cost: 2,000 tokens
```

**WITH Delegation:**
```
User: "Check npm dependencies"
Claude: I'll delegate this to preserve your quota:

PROMPT=$(python3 .claude/hooks/pre_delegate.py "npm ls" "Build analysis" 8)
gemini --model gemini-2.5-flash -p "$PROMPT"

[Gemini reads 1,847 lines, returns 150-token summary]
Total cost: 150 tokens (92% savings!)
```

---

## What Gets Delegated

### Always Delegate (Token Savings: 80-95%)

- **Shell commands with verbose output**
  - `npm ls`, `pip freeze`, `git log`
  - `find`, `grep -r`, directory searches
  - Build logs, test outputs
  
- **Multi-file analysis**
  - Security audits across codebase
  - Performance analysis of 5+ files
  - Dependency vulnerability scans

- **Web search & documentation**
  - Current framework documentation
  - API reference lookups
  - Stack Overflow searches

### Never Delegate (Claude Handles)

- Single-file edits already in context
- Architectural decisions (no new data needed)
- Code generation from scratch
- Quick clarifications (<50 tokens)

---

## Configuration Options

### Option 1: Automated Setup (Recommended)

```bash
python3 setup.py

# Interactive installer:
# - Detects installed CLIs
# - Enables Gemini by default; extra CLIs require --enable-cli or --all-clis
# - Installs or updates AGENTS.md, CLAUDE.md, .claude, and .Codex hook roots
```

### Option 2: Manual Setup (Minimal)

```bash
# Prefer setup.py --target. Manual installs must include AGENTS.md plus
# the hook directories used by your agent workflow.
```

### Option 3: Hooks Only

```bash
# Install delegation hooks for automation
python3 setup_hooks.py

# Hooks provide:
# - Automatic prompt formatting
# - Response validation
# - Usage metrics tracking
# - Optional Claude Code Bash guard enforcement
```

---

## Platform Support

### Cross-Platform Compatibility

| Platform | Support | Notes |
|----------|---------|-------|
| **Linux** | Full | Native bash support |
| **macOS** | Full | Native bash support |
| **Windows** | Full | PowerShell, CMD, and Git Bash supported |

### Python Version Requirement

- **Minimum:** Python 3.6
- **Recommended:** Python 3.8+
- **No additional packages required** (stdlib only)

---

## Advanced Usage

### Updating Token Budget

Keep Claude aware of token pressure by updating CLAUDE.md:

```markdown
# In your .claude/CLAUDE.md
**Budget: 19K tokens per 5hr | Remaining: 14,200**
**Status: WARNING (below 15K)**
```

Update this periodically during your session. When status is WARNING, delegation becomes more aggressive.

### Manual Delegation

When Claude doesn't auto-delegate, you can explicitly request it:

```
User: "Use Gemini to scan for security issues"
```

Claude will comply with explicit delegation requests.

### Delegation with Context

For better results, provide context in your requests:

```
User: "We're deploying tomorrow. Scan @src/ for hardcoded credentials and API keys. Use Gemini."
```

### PowerShell Full Pipeline

On Windows, use `delegate_and_log.ps1` when you want prompt formatting,
Gemini fallback, response validation, and metrics in one command:

```powershell
.claude/hooks/delegate_and_log.ps1 "npm ls" "Build analysis" 5
.claude/hooks/delegate_and_log.ps1 "find current docs for Next.js deployment limits" "Research task" 10 -Profile research
```

### Weekly Metrics (If Hooks Installed)

Track your delegation effectiveness:

```bash
python3 .claude/hooks/analyze_metrics.py

# Expected output:
# Delegation rate: 73%
# Average response: 180 tokens
# Token savings: 8,400 tokens this week
```

---

## Troubleshooting

### Claude Not Delegating

**Problem:** Claude executes commands directly instead of delegating.

**Solutions:**

1. **Verify CLAUDE.md location:**
   ```bash
   # Check project-specific
   ls .claude/CLAUDE.md
   
   # Check global
   ls ~/.claude/CLAUDE.md
   ```

2. **Restart Claude Code:**
   ```bash
   # Claude reads CLAUDE.md on startup
   exit
   claude
   ```

3. **Clear context:**
   ```
   # Inside Claude Code session
   /clear
   ```

4. **Be explicit:**
   ```
   User: "Use Gemini to check dependencies"
   ```

### Gemini Not Installed

**Problem:** Claude tries to delegate but Gemini isn't available.

**Solution:**
```bash
# Install Gemini CLI
npm install -g @google/gemini-cli

# Verify installation
gemini --version

# Set API key
export GEMINI_API_KEY="your-key-here"
# Add to ~/.bashrc or ~/.zshrc for persistence
```

### Windows Uses Python 2

**Problem:** `python` resolves to Python 2 and hook scripts fail with syntax errors.

**Solution:** Re-run the installer so wrappers use the Python 3 resolver, or call
Python scripts with `py -3` manually:

```powershell
py -3 .claude/hooks/pre_delegate.py "npm ls" "Build analysis" 5
```

### Bridge Not Loading

**Problem:** Claude reads root `CLAUDE.md` but delegation rules do not apply.

**Solution:**
```bash
# Root CLAUDE.md should contain only the AGENTS bridge:
cat CLAUDE.md

# Expected:
# @AGENTS.md

# Re-run setup if needed:
python3 setup.py --target /path/to/your/project
```

---

## Expected Results

### Token Savings by Usage Pattern

| Usage Pattern | Delegation Rate | Token Savings |
|---------------|-----------------|---------------|
| **Passive** (rely on auto-delegation) | 30-40% | 10-20% |
| **Active** (explicit requests) | 60-70% | 40-55% |
| **Power User** (maintain budget + hooks) | 80-90% | 60-75% |

---

## Project Structure
```
claude-gemini-delegation/
|-- hooks/                     # Hook templates copied into target projects
|   |-- pre_delegate.py
|   |-- post_delegate.py
|   |-- analyze_metrics.py
|   |-- gemini_delegate.py
|   |-- delegation_guard.py
|   |-- delegation_guard.ps1
|   `-- delegate_and_log.ps1
|-- tests/
|   |-- test_install.py
|   `-- regression/
|-- install-delegation.ps1     # Elevated Windows target installer
|-- setup.py                   # Cross-platform target installer
|-- setup_hooks.py             # Hooks-only installer
|-- install.py                 # Shared installer helpers
|-- AGENTS.md
|-- LICENSE
`-- README.md
```

**Last Updated**: May 29, 2026
