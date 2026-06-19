[CmdletBinding()]
param(
    [Parameter(ValueFromPipeline = $true, Position = 0)][string]$Task,
    [Parameter(Position = 1)][string]$Context = "General task",
    [Parameter(Position = 2)][int]$MaxLines = 0
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
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

    $argsToPass = @("$ScriptDir/pre_delegate.py", "-", $Context)
    if ($MaxLines -gt 0) {
        $argsToPass += $MaxLines
    }
    $Task | & $Python.Command @($Python.Prefix + $argsToPass)
    exit $LASTEXITCODE
}

Write-Error "Python 3 was not found."
exit 127
