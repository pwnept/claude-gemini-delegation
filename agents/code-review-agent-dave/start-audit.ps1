#!/usr/bin/env pwsh

# start-audit.ps1
# Starts the Dave code review agent (non-interactive via agy).
# Run from the project root or any subdirectory — the script resolves its own location.

$AgentDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $AgentDir ".." "..")
Set-Location $ProjectRoot

Write-Host "Checking git status..." -ForegroundColor Cyan

$GitStatus = git status --porcelain
if ($GitStatus) {
    Write-Host "Repository has uncommitted changes; auditing the working tree as-is." -ForegroundColor Yellow
} else {
    Write-Host "Repository is clean." -ForegroundColor Green
}

$CommitHash = git rev-parse --short HEAD
$DateTime   = Get-Date -Format "yyyyMMdd_HHmmss"
$ReportPath = "audit\agy-research_audit_${DateTime}_${CommitHash}.md"

Write-Host "Starting Dave Code Review Agent..." -ForegroundColor Cyan
Write-Host "Model profile: agy research"         -ForegroundColor DarkGray
Write-Host "Report: $ReportPath"                 -ForegroundColor DarkGray

$Prompt = "Conduct a deep and thorough code audit and verification. @agents\code-review-agent-dave\dave_audit.md. Never fix code directly. Only report your findings. Once you have completed your analysis, you MUST generate a final report and save it to the file path: $ReportPath"

$Pipeline = Join-Path $ProjectRoot ".gemini-delegation\hooks\delegate_and_log.ps1"
if (-not (Test-Path -LiteralPath $Pipeline)) {
    $Pipeline = Join-Path $ProjectRoot "hooks\delegate_and_log.ps1"
}
if (-not (Test-Path -LiteralPath $Pipeline)) {
    throw "Delegation pipeline not found. Run install-delegation.ps1 install --target <repo> first."
}

& $Pipeline $Prompt "Dave code audit" 0 -Profile research
exit $LASTEXITCODE
