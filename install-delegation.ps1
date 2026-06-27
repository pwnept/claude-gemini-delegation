[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("help", "info", "install", "verify", "uninstall")]
    [string]$Command = "help",

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"

function Show-DelegationHelp {
    Write-Host @"
claude-gemini-delegation

Usage:
  .\install-delegation.ps1 help
  .\install-delegation.ps1 info
  .\install-delegation.ps1 install --target "C:\path\to\repo"
  .\install-delegation.ps1 verify --target "C:\path\to\repo"
  .\install-delegation.ps1 uninstall --target "C:\path\to\repo"

Options:
  --target <path>       Target repository to modify. Required except for help.
  --create-target       Create a missing target directory during install.

Default behavior:
  Installs local, repo-carried delegation files. It does not rely on a global
  registry and does not require pip installing this package first.

Failure handling:
  The script stops on unexpected or complex errors. Paste the full output into
  an AI agent if you want it fixed automatically, or use the described missing
  file / marker / JSON error to repair it yourself.
"@
}

function Get-PythonCommand {
    $candidates = @(
        @{ Exe = "py"; Args = @("-3") },
        @{ Exe = "python3"; Args = @() },
        @{ Exe = "python"; Args = @() }
    )

    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) {
            continue
        }

        & $candidate.Exe @($candidate.Args + @("-c", "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)")) *> $null
        if ($LASTEXITCODE -eq 0) {
            return [pscustomobject]$candidate
        }
    }

    throw "Python 3.8+ was not found. Install Python 3.8+ or put py/python3/python on PATH."
}

function Invoke-DelegationCli {
    param(
        [string]$CliCommand,
        [string[]]$CliArgs
    )

    $srcDir = Join-Path $PSScriptRoot "src"
    if (-not (Test-Path -LiteralPath (Join-Path $srcDir "gemini_delegation"))) {
        throw "Cannot find source package at $srcDir. Run this script from a complete source checkout."
    }

    $python = Get-PythonCommand
    $oldPythonPath = $env:PYTHONPATH
    try {
        if ([string]::IsNullOrWhiteSpace($oldPythonPath)) {
            $env:PYTHONPATH = $srcDir
        } else {
            $env:PYTHONPATH = $srcDir + [IO.Path]::PathSeparator + $oldPythonPath
        }

        & $python.Exe @($python.Args + @("-m", "gemini_delegation.cli", $CliCommand) + $CliArgs)
        if ($LASTEXITCODE -ne 0) {
            throw "Delegation $CliCommand failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        $env:PYTHONPATH = $oldPythonPath
    }
}

try {
    if ($Command -eq "help") {
        Show-DelegationHelp
        exit 0
    }

    Invoke-DelegationCli -CliCommand $Command -CliArgs $RemainingArgs
    # 'info' needs no --target; the Python CLI handles it directly.
}
catch {
    Write-Error @"
Delegation installer stopped.

$($_.Exception.Message)

The operation did not continue because this installer avoids guessing through
complex state. You can fix the described problem manually, or paste this output
into an AI agent and ask it to repair the target install.
"@
    exit 2
}
