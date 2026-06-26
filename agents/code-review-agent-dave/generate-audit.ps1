#!/usr/bin/env pwsh

# generate-audit.ps1
# Runs Dave in headless mode — exhaustive, pedantic audit.
# Flags everything to minimise iteration rounds.

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
$ReportPath = "audit\agy-research_audit_${DateTime}_${CommitHash}_headless.md"

Write-Host "Starting Dave Headless Audit..." -ForegroundColor Cyan
Write-Host "Model profile: agy research"       -ForegroundColor DarkGray
Write-Host "Report: $ReportPath"              -ForegroundColor DarkGray

$Prompt = @"
Conduct an EXHAUSTIVE, pedantic, and extremely thorough code audit and verification.
Context: @agents\code-review-agent-dave\dave_audit.md

MANDATORY HEADLESS RULES:
1. NEVER ASK QUESTIONS. This is an automated run. There is no human to respond.
2. If any code is ambiguous, assume a worst-case security or performance risk and flag it.
3. FLAG EVERYTHING. Be as detailed as possible to minimise the need for future iteration rounds.
4. NEVER FIX CODE. Only report your findings in the audit document.
5. SAVE THE REPORT: You MUST save your complete findings to the file path: $ReportPath
6. EXIT: Once the report is saved, your task is complete.
"@

$Pipeline = Join-Path $ProjectRoot ".gemini-delegation\hooks\delegate_and_log.ps1"
if (-not (Test-Path -LiteralPath $Pipeline)) {
    $Pipeline = Join-Path $ProjectRoot "hooks\delegate_and_log.ps1"
}
if (-not (Test-Path -LiteralPath $Pipeline)) {
    throw "Delegation pipeline not found. Run install-delegation.ps1 install --target <repo> first."
}

& $Pipeline $Prompt "Dave headless code audit" 0 -Profile research
exit $LASTEXITCODE
