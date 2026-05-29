# Codex Configuration

## Enabled Delegation CLIs

- **Gemini CLI**: Google's Gemini models via CLI

## Delegation Presets

- **security_audit**: Routes to `gemini`
  Pattern: `(security|vulnerability|audit|xss|sql injection|csrf)`
- **web_search**: Routes to `gemini`
  Pattern: `(search|documentation|lookup|find.*docs)`
- **code_analysis**: Routes to `gemini`
  Pattern: `(analyze|review|inspect).*code`

## Quick Delegation

Use the wrapper scripts for easy delegation. Claude Code uses `.claude/hooks`;
Codex workflows can use the mirrored `.Codex/hooks` path.

**Unix/Mac:**
```bash
PROMPT=$(./.claude/hooks/delegate "npm ls" "Build analysis")
gemini --model gemini-2.5-flash -p "$PROMPT"
```

**Windows (PowerShell):**
```powershell
$prompt = & .claude/hooks/delegate.ps1 "npm ls" "Build analysis"
$prompt | py -3 .claude/hooks/gemini_delegate.py

# Full pipeline with validation/metrics:
.claude/hooks/delegate_and_log.ps1 "npm ls" "Build analysis" 5
```

**Windows (CMD):**
```cmd
FOR /F "delims=" %i IN ('.claude\hooks\delegate.bat "npm ls" "Build analysis"') DO SET PROMPT=%i
echo %PROMPT% | py -3 .claude\hooks\gemini_delegate.py
```

## Delegation Workflow

1. **Identify task type** - Security? Git ops? Analysis?
2. **Check presets** - Is there a matching preset?
3. **Use delegation hook** - Let the hook format the prompt
4. **Execute with appropriate CLI** - Use the routed CLI
5. **Validate response** - Check quality with post-delegate hook

`.claude/settings.json` registers a PreToolUse guard for known high-output Bash
commands. Treat it as enforcement for obvious misses, not a substitute for
proactive delegation on broad analysis and research tasks.

## Routing Examples

### Security Audit
```bash
# Auto-routes to Gemini (if enabled)
PROMPT=$(./.claude/hooks/delegate "scan auth.py for vulnerabilities" "Pre-deploy security check")
gemini --model gemini-2.5-flash -p "$PROMPT"
```

### Git Operations  
```bash
# Auto-routes to Aider (if enabled)
PROMPT=$(./.claude/hooks/delegate "git log --oneline --since=1.week" "Finding bug introduction")
aider -p "$PROMPT"
```

### Code Analysis
```bash
# Routes based on configured preference
PROMPT=$(./.claude/hooks/delegate "analyze @src/ for performance issues" "Optimization task")
gemini --model gemini-2.5-flash -p "$PROMPT"
```

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
