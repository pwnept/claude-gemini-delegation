# PowerShell launcher for delegation_guard.py.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$InputPayload = ($input | Out-String).TrimEnd()

$PythonCommands = @(
    @{ Command = "py"; Prefix = @("-3") },
    @{ Command = "python3"; Prefix = @() },
    @{ Command = "python"; Prefix = @() }
)

foreach ($Python in $PythonCommands) {
    if (-not (Get-Command $Python.Command -ErrorAction SilentlyContinue)) {
        continue
    }

    & $Python.Command @($Python.Prefix + @("-c", "import sys; sys.exit(0 if sys.version_info[0] >= 3 else 1)")) *> $null
    if ($LASTEXITCODE -ne 0) {
        continue
    }

    $InputPayload | & $Python.Command @($Python.Prefix + @("$ScriptDir/delegation_guard.py"))
    exit $LASTEXITCODE
}

Write-Error "Python 3 was not found. Install Python 3.6+ or ensure py -3/python3 is on PATH."
exit 127
