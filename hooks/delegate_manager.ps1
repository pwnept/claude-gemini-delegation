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

function Resolve-Python3 {
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

$pythonArgs = @($python | Select-Object -Skip 1) + @("$ScriptDir/delegate_manager.py") + $Rest
& $python[0] @pythonArgs
exit $LASTEXITCODE
