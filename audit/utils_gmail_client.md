---
module: utils/gmail_client.py
star: no
loc: 67
loc_verdict: lean
verdict: sound
findings: { high: 0, med: 0, low: 0, bloat: 0 }
---

# utils/gmail_client.py — Gmail service builder (consequential, not ★ in §2)

*Reviewed by the Cowork pass (read in full). Consequential because it backs the real send/read, though not ★-listed in brief §2.*

**1. What it does.** Builds an authenticated Gmail API service from env credentials (service-account DWD first, then OAuth token); google libs imported lazily.

**2. Correctness.** Sound and fail-soft. `build_gmail_service()` returns `None` (never raises) on missing libs / missing creds / any error; callers degrade (sender → error, reader → []). Credentials come only from env (`GMAIL_SERVICE_ACCOUNT_JSON`/`_FILE`, `GMAIL_OAUTH_TOKEN_FILE`); nothing hardcoded.

**3. Efficiency.** Lazy imports keep the test suite import-light; no redundant calls.

**4. Clarity.** Clear.

**5. Bloat.** None.

**6. Findings.** **No issues found — sound.** (Secrets handling detail belongs in `SECURITY.md`: the key path is read from env and not logged at error.)
