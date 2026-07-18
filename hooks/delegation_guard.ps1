# PowerShell launcher for delegation_guard.py.
[CmdletBinding()]
param(
    [Parameter(ValueFromPipeline = $true)]
    [string]$InputPayload
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrEmpty($InputPayload)) {
    $InputPayload = [Console]::In.ReadToEnd()
}

$PythonCommands = @(
    @{ Command = "py"; Prefix = @("-3") },
    @{ Command = "python3"; Prefix = @() },
    @{ Command = "python"; Prefix = @() }
)

foreach ($Python in $PythonCommands) {
    if (-not (Get-Command $Python.Command -ErrorAction SilentlyContinue)) {
        continue
    }

    & $Python.Command @($Python.Prefix + @("-c", "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)")) *> $null
    if ($LASTEXITCODE -ne 0) {
        continue
    }

    $InputPayload | & $Python.Command @($Python.Prefix + @("$ScriptDir/delegation_guard.py"))
    exit $LASTEXITCODE
}

Write-Error "Python 3.10+ was not found. Install Python 3.10+ or ensure py -3/python3 is on PATH."
exit 127
