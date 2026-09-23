# run_arc4b_loop.ps1 -- Opus 5 build -> Fable 5 review agentic loop
# Arc 4b: flag-on rulings (notification cap class, webhook rejection throttle, verify
# click-to-continue, invite governance) + signal discipline (anti alert-fatigue).
#
# BOTH agents run on the claude.ai subscription.
#
# Usage:
#   cd "C:\dev\_Arkim\Arkim Procurement Agent Prototype"
#   .\loop\run_arc4b_loop.ps1
#
#   -SkipGate       : gate already run
#   -SkipFirstBuild : build already done; iteration 1 goes straight to review
#   -SkipBaseline   : skip the dual-suite baseline check
#
# If a session limit is hit, the loop STOPS cleanly and prints the resume command.
# Resuming never re-runs completed work: the builder reads git log and continues.
#
# Handshake files:
#   ARC4B_BRIEF.md    -- input spec, repo root (must exist)
#   ARC4B_REPORT.md   -- builder: gate + findings
#   ARC4B_REVIEW.md   -- reviewer: numbered findings
#   loop/VERDICT.txt  -- reviewer: APPROVED | CHANGES_REQUESTED (gitignored)
#   loop/BRANCH_POINT.txt -- true cut point, persisted for resumes
# Logs: build.log / review.log / loop.log

param(
    [int]$MaxIterations   = 4,
    [switch]$SkipGate,
    [switch]$SkipFirstBuild,
    [switch]$SkipBaseline,
    [string]$RepoPath      = "C:\dev\_Arkim\Arkim Procurement Agent Prototype",
    [string]$Branch        = "arc4b/flag-on-rulings",
    [string]$BuilderModel  = "claude-opus-5",
    [string]$ReviewerModel = "claude-fable-5",
    [int]$BackendBaseline  = 2808,
    [int]$FrontendBaseline = 197
)

$ErrorActionPreference = "Continue"
$PSNativeCommandUseErrorActionPreference = $false
$ProgressPreference = "SilentlyContinue"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
Set-Location $RepoPath

$Brief       = "ARC4B_BRIEF.md"
$Report      = "ARC4B_REPORT.md"
$Review      = "ARC4B_REVIEW.md"
$Verdict     = "loop\VERDICT.txt"
$BranchPtFile = "loop\BRANCH_POINT.txt"

# The ONLY pre-existing test files this arc may modify (brief: PRIME DIRECTIVE exception table,
# extended by gate ruling G-STOP-1). Any other pre-existing test file modified is a hard stop.
$AuthorisedTestEdits = @(
    "frontend/src/app/supplier/verify/__tests__/verify-screen.test.tsx",
    "utils/procurement_agent/tests/conftest.py",
    "utils/procurement_agent/tests/test_notifications_governance.py",
    "utils/procurement_agent/tests/test_notifications_rfq_new.py",
    "utils/procurement_agent/tests/test_notifications_admin_alerts.py",
    "utils/procurement_agent/tests/test_notifications_escalation.py"
)

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
        Write-Host "Backend baseline is NOT $BackendBaseline passed. Merge arc 4 and the cleanup commits first." -ForegroundColor Red
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
        # Arc 4b carries a narrow, brief-authorised exception for exactly two files;
        # those are recorded as notes for the reviewer rather than stopping the run.
        if ($script:PreExistingTests -contains $f) {
            if ($AuthorisedTestEdits -contains $f) {
                if ($soft -notcontains "AUTHORISED TEST EDIT: $f") { $soft += "AUTHORISED TEST EDIT: $f" }
            } elseif ($hard -notcontains $f) {
                $hard += $f
            }
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
    $out = (claude --model $Model --dangerously-skip-permissions -p $Prompt 2>&1 | Out-String)
    Add-Content -Path $LogFile -Value $out

    # Arc 4 lost a night to this: the builder hit the subscription session limit at T5,
    # then the reviewer's call and its retry both returned the same one-line limit
    # message in six seconds. Detect it, stop cleanly, say when and how to resume.
    if ($out -match "(?i)hit your (session|usage) limit|rate limit|usage limit reached") {
        $reset = [regex]::Match($out, "(?i)resets?\s+([^\r\n\u00b7]+)").Groups[1].Value.Trim()
        Write-Host "`nSESSION LIMIT reached during: $Stage" -ForegroundColor Red
        if ($reset) { Write-Host "Resets: $reset" -ForegroundColor Red }
        Write-Log "SESSION LIMIT during $Stage; resets '$reset'"
        Write-Host "`nNothing is lost -- completed tasks are committed. After the reset, resume with:" -ForegroundColor Yellow
        if ($Stage -like "BUILDER*") {
            Write-Host "  .\loop\run_arc4b_loop.ps1 -SkipGate -SkipBaseline" -ForegroundColor Yellow
            Write-Host "  (the builder reads git log and continues from the last committed task)" -ForegroundColor Yellow
        } else {
            Write-Host "  .\loop\run_arc4b_loop.ps1 -SkipGate -SkipFirstBuild -SkipBaseline" -ForegroundColor Yellow
        }
        exit 3
    }
}

# ---- Prompts ----------------------------------------------------------------
$ExecRule = @"
EXECUTION RULE: you are in non-interactive print mode. Your session ENDS when your response ends --
nothing continues afterwards and you will not be notified of anything. Run every command
synchronously and wait for it. Never background 'uv run pytest', 'npm test' or anything else.
"@

$GatePrompt = @"
You are the BUILDER in a two-agent loop. Read ARC4B_BRIEF.md in the repo root.

$ExecRule

This turn: run the INVESTIGATION GATE ONLY (H1 through H12). Build nothing.
Write findings to ARC4B_REPORT.md with file:line references for every claim.

H9 matters most: report exactly which RFQ lifecycle states exist. If 'quote target met' does not
exist, say so -- S4 is then implemented for the states that DO exist, and you must not invent a
quote-target model. That is pre-ruled by the brief; it is not a reason to stop.

If you hit a scope question the brief does NOT answer and you cannot proceed without a decision,
end the report with a line beginning exactly 'GATE STOP:' followed by the question, and stop.
Otherwise do NOT write that marker -- commit the report and stop.

PRIME DIRECTIVE: no pre-existing test file may be modified EXCEPT those in the brief's exception
table. Editing any other existing test stops the loop.

Commit the report on branch $Branch. Stop after the gate.
"@

$BuildPrompt = @"
You are the BUILDER. Read ARC4B_BRIEF.md and your gate findings in ARC4B_REPORT.md.

$ExecRule

FIRST run 'git log --oneline' and check which of T1-T11 are already committed. A previous session
may have been cut off by a subscription limit. Build ONLY the tasks not yet committed, in order,
committing after each with a message naming the task. Never redo a committed task.

Run 'uv run pytest -q' after each backend task and 'npm test' (frontend/) after T3, T6 and T10.

The governing principle (SIGNAL DISCIPLINE): every notification must be actionable by the person
who receives it, and every signal has one owner. Hold these invariants:
- the daily ceiling DEFERS to the digest, it never discards (a dropped notification is a BLOCKER);
- a verified webhook event is never throttled (R-F10);
- the throttled 403 is byte-identical to every other rejection;
- business-hours, ceiling and actionability calculations take 'now' as a parameter and never read
  the wall clock inside the calculation (arc 4's date-dependent test was exactly this bug);
- gate ruling G-STOP-1 is in the brief's GATE RULINGS: the four arc-4 test files in the PRIME
  DIRECTIVE exception table may now be edited, fenced. In each, change ONLY the assertions encoding
  the superseded behaviour, REPLACE each with one pinning the new behaviour (never just delete),
  and leave every other assertion byte-identical. The named invariants (an RFQ never in two
  reminders, alert-once, same-now idempotency, cap-suppression) must stay tested in their new
  form. In verify-screen.test.tsx only POST-on-load assertions change, security assertions stay,
  and you ADD a test proving zero requests on render.
- T5 (conftest pin) is already done -- record it as satisfied, do not re-edit conftest.py.

Produce the S-criterion-9 volume scenario as a named test and state both counts (arc 4 behaviour
vs new) in the report.

When every task is committed, append to ARC4B_REPORT.md: both final test counts as observed, the
volume-scenario counts, a FINDINGS section ('None' if empty), and the updated env-config list.
Commit the report. Do not push.
"@

$FixPrompt = @"
You are the BUILDER. The reviewer returned CHANGES_REQUESTED. Read ARC4B_REVIEW.md and fix ONLY
the numbered findings. Do not refactor anything else.

$ExecRule

If loop/SCOPE_NOTES.txt exists, check each entry and justify or revert it in the report.
The prime-directive exception covers only the files in the brief's exception table, fenced as
the brief describes.

Append a fix log to ARC4B_REPORT.md per numbered finding. Run both suites, confirm green, commit on
branch $Branch. Do not push.
"@

$ReviewPrompt = @"
You are the REVIEWER, not the builder. You did not write this code and you are not here to be
agreeable. Read ARC4B_BRIEF.md, then ARC4B_REPORT.md, then the actual diff.

$ExecRule
Write loop/VERDICT.txt as the LAST thing you do, after every command has returned.

If ARC4B_REPORT.md shows the build is incomplete (tasks missing from git log), write
CHANGES_REQUESTED with a one-line review naming the missing tasks, and finish.

The arc's branch point is $BranchPoint. Use it for every diff.

Answer R1 through R11 from the brief's reviewer checklist explicitly, each with file:line evidence.

R1 FIRST: 'git diff --name-status $BranchPoint HEAD'. Only test files in the brief's PRIME DIRECTIVE
exception table may show M. Any other pre-existing test file with M is an immediate
CHANGES_REQUESTED.

R1b: for EACH of the four arc-4 test files (governance, rfq_new, admin_alerts, escalation), read the
diff line by line. Were only assertions encoding the superseded behaviour changed? Was each REPLACED
by one pinning the new behaviour, or merely deleted? Did the invariants survive in tested form? A
deletion without replacement, or any unrelated assertion touched, is MAJOR.

R2: read the verify-screen.test.tsx diff LINE BY LINE. Did any security assertion weaken? Is the
uniform-rejection equality still Set-size-1 with a contrast case? Is there a NEW
zero-requests-on-render test?

R4: prove a VERIFIED webhook event bypasses the rejection limiter by reading the test, not the
report.

R9: run the volume scenario yourself. A reduction achieved by DROPPING notifications rather than
coalescing, deferring or cancelling is a BLOCKER.

R10: grep the business-hours, ceiling and actionability code for datetime.now(, time.time( and
date.today( inside calculations. Any hit is MAJOR.

The ONLY files you may create or modify are ARC4B_REVIEW.md and loop/VERDICT.txt. Never fix
builder code. Do NOT commit loop/VERDICT.txt.

Write numbered findings with severity (BLOCKER/MAJOR/MINOR), file:line and brief clause.
Commit only the review file. Then write loop/VERDICT.txt containing exactly one word:
APPROVED  or  CHANGES_REQUESTED
"@

# ---- Gate -------------------------------------------------------------------
if (-not $SkipGate) {
    Write-Host "`n--- GATE: investigation ---" -ForegroundColor Cyan
    Invoke-Agent $BuilderModel $GatePrompt "build.log" "BUILDER gate"
    Assert-Scope "gate"
    # Halt on an explicit gate stop rather than spending a build turn the builder will refuse.
    # Matches only the exact marker the gate prompt requires, not the word 'stop' in prose.
    if ((Get-Content $Report -Raw -ErrorAction SilentlyContinue) -match '(?m)^GATE STOP:') {
        Write-Host "`nThe gate stopped on an unresolved question. Read the GATE STOP line in $Report," -ForegroundColor Yellow
        Write-Host "rule on it in $Brief (GATE RULINGS), then resume with:" -ForegroundColor Yellow
        Write-Host "  .\loop\run_arc4b_loop.ps1 -SkipGate -SkipBaseline" -ForegroundColor Yellow
        Write-Log "Gate stopped on an unresolved question"
        exit 4
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
