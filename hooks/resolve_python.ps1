# Shared Python-3 resolution for the delegation wrappers. Dot-source, then:
#   $python = @(Resolve-Python3)   # ALWAYS wrap in @() — PowerShell unrolls a
#                                  # 1-element return to a String, and
#                                  # $python[0] would index its first CHAR.
# Returns @(command, prefix-args...) or $null if no Python 3 is available.

$script:KnownPythonCandidates = @(
    @{ Command = "py"; Prefix = @("-3") },
    @{ Command = "python3"; Prefix = @() },
    @{ Command = "python"; Prefix = @() }
)

function Test-Python3 {
    param([string]$Command, [string[]]$Prefix)
    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
        return $false
    }
    & $Command @($Prefix + @("-c", "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)")) *> $null
    return ($LASTEXITCODE -eq 0)
}

function Resolve-Python3 {
    # DELEGATION_PYTHON caches a resolution as "command arg arg" so repeat
    # calls in one shell session skip the probe spawn. A value we produced
    # ourselves (a known candidate) is trusted after an existence check; a
    # user-supplied custom value is version-probed before use.
    if ($env:DELEGATION_PYTHON) {
        $raw = $env:DELEGATION_PYTHON
        $isKnown = $script:KnownPythonCandidates |
            Where-Object { (@($_.Command) + $_.Prefix) -join ' ' -eq $raw }
        if ($isKnown) {
            $cached = @($raw -split ' ')
            if (Get-Command $cached[0] -ErrorAction SilentlyContinue) {
                return $cached
            }
        }
        # Custom value: an interpreter path may contain spaces, so try the
        # whole value as one command before falling back to space-splitting
        # it into command + args.
        elseif ((Get-Command $raw -ErrorAction SilentlyContinue) -and (Test-Python3 $raw @())) {
            return @($raw)
        }
        else {
            $cached = @($raw -split ' ')
            if (Test-Python3 $cached[0] @($cached | Select-Object -Skip 1)) {
                return $cached
            }
        }
    }

    foreach ($candidate in $script:KnownPythonCandidates) {
        if (Test-Python3 $candidate.Command $candidate.Prefix) {
            $resolved = @($candidate.Command) + $candidate.Prefix
            $env:DELEGATION_PYTHON = $resolved -join ' '
            return $resolved
        }
    }
    return $null
}
