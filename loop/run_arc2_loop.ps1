# run_arc2_loop.ps1 -- GLM build -> Fable review agentic loop
# Arc 2: supplier identity (backend only, flag-gated).
#
# Usage:
#   cd "C:\dev\_Arkim\Arkim Procurement Agent Prototype"
#   $env:FIREWORKS_API_KEY = "fw_xxxx"        # session only, never commit
#   .\loop\run_arc2_loop.ps1
#
#   Do NOT append '*> loop.log' -- logs internally (PS 5.1 stderr trap).
#
#   -SkipGate       : gate already run; go straight to the build turn
#   -SkipFirstBuild : build already done; iteration 1 goes straight to review
#   -SkipBaseline   : skip the 2305-passed backend check (not recommended)
#
# Handshake files:
#   SUPPLIER_IDENTITY_BRIEF.md    -- input spec, repo root (must exist)
#   SUPPLIER_IDENTITY_REPORT.md   -- written by builder (gate + findings)
#   SUPPLIER_IDENTITY_REVIEW.md   -- written by reviewer (numbered findings)
#   loop/VERDICT.txt              -- reviewer: APPROVED | CHANGES_REQUESTED
#                                    (gitignored, never committed -- arc 1 tripped
#                                     over a tracked VERDICT.txt being deleted)
# Logs: build.log / review.log / loop.log

param(
    [int]$MaxIterations = 4,
    [switch]$SkipGate,
    [switch]$SkipFirstBuild,
    [switch]$SkipBaseline,
    [string]$RepoPath   = "C:\dev\_Arkim\Arkim Procurement Agent Prototype",
    [string]$Branch     = "arc2/supplier-identity",
    [string]$GlmModel   = "glm-fast-latest",
    [string]$CheapModel = "deepseek-v4-flash"
)

$ErrorActionPreference = "Continue"                 # native stderr must never terminate us
$PSNativeCommandUseErrorActionPreference = $false
$ProgressPreference = "SilentlyContinue"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
Set-Location $RepoPath

$Brief   = "SUPPLIER_IDENTITY_BRIEF.md"
$Report  = "SUPPLIER_IDENTITY_REPORT.md"
$Review  = "SUPPLIER_IDENTITY_REVIEW.md"
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

function Clear-ModelEnv {
    foreach ($v in "ANTHROPIC_API_KEY","ANTHROPIC_BASE_URL","ANTHROPIC_AUTH_TOKEN",
                   "ANTHROPIC_CUSTOM_HEADERS","ANTHROPIC_MODEL",
                   "ANTHROPIC_DEFAULT_FABLE_MODEL","ANTHROPIC_DEFAULT_OPUS_MODEL",
                   "ANTHROPIC_DEFAULT_SONNET_MODEL","ANTHROPIC_DEFAULT_HAIKU_MODEL",
                   "CLAUDE_CODE_SUBAGENT_MODEL","CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT") {
        Remove-Item "Env:$v" -ErrorAction SilentlyContinue
    }
}

# ---- Pre-flight -------------------------------------------------------------
if (-not $env:FIREWORKS_API_KEY) { throw "Set `$env:FIREWORKS_API_KEY before running." }
if ($env:FIREWORKS_API_KEY.Length -lt 20) {
    throw "FIREWORKS_API_KEY looks like a placeholder (length $($env:FIREWORKS_API_KEY.Length)). Set the real key."
}

if (-not (Test-Path (Join-Path $RepoPath $Brief))) {
    Write-Host "$Brief not found in repo root." -ForegroundColor Red
    throw "Missing brief."
}

$dirtyLines = Git-Safe status --porcelain | Where-Object { $_ -notmatch '^\?\?' }
if (($dirtyLines -join "`n").Trim()) {
    Write-Host "Tracked files are modified. Commit or stash first:" -ForegroundColor Red
    $dirtyLines | ForEach-Object { Write-Host "  $_" }
    throw "Dirty working tree."
}

if (-not $SkipBaseline) {
    Write-Host "Baseline: backend suite (expect 2305 passed / 73 skipped)..." -ForegroundColor Cyan
    foreach ($v in "RUN_CAPTURE","INTAKE_TYPE_AWARE","SCORING_V2","SUPPLIER_ACCOUNTS_V1") {
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

if (-not (Test-Path (Join-Path $RepoPath "loop"))) { New-Item -ItemType Directory -Path "loop" | Out-Null }
foreach ($entry in "build.log","review.log","loop.log","loop/VERDICT.txt") {
    $gi = Get-Content .gitignore -ErrorAction SilentlyContinue
    if ($gi -notcontains $entry) { Add-Content .gitignore $entry }
}
if ((Git-Safe status --porcelain .gitignore).Trim()) {
    Git-Safe add .gitignore | Out-Null
    Git-Safe commit -m "chore: ignore arc2 loop working files" | Out-Null
}

Write-Host "Branching from:" -ForegroundColor Cyan
Write-Host (Git-Safe log --oneline -3)
if ((Git-Safe branch --list $Branch).Trim() -eq "") { Git-Safe branch $Branch | Out-Null }
Git-Safe checkout $Branch | Out-Null
$BranchPoint = (Git-Safe rev-parse HEAD).Trim()
Write-Log "On branch: $((Git-Safe rev-parse --abbrev-ref HEAD).Trim()) at $BranchPoint"

# Snapshot pre-existing untracked clutter (repo carries ~70 briefs/dumps/audit docs)
$script:PreExistingUntracked = @((Git-Safe status --porcelain) -split "`n" |
    Where-Object { $_ -match '^\?\?' -and $_.Length -gt 3 } |
    ForEach-Object { ($_.Substring(3).Trim() -replace '"', '') })
Write-Log "Snapshot: $($script:PreExistingUntracked.Count) pre-existing untracked files excluded from scope check"

# Snapshot every EXISTING test file. The brief forbids modifying any of them --
# the 2305 existing tests are the regression net and must pass unedited.
$script:PreExistingTests = @((Git-Safe ls-tree -r --name-only HEAD) -split "`n" |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ -match '(^|/)tests?/' -or $_ -match 'test_.*\.py$' -or $_ -match '\.test\.(ts|tsx)$' })
Write-Log "Protected: $($script:PreExistingTests.Count) pre-existing test files (must not be modified)"

# ---- Prime-directive enforcer (INVERTED vs arc 1) ---------------------------
# Arc 1 forbade source changes. Arc 2 is a backend build: source IS the work.
# What is forbidden here is (a) anything under frontend/, and (b) modifying any
# test file that already existed at the branch point.
function Test-DiffScope {
    $allowed = @(
        '^utils/procurement_agent/',
        '^utils/[^/]+\.py$',
        '^migrations?/',
        '^alembic/',
        'pyproject\.toml$',
        'SUPPLIER_IDENTITY_(REPORT|REVIEW)\.md$',
        '\.gitignore$',
        '^loop/'
    )
    $forbidden = @(
        '^frontend/'
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

        # (a) hard-forbidden paths
        $isForbidden = $false
        foreach ($p in $forbidden) { if ($f -match $p) { $isForbidden = $true; break } }
        if ($isForbidden) {
            if ($bad -notcontains "FRONTEND: $f") { $bad += "FRONTEND: $f" }
            continue
        }

        # (b) modification of a pre-existing test file
        if ($script:PreExistingTests -contains $f) {
            if ($bad -notcontains "EXISTING TEST MODIFIED: $f") { $bad += "EXISTING TEST MODIFIED: $f" }
            continue
        }

        # (c) outside the allowlist
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
    $env:CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT = "1"

    "`n===== BUILDER $Stage -- $(Get-Date -Format o) =====" | Add-Content build.log
    claude --dangerously-skip-permissions -p $Prompt *>> build.log
}

function Invoke-Reviewer([string]$Prompt, [int]$Iter) {
    Clear-ModelEnv
    "`n===== REVIEWER iteration $Iter -- $(Get-Date -Format o) =====" | Add-Content review.log
    claude --model claude-fable-5 --dangerously-skip-permissions -p $Prompt *>> review.log
}

# ---- Prompts ----------------------------------------------------------------
$GatePrompt = @"
You are the BUILDER in a two-agent loop. Read SUPPLIER_IDENTITY_BRIEF.md in the repo root.

This turn: run the INVESTIGATION GATE ONLY (I1 through I9). Write NO implementation yet.
Write findings to SUPPLIER_IDENTITY_REPORT.md with file:line references for every claim.
Pay particular attention to I4/I5 (whether capability entries carry provenance today, and exactly
where a self-declared capability could influence band assignment -- this is the seam D3 protects)
and I6 (how outbound mail enters send governance).

If any task in the brief cannot be built as written, say so plainly rather than improvising.

PRIME DIRECTIVE for this arc: backend only. NOTHING under frontend/. You may NOT modify any
existing test file -- the 2305 existing tests must pass unedited; new tests go in new files.
Everything new is gated behind SUPPLIER_ACCOUNTS_V1, default off. The loop terminates
automatically if you touch frontend/ or edit an existing test file.

Commit the report on branch $Branch. Stop after the gate. Do not start T1.
"@

$BuildPrompt = @"
You are the BUILDER. Read SUPPLIER_IDENTITY_BRIEF.md and your own gate findings in
SUPPLIER_IDENTITY_REPORT.md.

This turn: build tasks T1 through T11. Commit after each task, message naming the task.
Run 'uv run pytest -q' after each task. The 2305 pre-existing tests must stay green WITHOUT
editing any of them.

PRIME DIRECTIVE: backend only, nothing under frontend/, no existing test file modified, every new
route and behaviour gated behind SUPPLIER_ACCOUNTS_V1 (default off). With the flag off the suite
must be indistinguishable from today's.

Pay special attention to D7 (RBAC): enforcement goes through ONE capability matrix and a
has_permission() dependency. Do NOT write inline role comparisons in route handlers.
And D3: a SUPPLIER_SELF capability must never move a supplier across evidence bands.

When done, append to SUPPLIER_IDENTITY_REPORT.md: the final test count, and a FINDINGS section
(write 'None' if empty). Do not push.
"@

$FixPrompt = @"
You are the BUILDER. The reviewer returned CHANGES_REQUESTED. Read SUPPLIER_IDENTITY_REVIEW.md
and fix ONLY the numbered findings it raises. Do not refactor anything else.

PRIME DIRECTIVE still applies: backend only, nothing under frontend/, no existing test file
modified, flag-gated and inert when off.

Append a fix log to SUPPLIER_IDENTITY_REPORT.md stating what you changed for each numbered
finding. Run 'uv run pytest -q', confirm green, commit on branch $Branch. Do not push.
"@

$ReviewPrompt = @"
You are the REVIEWER, not the builder. You did not write this code and you are not here to be
agreeable. Read SUPPLIER_IDENTITY_BRIEF.md, then SUPPLIER_IDENTITY_REPORT.md, then the actual
diff and the new code.

Answer R1 through R9 from the brief's reviewer checklist explicitly, each with file:line evidence.

R1 FIRST: run 'git diff --stat $BranchPoint HEAD'. Any change under frontend/, or any modification
to a test file that existed before this arc, is an immediate CHANGES_REQUESTED.

R2: run the suite with SUPPLIER_ACCOUNTS_V1 explicitly OFF and confirm it is the pre-arc suite plus
new tests asserting 404. Any new behaviour leaking through with the flag off is a BLOCKER.

R9: grep every route handler for inline role comparisons. Any inline role check in route code is
MAJOR -- D7 requires matrix-driven enforcement.

Do not trust the report's claims about hashing, send governance or the D3 band guard -- read the
code and the tests yourself. A test that mocks the send-governance gate rather than exercising it
is a MAJOR finding.

The ONLY files you may create or modify are SUPPLIER_IDENTITY_REVIEW.md and loop/VERDICT.txt.
Never fix builder code yourself. Do NOT commit loop/VERDICT.txt -- it is gitignored deliberately.

Write findings to SUPPLIER_IDENTITY_REVIEW.md: one numbered section per finding with severity
(BLOCKER/MAJOR/MINOR), file:line, and the brief clause violated. Commit only the review file.

Finally write loop/VERDICT.txt containing exactly one word on one line:
APPROVED  or  CHANGES_REQUESTED
"@

# ---- Gate -------------------------------------------------------------------
if (-not $SkipGate) {
    Write-Host "`n--- GATE: investigation ---" -ForegroundColor Cyan
    Invoke-Builder $GatePrompt "gate"
    Assert-Scope "gate"
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
        Invoke-Builder $BuildPrompt "build T1-T11"
        Assert-Scope "builder iteration $i"
    }
    else {
        Write-Host "`n--- Iteration $i of $MaxIterations : BUILD (fixes) ---" -ForegroundColor Cyan
        Invoke-Builder $FixPrompt "fix round $i"
        Assert-Scope "builder iteration $i"
    }

    Write-Host "--- Iteration $i : REVIEW ---" -ForegroundColor Yellow
    Remove-Item $Verdict -ErrorAction SilentlyContinue
    Invoke-Reviewer $ReviewPrompt $i

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

# ---- Close: flag-off verification ------------------------------------------
Clear-ModelEnv
Write-Host "`nFlag-OFF verification..." -ForegroundColor Cyan
$env:SUPPLIER_ACCOUNTS_V1 = "0"
$off = (uv run pytest -q 2>&1 | Out-String)
Remove-Item Env:SUPPLIER_ACCOUNTS_V1 -ErrorAction SilentlyContinue
Write-Log ("Flag-off: " + (($off -split "`n" | Where-Object { $_ -match 'passed' }) -join ' '))

Write-Host "`nMorning protocol:" -ForegroundColor Cyan
Write-Host "  1. Read FINDINGS in $Report."
Write-Host "  2. Read $Review."
Write-Host "  3. git diff --stat $BranchPoint HEAD   (expect backend python + new tests only)"
Write-Host "  4. uv run pytest -q                    (expect 2305 + N)"
Write-Host "  5. `$env:SUPPLIER_ACCOUNTS_V1='0' ; uv run pytest -q   (must still be green)"
Write-Host "  6. cd frontend ; npm test              (expect 64, untouched)"
Write-Host "  7. Merge yourself. Claude never merges."
