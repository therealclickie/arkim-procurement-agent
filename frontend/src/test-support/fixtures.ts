/**
 * Factory helpers for the live-verified backend payload shapes the public
 * surfaces render (gate finding I6: factory helpers rather than inline
 * literals). Defaults are minimal-but-valid; tests override only what the
 * behaviour under test depends on.
 */
import type {
  OpenRequest,
  PortalProfile,
  QuoteHistoryRow,
} from "@/lib/portal-api";
import type { QuoteContext } from "@/lib/quote-api";
import type { ClaimLink } from "@/app/admin/portal-admin";

/** Portal demand teaser — the hero of the claim page. */
export function teaser(over: Partial<PortalProfile["teaser"]> = {}): PortalProfile["teaser"] {
  return {
    has_matches: true,
    count: 7,
    window_days: 30,
    framing: null,
    ...over,
  };
}

/** A claimed supplier's profile (GET /api/portal/{token}/profile). */
export function portalProfile(over: Partial<PortalProfile> = {}): PortalProfile {
  return {
    teaser: teaser(),
    supplier_domain: "sealit.example.com",
    name: "Seal-It Industrial Supply",
    brands: [{ brand_id: "Goulds", relationship: "CARRIES", evidence: null }],
    classes: [{ class_id: "SEAL", is_core: true }],
    ship_area: { kind: "NATIONWIDE_US" },
    aftermarket_disclosure: null,
    ...over,
  };
}

/** One open RFQ row (GET /api/portal/{token}/open-requests). */
export function openRequest(over: Partial<OpenRequest> = {}): OpenRequest {
  return {
    run_id: "run_001",
    manufacturer: "Gusher Pumps",
    part_number: "3x4x14H",
    quantity: 2,
    sent_at: "2026-09-18T10:00:00Z",
    quoted: null,
    ...over,
  };
}

/** One quote-history row (GET /api/portal/{token}/quotes). */
export function quoteHistoryRow(over: Partial<QuoteHistoryRow> = {}): QuoteHistoryRow {
  return {
    quote_id: "q_001",
    run_id: "run_001",
    part_number: "3x4x14H",
    quoted_part_number: "3x4x14H",
    unit_price: 1250,
    quantity: 2,
    lead_time: "in stock",
    status: "active",
    submitted_at: "2026-09-19T09:00:00Z",
    submitted_via: "portal",
    valid_until: null,
    ...over,
  };
}

/** Quote-page form context (GET /api/quote/{token}). */
export function quoteContext(over: Partial<QuoteContext> = {}): QuoteContext {
  return {
    state: "live",
    request: {
      manufacturer: "Gusher Pumps",
      part_number: "3x4x14H",
      quantity: 2,
      need_by: "2026-09-27",
    },
    supplier: { name: "Seal-It Industrial Supply", domain: "sealit.example.com" },
    expires_at: "2026-09-27T00:00:00Z",
    existing_quote: null,
    ...over,
  };
}

/** The show-once claim link the admin endpoint mints (POST claim-link). */
export function claimLink(over: Partial<ClaimLink> = {}): ClaimLink {
  return {
    supplier_domain: "sealit.example.com",
    supplier_name: "Seal-It Industrial Supply",
    token: "claim_raw_tok_111222333",
    token_id: "tokid_1",
    expires_at: "2026-09-27T00:00:00Z",
    link_path: "/portal/claim_raw_tok_111222333",
    ...over,
  };
}
