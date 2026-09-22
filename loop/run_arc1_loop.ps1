# run_arc1_loop.ps1 -- GLM build -> Fable review agentic loop
# Arc 1: frontend test floor over portal/quote/admin surfaces.
#
# Usage:
#   cd "C:\dev\_Arkim\Arkim Procurement Agent Prototype"
#   $env:FIREWORKS_API_KEY = "fw_YWfwsxxdCtDKQmbFVB8HBy"        # session only, never commit
#   .\loop\run_arc1_loop.ps1
#
#   Do NOT append '*> loop.log' -- logs internally (PS 5.1 stderr trap).
#
#   -SkipGate     : gate already run externally; go straight to the build turn
#   -SkipBaseline : skip the 2305-passed backend check (not recommended)
#
# Handshake files (repo root):
#   FRONTEND_TEST_FLOOR_BRIEF.md   -- input spec (must exist)
#   FRONTEND_TEST_FLOOR_REPORT.md  -- written by builder (gate + findings)
#   FRONTEND_TEST_FLOOR_REVIEW.md  -- written by reviewer (numbered findings)
#   VERDICT.txt                    -- reviewer: APPROVED | CHANGES_REQUESTED
# Logs: build.log / review.log / loop.log

param(
    [int]$MaxIterations = 4,
    [switch]$SkipGate,
    [switch]$SkipFirstBuild,
    [switch]$SkipBaseline,
    [string]$RepoPath   = "C:\dev\_Arkim\Arkim Procurement Agent Prototype",
    [string]$Branch     = "arc1/frontend-test-floor",
    [string]$GlmModel   = "glm-fast-latest",
    [string]$CheapModel = "deepseek-v4-flash"
)

$ErrorActionPreference = "Continue"                 # native stderr must never terminate us
$PSNativeCommandUseErrorActionPreference = $false
$ProgressPreference = "SilentlyContinue"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
Set-Location $RepoPath

$Brief  = "FRONTEND_TEST_FLOOR_BRIEF.md"
$Report = "FRONTEND_TEST_FLOOR_REPORT.md"
$Review = "FRONTEND_TEST_FLOOR_REVIEW.md"

$LoopLog = Join-Path $RepoPath "loop.log"
function Write-Log([string]$msg) {
    $line = "$(Get-Date -Format 'HH:mm:ss')  $msg"
    Write-Host $line
    Add-Content -Path $LoopLog -Value $line
}

# git with stderr diverted to disk, so PowerShell never escalates git's
# informational messages ("Switched to branch...") into a terminating error.
function Git-Safe {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)
    $errFile = [System.IO.Path]::GetTempFileName()
    try {
        $out = & git @Args 2>$errFile
        return ($out | Out-String)
    } finally {
        Remove-Item $errFile -ErrorAction SilentlyContinue
    }
}

function Clear-ModelEnv {
    foreach ($v in "ANTHROPIC_API_KEY","ANTHROPIC_BASE_URL","ANTHROPIC_AUTH_TOKEN",
                   "ANTHROPIC_CUSTOM_HEADERS","ANTHROPIC_MODEL",
                   "ANTHROPIC_DEFAULT_FABLE_MODEL","ANTHROPIC_DEFAULT_OPUS_MODEL",
                   "ANTHROPIC_DEFAULT_SONNET_MODEL","ANTHROPIC_DEFAULT_HAIKU_MODEL",
                   "CLAUDE_CODE_SUBAGENT_MODEL") {
        Remove-Item "Env:$v" -ErrorAction SilentlyContinue
    }
}

# ---- Pre-flight -------------------------------------------------------------
if (-not $env:FIREWORKS_API_KEY) { throw "Set `$env:FIREWORKS_API_KEY before running." }

if (-not (Test-Path (Join-Path $RepoPath $Brief))) {
    Write-Host "$Brief not found in repo root." -ForegroundColor Red
    throw "Missing brief."
}

# Refuse to run over a dirty TRACKED tree. Untracked files are fine -- this repo
# has many untracked briefs, dump scripts and audit docs.
$dirtyLines = Git-Safe status --porcelain | Where-Object { $_ -notmatch '^\?\?' }
if (($dirtyLines -join "`n").Trim()) {
    Write-Host "Tracked files are modified. Commit or stash first:" -ForegroundColor Red
    $dirtyLines | ForEach-Object { Write-Host "  $_" }
    throw "Dirty working tree."
}

if (-not $SkipBaseline) {
    Write-Host "Baseline: backend suite (expect 2305 passed / 73 skipped)..." -ForegroundColor Cyan
    foreach ($v in "RUN_CAPTURE","INTAKE_TYPE_AWARE","SCORING_V2") {
        Remove-Item "Env:$v" -ErrorAction SilentlyContinue
    }
    $pytest = (uv run pytest -q 2>&1 | Out-String)
    if ($pytest -notmatch "2305 passed") {
        Write-Host "Backend baseline is NOT 2305 passed. Fix before building." -ForegroundColor Red
        Write-Host (($pytest -split "`n" | Select-Object -Last 3) -join "`n")
        throw "Baseline failed."
    }
    Write-Log "Baseline OK: 2305 passed"
}

foreach ($entry in "build.log","review.log","loop.log","VERDICT.txt") {
    $gi = Get-Content .gitignore -ErrorAction SilentlyContinue
    if ($gi -notcontains $entry) { Add-Content .gitignore $entry }
}
if ((Git-Safe status --porcelain .gitignore).Trim()) {
    Git-Safe add .gitignore | Out-Null
    Git-Safe commit -m "chore: ignore arc1 loop working files" | Out-Null
}

Write-Host "Branching from:" -ForegroundColor Cyan
Write-Host (Git-Safe log --oneline -3)
if ((Git-Safe branch --list $Branch).Trim() -eq "") { Git-Safe branch $Branch | Out-Null }
Git-Safe checkout $Branch | Out-Null
$BranchPoint = (Git-Safe rev-parse HEAD).Trim()
Write-Log "On branch: $((Git-Safe rev-parse --abbrev-ref HEAD).Trim()) at $BranchPoint"

# This repo carries many pre-existing untracked files (briefs, dumps, audit docs,
# screenshots). Snapshot them now so the scope check flags only files the BUILDER
# creates, not ones that were already sitting there.
$script:PreExistingUntracked = @((Git-Safe status --porcelain) -split "`n" |
    Where-Object { $_ -match '^\?\?' -and $_.Length -gt 3 } |
    ForEach-Object { ($_.Substring(3).Trim() -replace '"', '') })
Write-Log "Snapshot: $($script:PreExistingUntracked.Count) pre-existing untracked files excluded from scope check"

# ---- Prime-directive enforcer ----------------------------------------------
# The brief forbids application-source changes. Trusting the reviewer to catch a
# violation is not enough unsupervised -- this checks mechanically after every
# builder turn, against BOTH committed and working-tree changes, and kills the
# loop on the first breach.
function Test-DiffScope {
    $allowed = @(
        '__tests__/',
        'test-support/',
        'src/test/',
        '\.test\.(ts|tsx)$',
        '\.spec\.(ts|tsx)$',
        'vitest\.config',
        'vitest\.setup',
        'frontend/package\.json$',
        'frontend/package-lock\.json$',
        'FRONTEND_TEST_FLOOR_(REPORT|REVIEW)\.md$',
        'VERDICT\.txt$',
        '\.gitignore$',
        '^loop/'
    )

    # Committed since the branch point
    $committed = (Git-Safe diff --name-only $BranchPoint HEAD) -split "`n"

    # Working tree: all modified tracked files, plus untracked files that did NOT
    # exist when the loop started. Pre-existing untracked files are the repo's own
    # clutter and are not the builder's doing.
    $working = @()
    foreach ($line in ((Git-Safe status --porcelain) -split "`n")) {
        if ($line.Length -le 3) { continue }
        $path = ($line.Substring(3).Trim() -replace '"', '')
        if ($line -match '^\?\?') {
            if ($script:PreExistingUntracked -notcontains $path) { $working += $path }
        } else {
            $working += $path
        }
    }

    $bad = @()
    foreach ($f in ($committed + $working)) {
        $f = ($f -replace '"', '').Trim()
        if (-not $f) { continue }
        $ok = $false
        foreach ($p in $allowed) { if ($f -match $p) { $ok = $true; break } }
        if (-not $ok -and $bad -notcontains $f) { $bad += $f }
    }
    return $bad
}

function Assert-Scope([string]$stage) {
    $bad = Test-DiffScope
    if ($bad.Count -gt 0) {
        Write-Host "PRIME DIRECTIVE VIOLATED during $stage -- non-test files changed:" -ForegroundColor Red
        $bad | ForEach-Object { Write-Host "  $_" -ForegroundColor Red; Write-Log "  VIOLATION: $_" }
        Write-Log "Loop stopped. Inspect with: git diff $BranchPoint"
        Clear-ModelEnv
        exit 2
    }
    Write-Log "Scope clean after $stage"
}

function Invoke-Builder([string]$Prompt, [string]$Stage) {
    Clear-ModelEnv
    $env:ANTHROPIC_BASE_URL             = "https://api.fireworks.ai/inference"
    $env:ANTHROPIC_CUSTOM_HEADERS       = "x-fireworks-api-key: $($env:FIREWORKS_API_KEY)"
    $env:ANTHROPIC_MODEL                = $GlmModel
    $env:ANTHROPIC_DEFAULT_FABLE_MODEL  = $GlmModel
    $env:ANTHROPIC_DEFAULT_OPUS_MODEL   = $GlmModel
    $env:ANTHROPIC_DEFAULT_SONNET_MODEL = $GlmModel
    $env:ANTHROPIC_DEFAULT_HAIKU_MODEL  = $CheapModel
    $env:CLAUDE_CODE_SUBAGENT_MODEL     = $CheapModel

    "`n===== BUILDER $Stage -- $(Get-Date -Format o) =====" | Add-Content build.log
    claude --dangerously-skip-permissions --verbose -p $Prompt *>> build.log
}

function Invoke-Reviewer([string]$Prompt, [int]$Iter) {
    Clear-ModelEnv
    # All overrides cleared: Claude Code falls back to the claude.ai subscription.
    "`n===== REVIEWER iteration $Iter -- $(Get-Date -Format o) =====" | Add-Content review.log
    claude --model claude-fable-5 --dangerously-skip-permissions --verbose -p $Prompt *>> review.log
}

# ---- Prompts ----------------------------------------------------------------
$GatePrompt = @"
You are the BUILDER in a two-agent loop. Read FRONTEND_TEST_FLOOR_BRIEF.md in the repo root.

This turn: run the INVESTIGATION GATE ONLY (I1 through I6). Write NO tests yet.
Write findings to FRONTEND_TEST_FLOOR_REPORT.md with file:line references for every claim.
Pay particular attention to I2 (which components are server vs client -- this determines what is
testable at all) and I4 (what open-requests.tsx actually does; it was out of scope in the earlier
SUPPLIER_PORTAL_FRONTEND_BRIEF.md yet exists).

If the gate shows that tasks in the brief cannot be built as written, say so plainly in the report
rather than improvising an alternative.

PRIME DIRECTIVE, in force for the whole arc: you may not modify application source. Tests, test
config and frontend/package.json devDependencies only. The loop terminates automatically if you
touch source.

Commit the report on branch $Branch. Stop after the gate. Do not start T1.
"@

$BuildPrompt = @"
You are the BUILDER. Read FRONTEND_TEST_FLOOR_BRIEF.md and your own gate findings in
FRONTEND_TEST_FLOOR_REPORT.md.

This turn: build tasks T1 through T8. Commit after each task, message naming the task.
Run 'npm test' in the frontend directory after each task and fix your own test failures.

PRIME DIRECTIVE: no application source changes. If an assertion fails against current source, mark
the test .skip with a '// FINDING:' comment and record it under FINDINGS in the report. Do NOT edit
source to make a test pass -- that finding is the point of this arc.

When done, append to FRONTEND_TEST_FLOOR_REPORT.md: the final 'npm test' count, and a FINDINGS
section (write 'None' if empty). Do not push.
"@

$FixPrompt = @"
You are the BUILDER. The reviewer returned CHANGES_REQUESTED. Read FRONTEND_TEST_FLOOR_REVIEW.md
and fix ONLY the numbered findings it raises. Do not refactor anything else.

PRIME DIRECTIVE still applies: no application source changes.

Append a fix log to FRONTEND_TEST_FLOOR_REPORT.md stating what you changed for each numbered
finding. Run 'npm test', confirm green, commit on branch $Branch. Do not push.
"@

$ReviewPrompt = @"
You are the REVIEWER, not the builder. You did not write this code and you are not here to be
agreeable. Read FRONTEND_TEST_FLOOR_BRIEF.md, then FRONTEND_TEST_FLOOR_REPORT.md, then the actual
diff and test files.

Answer R1 through R8 from the brief's reviewer checklist explicitly, each with file:line evidence.
R1 FIRST: run 'git diff --stat $BranchPoint HEAD'. If any change falls outside test files, test
config or devDependencies, return CHANGES_REQUESTED immediately and name the files.

Run 'npm test' in the frontend directory TWICE to check for order or timing dependence.
Also run 'uv run pytest -q' once and confirm the backend is still 2305 passed / 73 skipped.

The ONLY files you may create or modify are FRONTEND_TEST_FLOOR_REVIEW.md and VERDICT.txt. Never
fix builder code yourself.

Write findings to FRONTEND_TEST_FLOOR_REVIEW.md: one numbered section per finding with severity
(BLOCKER/MAJOR/MINOR), file:line, and the brief clause violated. Judge the builder's FINDINGS
honestly -- are they genuine source-behaviour findings, or is .skip being used to dodge hard tests?

Finally write VERDICT.txt containing exactly one word on one line:
APPROVED  or  CHANGES_REQUESTED
Commit both files on branch $Branch.
"@

# ---- Gate -------------------------------------------------------------------
if (-not $SkipGate) {
    Write-Host "`n--- GATE: investigation ---" -ForegroundColor Cyan
    Invoke-Builder $GatePrompt "gate"
    Assert-Scope "gate"
} else {
    Write-Host "`n--- GATE skipped (run externally) ---" -ForegroundColor Cyan
}

# ---- Loop -------------------------------------------------------------------
Remove-Item VERDICT.txt -ErrorAction SilentlyContinue

for ($i = 1; $i -le $MaxIterations; $i++) {

    if ($i -eq 1 -and $SkipFirstBuild) {
        Write-Host "`n--- Iteration 1 : build already done externally, straight to review ---" -ForegroundColor Cyan
    }
    elseif ($i -eq 1) {
        Write-Host "`n--- Iteration $i of $MaxIterations : BUILD (T1-T8) ---" -ForegroundColor Cyan
        Invoke-Builder $BuildPrompt "build T1-T8"
        Assert-Scope "builder iteration $i"
    } else {
        Write-Host "`n--- Iteration $i of $MaxIterations : BUILD (fixes) ---" -ForegroundColor Cyan
        Invoke-Builder $FixPrompt "fix round $i"
        Assert-Scope "builder iteration $i"
    }

    Write-Host "--- Iteration $i : REVIEW ---" -ForegroundColor Yellow
    Remove-Item VERDICT.txt -ErrorAction SilentlyContinue
    Invoke-Reviewer $ReviewPrompt $i

    if (-not (Test-Path VERDICT.txt)) {
        Write-Host "Reviewer produced no VERDICT.txt -- stopping for human attention." -ForegroundColor Red
        break
    }
    $verdict = (Get-Content VERDICT.txt -Raw).Trim()
    Write-Log "Verdict: $verdict"

    if ($verdict -eq "APPROVED") {
        Write-Host "`nAPPROVED after $i iteration(s). Nothing pushed." -ForegroundColor Green
        break
    }
    if ($verdict -ne "CHANGES_REQUESTED") {
        Write-Host "Unexpected verdict '$verdict' -- stopping for human attention." -ForegroundColor Red
        break
    }
    if ($i -eq $MaxIterations) {
        Write-Host "`nMax iterations reached without APPROVED. Read $Review." -ForegroundColor Red
    }
}

Clear-ModelEnv
Write-Host "`nMorning protocol:" -ForegroundColor Cyan
Write-Host "  1. Read the FINDINGS section of $Report -- the deliverable only you can action."
Write-Host "  2. Read $Review."
Write-Host "  3. git diff --stat $BranchPoint HEAD   (expect tests/config/devDeps only)"
Write-Host "  4. cd frontend ; npm test              (expect 8 + N)"
Write-Host "  5. uv run pytest -q                    (expect 2305 passed / 73 skipped)"
Write-Host "  6. Merge yourself. Claude never merges."
