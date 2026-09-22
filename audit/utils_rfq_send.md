---
module: utils/rfq_send.py
star: yes
loc: 143
loc_verdict: lean
verdict: sound
findings: { high: 0, med: 0, low: 0, bloat: 0 }
---

# utils/rfq_send.py — ★ RFQ send + record path (HITL-gated)

*Reviewed by the Cowork pass (read in full).*

**1. What it does.** Turns an approved draft into a sent RFQ: existing draft → human `Approval` → send via the `EmailSender` interface → record sent-message metadata for inbound matching.

**2. Correctness.** Sound — the gate ordering is correct and complete. `approval is None` → never calls the provider (`not_sent_no_approval`); no usable recipient → `no_recipients`/`needs_human_contact`; provider call only when `EMAIL_SEND_ENABLED` is true, wrapped in `try/except → error` (fail-soft). The double gate (this approval AND the module flag) holds. `record_sent_message` keys (`run_id`/`supplier_domain`/`rfq_id`) match what inbound matching reads. No auto-send, no swallowed failure.

**3. Efficiency.** One provider call per approved draft; no redundancy.

**4. Clarity.** Clear `_result(...)` shape; status vocabulary documented inline.

**5. Bloat.** None.

**6. Findings.** **No issues found — sound.**
- *Resolved & verified:* D1-docs (`732b725`) — module docstring no longer claims the Gmail call is "STUBBED."
- *Cross-ref:* RBAC absence on `Approval.approved_by` is by-design at prototype stage (records who, doesn't authorize) — tracked CLEANUP §4.1; not a finding here.
