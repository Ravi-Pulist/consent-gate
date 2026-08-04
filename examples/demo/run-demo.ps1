<#
Reproduce the exhibit behind examples/demo/index.html.

Runs from anywhere: the script resolves the repo root from its own location.
Pass -DryRun to print the commands without executing them.

    .\examples\demo\run-demo.ps1
    .\examples\demo\run-demo.ps1 -DryRun

Exit codes come from the exhibit itself:
  0  exclusion leaked on nothing
  1  exclusion leaked (a failure, not a caveat)
  2  the exhibit could not run, including the case where the masking baseline
     leaked nothing and the corpus therefore proves nothing
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [string]$Python = "python"
)

$ErrorActionPreference = "Continue"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $root
$env:PYTHONPATH = "src"

function Invoke-Step {
    param([string[]]$Arguments)
    Write-Host ""
    Write-Host "`$ PYTHONPATH=src $Python $($Arguments -join ' ')"
    if ($DryRun) { return 0 }
    # Out-Host keeps the child process's output off the pipeline, so the
    # function returns the exit code and not the exit code appended to
    # everything python printed.
    & $Python @Arguments | Out-Host
    return $LASTEXITCODE
}

Write-Host "repo root: $root"
Write-Host "corpus:    deterministic, seed 20260804, synthetic throughout"

# 1. Regenerate the corpus. Byte-reproducible from the seed, so this
#    overwrites corpus/data with identical bytes.
$code = Invoke-Step @("corpus/generate.py")
if ($code -ne 0) { Write-Error "corpus generation failed"; exit 2 }

# 2. Run both configurations over the same probes and diff them.
#    Writes exhibits/exhibit.md and exhibits/exhibit.json.
$exhibit = Invoke-Step @(
    "-m", "consent_gate.cli", "exhibit", "--corpus", "corpus/data",
    "--out", "exhibits/exhibit.md", "--json", "exhibits/exhibit.json")

# 3. The test suite, including the 9 sabotage cases.
$code = Invoke-Step @("-m", "pytest", "-q")
if ($code -ne 0) { Write-Error "tests failed"; exit 2 }

# 4. Re-derive every figure printed in the demo.
$code = Invoke-Step @("examples/demo/verify.py")
if ($code -ne 0) { Write-Error "demo figures do not match"; exit 1 }

Write-Host ""
if ($DryRun) {
    Write-Host "dry run: nothing was executed."
} elseif ($exhibit -eq 0) {
    Write-Host "exhibit exit code: 0 (exclusion leaked nothing, as expected)"
} else {
    Write-Host "exhibit exit code: $exhibit"
    exit $exhibit
}

Write-Host ""
Write-Host "open examples/demo/index.html to read the results."
