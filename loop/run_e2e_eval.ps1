# run_e2e_eval.ps1 -- flags-on end-to-end evaluation (demo readiness)
#
# One evaluator run (Fable 5), plus an optional verify pass (Opus 5) that re-runs every
# BLOCKER/MAJOR repro. Nothing is built or fixed. Both run on the claude.ai subscription.
#
# Usage:
#   cd "C:\dev\_Arkim\Arkim Procurement Agent Prototype"
#   .\loop\run_e2e_eval.ps1              # evaluate
#   .\loop\run_e2e_eval.ps1 -VerifyOnly  # later: verify the findings in an existing report
#   .\loop\run_e2e_eval.ps1 -Verify      # evaluate, then verify in the same run
#
# Handshake:
#   E2E_EVAL_BRIEF.md   -- input, repo root (must exist)
#   E2E_EVAL_REPORT.md  -- output
#   eval/e2e/           -- fixtures, runner scripts, captured evidence

param(
    [switch]$Verify,
    [switch]$VerifyOnly,
    [string]$RepoPath     = "C:\dev\_Arkim\Arkim Procurement Agent Prototype",
    [string]$Branch       = "eval/e2e-flags-on",
    [string]$EvalModel    = "claude-fable-5",
    [string]$VerifyModel  = "claude-opus-5"
)

$ErrorActionPreference = "Continue"
$PSNativeCommandUseErrorActionPreference = $false
$ProgressPreference = "SilentlyContinue"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
Set-Location $RepoPath

$Brief  = "E2E_EVAL_BRIEF.md"
$Report = "E2E_EVAL_REPORT.md"
$LogFile = "eval.log"

function Write-Log([string]$msg) {
    $line = "$(Get-Date -Format 'HH:mm:ss')  $msg"
    Write-Host $line
    Add-Content -Path (Join-Path $RepoPath "loop.log") -Value $line
}

function Git-Safe {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)
    $errFile = [System.IO.Path]::GetTempFileName()
    try { $out = & git @Args 2>$errFile; return ($out | Out-String) }
    finally { Remove-Item $errFile -ErrorAction SilentlyContinue }
}

function Clear-ModelEnv {
    foreach ($v in "ANTHROPIC_API_KEY","ANTHROPIC_BASE_URL","ANTHROPIC_AUTH_TOKEN",
                   "ANTHROPIC_CUSTOM_HEADERS","ANTHROPIC_MODEL",
                   "ANTHROPIC_DEFAULT_FABLE_MODEL","ANTHROPIC_DEFAULT_OPUS_MODEL",
                   "ANTHROPIC_DEFAULT_SONNET_MODEL","ANTHROPIC_DEFAULT_HAIKU_MODEL",
                   "CLAUDE_CODE_SUBAGENT_MODEL",
                   "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT") {
        Remove-Item "Env:$v" -ErrorAction SilentlyContinue
    }
}

function Invoke-Agent([string]$Model, [string]$Prompt, [string]$Stage) {
    Clear-ModelEnv
    "`n===== $Stage ($Model) -- $(Get-Date -Format o) =====" | Add-Content $LogFile
    $out = (claude --model $Model --dangerously-skip-permissions -p $Prompt 2>&1 | Out-String)
    Add-Content -Path $LogFile -Value $out
    if ($out -match "(?i)hit your (session|usage) limit|usage limit reached") {
        $reset = [regex]::Match($out, "(?i)resets?\s+([^\r\n\u00b7]+)").Groups[1].Value.Trim()
        Write-Host "`nSESSION LIMIT reached during: $Stage" -ForegroundColor Red
        if ($reset) { Write-Host "Resets: $reset" -ForegroundColor Red }
        Write-Log "SESSION LIMIT during $Stage; resets '$reset'"
        Write-Host "Partial work is committed. After the reset, re-run the same command;" -ForegroundColor Yellow
        Write-Host "the evaluator reads the existing report and continues from where it stopped." -ForegroundColor Yellow
        exit 3
    }
}

# ---- Pre-flight -------------------------------------------------------------
if (-not (Test-Path (Join-Path $RepoPath $Brief))) { throw "$Brief not found in repo root." }

Clear-ModelEnv
$probe = (claude --model $EvalModel -p "Reply with exactly: AUTH OK" 2>&1 | Out-String)
if ($probe -notmatch "AUTH OK") { Write-Host $probe; throw "Auth probe failed. Run 'claude login'." }
Write-Log "Auth OK: $EvalModel"

$dirty = Git-Safe status --porcelain | Where-Object { $_ -notmatch '^\?\?' }
if (($dirty -join "`n").Trim()) { $dirty | ForEach-Object { Write-Host "  $_" }; throw "Dirty tracked tree." }

if ((Git-Safe branch --list $Branch).Trim() -eq "") {
    Git-Safe checkout test/flag-on-integration | Out-Null
    Git-Safe branch $Branch | Out-Null
}
Git-Safe checkout $Branch | Out-Null
$BranchPoint = (Git-Safe merge-base $Branch test/flag-on-integration).Trim()
Write-Log "On branch $Branch; branch point $BranchPoint"

$script:PreExistingUntracked = @((Git-Safe status --porcelain) -split "`n" |
    Where-Object { $_ -match '^\?\?' -and $_.Length -gt 3 } |
    ForEach-Object { ($_.Substring(3).Trim() -replace '"','') })

# ---- Scope: an evaluation may write ONLY eval/ and the report -------------
function Assert-EvalScope([string]$stage) {
    $allowed = @('^eval/', '^E2E_EVAL_REPORT\.md$', '^E2E_EVAL_BRIEF\.md$')
    $committed = (Git-Safe diff --name-only $BranchPoint HEAD) -split "`n"
    $working = @()
    foreach ($line in ((Git-Safe status --porcelain) -split "`n")) {
        if ($line.Length -le 3) { continue }
        $p = ($line.Substring(3).Trim() -replace '"','')
        if ($line -match '^\?\?' -and $script:PreExistingUntracked -contains $p) { continue }
        $working += $p
    }
    $bad = @()
    foreach ($f in ($committed + $working)) {
        $f = $f.Trim(); if (-not $f) { continue }
        $ok = $false; foreach ($a in $allowed) { if ($f -match $a) { $ok = $true; break } }
        if (-not $ok -and $bad -notcontains $f) { $bad += $f }
    }
    if ($bad.Count -gt 0) {
        Write-Host "`nEVALUATION SCOPE VIOLATED during $stage -- files outside eval/ were changed:" -ForegroundColor Red
        $bad | ForEach-Object { Write-Host "  $_" -ForegroundColor Red; Write-Log "  EVAL SCOPE: $_" }
        Write-Host "An evaluation must not modify the system it evaluates. Inspect: git diff $BranchPoint" -ForegroundColor Red
        exit 2
    }
    Write-Log "Scope OK after $stage"
}

# ---- Prompts ----------------------------------------------------------------
$EvalPrompt = @"
You are the EVALUATOR. Read E2E_EVAL_BRIEF.md in the repo root and carry it out exactly.

You are evaluating, not building. You may write ONLY under eval/e2e/ and E2E_EVAL_REPORT.md. Do not
modify any application source, test, or config file -- not even to fix an obvious one-line bug.
A break is a finding. The run terminates automatically if anything outside eval/ changes.

EXECUTION RULE: non-interactive print mode. Your session ends when your response ends. Run every
command synchronously. If you start a server in the background, stop it before you finish.

If E2E_EVAL_REPORT.md already exists, a previous session was cut off. Read it, continue from the
first incomplete phase or scenario, and do not redo completed work.

Do Phase 0 completely before any scenario, including the mail-safety proof -- if you cannot prove
zero real sends, stop and say so. Complete S1 fully before S2. Never print or copy any API key.

Commit E2E_EVAL_REPORT.md and eval/e2e/ on branch $Branch as you go (commit after each phase and
each scenario, so a session limit loses nothing). Do not push.
"@

$VerifyPrompt = @"
You are the VERIFIER. Read E2E_EVAL_BRIEF.md and E2E_EVAL_REPORT.md.

For EVERY finding marked BLOCKER or MAJOR, re-run its repro command yourself, in the same isolated
environment and pilot flag profile the report documents. Mark each CONFIRMED or NOT REPRODUCED, with
the output excerpt that decides it. Do not re-investigate MINOR findings.

You may write ONLY E2E_EVAL_REPORT.md (append a 'VERIFICATION' section with a table) and files under
eval/e2e/. Do not fix anything. Do not modify application source.

EXECUTION RULE: run every command synchronously; stop any server you start before finishing.

Commit the report on branch $Branch. Do not push.
"@

# ---- Run --------------------------------------------------------------------
if (-not $VerifyOnly) {
    Write-Host "`n--- EVALUATION (Phase 0 -> S1..S4 -> seams -> UI checklist) ---" -ForegroundColor Cyan
    Invoke-Agent $EvalModel $EvalPrompt "EVALUATOR"
    Assert-EvalScope "evaluation"
}

if ($Verify -or $VerifyOnly) {
    if (-not (Test-Path $Report)) { throw "$Report not found -- run the evaluation first." }
    Write-Host "`n--- VERIFY PASS (re-run every BLOCKER/MAJOR repro) ---" -ForegroundColor Cyan
    Invoke-Agent $VerifyModel $VerifyPrompt "VERIFIER"
    Assert-EvalScope "verification"
}

Clear-ModelEnv
Write-Host "`nDone. Next:" -ForegroundColor Cyan
Write-Host "  1. Read the VERDICT and PILOT FLAG PROFILE at the top of $Report."
Write-Host "  2. Read the BLOCKER and MAJOR findings; the S2 group is the equivalence-engine spec input."
Write-Host "  3. Walk the HUMAN UI CHECKLIST in a browser (<= 25 min)."
Write-Host "  4. If you have not verified yet:  .\loop\run_e2e_eval.ps1 -VerifyOnly"
Write-Host "  5. Nothing to merge -- this branch is evidence, not code."
