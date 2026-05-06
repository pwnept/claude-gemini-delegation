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

Use the wrapper scripts for easy delegation:

**Unix/Mac:**
```bash
PROMPT=$(./.Codex/hooks/delegate "npm ls" "Build analysis")
gemini --model gemini-3-flash -p "$PROMPT"
```

**Windows (PowerShell):**
```powershell
$prompt = & .Codex/hooks/delegate.ps1 "npm ls" "Build analysis"
gemini --model gemini-3-flash -p $prompt
```

**Windows (CMD):**
```cmd
FOR /F "delims=" %i IN ('.Codex\hooks\delegate.bat "npm ls" "Build analysis"') DO SET PROMPT=%i
gemini --model gemini-3-flash -p "%PROMPT%"
```

## Delegation Workflow

1. **Identify task type** - Security? Git ops? Analysis?
2. **Check presets** - Is there a matching preset?
3. **Use delegation hook** - Let the hook format the prompt
4. **Execute with appropriate CLI** - Use the routed CLI
5. **Validate response** - Check quality with post-delegate hook

## Routing Examples

### Security Audit
```bash
# Auto-routes to Gemini (if enabled)
PROMPT=$(./.Codex/hooks/delegate "scan auth.py for vulnerabilities" "Pre-deploy security check")
gemini --model gemini-3-flash -p "$PROMPT"
```

### Git Operations  
```bash
# Auto-routes to Aider (if enabled)
PROMPT=$(./.Codex/hooks/delegate "git log --oneline --since=1.week" "Finding bug introduction")
aider -p "$PROMPT"
```

### Code Analysis
```bash
# Routes based on configured preference
PROMPT=$(./.Codex/hooks/delegate "analyze @src/ for performance issues" "Optimization task")
gemini --model gemini-3-flash -p "$PROMPT"
```

## Weekly Maintenance

```bash
# Analyze delegation metrics
python .Codex/hooks/analyze_metrics.py

# Review routing effectiveness
# Update presets if needed
```

## Configuration

To reconfigure delegation preferences, run the setup wizard again manually.

This will let you enable/disable CLIs.
