# Full delegation pipeline: pre_delegate -> agy runner -> post_delegate
# Usage: ./delegate_and_log.ps1 <task> [context] [max_lines] [-Profile research]
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Task,
    [Parameter(Position = 1)][string]$Context = "General task",
    [Parameter(Position = 2)][int]$MaxLines = 0,
    [ValidateSet("default", "research")]
    [string]$Profile = "default"
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:LastPythonExitCode = 0

function Invoke-Python3 {
    param(
        [string[]]$PythonArgs,
        [AllowEmptyString()]
        [string]$InputText,
        [switch]$HasInput
    )

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

        if ($HasInput) {
            $InputText | & $candidate.Command @($candidate.Prefix + $PythonArgs)
        } else {
            & $candidate.Command @($candidate.Prefix + $PythonArgs)
        }
        $script:LastPythonExitCode = $LASTEXITCODE
        return
    }

    Write-Error "Python 3 was not found. Install Python 3.6+ or ensure py -3/python3 is on PATH."
    $script:LastPythonExitCode = 127
}

$promptArgs = @("$ScriptDir/pre_delegate.py", "-", $Context)
if ($MaxLines -gt 0) {
    $promptArgs += "$MaxLines"
}

# Pass Task via stdin to avoid PowerShell arg splitting/length issues
$prompt = Invoke-Python3 -PythonArgs $promptArgs -InputText $Task -HasInput
$preExitCode = $script:LastPythonExitCode
if ($preExitCode -ne 0 -or -not $prompt) {
    Write-Error "pre_delegate.py failed or produced no output."
    exit $(if ($preExitCode -ne 0) { $preExitCode } else { 1 })
}
$promptText = $prompt -join "`n"

$delegateArgs = @("$ScriptDir/gemini_delegate.py")
if ($Profile -ne "default") {
    $delegateArgs += @("--profile", $Profile)
}

# Pass Prompt via stdin to avoid command line length limits (8KB)
$response = Invoke-Python3 -PythonArgs $delegateArgs -InputText $promptText -HasInput
$delegateExitCode = $script:LastPythonExitCode
$responseText = $response -join "`n"

if ($responseText) {
    Write-Output $responseText
    $maxLinesForValidation = if ($MaxLines -gt 0) { $MaxLines } else { 10 }
    $tmpFile = [System.IO.Path]::GetTempFileName()
    try {
        $responseText | Set-Content -Path $tmpFile -Encoding UTF8
        Invoke-Python3 -PythonArgs @("$ScriptDir/post_delegate.py", "--input-file", $tmpFile, "$maxLinesForValidation", $Task) | Out-Null
    } finally {
        Remove-Item $tmpFile -ErrorAction SilentlyContinue
    }
}

exit $delegateExitCode
