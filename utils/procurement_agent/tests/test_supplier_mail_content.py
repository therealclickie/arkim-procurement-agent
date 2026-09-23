"""T8 / ruling R8 (evaluation findings F-09, F-10) — what supplier mail may say.

Fixtures are the evaluation's own S1 outbox, copied verbatim into
``fixtures/eval_s1_supplier_mail.json`` from
``eval/e2e-flags-on:eval/e2e/evidence/s1_outbox_final.json``.

Two findings, both visible in that outbox:

* **F-09** — mails 5 and 6 are the RFQ_NEW notifications. Subject: ``"New quote
  request"``. Body: ``"Arkim has sent you a request for quote."`` plus a portal
  link. The supplier is told nothing about WHAT is being requested, on a run
  whose specs say Chesterton 155, quantity 1. The template could always render
  the part — the caller passed three keys and none of them was the part.
* **F-10** — mail 3 is the Tier-1 FYI: subject ``"Arkim matched request — SEAL
  (run e0489093-4d8c-4563-ab8f-cb6c789ed6db)"``, body carrying ``"Matched
  class: SEAL"``, ``"Relationship: class-matched (no brand row)"`` and ``"Core
  class: yes"``. An internal run UUID and three lines of internal
  classification vocabulary, sent to a supplier.
"""

import json
import re
from pathlib import Path

import pytest

from utils import notifications

_EVIDENCE = json.loads(
    (Path(__file__).parent / "fixtures" / "eval_s1_supplier_mail.json").read_text(encoding="utf-8")
)

OBSERVED_RFQ_NEW = _EVIDENCE["observed_rfq_new_mail"]
OBSERVED_TIER1_FYI = _EVIDENCE["observed_tier1_fyi_mail"]
S1_SPECS = _EVIDENCE["s1_asset_specs"]

#: R8's denylist: internal classification vocabulary that must never be sent to
#: a supplier. Lowercased substrings.
INTERNAL_VOCABULARY = (
    "class-matched", "no brand row", "noun_class", "matched class",
    "core class", "band a", "band b", "band c", "suitability",
    "pn_match", "tier 2", "tier 3", "spec_incomplete",
)

#: A run UUID, or any other 8-4-4-4-12 identifier.
UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)


# ---------------------------------------------------------------------------
# F-09 — RFQ_NEW says what the request is
# ---------------------------------------------------------------------------

class TestTheEvidence:
    def test_the_observed_rfq_new_named_nothing(self):
        assert OBSERVED_RFQ_NEW["subject"] == "New quote request"
        assert "Chesterton" not in OBSERVED_RFQ_NEW["body"]

    def test_the_observed_run_did_have_a_part(self):
        assert S1_SPECS["manufacturer"] == "Chesterton"
        assert S1_SPECS["model"] == "155"

    def test_the_observed_tier1_fyi_leaked_a_run_uuid_and_internal_vocabulary(self):
        assert UUID_RE.search(OBSERVED_TIER1_FYI["subject"])
        body = OBSERVED_TIER1_FYI["body"].lower()
        assert "class-matched (no brand row)" in body
        assert "matched class:" in body


class TestRfqNewNamesThePart:
    def _rfq(self, **over):
        rfq = {"manufacturer": "Chesterton", "part_number": "155", "quantity": 1}
        rfq.update(over)
        return rfq

    def test_the_subject_names_the_part(self):
        subject, _body = notifications._rfq_subject_and_body(self._rfq())
        assert "Chesterton 155" in subject
        assert subject != OBSERVED_RFQ_NEW["subject"]

    def test_the_body_names_part_manufacturer_quantity_and_the_portal_link(self):
        _subject, body = notifications._rfq_subject_and_body(self._rfq())
        assert "Chesterton" in body
        assert "155" in body
        assert "Quantity: 1" in body
        assert "/supplier/requests" in body

    def test_the_needed_by_date_rides_along_when_known(self):
        _subject, body = notifications._rfq_subject_and_body(
            self._rfq(need_by="2026-09-30"))
        assert "Needed by: 2026-09-30" in body

    def test_it_is_absent_when_not_known(self):
        _subject, body = notifications._rfq_subject_and_body(self._rfq())
        assert "Needed by" not in body

    def test_the_description_rides_along_and_is_not_duplicated(self):
        _s, body = notifications._rfq_subject_and_body(
            self._rfq(description="single cartridge mechanical seal"))
        assert body.count("single cartridge mechanical seal") == 1

    def test_it_carries_no_price(self):
        """The existing no-numbers rule: an RFQ mail never quotes a figure."""
        _subject, body = notifications._rfq_subject_and_body(
            self._rfq(description="seal", need_by="2026-09-30"))
        assert "$" not in body
        assert "price" not in body.lower()

    def test_an_identity_less_request_degrades_to_the_old_wording(self):
        subject, body = notifications._rfq_subject_and_body({})
        assert subject == OBSERVED_RFQ_NEW["subject"]
        assert "/supplier/requests" in body


class TestTheIdentityReachesTheTemplate:
    """F-09 was a CALLER bug — the template was always capable."""

    def test_the_run_identity_helper_reads_the_specs(self, monkeypatch):
        from utils.procurement_agent.state import persistence
        monkeypatch.setattr(persistence, "get_run",
                            lambda rid: {"asset_specs_json": dict(S1_SPECS)})
        out = notifications.rfq_identity_for_run("run-1")
        assert out["manufacturer"] == "Chesterton"
        assert out["part_number"] == "155", "the model stands in for a family-level PN"
        assert out["quantity"] == 1

    def test_it_only_ever_carries_the_allowed_keys(self, monkeypatch):
        """R8's no-leak rule: nothing else on a run may reach supplier mail."""
        from utils.procurement_agent.state import persistence
        leaky = dict(S1_SPECS, spec_based_sourcing=True, family_open_commit=True,
                     confidence_reasoning="internal reasoning", _asked_fields=["x"])
        monkeypatch.setattr(persistence, "get_run",
                            lambda rid: {"asset_specs_json": leaky})
        out = notifications.rfq_identity_for_run("run-1")
        assert set(out) <= set(notifications.RFQ_IDENTITY_KEYS)

    def test_null_tokens_are_dropped(self, monkeypatch):
        from utils.procurement_agent.state import persistence
        monkeypatch.setattr(persistence, "get_run", lambda rid: {"asset_specs_json": {
            "manufacturer": "Chesterton", "part_number": "UNKNOWN-PN",
            "model": "155", "quantity": None}})
        out = notifications.rfq_identity_for_run("run-1")
        assert out["part_number"] == "155"
        assert "quantity" not in out

    def test_an_unreadable_run_is_fail_soft(self, monkeypatch):
        from utils.procurement_agent.state import persistence

        def boom(_rid):
            raise RuntimeError("db down")

        monkeypatch.setattr(persistence, "get_run", boom)
        assert notifications.rfq_identity_for_run("run-1") == {}

    def test_no_run_id_is_fail_soft(self):
        assert notifications.rfq_identity_for_run(None) == {}


# ---------------------------------------------------------------------------
# The coalesced batch
# ---------------------------------------------------------------------------

class TestTheCoalescedBatch:
    def _item(self, mfg, pn, qty=1):
        return {"detail": {"manufacturer": mfg, "part_number": pn, "quantity": qty}}

    def test_a_multi_item_subject_gives_the_count(self):
        items = [self._item("Goulds", "3296"), self._item("Chesterton", "155"),
                 self._item("SKF", "6205-2RS C3")]
        subject, _body = notifications._batch_subject_and_body(items)
        assert subject == "3 new quote requests"

    def test_the_body_lists_every_item(self):
        items = [self._item("Goulds", "3296", 2), self._item("Chesterton", "155"),
                 self._item("SKF", "6205-2RS C3", 4)]
        _subject, body = notifications._batch_subject_and_body(items)
        assert "Goulds 3296 (qty 2)" in body
        assert "Chesterton 155" in body
        assert "SKF 6205-2RS C3 (qty 4)" in body

    def test_a_one_item_batch_keeps_the_single_request_wording(self):
        subject, body = notifications._batch_subject_and_body(
            [self._item("Goulds", "3296")])
        assert subject == "New quote request — Goulds 3296"
        assert "1 new quote request" not in subject

    def test_a_batch_carries_no_price(self):
        items = [self._item("Goulds", "3296"), self._item("Chesterton", "155")]
        _subject, body = notifications._batch_subject_and_body(items)
        assert "$" not in body


# ---------------------------------------------------------------------------
# F-10 — nothing internal
# ---------------------------------------------------------------------------

class TestTheTier1Fyi:
    def _render(self):
        from utils.procurement_agent import tier1_notify
        import inspect
        return inspect.getsource(tier1_notify._send_fyi_mail) \
            if hasattr(tier1_notify, "_send_fyi_mail") else None

    def _send(self, monkeypatch, tmp_path):
        from utils import supplier_registry
        from utils.procurement_agent import tier1_notify

        monkeypatch.setattr(supplier_registry, "assemble_recipient_set",
                            lambda dom: {"to": ["sales@dxpe.com"], "cc": []})
        captured = {}

        class _Sender:
            def send(self, message):
                captured["subject"] = message.subject
                captured["body"] = message.body
                from utils.email_sender import SendResult
                return SendResult(status="sent", message_id="m-1")

        match = type("M", (), {
            "domain": "dxpe.com", "vendor_name": "DXP Enterprises",
            "noun_class": "SEAL", "brand_relationship": None, "is_core": True,
        })()
        tier1_notify._send_notify(
            match, "e0489093-4d8c-4563-ab8f-cb6c789ed6db", _Sender())
        return captured

    def test_the_subject_no_longer_carries_a_run_uuid(self, monkeypatch, tmp_path):
        sent = self._send(monkeypatch, tmp_path)
        assert UUID_RE.search(OBSERVED_TIER1_FYI["subject"]), "the evidence had one"
        assert UUID_RE.search(sent["subject"]) is None

    def test_neither_does_the_body(self, monkeypatch, tmp_path):
        sent = self._send(monkeypatch, tmp_path)
        assert UUID_RE.search(sent["body"]) is None

    def test_the_internal_classification_vocabulary_is_gone(self, monkeypatch, tmp_path):
        sent = self._send(monkeypatch, tmp_path)
        text = (sent["subject"] + "\n" + sent["body"]).lower()
        for term in INTERNAL_VOCABULARY:
            assert term not in text, term

    def test_it_still_tells_the_supplier_what_it_is(self, monkeypatch, tmp_path):
        sent = self._send(monkeypatch, tmp_path)
        assert "DXP Enterprises" in sent["body"]
        assert "automated FYI" in sent["body"]


# ---------------------------------------------------------------------------
# The scan across every supplier-facing template
# ---------------------------------------------------------------------------

class TestEverySupplierFacingTemplate:
    """A rendered scan, not a source grep: each template is built with realistic
    inputs and the OUTPUT is checked for a UUID and the denylist."""

    def _rendered(self):
        rfq = {"manufacturer": "Chesterton", "part_number": "155", "quantity": 1,
               "description": "single cartridge mechanical seal",
               "need_by": "2026-09-30",
               # Hostile inputs: internal keys that must NOT be rendered.
               "run_id": "e0489093-4d8c-4563-ab8f-cb6c789ed6db",
               "supplier_domain": "dxpe.com",
               "sent_message_id": "b2a1c0de-1111-2222-3333-444455556666"}
        items = [{"detail": dict(rfq)}, {"detail": {"manufacturer": "SKF",
                                                    "part_number": "6205-2RS C3"}}]
        out = [notifications._rfq_subject_and_body(rfq),
               notifications._batch_subject_and_body(items),
               notifications._batch_subject_and_body([{"detail": dict(rfq)}])]
        parent = {"detail": dict(rfq), "supplier_domain": "dxpe.com",
                  "run_id": rfq["run_id"], "id": rfq["sent_message_id"]}
        out.append(notifications._reminder_subject_and_body([parent]))
        out.append(notifications._reminder_subject_and_body([parent, parent]))
        return out

    def test_no_supplier_facing_template_renders_a_uuid(self):
        for subject, body in self._rendered():
            assert UUID_RE.search(subject) is None, subject
            assert UUID_RE.search(body) is None, body

    def test_no_supplier_facing_template_renders_internal_vocabulary(self):
        for subject, body in self._rendered():
            text = (subject + "\n" + body).lower()
            for term in INTERNAL_VOCABULARY:
                assert term not in text, f"{term!r} in {subject!r}"

    def test_the_uuid_matcher_would_catch_the_evidence(self):
        """Proves the scan has teeth — it flags the mail F-10 was raised on."""
        assert UUID_RE.search(OBSERVED_TIER1_FYI["subject"]) is not None
