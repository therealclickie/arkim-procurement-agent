"use client";

/**
 * OpenRequests — the portal's path-B quote section (Night 11, T5).
 *
 * A claimed supplier sees THEIR open requests (RFQs addressed to their
 * domain) and quotes them inline with the SAME five-field form the public
 * /quote/{token} page uses, plus their own quote history. Nothing
 * cross-supplier: the backend scopes everything to the validated claim
 * token's domain.
 *
 * Flag posture: when QUOTE_SUBMIT_V1 is off the endpoints 404 uniformly and
 * this component renders NOTHING — the claim portal looks exactly as it did
 * before Night 11 (no empty shells, no flag plumbing in the UI).
 *
 * ARC 3: the same component now also serves the SESSION inbox
 * (/supplier/requests). It does not know which door it came in by — the
 * auth-mode object supplies the operations (see lib/supplier-mode).
 *
 * TWO THINGS HERE ARE LOAD-BEARING and must not be "improved":
 *
 *  1. `token` stays an ordinary optional prop and, with NO provider above,
 *     behaviour is byte-identical to before arc 3. That is exactly how the
 *     pre-existing characterisation tests render it.
 *  2. When both lists are empty this renders LITERALLY NOTHING — no heading,
 *     no empty card, no "no requests yet". A supplier with no open RFQs gets
 *     a blank component, and the honest empty-state copy lives in the
 *     /supplier/requests PAGE instead. Putting that copy in here is the
 *     single easiest way to break the pre-existing empty-state assertions.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { OpenRequest, QuoteHistoryRow } from "@/lib/portal-api";
import { useSupplierMode } from "@/lib/supplier-mode";
import { QuoteForm, type QuoteFormState } from "../../quote/[token]/quote-form";

/** Effective quote statuses (portal-api QuoteHistoryRow.status) → chip labels.
 *  Unknown values fall through verbatim — never masked. */
const STATUS_LABEL: Record<string, string> = {
  active: "Active",
  review: "In review",
  superseded: "Superseded",
  expired: "Expired",
  withdrawn: "Withdrawn",
};

export function OpenRequests({
  token,
  onEmpty,
}: {
  token?: string;
  /** Called once the first load settles, with whether BOTH lists came back
   *  empty. Lets the session page own its empty-state copy without this
   *  component ever rendering one (see the header note). */
  onEmpty?: (isEmpty: boolean) => void;
}) {
  const mode = useSupplierMode(token);
  const onEmptyRef = useRef(onEmpty);
  onEmptyRef.current = onEmpty;
  const [requests, setRequests] = useState<OpenRequest[] | null>(null);
  const [history, setHistory] = useState<QuoteHistoryRow[] | null>(null);
  const [openRun, setOpenRun] = useState<string | null>(null);
  const [form, setForm] = useState<QuoteFormState | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitFailed, setSubmitFailed] = useState(false);

  const refresh = useCallback(async () => {
    const [reqs, hist] = await Promise.all([
      mode.getOpenRequests(),
      mode.getQuoteHistory(),
    ]);
    // Feature off / any failure ⇒ render nothing (uniform rejection).
    const nextRequests = reqs.ok ? reqs.data.requests : null;
    const nextHistory = hist.ok ? hist.data.quotes : null;
    setRequests(nextRequests);
    setHistory(nextHistory);
    onEmptyRef.current?.(
      (nextRequests?.length ?? 0) === 0 && (nextHistory?.length ?? 0) === 0,
    );
  }, [mode]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (!requests && !history) return null;

  const startQuoting = (r: OpenRequest) => {
    setOpenRun(r.run_id);
    setSubmitFailed(false);
    setForm({
      quoteNumber: "",
      unitPrice: r.quoted ? String(r.quoted.unit_price) : "",
      quantity: r.quantity != null ? String(r.quantity) : "",
      leadTimeDays: "",
      inStock: false,
      partNumber: r.part_number ?? "",
      freight: "",
      validUntil: "",
      notes: "",
    });
  };

  const handleSubmit = async (runId: string, f: QuoteFormState) => {
    setForm(f);
    setSubmitting(true);
    setSubmitFailed(false);
    const res = await mode.submitQuote({
      run_id: runId,
      quote_number: f.quoteNumber.trim(),
      unit_price: Number(f.unitPrice),
      quantity: Number(f.quantity),
      lead_time: f.inStock ? "in stock" : `${f.leadTimeDays.trim()} days`,
      part_number: f.partNumber.trim() || null,
      freight: f.freight.trim() || null,
      valid_until: f.validUntil || null,
      notes: f.notes.trim() || null,
    });
    setSubmitting(false);
    if (res.ok) {
      setOpenRun(null);
      setForm(null);
      await refresh(); // the row's quoted badge + history update honestly
    } else {
      setSubmitFailed(true); // input preserved in `form`
    }
  };

  return (
    <>
      {requests && requests.length > 0 && (
        <section className="portal-open-requests" aria-label="Open requests">
          <h2 className="portal-section-title">Open requests for you</h2>
          <ul className="portal-request-list">
            {requests.map((r) => (
              <li key={r.run_id} className="portal-request-row">
                <div className="portal-request-head">
                  <div>
                    <p className="portal-request-part">
                      {[r.manufacturer, r.part_number]
                        .filter(Boolean)
                        .join(" — ") || "Requested part"}
                    </p>
                    <p className="quote-hint">
                      {r.quantity != null ? `Qty ${r.quantity}` : ""}
                      {r.sent_at
                        ? `${r.quantity != null ? " · " : ""}requested ${r.sent_at.slice(0, 10)}`
                        : ""}
                    </p>
                  </div>
                  {r.quoted ? (
                    <span
                      className={
                        r.quoted.status === "active"
                          ? "portal-quoted-badge portal-quoted-badge--active"
                          : "portal-quoted-badge"
                      }
                    >
                      {r.quoted.status === "active"
                        ? `✓ Quoted $${r.quoted.unit_price}`
                        : `⏳ Quote in review ($${r.quoted.unit_price})`}
                    </span>
                  ) : null}
                  <button
                    type="button"
                    className="portal-brand-add-btn"
                    onClick={() =>
                      openRun === r.run_id ? setOpenRun(null) : startQuoting(r)
                    }
                  >
                    {openRun === r.run_id
                      ? "Close"
                      : r.quoted
                        ? "Revise quote"
                        : "Quote this"}
                  </button>
                </div>
                {openRun === r.run_id && form && (
                  <div className="portal-request-form">
                    {submitFailed && (
                      <div className="portal-soft-error" role="alert">
                        We couldn&apos;t submit your quote right now. Your
                        entries are kept — please try again in a moment.
                      </div>
                    )}
                    <QuoteForm
                      initial={form}
                      requestedPartNumber={r.part_number}
                      onChange={setForm}
                      onSubmit={(f) => void handleSubmit(r.run_id, f)}
                      submitting={submitting}
                      revising={Boolean(r.quoted)}
                    />
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {history && history.length > 0 && (
        <section className="portal-quote-history" aria-label="Your quotes">
          <h2 className="portal-section-title">Your quotes</h2>
          <ul className="portal-history-list">
            {history.map((q) => (
              <li key={q.quote_id} className="portal-history-row">
                <span className="portal-history-part">
                  {q.quoted_part_number || q.part_number || "—"}
                </span>
                <span className="portal-history-price">${q.unit_price}</span>
                <span className="portal-status-chip" data-status={q.status}>
                  {STATUS_LABEL[q.status] ?? q.status}
                </span>
                {q.submitted_at && (
                  <span className="portal-history-status">{q.submitted_at.slice(0, 10)}</span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
