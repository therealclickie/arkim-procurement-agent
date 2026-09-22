---
module: utils/email_sender.py
star: yes
loc: 189
loc_verdict: lean
verdict: sound
findings: { high: 0, med: 0, low: 0, bloat: 0 }
---

# utils/email_sender.py — ★ send path + double gate

*Reviewed by the Cowork pass (read in full). LOC counted as non-blank, non-comment lines (docstrings count as code); host file ~230 raw / ~189 code.*

**1. What it does.** Provider-agnostic outbound send seam. `EmailSender` ABC + `GmailSender`; builds an RFC822 message (attachments → multipart/mixed), gated by `EMAIL_SEND_ENABLED`.

**2. Correctness.** Sound. The canonical send gate `EMAIL_SEND_ENABLED` is read once from env via a strict `_env_truthy` (only `1/true/yes/on`), default OFF. `send()` short-circuits to a `stubbed` result with zero network when off; flag-on-but-no-creds returns a fail-soft `error` (never a silent stub or half-send); the live branch wraps the provider call in `try/except → error`. Deterministic `Message-ID` from `rfq_id` so a later bounce DSN matches. No swallowed-failure-as-success.

**3. Efficiency.** No redundant external calls; the Gmail service is injected or lazily built once. Nothing to do.

**4. Clarity.** Clear. Small, single-responsibility methods; good docstrings.

**5. Bloat.** None. No dead code or duplication (the identically-named flag in `sourcing_archieved/tier3_outreach.py` is dead-but-imported, tracked in CLEANUP §2.2 — not this module's bloat).

**6. Findings.** **No issues found — sound.**
- *Resolved & verified:* D1-docs (commit `732b725`) corrected the stale "Gmail STUBBED / not wired" class docstring to the wired, default-off, double-gated reality. Verified consistent with code.
