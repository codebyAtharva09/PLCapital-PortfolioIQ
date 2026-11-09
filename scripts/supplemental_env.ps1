# Environment for the separately labelled supplemental evaluation with gemini-3.5-flash-lite.
# Dot-source it in a PowerShell session:   . .\scripts\supplemental_env.ps1
# It changes nothing on disk and only affects the current session. The production default model stays
# gemini-3.6-flash. Runs, reports, the usage ledger and the response cache all live in their own folders,
# so Flash-Lite results can never be merged into the official Gemini 3.6 Flash results.

$env:GEMINI_MODEL = "gemini-3.5-flash-lite"
$env:PORTFOLIOIQ_USAGE_LOG = "reports/supplemental/gemini-3.5-flash-lite/api_usage.jsonl"
$env:PORTFOLIOIQ_CACHE_DIR = ".cache/supplemental-gemini-3.5-flash-lite"
$global:SUPPLEMENTAL_DIRS = @("--runs-dir", "reports/supplemental/gemini-3.5-flash-lite/runs",
                              "--reports-dir", "reports/supplemental/gemini-3.5-flash-lite")
Write-Output "Supplemental evaluation environment: model $env:GEMINI_MODEL; ledger $env:PORTFOLIOIQ_USAGE_LOG"
