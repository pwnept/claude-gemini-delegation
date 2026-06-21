PS1_SHIM = """[CmdletBinding()]
param(
    [Parameter(ValueFromPipeline = $true, Position = 0)][string]$Task = "",
    [Parameter(Position = 1)][string]$Context = "General task",
    [Parameter(Position = 2)][int]$MaxLines = 0,
    [ValidateSet("default", "research")]
    [string]$Profile = "default"
)
begin {
    $pipelineInput = @()
}
process {
    if ($_ -ne $null) {
        $pipelineInput += $_
    }
}
end {
    $mergedTask = $Task
    if ($pipelineInput.Count -gt 0) {
        if ($mergedTask -eq "") {
            $mergedTask = $pipelineInput -join "`n"
        } else {
            $mergedTask = $mergedTask + "`n" + ($pipelineInput -join "`n")
        }
    }
    
    # We pipe the task string into the python engine to avoid argv length limits
    $mergedTask | gemini-delegate run --context $Context --max-lines $MaxLines --profile $Profile
    exit $LASTEXITCODE
}
"""

SH_SHIM = """#!/bin/bash
# Minimal shim for Unix
if [ -p /dev/stdin ]; then
    cat | gemini-delegate run "$@"
else
    gemini-delegate run "$@"
fi
"""
