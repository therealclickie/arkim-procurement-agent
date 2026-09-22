# run_arc4_loop.ps1 -- Opus 5 build -> Fable 5 review agentic loop
# Arc 4: notifications & delivery tracking (SES).
#
# BOTH agents run on the claude.ai subscription. No Fireworks, no API key.
#
# Usage:
#   cd "C:\dev\_Arkim\Arkim Procurement Agent Prototype"
#   .\loop\run_arc4_loop.ps1
#
#   -SkipGate       : gate already run
#   -SkipFirstBuild : build already done; iteration 1 goes straight to review
#   -SkipBaseline   : skip the dual-suite baseline check
#
# Handshake files:
#   NOTIFICATIONS_BRIEF.md    -- input spec, repo root (must exist)
#   NOTIFICATIONS_REPORT.md   -- builder: gate + findings
#   NOTIFICATIONS_REVIEW.md   -- reviewer: numbered findings
#   loop/VERDICT.txt          -- reviewer: APPROVED | CHANGES_REQUESTED (gitignored)
#   loop/BRANCH_POINT.txt     -- the arc's true cut point, persisted so resume runs
#                                diff against it (arc 3 resume diffed HEAD..HEAD)
# Logs: build.log / review.log / loop.log

param(
    [int]$MaxIterations   = 4,
    [switch]$SkipGate,
    [switch]$SkipFirstBuild,
    [switch]$SkipBaseline,
    [string]$RepoPath      = "C:\dev\_Arkim\Arkim Procurement Agent Prototype",
    [string]$Branch        = "arc4/notifications",
    [string]$BuilderModel  = "claude-opus-5",
    [string]$ReviewerModel = "claude-fable-5",
    [int]$BackendBaseline  = 2479,
    [int]$FrontendBaseline = 172
)

$ErrorActionPreference = "Continue"
$PSNativeCommandUseErrorActionPreference = $false
$ProgressPreference = "SilentlyContinue"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
Set-Location $RepoPath

$Brief       = "NOTIFICATIONS_BRIEF.md"
$Report      = "NOTIFICATIONS_REPORT.md"
$Review      = "NOTIFICATIONS_REVIEW.md"
$Verdict     = "loop\VERDICT.txt"
$BranchPtFile = "loop\BRANCH_POINT.txt"

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
Write-Host "Auth check..." -ForegroundColor Cyan
$authProbe = (claude --model $BuilderModel -p "Reply with exactly: AUTH OK" 2>&1 | Out-String)
if ($authProbe -notmatch "AUTH OK") {
    Write-Host "Builder auth/model probe failed. Run 'claude login'." -ForegroundColor Red
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
    Write-Host "Baseline: backend (expect $BackendBaseline passed)..." -ForegroundColor Cyan
    foreach ($v in "RUN_CAPTURE","INTAKE_TYPE_AWARE","SCORING_V2","SUPPLIER_ACCOUNTS_V1","NOTIFICATIONS_V1") {
        Remove-Item "Env:$v" -ErrorAction SilentlyContinue
    }
    $py = (uv run pytest -q 2>&1 | Out-String)
    if ($py -notmatch "$BackendBaseline passed") {
        Write-Host "Backend baseline is NOT $BackendBaseline passed. Merge arc 3 first." -ForegroundColor Red
        Write-Host (($py -split "`n" | Select-Object -Last 3) -join "`n")
        throw "Backend baseline failed."
    }
    Write-Log "Baseline OK: backend $BackendBaseline passed"

    Write-Host "Baseline: frontend (expect $FrontendBaseline passed)..." -ForegroundColor Cyan
    Push-Location (Join-Path $RepoPath "frontend")
    $fe = (npm test 2>&1 | Out-String)
    Pop-Location
    if ($fe -notmatch "$FrontendBaseline passed") {
        Write-Host "Frontend baseline is NOT $FrontendBaseline passed." -ForegroundColor Red
        Write-Host (($fe -split "`n" | Where-Object { $_ -match 'Tests' }) -join "`n")
        throw "Frontend baseline failed."
    }
    Write-Log "Baseline OK: frontend $FrontendBaseline passed"
}

if (-not (Test-Path (Join-Path $RepoPath "loop"))) { New-Item -ItemType Directory -Path "loop" | Out-Null }
foreach ($entry in "build.log","review.log","loop.log","loop/VERDICT.txt","loop/BRANCH_POINT.txt") {
    $gi = Get-Content .gitignore -ErrorAction SilentlyContinue
    if ($gi -notcontains $entry) { Add-Content .gitignore $entry }
}
if ((Git-Safe status --porcelain .gitignore).Trim()) {
    Git-Safe add .gitignore | Out-Null
    Git-Safe commit -m "chore: ignore arc4 loop working files" | Out-Null
}

# ---- Branch + persisted branch point ---------------------------------------
# On a fresh run, cut the branch and record its cut point. On a resume
# (-SkipGate/-SkipFirstBuild) read the recorded point back, so every diff and
# every reviewer command compares against where the arc actually started.
$branchExists = (Git-Safe branch --list $Branch).Trim() -ne ""
if (-not $branchExists) {
    Write-Host "Branching from:" -ForegroundColor Cyan
    Write-Host (Git-Safe log --oneline -3)
    Git-Safe branch $Branch | Out-Null
    Git-Safe checkout $Branch | Out-Null
    $BranchPoint = (Git-Safe rev-parse HEAD).Trim()
    Set-Content -Path $BranchPtFile -Value $BranchPoint
} else {
    Git-Safe checkout $Branch | Out-Null
    if (Test-Path $BranchPtFile) {
        $BranchPoint = (Get-Content $BranchPtFile -Raw).Trim()
    } else {
        $BranchPoint = (Git-Safe merge-base $Branch test/flag-on-integration).Trim()
        Set-Content -Path $BranchPtFile -Value $BranchPoint
    }
}
Write-Log "On branch: $((Git-Safe rev-parse --abbrev-ref HEAD).Trim()); branch point $BranchPoint"

$script:PreExistingUntracked = @((Git-Safe status --porcelain) -split "`n" |
    Where-Object { $_ -match '^\?\?' -and $_.Length -gt 3 } |
    ForEach-Object { ($_.Substring(3).Trim() -replace '"', '') })
Write-Log "Snapshot: $($script:PreExistingUntracked.Count) pre-existing untracked files excluded"

$script:PreExistingTests = @((Git-Safe ls-tree -r --name-only $BranchPoint) -split "`n" |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ -match '__tests__/' -or $_ -match 'test_.*\.py$' -or $_ -match '\.test\.(ts|tsx)$' })
Write-Log "Protected: $($script:PreExistingTests.Count) pre-existing test files (must not be modified)"

# ---- Scope enforcer ---------------------------------------------------------
function Test-DiffScope {
    # Returns two buckets: HARD (a real invariant broken) and SOFT (merely outside
    # the allowlist). Three arcs running, every single stop was a SOFT false
    # positive -- test-support/, api_server.py, design/interactions.md -- each
    # costing a manual resume. Only HARD kills the run now.
    $allowed = @(
        '^frontend/',
        '^utils/',
        '^api_server\.py$',
        '^[^/]+\.py$',
        '^migrations?/',
        '^alembic/',
        '^design/',
        '^docs/',
        '\.md$',
        'pyproject\.toml$',
        'uv\.lock$',
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

    $hard = @()
    $soft = @()
    foreach ($f in ($committed + $working)) {
        $f = ($f -replace '"', '').Trim()
        if (-not $f) { continue }

        # HARD: a pre-existing test file was modified. The suites are the safety
        # net; editing them to make new work pass is the one thing no arc may do.
        if ($script:PreExistingTests -contains $f) {
            if ($hard -notcontains $f) { $hard += $f }
            continue
        }

        $ok = $false
        foreach ($p in $allowed) { if ($f -match $p) { $ok = $true; break } }
        if (-not $ok -and $soft -notcontains $f) { $soft += $f }
    }
    return @{ Hard = $hard; Soft = $soft }
}

function Assert-Scope([string]$stage) {
    $r = Test-DiffScope

    if ($r.Soft.Count -gt 0) {
        Write-Host "Scope note after $stage -- files outside the allowlist (not fatal):" -ForegroundColor Yellow
        $r.Soft | ForEach-Object {
            Write-Host "  $_" -ForegroundColor Yellow
            Write-Log "  SCOPE-NOTE: $_"
            Add-Content -Path "loop\SCOPE_NOTES.txt" -Value "$stage : $_"
        }
        Write-Host "  (recorded in loop\SCOPE_NOTES.txt for the reviewer and for you)" -ForegroundColor Yellow
    }

    if ($r.Hard.Count -gt 0) {
        Write-Host "PRIME DIRECTIVE VIOLATED during $stage -- pre-existing test file modified:" -ForegroundColor Red
        $r.Hard | ForEach-Object { Write-Host "  $_" -ForegroundColor Red; Write-Log "  HARD VIOLATION: $_" }
        Write-Log "Loop stopped. Inspect with: git diff $BranchPoint -- <file>"
        exit 2
    }

    Write-Log "Scope OK after $stage (hard: 0, notes: $($r.Soft.Count))"
}

function Invoke-Agent([string]$Model, [string]$Prompt, [string]$LogFile, [string]$Stage) {
    Clear-ModelEnv
    "`n===== $Stage ($Model) -- $(Get-Date -Format o) =====" | Add-Content $LogFile
    claude --model $Model --dangerously-skip-permissions -p $Prompt *>> $LogFile
}

# ---- Prompts ----------------------------------------------------------------
$GatePrompt = @"
You are the BUILDER in a two-agent loop. Read NOTIFICATIONS_BRIEF.md in the repo root.

This turn: run the INVESTIGATION GATE ONLY (G1 through G9). Build nothing.
Write findings to NOTIFICATIONS_REPORT.md with file:line references for every claim.

Read the GATE RULINGS section carefully. Decisions listed there are pre-authorised -- do not
stop to ask them. If you hit a scope question the brief does NOT answer and you cannot proceed
without a decision, STOP after the gate, state the question plainly in the report, and do not
build. A stopped arc with a clear question is the correct outcome.

PRIME DIRECTIVE: no pre-existing test file modified; flags off means today's behaviour; no live
AWS in tests. The loop terminates automatically if you edit an existing test file.

Commit the report on branch $Branch. Stop after the gate.
"@

$BuildPrompt = @"
You are the BUILDER. Read NOTIFICATIONS_BRIEF.md and your gate findings in NOTIFICATIONS_REPORT.md.

EXECUTION RULE: you are in non-interactive print mode. Your session ENDS when your response ends.
Run every command synchronously; never background a test suite. Commit before you finish.

The gate's open question Q1 (which seam owns RFQ_NEW) has been RULED -- see GATE RULINGS and the
revised T4 in the brief: hook at the rfq_send seam, not tier1_notify. Append one line to the
report acknowledging the ruling, then build.

Build tasks T1 through T12, committing after each task with a message naming the task. Run
'uv run pytest -q' and 'npm test' (frontend/) after each task; the pre-existing tests must stay
green WITHOUT editing any of them.

Hold these invariants: nothing bypasses send governance (D1); auth mail uses the tracking-off
configuration set (D2); the webhook verifies SNS signatures, allowlists the TopicArn, and is
idempotent (D4); OPENED never counts as seen (D5); at most one reminder per RFQ per member (D6);
no in-process timers (GATE RULINGS); no live AWS in tests (D10).

When done, append to NOTIFICATIONS_REPORT.md: both final test counts, a FINDINGS section
('None' if empty), and the REQUIRED ENV CONFIG list for the human's live verification. Do not
push.
"@

$FixPrompt = @"
You are the BUILDER. The reviewer returned CHANGES_REQUESTED. Read NOTIFICATIONS_REVIEW.md and
fix ONLY the numbered findings. Do not refactor anything else.

If loop/SCOPE_NOTES.txt exists, it lists files you changed that fell outside the arc's expected
file set. Most are benign; check each one and either justify it in the report or revert it.

PRIME DIRECTIVE still applies. Append a fix log to NOTIFICATIONS_REPORT.md per numbered finding.
Run both suites, confirm green, commit on branch $Branch. Do not push.
"@

$ReviewPrompt = @"
You are the REVIEWER, not the builder. You did not write this code and you are not here to be
agreeable. Read NOTIFICATIONS_BRIEF.md, then NOTIFICATIONS_REPORT.md, then the actual diff.

EXECUTION RULE: you are running in non-interactive print mode. Your session ENDS when your
response ends -- nothing continues afterwards and you will NOT be notified of anything. Run every
command synchronously and wait for it to finish. Do NOT background 'uv run pytest', 'npm test'
or any other command. If you end your turn with a test suite still running, the loop records NO
verdict and the arc stalls. Write loop/VERDICT.txt as the LAST thing you do, after every command
has returned.

First check: if NOTIFICATIONS_REPORT.md says the build was not started (an unresolved STOP),
write CHANGES_REQUESTED with a one-line review saying so and finish -- there is nothing to
review.

The arc's branch point is $BranchPoint. Use it for every diff.

Answer R1 through R9 from the brief's reviewer checklist explicitly, each with file:line evidence.
R1 FIRST: 'git diff --name-status $BranchPoint HEAD' -- any M on a pre-existing test file is an
immediate CHANGES_REQUESTED.
R2: both suites with flags OFF; the webhook must 404 and no Notification rows may be written.
R3: grep every test for boto3 clients not wrapped by Stubber/FakeProvider. A live client is a
BLOCKER.
R4: read the webhook handler yourself -- cert URL restricted to amazonaws.com, TopicArn checked
BEFORE SubscribeURL is visited, idempotency keyed on messageId + event type.
R5: is the governance test exercising the REAL gate?
R7: does the escalation table test include an OPENED-only row that still escalates?

The ONLY files you may create or modify are NOTIFICATIONS_REVIEW.md and loop/VERDICT.txt.
Never fix builder code. Do NOT commit loop/VERDICT.txt.

Write numbered findings with severity (BLOCKER/MAJOR/MINOR), file:line and brief clause.
Commit only the review file. Then write loop/VERDICT.txt containing exactly one word:
APPROVED  or  CHANGES_REQUESTED
"@

# ---- Gate -------------------------------------------------------------------
if (-not $SkipGate) {
    Write-Host "`n--- GATE: investigation ---" -ForegroundColor Cyan
    Invoke-Agent $BuilderModel $GatePrompt "build.log" "BUILDER gate"
    Assert-Scope "gate"
    if ((Get-Content $Report -Raw -ErrorAction SilentlyContinue) -match 'STOP') {
        Write-Host "Gate report contains a STOP. Read $Report before continuing." -ForegroundColor Yellow
    }
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
        Write-Host "`n--- Iteration $i of $MaxIterations : BUILD (T1-T12) ---" -ForegroundColor Cyan
        Invoke-Agent $BuilderModel $BuildPrompt "build.log" "BUILDER build T1-T12"
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

    # One retry: a dropped connection or a lapsed session should not end the arc.
    if (-not (Test-Path $Verdict)) {
        Write-Host "No verdict file. Retrying the reviewer once..." -ForegroundColor Yellow
        Write-Log "Reviewer produced no verdict; retrying once"
        Start-Sleep -Seconds 20
        Invoke-Agent $ReviewerModel $ReviewPrompt "review.log" "REVIEWER iteration $i (retry)"
    }

    if (-not (Test-Path $Verdict)) {
        Write-Host "Reviewer produced no $Verdict after a retry -- stopping for human attention." -ForegroundColor Red
        Write-Host "Check review.log for an auth or rate-limit error." -ForegroundColor Red
        break
    }

    # Tolerant parse: accept CHANGES_REQUESTED or CHANGES_REQUIRED, any case,
    # with or without a 'VERDICT:' prefix. Arc 1 stalled on exactly this
    # mismatch between the brief's wording and the script's comparison.
    $raw = (Get-Content $Verdict -Raw).Trim()
    $v = ($raw -replace '(?i)^\s*verdict\s*:\s*', '').Trim().ToUpper()
    Write-Log "Verdict (raw '$raw') parsed as: $v"

    if ($v -match 'APPROVED') {
        Write-Host "`nAPPROVED after $i iteration(s). Nothing pushed." -ForegroundColor Green
        break
    }
    if ($v -match 'CHANGES_REQUE?[SI]?R?E?D' -or $v -match 'CHANGES') {
        Write-Host "Changes requested -- continuing to fix round." -ForegroundColor Yellow
        if ($i -eq $MaxIterations) {
            Write-Host "`nMax iterations reached without APPROVED. Read $Review." -ForegroundColor Red
        }
        continue
    }

    Write-Host "Unrecognised verdict '$raw' -- stopping for human attention." -ForegroundColor Red
    break
}

# ---- Close: flags-off verification -----------------------------------------
Clear-ModelEnv
Write-Host "`nFlags-OFF verification..." -ForegroundColor Cyan
$env:NOTIFICATIONS_V1 = "0"
$env:SUPPLIER_ACCOUNTS_V1 = "0"
$off = (uv run pytest -q 2>&1 | Out-String)
Remove-Item Env:NOTIFICATIONS_V1 -ErrorAction SilentlyContinue
Remove-Item Env:SUPPLIER_ACCOUNTS_V1 -ErrorAction SilentlyContinue
Write-Log ("Flags-off backend: " + (($off -split "`n" | Where-Object { $_ -match 'passed' }) -join ' '))

Push-Location (Join-Path $RepoPath "frontend")
$feOff = (npm test 2>&1 | Out-String)
Pop-Location
Write-Log ("Frontend: " + (($feOff -split "`n" | Where-Object { $_ -match 'Tests' }) -join ' '))

Write-Host "`nMorning protocol:" -ForegroundColor Cyan
Write-Host "  1. Read FINDINGS and REQUIRED ENV CONFIG in $Report."
Write-Host "  2. Read $Review."
Write-Host "  3. git diff --name-status $BranchPoint HEAD  (no 'M' on any pre-existing test file)"
Write-Host "  4. uv run pytest -q                          (expect $BackendBaseline + N)"
Write-Host "  5. cd frontend ; npm test                    (expect $FrontendBaseline + M)"
Write-Host "  6. Merge yourself. Claude never merges."
Write-Host "  7. Live SES verification per the brief's HUMAN VERIFICATION section."
