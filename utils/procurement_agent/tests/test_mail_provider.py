"""
Arc 4 T2 — the flag, the ``MailProvider`` adapter, and provider selection.

The brief's test list: "flag off → Gmail path unchanged (existing tests
untouched and green); flag on → SesProvider chosen; Stubber-verified
``SendEmail`` call shape including configuration set; no network".

D10 IS STRUCTURAL HERE, NOT A PROMISE. ``SesProvider`` is always given either
an injected client wrapped in a botocore ``Stubber`` or no region at all. A
``Stubber`` raises ``UnStubbedResponseError`` on any call it was not primed
for, so a test that accidentally made a second, unexpected AWS call would
FAIL rather than quietly reach the internet.
"""
from __future__ import annotations

import pytest

from utils import email_sender, mail_provider
from utils.email_sender import EmailAttachment, EmailMessage, GmailSender
from utils.procurement_agent.tests._arc4_notifications_fixtures import (  # noqa: F401
    AUTH_SET, NOTIFY_SET, allowlist, isolate_notification_stores, notif_stores,
)


@pytest.fixture
def ses_config(monkeypatch):
    """Arc-4 config, flag ON, no stores needed (this file is pure transport)."""
    monkeypatch.setenv("NOTIFICATIONS_V1", "1")
    monkeypatch.setenv("SES_CONFIGURATION_SET_NOTIFICATIONS", NOTIFY_SET)
    monkeypatch.setenv("SES_CONFIGURATION_SET_AUTH", AUTH_SET)
    monkeypatch.setenv("SES_FROM_ADDRESS", "procurement@arkim.ai")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setattr(mail_provider, "_OVERRIDE_PROVIDER", None)


def msg(**kw) -> EmailMessage:
    base = dict(to=["sales@dxpe.com"], subject="Quote request", body="hello")
    base.update(kw)
    return EmailMessage(**base)


def stubbed_ses():
    """A real SESv2 client with a botocore ``Stubber`` attached. Region and
    credentials are supplied inline so botocore never consults the ambient
    environment, an instance-metadata endpoint, or ``~/.aws``."""
    import boto3
    from botocore.stub import Stubber
    client = boto3.client("sesv2", region_name="us-east-1",
                          aws_access_key_id="test", aws_secret_access_key="test")
    return client, Stubber(client)


# ---------------------------------------------------------------------------
# The flag + selection
# ---------------------------------------------------------------------------

def test_flag_defaults_off_and_parses_strictly(monkeypatch):
    monkeypatch.delenv("NOTIFICATIONS_V1", raising=False)
    assert mail_provider.notifications_active() is False
    for falsy in ("", "0", "false", "no", "junk", "  "):
        monkeypatch.setenv("NOTIFICATIONS_V1", falsy)
        assert mail_provider.notifications_active() is False
    for truthy in ("1", "true", "YES", "On", " true "):
        monkeypatch.setenv("NOTIFICATIONS_V1", truthy)
        assert mail_provider.notifications_active() is True


def test_flag_off_selects_no_provider_at_all(monkeypatch):
    """The flag-off contract in one assertion: no provider ⇒ email_sender
    runs its existing Gmail code, byte-identical to before arc 4."""
    monkeypatch.setattr(mail_provider, "_OVERRIDE_PROVIDER", None)
    monkeypatch.setenv("NOTIFICATIONS_V1", "")
    assert mail_provider.active_provider() is None


def test_flag_on_selects_ses_by_default(ses_config):
    provider = mail_provider.active_provider()
    assert isinstance(provider, mail_provider.SesProvider)
    assert provider.name == "ses"


def test_provider_choice_is_config_not_hard_coded(ses_config, monkeypatch):
    monkeypatch.setenv("MAIL_PROVIDER", "fake")
    assert isinstance(mail_provider.active_provider(), mail_provider.FakeProvider)


# ---------------------------------------------------------------------------
# Flag OFF ⇒ the Gmail path is genuinely untouched
# ---------------------------------------------------------------------------

def test_flag_off_still_reaches_the_injected_gmail_service(monkeypatch):
    monkeypatch.setattr(mail_provider, "_OVERRIDE_PROVIDER", None)
    monkeypatch.setenv("NOTIFICATIONS_V1", "")
    monkeypatch.setattr(email_sender, "EMAIL_SEND_ENABLED", True)
    calls = []

    class FakeGmail:
        def users(self):
            return self

        def messages(self):
            return self

        def send(self, userId, body):
            calls.append((userId, body))
            return self

        def execute(self):
            return {"id": "gmail-1", "threadId": "thread-1"}

    result = GmailSender(service=FakeGmail()).send(msg())
    assert result.status == "sent" and result.thread_id == "thread-1"
    assert len(calls) == 1


def test_flag_on_bypasses_gmail_entirely(ses_config, monkeypatch):
    monkeypatch.setattr(email_sender, "EMAIL_SEND_ENABLED", True)
    fake = mail_provider.FakeProvider()
    monkeypatch.setattr(mail_provider, "_OVERRIDE_PROVIDER", fake)

    class ExplodingGmail:
        def users(self):
            raise AssertionError("the Gmail path must not run with the flag on")

    result = GmailSender(service=ExplodingGmail()).send(msg())
    assert result.status == "sent"
    assert result.message_id == fake.outbox[0]["provider_message_id"]


def test_the_delivery_gate_still_sits_above_the_provider(ses_config, monkeypatch):
    """EMAIL_SEND_ENABLED off ⇒ stubbed, and the provider is never reached —
    the reason the whole suite can run with the arc flag on and still send
    nothing."""
    monkeypatch.setattr(email_sender, "EMAIL_SEND_ENABLED", False)
    fake = mail_provider.FakeProvider()
    monkeypatch.setattr(mail_provider, "_OVERRIDE_PROVIDER", fake)
    assert GmailSender().send(msg()).status == "stubbed"
    assert fake.outbox == []


# ---------------------------------------------------------------------------
# FakeProvider
# ---------------------------------------------------------------------------

def test_fake_provider_records_and_returns_an_id(ses_config):
    fake = mail_provider.FakeProvider()
    out = fake.send(msg(metadata={"notification_id": "n-1",
                                  "notification_kind": "RFQ_NEW"}))
    assert out.status == "sent" and out.provider_message_id.startswith("fake-")
    assert out.configuration_set == NOTIFY_SET
    assert fake.outbox[0]["tags"] == [
        {"Name": "notification_id", "Value": "n-1"},
        {"Name": "notification_kind", "Value": "RFQ_NEW"},
    ]


def test_fake_provider_failure_is_fail_soft(ses_config):
    fake = mail_provider.FakeProvider()
    fake.fail_next = True
    out = fake.send(msg())
    assert out.status == "error" and fake.outbox == []


# ---------------------------------------------------------------------------
# SesProvider — Stubber-verified call shape (no network)
# ---------------------------------------------------------------------------

def test_ses_send_email_call_shape_including_configuration_set(ses_config):
    from botocore.stub import ANY
    client, stubber = stubbed_ses()
    stubber.add_response(
        "send_email", {"MessageId": "0100018e-ses"},
        expected_params={
            "FromEmailAddress": "procurement@arkim.ai",
            "Destination": {"ToAddresses": ["sales@dxpe.com"],
                            "CcAddresses": ["info@dxpe.com"]},
            "Content": {"Raw": {"Data": ANY}},
            "ConfigurationSetName": NOTIFY_SET,
            "EmailTags": [{"Name": "notification_id", "Value": "n-1"}],
        })
    with stubber:
        out = mail_provider.SesProvider(client=client).send(
            msg(cc=["info@dxpe.com"], metadata={"notification_id": "n-1"}))
    stubber.assert_no_pending_responses()
    assert out.status == "sent" and out.provider_message_id == "0100018e-ses"
    assert out.configuration_set == NOTIFY_SET


def test_ses_raw_content_carries_subject_recipients_and_attachments(ses_config):
    from botocore.stub import ANY
    client, stubber = stubbed_ses()
    captured = {}
    stubber.add_response("send_email", {"MessageId": "m-1"},
                         expected_params={"FromEmailAddress": ANY,
                                          "Destination": ANY, "Content": ANY,
                                          "ConfigurationSetName": ANY})
    real_send = client.send_email

    def capture(**kw):
        captured.update(kw)
        return real_send(**kw)

    client.send_email = capture
    with stubber:
        mail_provider.SesProvider(client=client).send(
            msg(cc=["info@dxpe.com"],
                attachments=[EmailAttachment(filename="rfq.pdf", content=b"%PDF",
                                             mime_type="application/pdf")]))
    raw = captured["Content"]["Raw"]["Data"].decode("utf-8", "replace")
    assert "Subject: Quote request" in raw
    assert "To: sales@dxpe.com" in raw and "Cc: info@dxpe.com" in raw
    assert "rfq.pdf" in raw


def test_ses_no_region_no_client_is_a_clean_no_op(ses_config, monkeypatch):
    """CLAUDE.md §9: an unconfigured provider returns fail-soft, never raises
    and never constructs a client."""
    monkeypatch.setenv("AWS_REGION", "")
    out = mail_provider.SesProvider().send(msg())
    assert out.status == "error" and "not configured" in out.error


def test_ses_client_error_is_fail_soft(ses_config):
    client, stubber = stubbed_ses()
    stubber.add_client_error("send_email", service_error_code="Throttling",
                             http_status_code=400)
    with stubber:
        out = mail_provider.SesProvider(client=client).send(msg())
    assert out.status == "error" and out.provider_message_id is None


def test_ses_missing_message_id_is_an_error_not_a_fake_success(ses_config):
    client, stubber = stubbed_ses()
    stubber.add_response("send_email", {}, expected_params=None)
    with stubber:
        out = mail_provider.SesProvider(client=client).send(msg())
    assert out.status == "error" and "no MessageId" in out.error


# ---------------------------------------------------------------------------
# D2 — the configuration-set rule, resolved here and enforced fail-closed
# ---------------------------------------------------------------------------

def test_auth_mail_resolves_to_the_tracking_off_set(ses_config):
    cs, refusal = mail_provider.message_configuration_set(
        msg(metadata={"auth_mail": True}))
    assert cs == AUTH_SET and refusal is None


def test_auth_mail_is_refused_when_the_auth_set_is_unconfigured(ses_config, monkeypatch):
    """Fail-CLOSED, and the direction matters: falling back to the
    notifications set would put a single-use token behind SES click
    tracking, where a corporate link scanner burns it before the human."""
    monkeypatch.setenv("SES_CONFIGURATION_SET_AUTH", "")
    cs, refusal = mail_provider.message_configuration_set(
        msg(metadata={"auth_mail": True}))
    assert cs is None and "tracking-off" in refusal
    out = mail_provider.SesProvider(client=object()).send(
        msg(metadata={"auth_mail": True}))
    assert out.status == "error"


def test_auth_mail_ignores_an_explicit_tracking_set_in_metadata(ses_config):
    """An explicit override must not be able to put auth mail on the
    tracking set — D2 is not a default, it is a rule."""
    cs, _ = mail_provider.message_configuration_set(
        msg(metadata={"auth_mail": True, "configuration_set": NOTIFY_SET}))
    assert cs == AUTH_SET


def test_tag_values_are_sanitised_to_the_ses_charset(ses_config):
    tags = mail_provider.message_tags(
        msg(metadata={"notification_id": "a b/c@d", "notification_kind": "RFQ_NEW"}))
    assert tags[0]["Value"] == "a-b-c-d"
