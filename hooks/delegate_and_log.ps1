# Full delegation pipeline in a single Python spawn: gemini_delegate.py handles
# pre-format (prompt building), the agy run, and post-validate (metrics) itself.
# Usage: ./delegate_and_log.ps1 <task> [context] [max_lines] [-Profile research]
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Task,
    [Parameter(Position = 1)][string]$Context = "General task",
    [Parameter(Position = 2)][int]$MaxLines = 0,
    [ValidateSet("default", "research", "scout", "skim")]
    [string]$Profile = "default",
    [ValidateSet("claude", "codex", "agy", "auto")]
    [string]$Caller = "auto",
    [int]$IdleTimeoutSeconds = 0,
    [int]$TimeoutSeconds = 0
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
# .gemini-delegation dir (parent of hooks/) — passed to Python hooks so they
# skip the per-call cwd up-walk (path-discovery optimization).
$AgentDir  = Split-Path -Parent $ScriptDir

# Unique per-call slug so delegation transcripts are not all labelled "turn-unknown".
# gemini_delegate.py reads DELEGATION_TURN_ID from the environment.
if (-not $env:DELEGATION_TURN_ID) {
    $env:DELEGATION_TURN_ID = "call-$(Get-Date -Format 'HHmmss-fff')"
}

Write-Host "[delegation] Starting Gemini delegation (profile: $Profile)..." -ForegroundColor Cyan

function Resolve-Python3 {
    # Returns @(command, prefix-args...). Caches the probe result in
    # DELEGATION_PYTHON (process env) so repeat calls in the same shell
    # session skip the version-probe spawn entirely.
    if ($env:DELEGATION_PYTHON) {
        $cached = $env:DELEGATION_PYTHON -split ' '
        if (Get-Command $cached[0] -ErrorAction SilentlyContinue) {
            return $cached
        }
    }

    $candidates = @(
        @{ Command = "py"; Prefix = @("-3") },
        @{ Command = "python3"; Prefix = @() },
        @{ Command = "python"; Prefix = @() }
    )
    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Command -ErrorAction SilentlyContinue)) {
            continue
        }
        & $candidate.Command @($candidate.Prefix + @("-c", "import sys; sys.exit(0 if sys.version_info[0] >= 3 else 1)")) *> $null
        if ($LASTEXITCODE -ne 0) {
            continue
        }
        $resolved = @($candidate.Command) + $candidate.Prefix
        $env:DELEGATION_PYTHON = $resolved -join ' '
        return $resolved
    }
    return $null
}

# @() guards PowerShell's pipeline unrolling: a 1-element return (e.g. bare
# "python3") arrives as a String, and $python[0] would index its first CHAR.
$python = @(Resolve-Python3)
if (-not $python) {
    Write-Error "Python 3 was not found. Install Python 3.6+ or ensure py -3/python3 is on PATH."
    exit 127
}

$delegateArgs = @(
    "$ScriptDir/gemini_delegate.py",
    "--agent-dir", $AgentDir,
    "--pre-format", "--context", $Context,
    "--post-validate"
)
if ($MaxLines -gt 0) {
    $delegateArgs += @("--max-lines", "$MaxLines")
}
if ($Profile -ne "default") {
    $delegateArgs += @("--profile", $Profile)
}
if ($Caller -ne "auto") {
    $delegateArgs += @("--caller", $Caller)
}
if ($IdleTimeoutSeconds -gt 0) {
    $delegateArgs += @("--idle-timeout-seconds", "$IdleTimeoutSeconds")
}
if ($TimeoutSeconds -gt 0) {
    $delegateArgs += @("--timeout-seconds", "$TimeoutSeconds")
}

# Pass Task via stdin to avoid PowerShell arg splitting/length issues
$pythonArgs = @($python | Select-Object -Skip 1) + $delegateArgs
$response = $Task | & $python[0] @pythonArgs
$delegateExitCode = $LASTEXITCODE
$responseText = $response -join "`n"

if ($responseText) {
    Write-Output $responseText
}

exit $delegateExitCode
