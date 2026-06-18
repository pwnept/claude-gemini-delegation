# Claude Code Configuration

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

Use the wrapper scripts for easy delegation:

**Windows (PowerShell):**
```powershell
$prompt = & .claude/hooks/delegate.ps1 "npm ls" "Build analysis"
$prompt | python3 .claude/hooks/gemini_delegate.py
```

**Windows (CMD):**
```cmd
FOR /F "delims=" %i IN ('.claude\hooks\delegate.bat "npm ls" "Build analysis"') DO SET PROMPT=%i
echo %PROMPT% | python .claude\hooks\gemini_delegate.py
```

## Subagent Policy

Do **not** use Claude subagents for delegation work. Subagents spend Claude tokens and defeat this configuration's token-saving purpose.

When a task matches any delegation preset, banned operation, or large-output condition, use the local hooks and agy instead of spawning a Claude subagent. Only use Claude subagents when the user explicitly asks for Claude subagents by name.

## Always Delegate To agy

- Commands expected to produce more than 500 lines of output
- `npm ls`, `pip list`, `pip freeze`, and verbose dependency listings
- `git log` beyond 5 commits or broad git history analysis
- Recursive searches such as `find`, `grep -r`, or repository-wide scans
- Reading or analyzing 3 or more new files
- Security audits, vulnerability scans, XSS/SQL injection/CSRF checks
- Documentation lookup or web search. Use `gemini_delegate.py --profile research` so research profile is tried first.
- Broad codebase analysis, performance review, or inspection tasks

## Delegation Workflow

1. **Identify task type** - Security? Git ops? Analysis?
2. **Check presets** - Is there a matching preset?
3. **Use delegation hook** - Let the hook format the prompt
4. **Execute with agy** - Use `gemini_delegate.py`, not Claude subagents
5. **Validate response** - Check quality with the post-delegation hook

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

### Research / Documentation / Web Search
```powershell
$prompt = & .claude/hooks/delegate.ps1 "find current docs for deployment limits" "Research task"
$prompt | python3 .claude/hooks/gemini_delegate.py --profile research
```

## Weekly Maintenance

```bash
# Analyze delegation metrics
python3 .claude/hooks/analyze_metrics.py

# Review routing effectiveness
# Update presets if needed
```

## Configuration

To reconfigure delegation preferences, run the setup wizard again manually.

This will let you enable/disable CLIs.
