# run_arc6_loop.ps1 -- Opus 5.5 build -> Fable 5 review agentic loop
# Arc 6: buyer identity and roles (Requester/Buyer/Approver/Admin), company isolation, attribution.
#
# BOTH agents run on the claude.ai subscription.
#
# Usage:
#   cd "C:\dev\_Arkim\Arkim Procurement Agent Prototype"
#   .\loop\run_arc6_loop.ps1
#
#   -SkipGate       : gate already run
#   -SkipFirstBuild : build already done; iteration 1 goes straight to review
#   -SkipBaseline   : skip the dual-suite baseline check
#
# TEST-EDIT APPROVAL CHECKPOINT
#   The gate proposes which pre-existing test assertions this arc supersedes. If it proposes any,
#   the loop HALTS so a human can review them. Approve by writing one repo-relative path per line
#   into loop\AUTHORISED_TEST_EDITS.txt, then resume with -SkipGate -SkipBaseline.
#   The builder can never authorise its own test edits.
#
# Handshake files:
#   BUYER_IDENTITY_BRIEF.md     -- input spec, repo root (must exist)
#   BUYER_IDENTITY_REPORT.md    -- builder: gate + findings
#   BUYER_IDENTITY_REVIEW.md    -- reviewer: numbered findings
#   loop/AUTHORISED_TEST_EDITS.txt -- human-approved test files (created by you, never the builder)
#   loop/VERDICT.txt            -- reviewer verdict (gitignored)
#   loop/BRANCH_POINT.txt       -- true cut point, persisted for resumes
# Logs: build.log / review.log / loop.log

param(
    [int]$MaxIterations   = 4,
    [switch]$SkipGate,
    [switch]$SkipFirstBuild,
    [switch]$SkipBaseline,
    [string]$RepoPath      = "C:\dev\_Arkim\Arkim Procurement Agent Prototype",
    [string]$Branch        = "arc6/buyer-identity",
    [string]$BaseBranch    = "test/flag-on-integration",
    [string]$BuilderModel  = "claude-opus-5-5",
    [string]$ReviewerModel = "claude-fable-5",
    [int]$BackendBaseline  = 0,   # 0 = measure at start (PH-01 changes the count)
    [int]$FrontendBaseline = 0    # 0 = measure at start
)

$ErrorActionPreference = "Continue"
$PSNativeCommandUseErrorActionPreference = $false
$ProgressPreference = "SilentlyContinue"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
Set-Location $RepoPath

$Brief        = "BUYER_IDENTITY_BRIEF.md"
$Report       = "BUYER_IDENTITY_REPORT.md"
$Review       = "BUYER_IDENTITY_REVIEW.md"
$Verdict      = "loop\VERDICT.txt"
$BranchPtFile = "loop\BRANCH_POINT.txt"
$ApprovalFile = "loop\AUTHORISED_TEST_EDITS.txt"
$ScriptName   = "run_arc6_loop.ps1"
$LoopLog      = Join-Path $RepoPath "loop.log"

# Human-approved pre-existing test files. Empty until you write the approval file.
$AuthorisedTestEdits = @()
if (Test-Path (Join-Path $RepoPath $ApprovalFile)) {
    $AuthorisedTestEdits = @(Get-Content (Join-Path $RepoPath $ApprovalFile) |
        ForEach-Object { ($_ -replace '\\','/').Trim() } |
        Where-Object { $_ -and -not $_.StartsWith('#') })
}

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

# Prerequisite: PH-01 must be merged into the base branch before buyer identity is built.
$ph01 = (Git-Safe log $BaseBranch --oneline --grep "PH-01").Trim()
if (-not $ph01) {
    Write-Host "PH-01 is not merged into $BaseBranch. Merge fix/ph01-hygienic-fields first." -ForegroundColor Red
    throw "Prerequisite missing: PH-01."
}
Write-Log "Prerequisite OK: PH-01 present on $BaseBranch"

# Baselines. 0 = measure now and require zero failures (counts change as fixes merge);
# a positive number = require exactly that many passed.
if (-not $SkipBaseline) {
    foreach ($v in "RUN_CAPTURE","INTAKE_TYPE_AWARE","SCORING_V2","SUPPLIER_ACCOUNTS_V1",
                   "NOTIFICATIONS_V1","BUYER_ACCOUNTS_V1") {
        Remove-Item "Env:$v" -ErrorAction SilentlyContinue
    }
    Write-Host "Baseline: backend..." -ForegroundColor Cyan
    $py = (uv run pytest -q 2>&1 | Out-String)
    $pyLine = ($py -split "`n" | Where-Object { $_ -match '\d+ passed' } | Select-Object -Last 1)
    if ($py -match '\d+ failed' -or $py -match '\d+ error' -or -not $pyLine) {
        Write-Host "Backend baseline is not green." -ForegroundColor Red
        Write-Host (($py -split "`n" | Select-Object -Last 4) -join "`n")
        throw "Backend baseline failed."
    }
    $measured = [int]([regex]::Match($pyLine, '(\d+) passed').Groups[1].Value)
    if ($BackendBaseline -gt 0 -and $measured -ne $BackendBaseline) {
        throw "Backend passed $measured, expected $BackendBaseline."
    }
    $BackendBaseline = $measured
    Write-Log "Baseline OK: backend $BackendBaseline passed"

    Write-Host "Baseline: frontend..." -ForegroundColor Cyan
    Push-Location (Join-Path $RepoPath "frontend")
    $fe = (npm test 2>&1 | Out-String)
    Pop-Location
    $feLine = ($fe -split "`n" | Where-Object { $_ -match 'Tests\s+\d+ passed' } | Select-Object -Last 1)
    if ($fe -match 'Tests\s+.*\d+ failed' -or -not $feLine) {
        Write-Host "Frontend baseline is not green." -ForegroundColor Red
        Write-Host (($fe -split "`n" | Where-Object { $_ -match 'Tests' }) -join "`n")
        throw "Frontend baseline failed."
    }
    $measuredFe = [int]([regex]::Match($feLine, '(\d+) passed').Groups[1].Value)
    if ($FrontendBaseline -gt 0 -and $measuredFe -ne $FrontendBaseline) {
        throw "Frontend passed $measuredFe, expected $FrontendBaseline."
    }
    $FrontendBaseline = $measuredFe
    Write-Log "Baseline OK: frontend $FrontendBaseline passed"
    Set-Content -Path "loop\BASELINE.txt" -Value "backend=$BackendBaseline frontend=$FrontendBaseline"
} elseif (Test-Path "loop\BASELINE.txt") {
    $bl = Get-Content "loop\BASELINE.txt" -Raw
    $BackendBaseline  = [int]([regex]::Match($bl,'backend=(\d+)').Groups[1].Value)
    $FrontendBaseline = [int]([regex]::Match($bl,'frontend=(\d+)').Groups[1].Value)
}

if (-not (Test-Path (Join-Path $RepoPath "loop"))) { New-Item -ItemType Directory -Path "loop" | Out-Null }
foreach ($entry in "build.log","review.log","loop.log","loop/VERDICT.txt","loop/BRANCH_POINT.txt","loop/BASELINE.txt","loop/SCOPE_NOTES.txt") {
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
    # Always cut from the integration branch, never from whatever happens to be checked out.
    # The first arc-5 run inherited the evaluation branch this way.
    Write-Host "Branching from ${BaseBranch}:" -ForegroundColor Cyan
    Write-Host (Git-Safe log --oneline -3 $BaseBranch)
    Git-Safe branch $Branch $BaseBranch | Out-Null
    Git-Safe checkout $Branch | Out-Null
    $BranchPoint = (Git-Safe rev-parse $BaseBranch).Trim()
    Set-Content -Path $BranchPtFile -Value $BranchPoint
} else {
    Git-Safe checkout $Branch | Out-Null
    if (Test-Path $BranchPtFile) {
        $BranchPoint = (Get-Content $BranchPtFile -Raw).Trim()
    } else {
        $BranchPoint = (Git-Safe merge-base $Branch $BaseBranch).Trim()
        Set-Content -Path $BranchPtFile -Value $BranchPoint
    }
}

# Guard: the recorded branch point must be on the base branch. If it is not, the arc
# was cut from somewhere else and would drag foreign history into the merge.
& git merge-base --is-ancestor $BranchPoint $BaseBranch 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "`nBranch point $BranchPoint is not on $BaseBranch -- the arc was cut from the wrong base." -ForegroundColor Red
    Write-Host "Recut it from $BaseBranch before continuing (see the fix instructions you were given)." -ForegroundColor Red
    exit 6
}
$foreign = (Git-Safe log --oneline "$BaseBranch..$Branch" -- eval/ E2E_EVAL_REPORT.md).Trim()
if ($foreign) {
    Write-Host "`n$Branch contains evaluation-branch history -- it would be merged into ${BaseBranch}:" -ForegroundColor Red
    Write-Host $foreign -ForegroundColor Red
    exit 6
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
if ($AuthorisedTestEdits.Count -gt 0) {
    Write-Log "Human-approved test edits ($($AuthorisedTestEdits.Count)) from ${ApprovalFile}:"
    $AuthorisedTestEdits | ForEach-Object { Write-Log "  APPROVED: $_" }
}

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
            Write-Host "  .\loop\run_arc6_loop.ps1 -SkipGate -SkipBaseline" -ForegroundColor Yellow
            Write-Host "  (the builder reads git log and continues from the last committed task)" -ForegroundColor Yellow
        } else {
            Write-Host "  .\loop\run_arc6_loop.ps1 -SkipGate -SkipFirstBuild -SkipBaseline" -ForegroundColor Yellow
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
You are the BUILDER in a two-agent loop. Read BUYER_IDENTITY_BRIEF.md in the repo root.

$ExecRule

This turn: run the INVESTIGATION GATE ONLY (K1 through K7). Build nothing.
Write findings to BUYER_IDENTITY_REPORT.md with file:line references for every claim.

K2 is the security-critical item: list EVERY buyer-facing API endpoint with its current auth status.
An endpoint you miss becomes a company-isolation hole. Enumerate from the router, not from memory.

K7: list every pre-existing test assertion this arc supersedes, one line per file, EXACTLY:
PROPOSED TEST EDIT: <repo-relative path> :: <line ranges> :: <superseded behaviour> :: <decision>
or the single line: PROPOSED TEST EDITS: NONE
A human approves this list before any build begins.

If a scope question the brief does NOT answer blocks you, end the report with a line beginning exactly
'GATE STOP:' followed by the question. Otherwise do NOT write that marker.

Do not modify any test, source or config file this turn. Do not create or edit
loop/AUTHORISED_TEST_EDITS.txt -- only the human writes it.

Commit the report on branch $Branch. Stop after the gate.
"@

$BuildPrompt = @"
You are the BUILDER. Read BUYER_IDENTITY_BRIEF.md and your gate findings in BUYER_IDENTITY_REPORT.md.

$ExecRule

FIRST run 'git log --oneline' and check which of T1-T8 are already committed. A previous session may
have been cut off by a subscription limit. Build ONLY the tasks not yet committed, in order,
committing after each with a message naming the task. Never redo a committed task.

TEST-EDIT APPROVAL: read loop/AUTHORISED_TEST_EDITS.txt. You may modify a pre-existing test file ONLY if
it is listed there, and only under the fence: change only assertions encoding superseded behaviour,
REPLACE each rather than delete, leave every other assertion byte-identical, keep invariants tested.
Never create or edit the approval file. An edit you need that is not approved: do not make it --
record it under FINDINGS with file:line.

Hold the invariants:
- Company isolation (D6): under the flag, every buyer endpoint from K2 requires a session and is
  company-scoped; cross-company access is indistinguishable from not-found.
- Attribution (D7): acting member comes from the session, NEVER from request-body text.
- One matrix, has_permission(); no inline role comparisons in route code (D2).
- Supplier behaviour unchanged: every arc 2/3 test passes unmodified (D4).
- The approval policy is STORED and audited here; it is NOT enforced at order time (arc 7).

Run 'uv run pytest -q' after each backend task and 'npm test' (frontend/) after any frontend change.

When every task is committed, append to BUYER_IDENTITY_REPORT.md: both final test counts as observed,
the endpoint isolation table (every K2 endpoint -> the test that proves it is scoped), a FINDINGS
section ('None' if empty). Commit the report. Do not push.
"@

$FixPrompt = @"
You are the BUILDER. The reviewer returned CHANGES_REQUESTED. Read BUYER_IDENTITY_REVIEW.md and fix ONLY
the numbered findings. Do not refactor anything else.

$ExecRule

The test-edit approval list in loop/AUTHORISED_TEST_EDITS.txt still governs.
If loop/SCOPE_NOTES.txt exists, justify or revert each entry in the report.

Append a fix log to BUYER_IDENTITY_REPORT.md per numbered finding. Run both suites, confirm green, commit
on branch $Branch. Do not push.
"@

$ReviewPrompt = @"
You are the REVIEWER, not the builder. You did not write this code and you are not here to be
agreeable. Read BUYER_IDENTITY_BRIEF.md, then BUYER_IDENTITY_REPORT.md, then the actual diff.

$ExecRule
Write loop/VERDICT.txt as the LAST thing you do, after every command has returned.

If the report shows the build is incomplete (tasks missing from git log), write CHANGES_REQUESTED with a
one-line review naming the missing tasks, and finish.

The arc's branch point is $BranchPoint. Use it for every diff.

Answer R1 through R8 from the brief's reviewer checklist explicitly, each with file:line evidence.

R1 FIRST: 'git diff --name-status $BranchPoint HEAD'. Every pre-existing test file with M must appear in
loop/AUTHORISED_TEST_EDITS.txt. Any other is an immediate CHANGES_REQUESTED.

R2 is BLOCKER-class: enumerate the buyer-facing routes in the ACTUAL router yourself and compare with the
report's K2 list and the isolation tests. A buyer endpoint with no isolation test is a BLOCKER.

R3: trace every attribution field. If any can still be set from request-body text, that is MAJOR.

R6: run the arc 2/3 supplier tests and read the diff of supplier auth modules. Any behaviour change there
is MAJOR.

The ONLY files you may create or modify are BUYER_IDENTITY_REVIEW.md and loop/VERDICT.txt. Never fix
builder code. Do NOT commit loop/VERDICT.txt.

Write numbered findings with severity (BLOCKER/MAJOR/MINOR), file:line and brief clause. Commit only the
review file. Then write loop/VERDICT.txt containing exactly one word:
APPROVED  or  CHANGES_REQUESTED
"@

# ---- Gate -------------------------------------------------------------------
if (-not $SkipGate) {
    Write-Host "`n--- GATE: investigation (K1-K7) ---" -ForegroundColor Cyan
    Invoke-Agent $BuilderModel $GatePrompt "build.log" "BUILDER gate"
    Assert-Scope "gate"
}

$reportText = (Get-Content $Report -Raw -ErrorAction SilentlyContinue)

if ($reportText -match '(?m)^GATE STOP:') {
    Write-Host "`nThe gate stopped on an unresolved question. Read the GATE STOP line in $Report," -ForegroundColor Yellow
    Write-Host "rule on it in $Brief, then resume with:" -ForegroundColor Yellow
    Write-Host "  .\loop\$ScriptName -SkipGate -SkipBaseline" -ForegroundColor Yellow
    Write-Log "Gate stopped on an unresolved question"
    exit 4
}

# ---- Test-edit approval checkpoint -----------------------------------------
$proposed = @()
if ($reportText) {
    $proposed = @([regex]::Matches($reportText, '(?m)^PROPOSED TEST EDIT:\s*(.+?)\s*::') |
        ForEach-Object { ($_.Groups[1].Value -replace '\\','/').Trim() } | Select-Object -Unique)
}

if ($proposed.Count -gt 0 -and $AuthorisedTestEdits.Count -eq 0 -and -not (Test-Path $ApprovalFile)) {
    Write-Host "`nAPPROVAL CHECKPOINT -- the gate proposes editing $($proposed.Count) pre-existing test file(s):" -ForegroundColor Yellow
    $proposed | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
    Write-Host "`nReview each PROPOSED TEST EDIT line in $Report (behaviour superseded + ruling)." -ForegroundColor Yellow
    Write-Host "Approve by writing the paths you accept, one per line, to $ApprovalFile -- e.g. to approve all:" -ForegroundColor Yellow
    Write-Host "  Select-String -Path .\$Report -Pattern '^PROPOSED TEST EDIT:\s*(.+?)\s*::' |" -ForegroundColor Yellow
    Write-Host "    ForEach-Object { `$_.Matches[0].Groups[1].Value.Trim() } | Sort-Object -Unique |" -ForegroundColor Yellow
    Write-Host "    Set-Content .\$ApprovalFile" -ForegroundColor Yellow
    Write-Host "Delete any line you do not approve. To approve none, create the file empty." -ForegroundColor Yellow
    Write-Host "Then resume:  .\loop\$ScriptName -SkipGate -SkipBaseline" -ForegroundColor Yellow
    Write-Log "Halted for human approval of $($proposed.Count) proposed test edit(s)"
    exit 5
}

if ($proposed.Count -gt 0) {
    $unapproved = @($proposed | Where-Object { $AuthorisedTestEdits -notcontains $_ })
    if ($unapproved.Count -gt 0) {
        Write-Log "Proceeding; $($unapproved.Count) proposed edit(s) NOT approved -- builder will record them as FINDINGS:"
        $unapproved | ForEach-Object { Write-Log "  NOT APPROVED: $_" }
    }
}

# ---- Loop -------------------------------------------------------------------
Remove-Item $Verdict -ErrorAction SilentlyContinue

for ($i = 1; $i -le $MaxIterations; $i++) {

    if ($i -eq 1 -and $SkipFirstBuild) {
        Write-Host "`n--- Iteration 1 : build already done, straight to review ---" -ForegroundColor Cyan
    }
    elseif ($i -eq 1) {
        Write-Host "`n--- Iteration $i of $MaxIterations : BUILD (T1-T8) ---" -ForegroundColor Cyan
        Invoke-Agent $BuilderModel $BuildPrompt "build.log" "BUILDER build T1-T8"
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
$env:BUYER_ACCOUNTS_V1 = "0"
$off = (uv run pytest -q 2>&1 | Out-String)
Remove-Item Env:NOTIFICATIONS_V1 -ErrorAction SilentlyContinue
Remove-Item Env:SUPPLIER_ACCOUNTS_V1 -ErrorAction SilentlyContinue
Remove-Item Env:BUYER_ACCOUNTS_V1 -ErrorAction SilentlyContinue
Write-Log ("Flags-off backend: " + (($off -split "`n" | Where-Object { $_ -match 'passed' }) -join ' '))

Push-Location (Join-Path $RepoPath "frontend")
$feOff = (npm test 2>&1 | Out-String)
Pop-Location
Write-Log ("Frontend: " + (($feOff -split "`n" | Where-Object { $_ -match 'Tests' }) -join ' '))

Write-Host "`nMorning protocol:" -ForegroundColor Cyan
Write-Host "  1. Read FINDINGS and the endpoint isolation table in $Report."
Write-Host "  2. Read $Review."
Write-Host "  3. git diff --name-status $BranchPoint HEAD  ('M' only on files in $ApprovalFile)"
Write-Host "  4. uv run pytest -q                          (expect $BackendBaseline + N)"
Write-Host "  5. cd frontend ; npm test                    (expect $FrontendBaseline + M)"
Write-Host "  6. Merge yourself. Claude never merges."
Write-Host "  7. Flags on: log in as a buyer; confirm a Requester cannot approve and one company cannot see another."
