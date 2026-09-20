"""
utils/supplier_accounts.py
Arc 2 — Supplier identity: accounts, members, magic links, sessions (T1).

The durable supplier-identity layer the July decision named the first
post-MVP arc: an account belongs to a COMPANY (1:1 with a registry
``supplier_domain``), people are MEMBERS under it (D1), authentication is
magic-link only (D4 — no passwords), and every security-relevant action is
audited (guardrail 7). Arc 3 converts the portal routes to use this; this arc
builds the layer itself.

Built to the house standard as a standalone module in the token-store
convention (the gate report's convention B — claim_tokens / quote_tokens /
quote_store): own sqlite file, DDL as module-level strings executed
CREATE-TABLE-IF-NOT-EXISTS on every connection, uuid4 string PKs, ISO-8601-UTC
TEXT timestamps, ``is_test`` provenance on rows, ``_DB_PATH`` as the
monkeypatch seam, fail-soft (None/[]/False, never raises into a request path)
on store errors.

Namespace isolation (the quote_tokens.py:9-19 precedent, gate I2/F5): the
magic-link token is a SEPARATE store from claim_tokens — a claim token must
never authenticate a member session and vice versa. The hygiene helpers are
re-implemented here (they are small and private to claim_tokens), NOT shared.

Token hygiene (D4 — identical posture to the claim tokens):
  - Entropy: ``secrets.token_urlsafe(32)`` (~256 bits) for both magic links
    and session tokens.
  - Hashed at rest: only the SHA-256 hex digest is stored. A store read can
    never yield a live link or a usable session; the raw value exists exactly
    once, in the mint return value.
  - Lookup by hash, never a string compare over raw tokens.
  - Magic links are single-use (``used_at`` set by a conditional UPDATE — the
    consume is atomic, so a replayed link loses the race) and expiring
    (default 30 minutes).
  - Sessions are server-side records with an opaque bearer token, expiry
    enforced at read time, revocable (logout).

One-OWNER-per-account (D7 / brief T1) is enforced AT THE PERSISTENCE LAYER,
not merely in application code: a partial unique index
``ux_supplier_members_one_owner ON supplier_members (account_id)
WHERE role = 'OWNER' AND status != 'REVOKED'`` — SQLite enforces it no matter
which code path attempts the write.

Schema (own file ``data/supplier_accounts.sqlite``):
  supplier_accounts   — id, supplier_domain UNIQUE, status, created/updated
  supplier_members    — id, account_id, email (normalized), registrable_domain,
                        role ∈ {OWNER, ADMIN, MEMBER}, status ∈
                        {ACTIVE, PENDING, REVOKED}, invited_by, created/updated;
                        UNIQUE(account_id, email)
  supplier_magic_links— id, member_id, token_hash UNIQUE, token_prefix,
                        expires_at, used_at (single-use), created_at
  supplier_sessions   — id, member_id, account_id, token_hash UNIQUE,
                        expires_at, revoked_at, created_at
  supplier_auth_audit — id, event, account_id, member_id, email, actor, ip,
                        detail_json, created_at (guardrail 7)

Flag gating: ``SUPPLIER_ACCOUNTS_V1`` (default OFF). The ROUTE gate in
api_server is load-bearing; these store functions no-op (None/[]/False) when
the flag is off — defense-in-depth, same layering as claim_tokens.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Feature flag — SUPPLIER_ACCOUNTS_V1 (I8 convention: strict truthy, live read,
# default OFF; the api_server route gate is the load-bearing one).
# ---------------------------------------------------------------------------

def _env_truthy(value: Optional[str]) -> bool:
    """Strict opt-in parse (house rule): only 1/true/yes/on enable; anything
    else — None, "", "0", junk — fails safe to False."""
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def supplier_accounts_active() -> bool:
    """True iff env SUPPLIER_ACCOUNTS_V1 is truthy. Read at call time so tests
    can set/unset per case (same convention as quote_store / ranking_bands)."""
    return _env_truthy(os.environ.get("SUPPLIER_ACCOUNTS_V1"))


def _dormant() -> bool:
    """Defense-in-depth store gate (the api_server route gate is load-bearing)."""
    return not supplier_accounts_active()


# ---------------------------------------------------------------------------
# Vocabulary (roles live here as the data-layer constants; the permission
# MATRIX over them lives in utils/supplier_accounts_rbac.py — D7's ONE module).
# ---------------------------------------------------------------------------

ROLE_OWNER = "OWNER"
ROLE_ADMIN = "ADMIN"
ROLE_MEMBER = "MEMBER"
ROLES: tuple[str, ...] = (ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER)

MEMBER_ACTIVE = "ACTIVE"
MEMBER_PENDING = "PENDING"
MEMBER_REVOKED = "REVOKED"
MEMBER_STATUSES: tuple[str, ...] = (MEMBER_ACTIVE, MEMBER_PENDING, MEMBER_REVOKED)

ACCOUNT_ACTIVE = "active"
ACCOUNT_SUSPENDED = "suspended"
ACCOUNT_STATUSES: tuple[str, ...] = (ACCOUNT_ACTIVE, ACCOUNT_SUSPENDED)


class SupplierAccountsError(Exception):
    """A RULE violation (not a store failure): the invariant the caller tried
    to break is enforced here so no route can bypass it. Carries a stable
    ``code`` the API layer maps to an HTTP status. Store I/O failures remain
    fail-soft (None/[]/False + log) per the house convention — this exception
    is only raised when a write was REJECTED on purpose."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


# ---------------------------------------------------------------------------
# Email / domain helpers (I9 — reuse url_normalize.registrable_domain; the
# public-mailbox list is NEW, no backend list existed — gate I9 gap).
# ---------------------------------------------------------------------------

# Consumer-mailbox domains: an address on one of these can never prove domain
# membership of a supplier (anyone can open one), so it can never AUTO-match
# an account (D2). Such an email lands as a PENDING membership the concierge
# approves. Extend as providers appear (noted follow-up).
PUBLIC_MAILBOX_DOMAINS: frozenset[str] = frozenset({
    "gmail.com", "googlemail.com",
    "outlook.com", "hotmail.com", "hotmail.co.uk", "live.com", "msn.com",
    "yahoo.com", "ymail.com", "rocketmail.com",
    "aol.com", "icloud.com", "me.com", "mac.com",
    "protonmail.com", "proton.me", "pm.me",
    "mail.com", "gmx.com", "gmx.net", "zoho.com", "yandex.com", "yandex.ru",
    "hushmail.com", "tutanota.com", "hey.com",
})


def normalize_email(raw: str) -> str:
    """Normalize an email for storage/matching: strip + lowercase. Returns ""
    for input without exactly one ``@`` and non-empty local+domain parts.
    Never raises."""
    s = (raw or "").strip().lower()
    if s.count("@") != 1:
        return ""
    local, _, domain = s.partition("@")
    if not local or not domain:
        return ""
    return s


def email_registrable_domain(email: str) -> str:
    """The registrable (eTLD+1) domain of an email's host part, via the shared
    ``url_normalize.registrable_domain`` helper (I9 — reuse, don't duplicate).
    ``""`` for a non-email / unparseable input. Never raises."""
    norm = normalize_email(email)
    if not norm:
        return ""
    from utils.url_normalize import registrable_domain
    return registrable_domain(norm.rsplit("@", 1)[-1])


def is_public_mailbox_email(email: str) -> bool:
    """True when the email's registrable domain is a consumer-mailbox provider
    (D2: can never auto-match an account). Fail-open is impossible here — an
    unparseable email is not a public-mailbox email (it simply has no domain
    to match, so it can never be auto-ACTIVE either)."""
    dom = email_registrable_domain(email)
    return bool(dom) and dom in PUBLIC_MAILBOX_DOMAINS


# ---------------------------------------------------------------------------
# Store plumbing (convention B)
# ---------------------------------------------------------------------------

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_DB_PATH = os.path.join(_DATA_DIR, "supplier_accounts.sqlite")

_TOKEN_BYTES = 32            # token_urlsafe(32) -> ~43-char URL-safe, ~256 bits
_PREFIX_LEN = 8              # chars of the RAW token kept for diagnostics/limit keys
_LINK_EXPIRY_MINUTES = 30    # magic links are short-lived (D4)
_SESSION_EXPIRY_HOURS = 24   # sessions expire; logout revokes earlier


_DDL_ACCOUNTS = """
CREATE TABLE IF NOT EXISTS supplier_accounts (
    id              TEXT PRIMARY KEY,
    supplier_domain TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT NOT NULL,
    updated_at      TEXT,
    is_test         INTEGER NOT NULL DEFAULT 0
);
"""

_DDL_MEMBERS = """
CREATE TABLE IF NOT EXISTS supplier_members (
    id                  TEXT PRIMARY KEY,
    account_id          TEXT NOT NULL,
    email               TEXT NOT NULL,
    registrable_domain  TEXT NOT NULL,
    role                TEXT NOT NULL DEFAULT 'MEMBER',
    status              TEXT NOT NULL DEFAULT 'PENDING',
    invited_by          TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT,
    is_test             INTEGER NOT NULL DEFAULT 0,
    UNIQUE(account_id, email)
);
"""

# One OWNER per account, enforced by the persistence layer (brief T1): the
# partial unique index rejects a second live OWNER row no matter which code
# path attempts it. A REVOKED owner frees the slot (ownership transfer marks
# the old owner REVOKED/member first in a future arc).
_INDEX_ONE_OWNER = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_supplier_members_one_owner "
    "ON supplier_members (account_id) WHERE role = 'OWNER' AND status != 'REVOKED'"
)

_DDL_MAGIC_LINKS = """
CREATE TABLE IF NOT EXISTS supplier_magic_links (
    id           TEXT PRIMARY KEY,
    member_id    TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,
    token_prefix TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    used_at      TEXT,
    created_at   TEXT NOT NULL,
    is_test      INTEGER NOT NULL DEFAULT 0
);
"""

_DDL_SESSIONS = """
CREATE TABLE IF NOT EXISTS supplier_sessions (
    id          TEXT PRIMARY KEY,
    member_id   TEXT NOT NULL,
    account_id  TEXT NOT NULL,
    token_hash  TEXT NOT NULL UNIQUE,
    expires_at  TEXT NOT NULL,
    revoked_at  TEXT,
    created_at  TEXT NOT NULL,
    is_test     INTEGER NOT NULL DEFAULT 0
);
"""

_DDL_AUDIT = """
CREATE TABLE IF NOT EXISTS supplier_auth_audit (
    id           TEXT PRIMARY KEY,
    event        TEXT NOT NULL,
    account_id   TEXT,
    member_id    TEXT,
    email        TEXT,
    actor        TEXT,
    ip           TEXT,
    detail_json  TEXT,
    created_at   TEXT NOT NULL,
    is_test      INTEGER NOT NULL DEFAULT 0
);
"""

_INDEX_SESSION_HASH = (
    "CREATE INDEX IF NOT EXISTS ix_supplier_sessions_hash "
    "ON supplier_sessions (token_hash)"
)
_INDEX_LINK_HASH = (
    "CREATE INDEX IF NOT EXISTS ix_supplier_magic_links_hash "
    "ON supplier_magic_links (token_hash)"
)
_INDEX_MEMBERS_ACCOUNT = (
    "CREATE INDEX IF NOT EXISTS ix_supplier_members_account "
    "ON supplier_members (account_id)"
)


def _get_conn() -> sqlite3.Connection:
    os.makedirs(_DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute(_DDL_ACCOUNTS)
    conn.execute(_DDL_MEMBERS)
    conn.execute(_INDEX_ONE_OWNER)
    conn.execute(_DDL_MAGIC_LINKS)
    conn.execute(_INDEX_LINK_HASH)
    conn.execute(_DDL_SESSIONS)
    conn.execute(_INDEX_SESSION_HASH)
    conn.execute(_DDL_AUDIT)
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_token(raw: str) -> str:
    """SHA-256 hex digest of the raw token — the ONLY thing stored/looked up."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_domain(raw: str) -> str:
    """One domain rule across stores — delegate to the registry's normalizer
    (I9: every store keys supplier_domain identically)."""
    from utils.supplier_registry import _normalize_domain as _nd
    return _nd(raw or "")


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    """Tolerant ISO parse → aware UTC (naive treated as UTC — mirrors
    claim_tokens._is_expired). None on blank/unparseable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _is_expired(expires_at: Optional[str]) -> bool:
    """True when ``expires_at`` is past (blank/unparseable counts as expired —
    fail closed on time evidence)."""
    dt = _parse_dt(expires_at)
    return dt is None or dt <= datetime.now(timezone.utc)


def _row(r: sqlite3.Row) -> dict:
    return dict(r)


# ---------------------------------------------------------------------------
# Accounts (D1 — 1:1 with a registry supplier_domain, enforced by UNIQUE)
# ---------------------------------------------------------------------------

def create_account(supplier_domain: str, *, created_by: Optional[str] = None,
                   is_test: bool = True) -> Optional[dict]:
    """Create the SupplierAccount for ``supplier_domain`` (1:1 — UNIQUE domain).
    Idempotent: an existing account is returned unchanged (no second row can
    exist). Audits ``account_created`` on an actual creation only. Fail-soft:
    None on flag-off / empty domain / store failure."""
    if _dormant():
        return None
    dom = _normalize_domain(supplier_domain)
    if not dom:
        return None
    existing = get_account_by_domain(dom)
    if existing is not None:
        return existing
    account_id = str(uuid.uuid4())
    now = _now()
    try:
        with closing(_get_conn()) as conn:
            conn.execute(
                """INSERT INTO supplier_accounts
                   (id, supplier_domain, status, created_at, updated_at, is_test)
                   VALUES (?,?,?,?,?,?)""",
                (account_id, dom, ACCOUNT_ACTIVE, now, now, 1 if is_test else 0),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        # A concurrent create won the UNIQUE(domain) race — return theirs.
        return get_account_by_domain(dom)
    except Exception as exc:
        print(f"[SupplierAccounts] create_account failed for {dom!r}: {exc}")
        return None
    audit("account_created", account_id=account_id,
          actor=created_by or "system", detail={"supplier_domain": dom},
          is_test=is_test)
    return get_account_by_domain(dom)


def get_account_by_domain(supplier_domain: str) -> Optional[dict]:
    """The account bound to ``supplier_domain`` (normalized), or None. Not
    flag-gated (reads of existing rows are harmless; the route gate is the
    boundary — mirrors quote_store.get_quote)."""
    dom = _normalize_domain(supplier_domain)
    if not dom:
        return None
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            r = conn.execute(
                "SELECT * FROM supplier_accounts WHERE supplier_domain = ?",
                (dom,)).fetchone()
            return _row(r) if r else None
    except Exception as exc:
        print(f"[SupplierAccounts] get_account_by_domain failed for {dom!r}: {exc}")
        return None


def get_account(account_id: str) -> Optional[dict]:
    """One account by id, or None (fail-soft)."""
    if not account_id:
        return None
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            r = conn.execute(
                "SELECT * FROM supplier_accounts WHERE id = ?", (account_id,)).fetchone()
            return _row(r) if r else None
    except Exception as exc:
        print(f"[SupplierAccounts] get_account failed for {account_id!r}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Members (D1 — people under the company account; D2 — domain-matched by
# default; D7 — roles + the one-OWNER invariant at the persistence layer)
# ---------------------------------------------------------------------------

def add_member(account_id: str, email: str, *, role: str = ROLE_MEMBER,
               status: str = MEMBER_PENDING, invited_by: Optional[str] = None,
               is_test: bool = True) -> Optional[dict]:
    """Insert one member row. The one-OWNER-per-account invariant is enforced
    HERE by the partial unique index — a second live OWNER raises
    SupplierAccountsError("owner_exists") no matter which code path called.
    Returns the member dict, or None on flag-off / bad input / store failure."""
    if _dormant():
        return None
    norm = normalize_email(email)
    dom = email_registrable_domain(email)
    if not account_id or not norm or not dom:
        return None
    if role not in ROLES:
        raise SupplierAccountsError("invalid_role", f"not a role: {role!r}")
    if status not in MEMBER_STATUSES:
        raise SupplierAccountsError("invalid_status", f"not a status: {status!r}")
    member_id = str(uuid.uuid4())
    now = _now()
    try:
        with closing(_get_conn()) as conn:
            conn.execute(
                """INSERT INTO supplier_members
                   (id, account_id, email, registrable_domain, role, status,
                    invited_by, created_at, updated_at, is_test)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (member_id, account_id, norm, dom, role, status,
                 invited_by, now, now, 1 if is_test else 0),
            )
            conn.commit()
    except sqlite3.IntegrityError as exc:
        # The partial unique index fired: a second live OWNER for this account.
        if role == ROLE_OWNER:
            raise SupplierAccountsError(
                "owner_exists",
                "an account has exactly one OWNER (persistence-enforced)") from exc
        print(f"[SupplierAccounts] add_member integrity error for "
              f"{norm!r}: {exc}")
        return None
    except Exception as exc:
        print(f"[SupplierAccounts] add_member failed for {norm!r}: {exc}")
        return None
    return get_member(member_id)


def get_member(member_id: str) -> Optional[dict]:
    """One member by id, or None (fail-soft)."""
    if not member_id:
        return None
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            r = conn.execute(
                "SELECT * FROM supplier_members WHERE id = ?", (member_id,)).fetchone()
            return _row(r) if r else None
    except Exception as exc:
        print(f"[SupplierAccounts] get_member failed for {member_id!r}: {exc}")
        return None


def get_member_by_email(account_id: str, email: str) -> Optional[dict]:
    """The member row for (account, email) — the membership key (D1: several
    people act for one company; one row per person per account). None when
    absent / fail-soft."""
    norm = normalize_email(email)
    if not account_id or not norm:
        return None
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            r = conn.execute(
                "SELECT * FROM supplier_members WHERE account_id = ? AND email = ?",
                (account_id, norm)).fetchone()
            return _row(r) if r else None
    except Exception as exc:
        print(f"[SupplierAccounts] get_member_by_email failed: {exc}")
        return None


def list_members(account_id: str) -> list[dict]:
    """All members of an account (any status — the admin/owner views show
    pending + revoked honestly), newest last. [] on fail-soft."""
    if not account_id:
        return []
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM supplier_members WHERE account_id = ? "
                "ORDER BY created_at", (account_id,)).fetchall()
            return [_row(r) for r in rows]
    except Exception as exc:
        print(f"[SupplierAccounts] list_members failed for {account_id!r}: {exc}")
        return []


def list_pending_members() -> list[dict]:
    """Every PENDING membership across accounts (the concierge queue, T10),
    newest last. [] on fail-soft. Not flag-gated (read of existing rows)."""
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM supplier_members WHERE status = ? "
                "ORDER BY created_at", (MEMBER_PENDING,)).fetchall()
            return [_row(r) for r in rows]
    except Exception as exc:
        print(f"[SupplierAccounts] list_pending_members failed: {exc}")
        return []


def update_member_status(member_id: str, status: str, *,
                         updated_by: Optional[str] = None) -> Optional[dict]:
    """Transition one member's status (approve → ACTIVE, reject → REVOKED,
    revoke → REVOKED). Validates the target status; the one-OWNER index
    ignores REVOKED rows so an owner transitioned out of OWNER keeps the
    invariant intact. Returns the updated member or None (fail-soft)."""
    if _dormant():
        return None
    if not member_id or status not in MEMBER_STATUSES:
        return None
    try:
        with closing(_get_conn()) as conn:
            cur = conn.execute(
                "UPDATE supplier_members SET status = ?, updated_at = ?, "
                "invited_by = COALESCE(?, invited_by) WHERE id = ?",
                (status, _now(), updated_by, member_id),
            )
            conn.commit()
            if cur.rowcount == 0:
                return None
    except Exception as exc:
        print(f"[SupplierAccounts] update_member_status failed for "
              f"{member_id!r}: {exc}")
        return None
    return get_member(member_id)


def update_member_role(member_id: str, role: str) -> Optional[dict]:
    """Change one member's role. The one-OWNER invariant is enforced HERE by
    the partial unique index: promoting a second live OWNER raises
    SupplierAccountsError("owner_exists"). Role policy (who may change what)
    lives in the caller behind the rbac matrix; the persistence-level
    invariant is this index. Returns the updated member or None (fail-soft)."""
    if _dormant():
        return None
    if not member_id or role not in ROLES:
        return None
    try:
        with closing(_get_conn()) as conn:
            cur = conn.execute(
                "UPDATE supplier_members SET role = ?, updated_at = ? WHERE id = ?",
                (role, _now(), member_id),
            )
            conn.commit()
            if cur.rowcount == 0:
                return None
    except sqlite3.IntegrityError as exc:
        if role == ROLE_OWNER:
            raise SupplierAccountsError(
                "owner_exists",
                "an account has exactly one OWNER (persistence-enforced)") from exc
        print(f"[SupplierAccounts] update_member_role integrity error: {exc}")
        return None
    except Exception as exc:
        print(f"[SupplierAccounts] update_member_role failed for "
              f"{member_id!r}: {exc}")
        return None
    return get_member(member_id)


# ---------------------------------------------------------------------------
# Magic links (D4 — single-use, expiring, hashed at rest)
# ---------------------------------------------------------------------------

def mint_magic_link(member_id: str, *,
                    expiry_minutes: int = _LINK_EXPIRY_MINUTES,
                    is_test: bool = True) -> Optional[dict]:
    """Mint one single-use magic link for ``member_id``. Returns
    ``{token, link_id, member_id, expires_at}`` — the RAW token is returned
    ONCE (only the hash is stored). Fail-soft: None on flag-off / empty
    member_id / store failure. Prior live links for the member are NOT
    revoked (a member may hold several outstanding links, like claim tokens
    pre-regeneration); each is single-use independently."""
    if _dormant():
        return None
    if not member_id:
        return None
    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    link_id = str(uuid.uuid4())
    expires = (datetime.now(timezone.utc)
               + timedelta(minutes=expiry_minutes)).isoformat()
    try:
        with closing(_get_conn()) as conn:
            conn.execute(
                """INSERT INTO supplier_magic_links
                   (id, member_id, token_hash, token_prefix, expires_at,
                    used_at, created_at, is_test)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (link_id, member_id, _hash_token(raw), raw[:_PREFIX_LEN],
                 expires, None, _now(), 1 if is_test else 0),
            )
            conn.commit()
        return {"token": raw, "link_id": link_id, "member_id": member_id,
                "expires_at": expires}
    except Exception as exc:
        print(f"[SupplierAccounts] mint_magic_link failed for {member_id!r}: {exc}")
        return None


def verify_magic_link(raw: str) -> Optional[dict]:
    """Verify + CONSUME a presented magic link (single-use: the consume is a
    conditional UPDATE on ``used_at IS NULL``, so a replayed link loses the
    race atomically). Hash lookup — never a string compare over raw tokens.

    Returns ``{member_id, account_id, email}`` when the link is known, live,
    unused, unexpired AND its member is currently ACTIVE — else None (the
    route renders the uniform rejection; a PENDING/REVOKED member is
    indistinguishable from an unknown token, T4). Fail-soft: None on any
    store error / flag-off."""
    if _dormant():
        return None
    if not raw or not isinstance(raw, str):
        return None
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM supplier_magic_links WHERE token_hash = ?",
                (_hash_token(raw),)).fetchone()
            if not row:
                return None
            d = _row(row)
            if d.get("used_at") or _is_expired(d.get("expires_at")):
                return None
            # Consume FIRST (atomic single-use), then judge the member — a
            # PENDING member's link is spent and uniformly rejected.
            cur = conn.execute(
                "UPDATE supplier_magic_links SET used_at = ? WHERE id = ? "
                "AND used_at IS NULL", (_now(), d.get("id")))
            conn.commit()
            if cur.rowcount == 0:
                return None  # a concurrent verify consumed it
    except Exception as exc:
        print(f"[SupplierAccounts] verify_magic_link failed: {exc}")
        return None
    member = get_member(d.get("member_id") or "")
    if not member or member.get("status") != MEMBER_ACTIVE:
        return None
    return {"member_id": member["id"], "account_id": member["account_id"],
            "email": member["email"]}


# ---------------------------------------------------------------------------
# Sessions (guardrail 6 — server-side records, opaque bearer token, expiry,
# logout revokes)
# ---------------------------------------------------------------------------

def create_session(member_id: str, *,
                   expiry_hours: int = _SESSION_EXPIRY_HOURS,
                   is_test: bool = True) -> Optional[dict]:
    """Create a session for an ACTIVE member. Returns
    ``{token, session_id, member_id, account_id, expires_at}`` — the RAW
    bearer token is returned ONCE (only the hash is stored). None on
    flag-off / unknown or non-ACTIVE member / store failure."""
    if _dormant():
        return None
    member = get_member(member_id)
    if not member or member.get("status") != MEMBER_ACTIVE:
        return None
    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    session_id = str(uuid.uuid4())
    expires = (datetime.now(timezone.utc)
               + timedelta(hours=expiry_hours)).isoformat()
    try:
        with closing(_get_conn()) as conn:
            conn.execute(
                """INSERT INTO supplier_sessions
                   (id, member_id, account_id, token_hash, expires_at,
                    revoked_at, created_at, is_test)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (session_id, member_id, member["account_id"], _hash_token(raw),
                 expires, None, _now(), 1 if is_test else 0),
            )
            conn.commit()
        return {"token": raw, "session_id": session_id, "member_id": member_id,
                "account_id": member["account_id"], "expires_at": expires}
    except Exception as exc:
        print(f"[SupplierAccounts] create_session failed for {member_id!r}: {exc}")
        return None


def validate_session(raw: str) -> Optional[dict]:
    """Validate a presented bearer token (hash lookup). Returns
    ``{session_id, member_id, account_id, member, account}`` iff the session
    is known, unexpired, unrevoked, and its member is still ACTIVE — else
    None (401 at the route; no oracle beyond valid/invalid). Fail-soft."""
    if _dormant():
        return None
    if not raw or not isinstance(raw, str):
        return None
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM supplier_sessions WHERE token_hash = ?",
                (_hash_token(raw),)).fetchone()
            if not row:
                return None
            d = _row(row)
            if d.get("revoked_at") or _is_expired(d.get("expires_at")):
                return None
    except Exception as exc:
        print(f"[SupplierAccounts] validate_session failed: {exc}")
        return None
    member = get_member(d.get("member_id") or "")
    account = get_account(d.get("account_id") or "")
    if not member or member.get("status") != MEMBER_ACTIVE or not account:
        return None
    return {"session_id": d["id"], "member_id": member["id"],
            "account_id": member["account_id"], "member": member,
            "account": account}


def revoke_session(session_id: str) -> bool:
    """Revoke one session (logout). True on a write; False on flag-off /
    missing / store failure. Idempotent-safe (rowcount 0 on a re-revoke)."""
    if _dormant():
        return False
    if not session_id:
        return False
    try:
        with closing(_get_conn()) as conn:
            cur = conn.execute(
                "UPDATE supplier_sessions SET revoked_at = ? "
                "WHERE id = ? AND revoked_at IS NULL", (_now(), session_id))
            conn.commit()
            return cur.rowcount > 0
    except Exception as exc:
        print(f"[SupplierAccounts] revoke_session failed for {session_id!r}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Audit (guardrail 7 — every link request, verification, login, logout, and
# membership decision writes a row: who, what, when, from where)
# ---------------------------------------------------------------------------

def audit(event: str, *, account_id: Optional[str] = None,
          member_id: Optional[str] = None, email: Optional[str] = None,
          actor: Optional[str] = None, ip: Optional[str] = None,
          detail: Optional[dict] = None, is_test: bool = True) -> None:
    """Write one supplier-auth audit row. Best-effort BY DESIGN at the write
    (a failure to audit must not kill the auth flow) but LOUD (printed) —
    the same posture as send_governance._audit. ``detail`` is JSON-encoded.
    Flag-gated like every write (only flag-on flows have auditable actions;
    flag-off must touch no new table)."""
    if _dormant():
        return
    import json as _json
    try:
        with closing(_get_conn()) as conn:
            conn.execute(
                """INSERT INTO supplier_auth_audit
                   (id, event, account_id, member_id, email, actor, ip,
                    detail_json, created_at, is_test)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), event, account_id, member_id, email,
                 actor, ip, _json.dumps(detail) if detail else None,
                 _now(), 1 if is_test else 0),
            )
            conn.commit()
    except Exception as exc:
        print(f"[SupplierAccounts] audit write failed for {event!r}: {exc}")


def list_audit(*, account_id: Optional[str] = None,
               event: Optional[str] = None,
               email: Optional[str] = None) -> list[dict]:
    """Audit rows (newest last), optionally filtered — the test/admin read.
    [] on fail-soft. Not flag-gated (reads of existing rows)."""
    clauses, params = [], []
    if account_id is not None:
        clauses.append("account_id = ?")
        params.append(account_id)
    if event is not None:
        clauses.append("event = ?")
        params.append(event)
    if email is not None:
        clauses.append("email = ?")
        params.append(normalize_email(email) or email)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT * FROM supplier_auth_audit{where} "
                f"ORDER BY created_at", params).fetchall()
            return [_row(r) for r in rows]
    except Exception as exc:
        print(f"[SupplierAccounts] list_audit failed: {exc}")
        return []
