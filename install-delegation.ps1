[CmdletBinding()]
param(
    [string]$ProjectDir,
    [ValidateSet("gemini", "aider", "copilot", "gpt-me")]
    [string[]]$EnableCli = @(),
    [switch]$AllClis,
    [switch]$NoElevate,
    [switch]$LaunchedElevated
)

$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    if (-not $IsWindows -and $PSVersionTable.PSEdition -eq "Core") {
        return $false
    }

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-SelfElevated {
    $command = "& '$($PSCommandPath.Replace("'", "''"))' -LaunchedElevated"

    if ($ProjectDir) {
        $command += " -ProjectDir '$($ProjectDir.Replace("'", "''"))'"
    }
    foreach ($cli in $EnableCli) {
        $command += " -EnableCli '$($cli.Replace("'", "''"))'"
    }
    if ($AllClis) {
        $command += " -AllClis"
    }

    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $encoded) `
        -Verb RunAs `
        -Wait
}

function Resolve-ProjectDirectory {
    param([string]$InitialPath)

    $path = $InitialPath
    while ([string]::IsNullOrWhiteSpace($path)) {
        $path = Read-Host "Project directory to install/update delegation"
    }

    $expanded = [Environment]::ExpandEnvironmentVariables($path.Trim().Trim('"'))
    if (-not (Test-Path -LiteralPath $expanded -PathType Container)) {
        $answer = Read-Host "Directory does not exist. Create it? [y/N]"
        if ($answer -notmatch "^(y|yes)$") {
            throw "Project directory does not exist: $expanded"
        }
        New-Item -ItemType Directory -Path $expanded -Force | Out-Null
    }

    return (Resolve-Path -LiteralPath $expanded).Path
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

        try {
            $args = @($candidate.Args) + @("-c", "import sys; print(sys.version_info[0])")
            $major = & $candidate.Exe @args 2>$null
            if ($LASTEXITCODE -eq 0 -and "$major".Trim() -eq "3") {
                return [pscustomobject]$candidate
            }
        }
        catch {
            continue
        }
    }

    throw "Python 3 was not found. Install Python 3.6+ and rerun this script."
}

function Test-DelegationInstall {
    param([string]$TargetDir)

    $required = @(
        "CLAUDE.md",
        "AGENTS.md",
        # Shared scripts in .gemini-delegation/
        ".gemini-delegation\hooks\gemini_delegate.py",
        ".gemini-delegation\hooks\pre_delegate.py",
        ".gemini-delegation\hooks\post_delegate.py",
        ".gemini-delegation\hooks\analyze_metrics.py",
        ".gemini-delegation\hooks\delegation_guard.py",
        ".gemini-delegation\hooks\delegation_guard.ps1",
        ".gemini-delegation\hooks\delegate_and_log.ps1",
        ".gemini-delegation\delegation_config.json",
        # Claude per-env shims
        ".claude\CLAUDE.md",
        ".claude\hooks\delegate.ps1",
        ".claude\hooks\delegate.bat",
        ".claude\hooks\delegate",
        ".claude\hooks\delegate_and_log.ps1",
        ".claude\hooks\delegation_guard.ps1",
        ".claude\settings.json",
        # Codex per-env shims
        ".Codex\hooks\delegate.ps1",
        ".Codex\hooks\delegate.bat",
        ".Codex\hooks\delegate",
        ".Codex\hooks\delegate_and_log.ps1",
        ".Codex\hooks\delegation_guard.ps1",
        ".Codex\settings.json"
    )

    foreach ($relative in $required) {
        $fullPath = Join-Path $TargetDir $relative
        if (-not (Test-Path -LiteralPath $fullPath)) {
            throw "Missing expected delegation file: $fullPath"
        }
    }

    $bridge = Get-Content -LiteralPath (Join-Path $TargetDir "CLAUDE.md")
    $bridgeText = ($bridge -join "`n").Trim()
    if ($bridgeText -ne "@AGENTS.md") {
        throw "Root CLAUDE.md is not exactly @AGENTS.md"
    }

    $wrapper = Join-Path $TargetDir ".claude\hooks\delegate.ps1"
    $prompt = & $wrapper "npm ls" "Delegation smoke test" 5
    $promptText = $prompt -join "`n"
    if ($LASTEXITCODE -ne 0 -or $promptText -notmatch "Delegation smoke test") {
        throw "Claude PowerShell delegation wrapper smoke test failed."
    }

    $codexWrapper = Join-Path $TargetDir ".Codex\hooks\delegate.ps1"
    $codexPrompt = & $codexWrapper "npm ls" "Codex delegation smoke test" 5
    $codexPromptText = $codexPrompt -join "`n"
    if ($LASTEXITCODE -ne 0 -or $codexPromptText -notmatch "Codex delegation smoke test") {
        throw "Codex PowerShell delegation wrapper smoke test failed."
    }

    $guard = Join-Path $TargetDir ".claude\hooks\delegation_guard.ps1"
    $guardArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $guard)

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        '{"tool_name":"Bash","tool_input":{"command":"npm ls"}}' | powershell @guardArgs 2>$null | Out-Null
        $blockedExitCode = $LASTEXITCODE

        '{"tool_name":"Bash","tool_input":{"command":"git status --short"}}' | powershell @guardArgs 2>$null | Out-Null
        $allowedExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    if ($blockedExitCode -ne 2) {
        throw "Claude delegation guard did not block a known delegation command."
    }

    if ($allowedExitCode -ne 0) {
        throw "Claude delegation guard blocked a safe non-delegation command."
    }
}

if (-not $NoElevate -and -not (Test-IsAdministrator)) {
    Write-Host "Requesting administrator elevation for the delegation installer..."
    Invoke-SelfElevated
    exit 0
}

try {
    $targetDir = Resolve-ProjectDirectory -InitialPath $ProjectDir
    $setupPy = Join-Path $PSScriptRoot "setup.py"
    if (-not (Test-Path -LiteralPath $setupPy)) {
        throw "setup.py was not found next to this script: $setupPy"
    }

    $python = Get-PythonCommand
    $setupArgs = @($python.Args) + @($setupPy, "--target", $targetDir)

    foreach ($cli in $EnableCli) {
        $setupArgs += @("--enable-cli", $cli)
    }
    if ($AllClis) {
        $setupArgs += "--all-clis"
    }

    Write-Host "Installing or updating delegation in: $targetDir"
    & $python.Exe @setupArgs
    if ($LASTEXITCODE -ne 0) {
        throw "setup.py failed with exit code $LASTEXITCODE"
    }

    Test-DelegationInstall -TargetDir $targetDir
    Write-Host ""
    Write-Host "Delegation install/update completed successfully."
    Write-Host "Restart Claude Code or clear/reload context before relying on the new instructions."
}
finally {
    if ($LaunchedElevated) {
        Write-Host ""
        Read-Host "Press Enter to close this elevated window"
    }
}
