# Thin wrapper for delegate_manager.py (async delegations + persistent delegates).
# Usage:
#   ./delegate_manager.ps1 async "<task>" --context "<ctx>" --profile skim
#   ./delegate_manager.ps1 wait <id>          # run backgrounded: exit = wake signal
#   ./delegate_manager.ps1 spawn --profile research
#   ./delegate_manager.ps1 list | steer <id> "<prompt>" | read <id> | stop <id>
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$ScriptDir/resolve_python.ps1"

$python = @(Resolve-Python3)
if (-not $python) {
    Write-Error "Python 3.10+ was not found. Install Python 3.10+ or ensure py -3/python3 is on PATH."
    exit 127
}

$pythonArgs = @($python | Select-Object -Skip 1) + @("$ScriptDir/delegate_manager.py") + $Rest
& $python[0] @pythonArgs
exit $LASTEXITCODE
