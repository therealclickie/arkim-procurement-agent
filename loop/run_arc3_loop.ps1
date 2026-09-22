# run_arc3_loop.ps1 -- Opus 5 build -> Fable 5 review agentic loop
# Arc 3: supplier portal session conversion (frontend + minimal backend).
#
# BOTH agents run on the claude.ai subscription. No Fireworks, no API key.
#
# Usage:
#   cd "C:\dev\_Arkim\Arkim Procurement Agent Prototype"
#   .\loop\run_arc3_loop.ps1
#
#   Do NOT append '*> loop.log' -- logs internally (PS 5.1 stderr trap).
#
#   -SkipGate       : gate already run
#   -SkipFirstBuild : build already done; iteration 1 goes straight to review
#   -SkipBaseline   : skip the dual-suite baseline check (not recommended)
#
# Handshake files:
#   SUPPLIER_SESSION_BRIEF.md    -- input spec, repo root (must exist)
#   SUPPLIER_SESSION_REPORT.md   -- builder: gate + findings
#   SUPPLIER_SESSION_REVIEW.md   -- reviewer: numbered findings
#   loop/VERDICT.txt             -- reviewer: APPROVED | CHANGES_REQUESTED (gitignored)
# Logs: build.log / review.log / loop.log

param(
    [int]$MaxIterations   = 4,
    [switch]$SkipGate,
    [switch]$SkipFirstBuild,
    [switch]$SkipBaseline,
    [string]$RepoPath      = "C:\dev\_Arkim\Arkim Procurement Agent Prototype",
    [string]$Branch        = "arc3/supplier-session",
    [string]$BuilderModel  = "claude-opus-5",
    [string]$ReviewerModel = "claude-fable-5"
)

$ErrorActionPreference = "Continue"
$PSNativeCommandUseErrorActionPreference = $false
$ProgressPreference = "SilentlyContinue"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
Set-Location $RepoPath

$Brief   = "SUPPLIER_SESSION_BRIEF.md"
$Report  = "SUPPLIER_SESSION_REPORT.md"
$Review  = "SUPPLIER_SESSION_REVIEW.md"
$Verdict = "loop\VERDICT.txt"

$LoopLog = Join-Path $RepoPath "loop.log"
function Write-Log([string]$msg) {
    $line = "$(Get-Date -Format 'HH:mm:ss')  $msg"
    Write-Host $line
    Add-Content -Path $LoopLog -Value $line
}

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

# Both agents are on the subscription, so every provider override must be absent
# for BOTH calls. A stale ANTHROPIC_* var from a previous arc-2 session would
# silently route Opus to Fireworks.
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

# ---- Pre-flight -------------------------------------------------------------
if (-not (Test-Path (Join-Path $RepoPath $Brief))) {
    Write-Host "$Brief not found in repo root." -ForegroundColor Red
    throw "Missing brief."
}

Clear-ModelEnv
Write-Host "Auth check: both agents run on the subscription..." -ForegroundColor Cyan
$authProbe = (claude --model $BuilderModel -p "Reply with exactly: AUTH OK" 2>&1 | Out-String)
if ($authProbe -notmatch "AUTH OK") {
    Write-Host "Builder auth/model probe failed. Run 'claude login' and confirm the model string." -ForegroundColor Red
    Write-Host $authProbe
    throw "Auth probe failed."
}
Write-Log "Auth OK: $BuilderModel responding"

$dirtyLines = Git-Safe status --porcelain | Where-Object { $_ -notmatch '^\?\?' }
if (($dirtyLines -join "`n").Trim()) {
    Write-Host "Tracked files are modified. Commit or stash first:" -ForegroundColor Red
    $dirtyLines | ForEach-Object { Write-Host "  $_" }
    throw "Dirty working tree."
}

if (-not $SkipBaseline) {
    Write-Host "Baseline: backend (expect 2425 passed)..." -ForegroundColor Cyan
    foreach ($v in "RUN_CAPTURE","INTAKE_TYPE_AWARE","SCORING_V2","SUPPLIER_ACCOUNTS_V1") {
        Remove-Item "Env:$v" -ErrorAction SilentlyContinue
    }
    $py = (uv run pytest -q 2>&1 | Out-String)
    if ($py -notmatch "2425 passed") {
        Write-Host "Backend baseline is NOT 2425 passed. Merge arc 2 first." -ForegroundColor Red
        Write-Host (($py -split "`n" | Select-Object -Last 3) -join "`n")
        throw "Backend baseline failed."
    }
    Write-Log "Baseline OK: backend 2425 passed"

    Write-Host "Baseline: frontend (expect 64 passed)..." -ForegroundColor Cyan
    Push-Location (Join-Path $RepoPath "frontend")
    $fe = (npm test 2>&1 | Out-String)
    Pop-Location
    if ($fe -notmatch "64 passed") {
        Write-Host "Frontend baseline is NOT 64 passed." -ForegroundColor Red
        Write-Host (($fe -split "`n" | Where-Object { $_ -match 'Tests' }) -join "`n")
        throw "Frontend baseline failed."
    }
    Write-Log "Baseline OK: frontend 64 passed"
}

if (-not (Test-Path (Join-Path $RepoPath "loop"))) { New-Item -ItemType Directory -Path "loop" | Out-Null }
foreach ($entry in "build.log","review.log","loop.log","loop/VERDICT.txt") {
    $gi = Get-Content .gitignore -ErrorAction SilentlyContinue
    if ($gi -notcontains $entry) { Add-Content .gitignore $entry }
}
if ((Git-Safe status --porcelain .gitignore).Trim()) {
    Git-Safe add .gitignore | Out-Null
    Git-Safe commit -m "chore: ignore arc3 loop working files" | Out-Null
}

Write-Host "Branching from:" -ForegroundColor Cyan
Write-Host (Git-Safe log --oneline -3)
if ((Git-Safe branch --list $Branch).Trim() -eq "") { Git-Safe branch $Branch | Out-Null }
Git-Safe checkout $Branch | Out-Null
$BranchPoint = (Git-Safe rev-parse HEAD).Trim()
Write-Log "On branch: $((Git-Safe rev-parse --abbrev-ref HEAD).Trim()) at $BranchPoint"

$script:PreExistingUntracked = @((Git-Safe status --porcelain) -split "`n" |
    Where-Object { $_ -match '^\?\?' -and $_.Length -gt 3 } |
    ForEach-Object { ($_.Substring(3).Trim() -replace '"', '') })
Write-Log "Snapshot: $($script:PreExistingUntracked.Count) pre-existing untracked files excluded"

# Every test file that exists NOW is protected. Arc 1's two security files are the
# regression net for the token routes; arc 2's 120 tests guard the identity layer.
$script:PreExistingTests = @((Git-Safe ls-tree -r --name-only HEAD) -split "`n" |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ -match '__tests__/' -or $_ -match 'test_.*\.py$' -or $_ -match '\.test\.(ts|tsx)$' })
Write-Log "Protected: $($script:PreExistingTests.Count) pre-existing test files (must not be modified)"

# ---- Scope enforcer ---------------------------------------------------------
# Arc 3 legitimately touches BOTH frontend and backend. The rule that matters is
# that no pre-existing test file is edited -- the suites are the safety net.
function Test-DiffScope {
    $allowed = @(
        '^frontend/',
        '^utils/',
        '^api_server\.py$',
        '^[^/]+\.py$',
        '^migrations?/',
        '^alembic/',
        'pyproject\.toml$',
        'SUPPLIER_SESSION_(REPORT|REVIEW)\.md$',
        '\.gitignore$',
        '^loop/'
    )

    $committed = (Git-Safe diff --name-only $BranchPoint HEAD) -split "`n"
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

        if ($script:PreExistingTests -contains $f) {
            if ($bad -notcontains "EXISTING TEST MODIFIED: $f") { $bad += "EXISTING TEST MODIFIED: $f" }
            continue
        }

        $ok = $false
        foreach ($p in $allowed) { if ($f -match $p) { $ok = $true; break } }
        if (-not $ok -and $bad -notcontains $f) { $bad += $f }
    }
    return $bad
}

function Assert-Scope([string]$stage) {
    $bad = Test-DiffScope
    if ($bad.Count -gt 0) {
        Write-Host "PRIME DIRECTIVE VIOLATED during $stage :" -ForegroundColor Red
        $bad | ForEach-Object { Write-Host "  $_" -ForegroundColor Red; Write-Log "  VIOLATION: $_" }
        Write-Log "Loop stopped. Inspect with: git diff $BranchPoint"
        exit 2
    }
    Write-Log "Scope clean after $stage"
}

function Invoke-Agent([string]$Model, [string]$Prompt, [string]$LogFile, [string]$Stage) {
    Clear-ModelEnv
    "`n===== $Stage ($Model) -- $(Get-Date -Format o) =====" | Add-Content $LogFile
    claude --model $Model --dangerously-skip-permissions -p $Prompt *>> $LogFile
}

# ---- Prompts ----------------------------------------------------------------
$GatePrompt = @"
You are the BUILDER in a two-agent loop. Read SUPPLIER_SESSION_BRIEF.md in the repo root.

This turn: run the INVESTIGATION GATE ONLY (G1 through G8). Build nothing.
Write findings to SUPPLIER_SESSION_REPORT.md with file:line references for every claim.

G1 IS A POTENTIAL STOP CONDITION: determine whether the new session routes can be built and
render-tested on the installed React 18.3.1, or whether a React 19 bump is a prerequisite. If it
is a prerequisite, say so plainly and STOP -- the bump is its own arc with a full regression run.
Do NOT bump React inside this arc.

PRIME DIRECTIVE: you may not modify ANY pre-existing test file, frontend or backend. Arc 1's two
security test files are the regression net for the token routes. New tests go in new files. The
loop terminates automatically if you edit an existing test file.

Commit the report on branch $Branch. Stop after the gate.
"@

$BuildPrompt = @"
You are the BUILDER. Read SUPPLIER_SESSION_BRIEF.md and your gate findings in
SUPPLIER_SESSION_REPORT.md.

Build tasks T1 through T11. Commit after each task, message naming the task. Run 'uv run pytest -q'
and 'npm test' (in frontend/) after each task; the 2425 backend and 64 frontend tests must stay
green WITHOUT editing any of them.

Hold these invariants: the session is an httpOnly cookie the frontend never reads (D1); no auth UI
copy distinguishes a known email from an unknown one (D5); RBAC is reflected in the UI but enforced
on the server (D6); the token routes behave identically to today (D3/prime directive 4).

T11 is droppable. If T1-T10 have consumed the arc, report T11 as deferred -- do not rush it.

When done, append to SUPPLIER_SESSION_REPORT.md: both final test counts, and a FINDINGS section
(write 'None' if empty). Do not push.
"@

$FixPrompt = @"
You are the BUILDER. The reviewer returned CHANGES_REQUESTED. Read SUPPLIER_SESSION_REVIEW.md and
fix ONLY the numbered findings. Do not refactor anything else.

PRIME DIRECTIVE still applies: no pre-existing test file modified; flags off means today's
behaviour.

Append a fix log to SUPPLIER_SESSION_REPORT.md per numbered finding. Run both suites, confirm
green, commit on branch $Branch. Do not push.
"@

$ReviewPrompt = @"
You are the REVIEWER, not the builder. You did not write this code and you are not here to be
agreeable. Read SUPPLIER_SESSION_BRIEF.md, then SUPPLIER_SESSION_REPORT.md, then the actual diff.

Answer R1 through R9 from the brief's reviewer checklist explicitly, each with file:line evidence.

R1 FIRST: run 'git diff --name-status $BranchPoint HEAD'. Any modification (status M) to a
pre-existing test file -- especially arc 1's portal/quote security.test.tsx -- is an immediate
CHANGES_REQUESTED.

R2: run BOTH suites with flags off (SUPPLIER_ACCOUNTS_V1=0 and the frontend session flag off).

R3: read the actual Set-Cookie header in a test response and confirm HttpOnly, Secure,
SameSite=Lax and Path. Do not trust the report's claim.

R7: confirm the CSRF origin check is tested against the real dependency, not a mock.

R8: confirm there is a test that calls a hidden member-management endpoint DIRECTLY and gets 403 --
proving the UI is not the enforcement point.

The ONLY files you may create or modify are SUPPLIER_SESSION_REVIEW.md and loop/VERDICT.txt.
Never fix builder code. Do NOT commit loop/VERDICT.txt -- it is gitignored deliberately.

Write numbered findings with severity (BLOCKER/MAJOR/MINOR), file:line and the brief clause
violated. Commit only the review file. Then write loop/VERDICT.txt containing exactly one word:
APPROVED  or  CHANGES_REQUESTED
"@

# ---- Gate -------------------------------------------------------------------
if (-not $SkipGate) {
    Write-Host "`n--- GATE: investigation (G1 may stop the arc) ---" -ForegroundColor Cyan
    Invoke-Agent $BuilderModel $GatePrompt "build.log" "BUILDER gate"
    Assert-Scope "gate"
    Write-Host "Gate complete. If G1 reported a React bump as prerequisite, STOP and read the report." -ForegroundColor Yellow
} else {
    Write-Host "`n--- GATE skipped ---" -ForegroundColor Cyan
}

# ---- Loop -------------------------------------------------------------------
Remove-Item $Verdict -ErrorAction SilentlyContinue

for ($i = 1; $i -le $MaxIterations; $i++) {

    if ($i -eq 1 -and $SkipFirstBuild) {
        Write-Host "`n--- Iteration 1 : build already done, straight to review ---" -ForegroundColor Cyan
    }
    elseif ($i -eq 1) {
        Write-Host "`n--- Iteration $i of $MaxIterations : BUILD (T1-T11) ---" -ForegroundColor Cyan
        Invoke-Agent $BuilderModel $BuildPrompt "build.log" "BUILDER build T1-T11"
        Assert-Scope "builder iteration $i"
    }
    else {
        Write-Host "`n--- Iteration $i of $MaxIterations : BUILD (fixes) ---" -ForegroundColor Cyan
        Invoke-Agent $BuilderModel $FixPrompt "build.log" "BUILDER fix round $i"
        Assert-Scope "builder iteration $i"
    }

    Write-Host "--- Iteration $i : REVIEW ---" -ForegroundColor Yellow
    Remove-Item $Verdict -ErrorAction SilentlyContinue
    Invoke-Agent $ReviewerModel $ReviewPrompt "review.log" "REVIEWER iteration $i"

    if (-not (Test-Path $Verdict)) {
        Write-Host "Reviewer produced no $Verdict -- stopping for human attention." -ForegroundColor Red
        break
    }
    $v = (Get-Content $Verdict -Raw).Trim()
    Write-Log "Verdict: $v"

    if ($v -eq "APPROVED") {
        Write-Host "`nAPPROVED after $i iteration(s). Nothing pushed." -ForegroundColor Green
        break
    }
    if ($v -ne "CHANGES_REQUESTED") {
        Write-Host "Unexpected verdict '$v' -- stopping for human attention." -ForegroundColor Red
        break
    }
    if ($i -eq $MaxIterations) {
        Write-Host "`nMax iterations reached without APPROVED. Read $Review." -ForegroundColor Red
    }
}

# ---- Close: flags-off verification -----------------------------------------
Clear-ModelEnv
Write-Host "`nFlags-OFF verification..." -ForegroundColor Cyan
$env:SUPPLIER_ACCOUNTS_V1 = "0"
$off = (uv run pytest -q 2>&1 | Out-String)
Remove-Item Env:SUPPLIER_ACCOUNTS_V1 -ErrorAction SilentlyContinue
Write-Log ("Flags-off backend: " + (($off -split "`n" | Where-Object { $_ -match 'passed' }) -join ' '))

Push-Location (Join-Path $RepoPath "frontend")
$feOff = (npm test 2>&1 | Out-String)
Pop-Location
Write-Log ("Frontend: " + (($feOff -split "`n" | Where-Object { $_ -match 'Tests' }) -join ' '))

Write-Host "`nMorning protocol:" -ForegroundColor Cyan
Write-Host "  1. Read FINDINGS in $Report (and the G1 React verdict)."
Write-Host "  2. Read $Review."
Write-Host "  3. git diff --name-status $BranchPoint HEAD  (no 'M' on any pre-existing test file)"
Write-Host "  4. uv run pytest -q                          (expect 2425 + N)"
Write-Host "  5. cd frontend ; npm test                    (expect 64 + M)"
Write-Host "  6. Manually: cookie is HttpOnly and unreadable from the JS console."
Write-Host "  7. Merge yourself. Claude never merges."
