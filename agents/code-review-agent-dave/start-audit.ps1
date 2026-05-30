#!/usr/bin/env pwsh

# start-audit.ps1
# Starts the Dave code review agent in interactive mode (gemini chat --yolo).
# Run from the project root or any subdirectory — the script resolves its own location.

$AgentDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $AgentDir ".." "..")
Set-Location $ProjectRoot

Write-Host "Checking git status..." -ForegroundColor Cyan

$GitStatus = git status --porcelain
if ($GitStatus) {
    Write-Host "Repository has uncommitted changes. Committing before audit..." -ForegroundColor Yellow
    git add .
    git commit -m "chore: auto-commit before Dave code audit"
} else {
    Write-Host "Repository is clean." -ForegroundColor Green
}

$CommitHash = git rev-parse --short HEAD
$DateTime   = Get-Date -Format "yyyyMMdd_HHmmss"
$ReportPath = "audit\gemini-3.1-pro-preview_audit_${DateTime}_${CommitHash}.md"

Write-Host "Starting Dave Code Review Agent..." -ForegroundColor Cyan
Write-Host "Model: gemini-3.1-pro-preview"      -ForegroundColor DarkGray
Write-Host "Report: $ReportPath"                 -ForegroundColor DarkGray

$Prompt = "Conduct a deep and thorough code audit and verification. @agents\code-review-agent-dave\dave_audit.md. Never fix code directly. Only report your findings. Once you have completed your analysis, you MUST generate a final report and save it to the file path: $ReportPath"

gemini chat --yolo --model gemini-3.1-pro-preview $Prompt
