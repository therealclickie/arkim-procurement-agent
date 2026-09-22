# audit/INDEX.md

**PARTIAL — Cowork-authored subset.** Per-module files for the modules the Cowork pass
reviewed directly. The fresh v1.2 pass (Claude Code, canonical `.venv`) should add the
remaining §2 modules and the cross-cutting files (`SECURITY.md`, `KNOWN_INPUTS.md`,
`SUMMARY.md`, `PRODUCTION_READINESS.md`); `CROSS_MODULE.md` already present.

LOC = non-blank, non-comment source lines (docstrings counted as code). Verdict thresholds:
lean <200 · acceptable 200–500 · bloated 501–900 · egregious >900.

| Tier | Module | ★ | LOC | LOC verdict | H | M | L | B | Module verdict | Link |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | utils/email_sender.py | ★ | 189 | lean | 0 | 0 | 0 | 0 | sound (D1-docs resolved) | [./utils_email_sender.md](./utils_email_sender.md) |
| 1 | utils/orders.py | ★ | 262 | acceptable | 0 | 0 | 0 | 0 | sound (L1 resolved) | [./utils_orders.md](./utils_orders.md) |
| 1 | utils/price_db.py | ★ | 65 | lean | 0 | 0 | 0 | 0 | sound (L2 + §3.3 resolved) | [./utils_price_db.md](./utils_price_db.md) |
| 1 | utils/supplier_registry.py |  | 742 | bloated | 0 | 0 | 1 | 0 | N1 conn leak; bloated-cohesive | [./utils_supplier_registry.md](./utils_supplier_registry.md) |
| 1 | utils/gmail_client.py |  | 67 | lean | 0 | 0 | 0 | 0 | sound | [./utils_gmail_client.md](./utils_gmail_client.md) |
| 2 | utils/rfq_send.py | ★ | 143 | lean | 0 | 0 | 0 | 0 | sound | [./utils_rfq_send.md](./utils_rfq_send.md) |
| 3 | utils/procurement_agent/agents/procurement_agent.py |  | 129 | lean | 0 | 0 | 0 | 0 | sound (H1 guard resolved) | [./utils_procurement_agent_agents_procurement_agent.md](./utils_procurement_agent_agents_procurement_agent.md) |
| 3 | utils/procurement_agent/orchestrator/core.py |  | 401 | acceptable | 0 | 1 | 1 | 0 | M1 + §4.5 (deferred/known) | [./utils_procurement_agent_orchestrator_core.md](./utils_procurement_agent_orchestrator_core.md) |
| 3 | utils/procurement_agent/state/approval_rules.py |  | 63 | lean | 0 | 0 | 0 | 0 | sound (RBAC deferred §4.1) | [./utils_procurement_agent_state_approval_rules.md](./utils_procurement_agent_state_approval_rules.md) |
| 4 | api_server.py | ★ | 1430 | egregious | 1 | 1 | 0 | 1 | H1 dual-approver (deferred-auth); decompose | [./api_server.md](./api_server.md) |
| — | utils/apollo_client.py |  | 234 | acceptable | 0 | 0 | 0 | 0 | sound (paid; fail-soft) | [./utils_apollo_client.md](./utils_apollo_client.md) |
| — | utils/brand_intelligence.py |  | 480 | acceptable | 0 | 0 | 1 | 1 | N1 leak + N2 dead probe | [./utils_brand_intelligence.md](./utils_brand_intelligence.md) |
| — | utils/procurement_agent/outreach.py |  | 74 | lean | 0 | 0 | 0 | 0 | sound (L3 resolved) | [./utils_procurement_agent_outreach.md](./utils_procurement_agent_outreach.md) |
| — | utils/reorder.py |  | 83 | lean | 0 | 0 | 0 | 0 | sound | [./utils_reorder.md](./utils_reorder.md) |
| | **TOTALS (14 modules)** | | **4362** | | **1** | **2** | **3** | **2** | | |

Rows marked `—` in Tier are modules not enumerated in brief §2 but reviewed because an
endpoint/agent depends on them.

## Not yet covered (for the fresh v1.2 pass — owns suite certification)
Tier 1: `inbox_reader.py` ★, `quote_extractor.py` ★, `contact_extractor.py`, `bounce_parser.py`,
`bounce_processor.py`, `impact.py` ★, `models.py`, `contact_resolution.py`, `site_settings.py`,
`sourcing_filter.py`, `spec_lookup.py`, `llm_tracker.py`, `audit_log.py`, `inventory.py`, `quoting.py`, `vision.py`.
Tier 2: `reply_processor.py` ★, `reply_matcher.py`.
Tier 3: `sourcing_agent.py`, `intake_agent.py`, `spec_comparison_agent.py`, `comparison_helpers.py`, `inventory_agent.py`, `state/persistence.py`, `state/phases.py`.
Tier 5: the `proc-*` frontend components.
Cross-cutting still to write: `SECURITY.md`, `KNOWN_INPUTS.md`, `SUMMARY.md`, `PRODUCTION_READINESS.md`.

> Open-findings totals above count **only the 14 authored modules**; they are not the codebase total.
> H1/D2/M1 are the deferred-pending-auth cluster (§4.1). The fixes already landed (H1-guard, D1, C1, L1, L2, L3, D1-docs) are recorded as *resolved & verified* in the per-module files, not as open counts.
