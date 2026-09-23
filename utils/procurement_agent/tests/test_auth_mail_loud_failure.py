"""T7 / ruling R7 (evaluation finding F-03) — fail loudly, to the OPERATOR.

What the evaluation verified offline
(``eval/e2e-flags-on:eval/e2e/evidence/verify/verify_offline_checks.json`` →
``F-03``): with ``SES_CONFIGURATION_SET_AUTH`` unset,
``mail_provider.message_configuration_set`` returns

    [null, "auth mail requires the tracking-off configuration set
            (SES_CONFIGURATION_SET_AUTH is unset); refusing to send on a
            tracking-enabled set"]

— every supplier sign-in link is silently refused, under EVERY provider, and
nobody is told. The system boots happily, the supplier requests a link, gets a
cheerful ``{"ok": true}``, and no mail ever arrives.

R7 splits the failure three ways:

1. **Boot.** Under SES with accounts on and the variable unset, refuse to start,
   naming the variable.
2. **Dev/demo/eval.** Under ``FakeProvider`` the auth configuration set is NOT
   required, and auth mail is captured normally.
3. **Runtime.** Any refused auth-mail send raises one deduped ACTION_NOW
   concierge alert.

And the constraint that binds all three: **the supplier-facing
``request-link`` response stays byte-identical in every case.** Failing loudly
must never create an enumeration oracle.
"""

import json
from pathlib import Path

import pytest

from utils import mail_provider, notifications_store as ns, supplier_accounts
from utils.email_sender import EmailMessage
from utils.procurement_agent.tests._arc4_notifications_fixtures import (  # noqa: F401
    AUTH_SET, active_member, isolate_notification_stores, notif_api,
)

_VERIFY = json.loads(
    (Path(__file__).parent / "fixtures" / "eval_f03_auth_mail.json").read_text(encoding="utf-8")
)
OBSERVED_REFUSAL = _VERIFY["observed_message_configuration_set"][1]


def _auth_message(to="sales@dxpe.com", domain="dxpe.com"):
    return EmailMessage(
        to=[to],
        subject="Your Arkim supplier sign-in link",
        body="https://portal.example.com/s/abc123",
        metadata={"auth_mail": True, "message_class": "auth",
                  "supplier_domain": domain},
    )


# ---------------------------------------------------------------------------
# 1. Boot
# ---------------------------------------------------------------------------

class TestBootRefusal:
    """The guard is a pure function so it can be asserted without re-importing
    api_server (which would re-run every other module-level guard)."""

    def _assert_guard(self, monkeypatch, **env):
        import api_server
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        return api_server._assert_auth_mail_configured

    def test_ses_with_accounts_on_and_the_var_unset_refuses_to_boot(self, monkeypatch):
        guard = self._assert_guard(
            monkeypatch, SUPPLIER_ACCOUNTS_V1="1", NOTIFICATIONS_V1="1",
            MAIL_PROVIDER="ses", SES_CONFIGURATION_SET_AUTH="")
        with pytest.raises(RuntimeError) as exc:
            guard()
        assert "Refusing to start" in str(exc.value)
        assert mail_provider.ENV_CONFIG_SET_AUTH in str(exc.value), (
            "R7: the error must NAME the missing variable")

    def test_the_same_config_with_the_var_set_boots(self, monkeypatch):
        guard = self._assert_guard(
            monkeypatch, SUPPLIER_ACCOUNTS_V1="1", NOTIFICATIONS_V1="1",
            MAIL_PROVIDER="ses", SES_CONFIGURATION_SET_AUTH=AUTH_SET)
        guard()

    def test_the_fake_provider_does_not_require_it(self, monkeypatch):
        guard = self._assert_guard(
            monkeypatch, SUPPLIER_ACCOUNTS_V1="1", NOTIFICATIONS_V1="1",
            MAIL_PROVIDER="fake", SES_CONFIGURATION_SET_AUTH="")
        guard()

    def test_accounts_off_does_not_require_it(self, monkeypatch):
        guard = self._assert_guard(
            monkeypatch, SUPPLIER_ACCOUNTS_V1="", NOTIFICATIONS_V1="1",
            MAIL_PROVIDER="ses", SES_CONFIGURATION_SET_AUTH="")
        guard()

    def test_notifications_off_does_not_require_it(self, monkeypatch):
        """Flag off ⇒ the Gmail path ⇒ no SES configuration set exists at all."""
        guard = self._assert_guard(
            monkeypatch, SUPPLIER_ACCOUNTS_V1="1", NOTIFICATIONS_V1="",
            MAIL_PROVIDER="ses", SES_CONFIGURATION_SET_AUTH="")
        guard()


# ---------------------------------------------------------------------------
# 2. FakeProvider is exempt
# ---------------------------------------------------------------------------

class TestFakeProviderCapturesAuthMail:
    def test_it_captures_auth_mail_with_no_auth_configuration_set(self, monkeypatch):
        monkeypatch.setenv("SES_CONFIGURATION_SET_AUTH", "")
        provider = mail_provider.FakeProvider()
        result = provider.send(_auth_message(), sender="procurement@arkim.ai")
        assert result.status == "sent"
        assert len(provider.outbox) == 1
        assert provider.outbox[0]["subject"] == "Your Arkim supplier sign-in link"

    def test_the_provider_agnostic_helper_still_reports_the_refusal(self, monkeypatch):
        """``message_configuration_set`` is unchanged — the exemption lives in
        ``FakeProvider.send``, so every existing assertion on the helper holds."""
        monkeypatch.setenv("SES_CONFIGURATION_SET_AUTH", "")
        config_set, refusal = mail_provider.message_configuration_set(_auth_message())
        assert config_set is None
        assert refusal == OBSERVED_REFUSAL

    def test_ses_still_refuses(self, monkeypatch):
        monkeypatch.setenv("SES_CONFIGURATION_SET_AUTH", "")
        result = mail_provider.SesProvider().send(_auth_message())
        assert result.status == "error"
        assert result.error == OBSERVED_REFUSAL

    def test_the_fake_provider_still_honours_a_configured_set(self, monkeypatch):
        monkeypatch.setenv("SES_CONFIGURATION_SET_AUTH", AUTH_SET)
        provider = mail_provider.FakeProvider()
        assert provider.send(_auth_message()).status == "sent"


# ---------------------------------------------------------------------------
# 3. The runtime alert
# ---------------------------------------------------------------------------

class TestRefusedSendRaisesAnAlert:
    @pytest.fixture
    def stores(self, tmp_path, monkeypatch):
        store = isolate_notification_stores(tmp_path, monkeypatch)
        monkeypatch.setenv("SES_CONFIGURATION_SET_AUTH", "")
        return store

    def test_a_refused_auth_send_raises_one_action_now_alert(self, stores):
        mail_provider.SesProvider().send(_auth_message())
        matching = stores.list_alerts(
            status=None, kind=mail_provider.ALERT_AUTH_MAIL_REFUSED)
        assert len(matching) == 1
        assert matching[0]["tier"] == ns.TIER_ACTION_NOW

    def test_the_alert_names_the_missing_variable(self, stores):
        mail_provider.SesProvider().send(_auth_message())
        alert = stores.list_alerts(
            status=None, kind=mail_provider.ALERT_AUTH_MAIL_REFUSED)[0]
        assert alert["detail"]["missing_env"] == mail_provider.ENV_CONFIG_SET_AUTH
        assert alert["detail"]["reason"] == OBSERVED_REFUSAL

    def test_it_is_deduped_across_a_burst(self, stores):
        for email in ("a@dxpe.com", "b@dxpe.com", "c@other.com"):
            mail_provider.SesProvider().send(_auth_message(to=email))
        matching = stores.list_alerts(
            status=None, kind=mail_provider.ALERT_AUTH_MAIL_REFUSED)
        assert len(matching) == 1, (
            "one misconfiguration is one thing to fix, however many sends it blocks")

    def test_a_successful_auth_send_raises_nothing(self, stores, monkeypatch):
        monkeypatch.setenv("SES_CONFIGURATION_SET_AUTH", AUTH_SET)
        mail_provider.FakeProvider().send(_auth_message())
        assert stores.list_alerts(
            status=None, kind=mail_provider.ALERT_AUTH_MAIL_REFUSED) == []

    def test_the_new_kind_defaults_to_action_now(self):
        assert ns.alert_tier(mail_provider.ALERT_AUTH_MAIL_REFUSED) == ns.TIER_ACTION_NOW

    def test_an_alerting_failure_never_changes_the_send_result(self, stores, monkeypatch):
        def boom(**_kw):
            raise RuntimeError("alert store down")

        monkeypatch.setattr(ns, "raise_alert", boom)
        result = mail_provider.SesProvider().send(_auth_message())
        assert result.status == "error" and result.error == OBSERVED_REFUSAL


# ---------------------------------------------------------------------------
# 4. The constraint: no enumeration oracle
# ---------------------------------------------------------------------------

class TestRequestLinkIsByteIdentical:
    """R7: failing loudly must never make the supplier-facing response differ.

    Compared as BYTES, not as parsed JSON, across the three cases R7 names —
    send succeeded, send refused, address unknown — plus a contrast case that
    proves the comparison can tell responses apart at all.
    """

    _EMAIL_KNOWN = "sales@dxpe.com"
    _EMAIL_UNKNOWN = "nobody@an-account-that-does-not-exist.example"

    def _post(self, client, email):
        return client.post("/api/supplier/auth/request-link", json={"email": email})

    def _fingerprint(self, resp):
        """Everything a client can observe, except timing."""
        return (resp.status_code, resp.content, tuple(sorted(
            (k.lower(), v) for k, v in resp.headers.items()
            if k.lower() not in ("date", "content-length", "server"))))

    @pytest.fixture
    def api(self, notif_api, monkeypatch):
        monkeypatch.setenv("SUPPLIER_AUTH_RATE_CAP_EMAIL", "0")   # limiter off
        monkeypatch.setenv("SUPPLIER_AUTH_RATE_CAP_IP", "0")
        active_member(notif_api, domain="dxpe.com", email=self._EMAIL_KNOWN)
        return notif_api

    def test_send_succeeded_vs_send_refused_are_byte_identical(self, api, monkeypatch):
        monkeypatch.setenv("SES_CONFIGURATION_SET_AUTH", AUTH_SET)
        ok = self._fingerprint(self._post(api, self._EMAIL_KNOWN))

        monkeypatch.setenv("SES_CONFIGURATION_SET_AUTH", "")      # the F-03 state
        refused = self._fingerprint(self._post(api, self._EMAIL_KNOWN))

        assert ok == refused

    def test_send_refused_vs_unknown_address_are_byte_identical(self, api, monkeypatch):
        monkeypatch.setenv("SES_CONFIGURATION_SET_AUTH", "")
        refused = self._fingerprint(self._post(api, self._EMAIL_KNOWN))
        unknown = self._fingerprint(self._post(api, self._EMAIL_UNKNOWN))
        assert refused == unknown

    def test_all_three_cases_agree(self, api, monkeypatch):
        monkeypatch.setenv("SES_CONFIGURATION_SET_AUTH", AUTH_SET)
        fingerprints = {self._fingerprint(self._post(api, self._EMAIL_KNOWN))}
        monkeypatch.setenv("SES_CONFIGURATION_SET_AUTH", "")
        fingerprints.add(self._fingerprint(self._post(api, self._EMAIL_KNOWN)))
        fingerprints.add(self._fingerprint(self._post(api, self._EMAIL_UNKNOWN)))
        assert len(fingerprints) == 1

    def test_the_contrast_case_proves_the_comparison_has_teeth(self, api):
        """A response that IS different must compare different — otherwise the
        three equalities above would pass vacuously."""
        uniform = self._fingerprint(self._post(api, self._EMAIL_KNOWN))
        different = self._fingerprint(
            api.post("/api/supplier/auth/request-link", json={"not_email": "x"}))
        assert uniform != different
        assert different[0] == 422

    def test_the_alert_is_raised_but_never_reflected_in_the_response(
            self, api, monkeypatch, tmp_path):
        monkeypatch.setenv("SES_CONFIGURATION_SET_AUTH", "")
        resp = self._post(api, self._EMAIL_KNOWN)
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        body = resp.content
        assert b"CONFIGURATION" not in body.upper()
        assert b"refus" not in body.lower()
        assert b"dxpe" not in body.lower()
