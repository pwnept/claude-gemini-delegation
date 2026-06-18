# Codex Configuration

## Enabled Delegation CLIs

- **agy (Antigravity)**: Google's Gemini models via agy CLI

## Delegation Presets

- **security_audit**: Routes to `agy`
  Pattern: `(security|vulnerability|audit|xss|sql injection|csrf)`
- **web_search**: Routes to `agy`
  Pattern: `(search|documentation|lookup|find.*docs)`
- **code_analysis**: Routes to `agy`
  Pattern: `(analyze|review|inspect).*code`

## Quick Delegation

Use the wrapper scripts for easy delegation. Claude Code uses `.claude/hooks`;
Codex workflows can use the mirrored `.Codex/hooks` path.

**Windows (PowerShell):**
```powershell
$prompt = & .claude/hooks/delegate.ps1 "npm ls" "Build analysis"
$prompt | python3 .claude/hooks/gemini_delegate.py

# Full pipeline with validation/metrics:
.claude/hooks/delegate_and_log.ps1 "npm ls" "Build analysis" 5
```

**Windows (CMD):**
```cmd
FOR /F "delims=" %i IN ('.claude\hooks\delegate.bat "npm ls" "Build analysis"') DO SET PROMPT=%i
echo %PROMPT% | python .claude\hooks\gemini_delegate.py
```

## Delegation Workflow

1. **Identify task type** - Security? Git ops? Analysis?
2. **Check presets** - Is there a matching preset?
3. **Use delegation hook** - Let the hook format the prompt
4. **Execute with agy** - Use `gemini_delegate.py`, not direct CLI calls
5. **Validate response** - Check quality with post-delegate hook

`.claude/settings.json` registers a PreToolUse guard for known high-output Bash
commands. Treat it as enforcement for obvious misses, not a substitute for
proactive delegation on broad analysis and research tasks.

## Routing Examples

### Security Audit
```powershell
$prompt = & .claude/hooks/delegate.ps1 "scan auth.py for vulnerabilities" "Pre-deploy security check"
$prompt | python3 .claude/hooks/gemini_delegate.py
```

### Git Operations
```powershell
$prompt = & .claude/hooks/delegate.ps1 "git log --oneline --since=1.week" "Finding bug introduction"
$prompt | python3 .claude/hooks/gemini_delegate.py
```

### Code Analysis
```powershell
$prompt = & .claude/hooks/delegate.ps1 "analyze @src/ for performance issues" "Optimization task"
$prompt | python3 .claude/hooks/gemini_delegate.py
```

## Subagent Policy

Available Claude Code subagent types and their status:

| Subagent | Cost | Status | Rule |
|----------|------|--------|------|
| `Plan` | Low | **Allowed** | Design-only; no file reads or web calls |
| `statusline-setup` | Very low | **Allowed** | Single-purpose config; fully bounded |
| `claude-code-guide` | Medium | Allowed | Claude Code / API questions; may use WebFetch |
| `Explore` | High | **Banned** | Many file reads/greps — delegate to agy instead |
| `general-purpose` | High | **Banned** | Uses WebSearch/WebFetch — use agy `--profile research` |
| `claude` | Unpredictable | **Banned** | Catch-all; use agy for broad tasks |

For delegation tasks, banned operations, or large-output work: use the agy hooks, not Claude subagents.

## Weekly Maintenance

```bash
# Analyze delegation metrics
python3 .claude/hooks/analyze_metrics.py

# Windows:
py -3 .claude/hooks/analyze_metrics.py

# Review routing effectiveness
# Update presets if needed
```

## Configuration

To reconfigure delegation preferences, run the setup wizard again manually.

This will let you enable/disable CLIs.

For Windows target installs, use:

```powershell
.\install-delegation.ps1
```
