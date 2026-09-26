"""
utils/buyer_accounts.py
Arc 6 — Buyer identity: companies, members, magic links, sessions, audit, and
the stored approval policy (T1).

A buyer account is a COMPANY; people are MEMBERS of exactly one company; runs
belong to a facility of that company (D1). The company key IS the existing
company id string the intake channels already stamp on runs
(``company-bayfoods`` — gate ruling Q1), and the company row carries its
facility ids, so ``_TENANT_MAP``, ``_MOCK_FACILITIES`` and run/order
``company_id`` all resolve to one entity.

Built in the token-store convention the supplier store uses (convention B):
own sqlite file, DDL executed CREATE-TABLE-IF-NOT-EXISTS on every connection,
uuid4 string PKs, ISO-8601-UTC TEXT timestamps, ``is_test`` provenance,
``_DB_PATH`` as the monkeypatch seam, fail-soft (None/[]/False, never raises
into a request path) on store errors.

Deliberately a SEPARATE store from utils/supplier_accounts.py (D4): a buyer
session can never authenticate a supplier route and vice versa, and nothing in
the supplier module changes. The small token-hygiene helpers are
re-implemented here for the same reason the supplier store re-implemented
claim_tokens' — sharing them would mean editing supplier code.

Token hygiene (identical posture to the supplier store):
  - ``secrets.token_urlsafe(32)`` for magic links and sessions;
  - only the SHA-256 digest is stored — a store read never yields a credential;
  - magic links are single-use (atomic conditional UPDATE) and expire (30 min);
  - sessions are server-side records, expire (24 h), and are revocable.

One company per member is enforced AT THE PERSISTENCE LAYER: ``email`` is
UNIQUE across ``buyer_members``, so the same person cannot hold a membership in
two companies no matter which code path tries.

The approval policy (D5) is STORED here — ``auto_approval_limit`` (USD, default
2500, 0 = every order needs a second approval) and ``allow_admin_override``
(default on) — and every change is audited with the old value, the new value
and the actor. Nothing here ENFORCES the policy at order time; that is arc 7.

Flag gating: ``BUYER_ACCOUNTS_V1`` (default OFF). The api_server route gate is
load-bearing; the writes here no-op when the flag is off (defense-in-depth,
the supplier store's layering).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from utils import data_dir


# ---------------------------------------------------------------------------
# Feature flag — BUYER_ACCOUNTS_V1 (strict truthy, live read, default OFF)
# ---------------------------------------------------------------------------

def _env_truthy(value: Optional[str]) -> bool:
    """Strict opt-in parse (house rule): only 1/true/yes/on enable."""
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def buyer_accounts_active() -> bool:
    """True iff env BUYER_ACCOUNTS_V1 is truthy. Read at call time so tests can
    set/unset it per case."""
    return _env_truthy(os.environ.get("BUYER_ACCOUNTS_V1"))


def _dormant() -> bool:
    """Defense-in-depth store gate (the api_server route gate is load-bearing)."""
    return not buyer_accounts_active()


# ---------------------------------------------------------------------------
# Vocabulary. The permission MATRIX over these roles lives in
# utils/buyer_accounts_rbac.py — D2's one module.
# ---------------------------------------------------------------------------

ROLE_REQUESTER = "REQUESTER"
ROLE_BUYER = "BUYER"
ROLE_APPROVER = "APPROVER"
ROLE_ADMIN = "ADMIN"
ROLES: tuple[str, ...] = (ROLE_REQUESTER, ROLE_BUYER, ROLE_APPROVER, ROLE_ADMIN)

# D3: an invited member defaults to the least-privileged role.
DEFAULT_INVITE_ROLE = ROLE_REQUESTER

MEMBER_ACTIVE = "ACTIVE"
MEMBER_REVOKED = "REVOKED"
MEMBER_STATUSES: tuple[str, ...] = (MEMBER_ACTIVE, MEMBER_REVOKED)

COMPANY_ACTIVE = "active"

# D5 defaults.
DEFAULT_AUTO_APPROVAL_LIMIT = 2500.0
DEFAULT_ALLOW_ADMIN_OVERRIDE = True


class BuyerAccountsError(Exception):
    """A RULE violation (not a store failure), carrying a stable ``code`` the
    API layer maps to an HTTP status. Store I/O failures stay fail-soft."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


# ---------------------------------------------------------------------------
# Email helpers
# ---------------------------------------------------------------------------

def normalize_email(raw: str) -> str:
    """strip + lowercase; "" unless exactly one ``@`` with non-empty parts."""
    s = (raw or "").strip().lower()
    if s.count("@") != 1:
        return ""
    local, _, domain = s.partition("@")
    if not local or not domain:
        return ""
    return s


def email_domain(email: str) -> str:
    """The host part of a normalized email, or ""."""
    norm = normalize_email(email)
    return norm.rsplit("@", 1)[-1] if norm else ""


# ---------------------------------------------------------------------------
# Store plumbing (convention B)
# ---------------------------------------------------------------------------

_DATA_DIR = data_dir.data_dir()
_DB_PATH = os.path.join(_DATA_DIR, "buyer_accounts.sqlite")

_TOKEN_BYTES = 32
_PREFIX_LEN = 8
_LINK_EXPIRY_MINUTES = 30
_SESSION_EXPIRY_HOURS = 24

_DDL_COMPANIES = """
CREATE TABLE IF NOT EXISTS buyer_companies (
    id                    TEXT PRIMARY KEY,
    name                  TEXT NOT NULL,
    facility_ids_json     TEXT NOT NULL DEFAULT '[]',
    email_domains_json    TEXT NOT NULL DEFAULT '[]',
    auto_approval_limit   REAL NOT NULL DEFAULT 2500,
    allow_admin_override  INTEGER NOT NULL DEFAULT 1,
    status                TEXT NOT NULL DEFAULT 'active',
    created_at            TEXT NOT NULL,
    updated_at            TEXT,
    is_test               INTEGER NOT NULL DEFAULT 0
);
"""

# email is UNIQUE across the table: one company per member, enforced here.
_DDL_MEMBERS = """
CREATE TABLE IF NOT EXISTS buyer_members (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    role        TEXT NOT NULL DEFAULT 'REQUESTER',
    status      TEXT NOT NULL DEFAULT 'ACTIVE',
    invited_by  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT,
    is_test     INTEGER NOT NULL DEFAULT 0
);
"""

_DDL_MAGIC_LINKS = """
CREATE TABLE IF NOT EXISTS buyer_magic_links (
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
CREATE TABLE IF NOT EXISTS buyer_sessions (
    id          TEXT PRIMARY KEY,
    member_id   TEXT NOT NULL,
    company_id  TEXT NOT NULL,
    token_hash  TEXT NOT NULL UNIQUE,
    expires_at  TEXT NOT NULL,
    revoked_at  TEXT,
    created_at  TEXT NOT NULL,
    is_test     INTEGER NOT NULL DEFAULT 0
);
"""

_DDL_AUDIT = """
CREATE TABLE IF NOT EXISTS buyer_audit (
    id           TEXT PRIMARY KEY,
    event        TEXT NOT NULL,
    company_id   TEXT,
    member_id    TEXT,
    email        TEXT,
    actor        TEXT,
    ip           TEXT,
    run_id       TEXT,
    detail_json  TEXT,
    created_at   TEXT NOT NULL,
    is_test      INTEGER NOT NULL DEFAULT 0
);
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_buyer_members_company ON buyer_members (company_id)",
    "CREATE INDEX IF NOT EXISTS ix_buyer_sessions_hash ON buyer_sessions (token_hash)",
    "CREATE INDEX IF NOT EXISTS ix_buyer_links_hash ON buyer_magic_links (token_hash)",
    "CREATE INDEX IF NOT EXISTS ix_buyer_audit_company ON buyer_audit (company_id)",
)


def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    for ddl in (_DDL_COMPANIES, _DDL_MEMBERS, _DDL_MAGIC_LINKS, _DDL_SESSIONS,
                _DDL_AUDIT, *_INDEXES):
        conn.execute(ddl)
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_token(raw: str) -> str:
    """SHA-256 hex digest of the raw token — the ONLY thing stored/looked up."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
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
    """Past, blank or unparseable ⇒ expired (fail closed on time evidence)."""
    dt = _parse_dt(expires_at)
    return dt is None or dt <= datetime.now(timezone.utc)


def _company_row(r: sqlite3.Row) -> dict:
    d = dict(r)
    for src, dst in (("facility_ids_json", "facility_ids"),
                     ("email_domains_json", "email_domains")):
        try:
            d[dst] = list(json.loads(d.pop(src) or "[]"))
        except (ValueError, TypeError):
            d[dst] = []
    d["auto_approval_limit"] = float(d.get("auto_approval_limit") or 0.0)
    d["allow_admin_override"] = bool(d.get("allow_admin_override"))
    return d


# ---------------------------------------------------------------------------
# Companies (D1)
# ---------------------------------------------------------------------------

def create_company(company_id: str, name: str, *,
                   facility_ids: Optional[list[str]] = None,
                   email_domains: Optional[list[str]] = None,
                   created_by: Optional[str] = None,
                   is_test: bool = True) -> Optional[dict]:
    """Create a buyer company keyed by the existing company id string. Refuses
    an id that already exists (``company_exists``) — bootstrap is deliberate,
    not idempotent, so a second create cannot silently re-point facilities.
    Defaults: auto_approval_limit 2500, allow_admin_override on (D5). Audited.
    None on flag-off / blank input / store failure."""
    if _dormant():
        return None
    cid = (company_id or "").strip()
    nm = (name or "").strip()
    if not cid or not nm:
        return None
    facilities = [f.strip() for f in (facility_ids or []) if (f or "").strip()]
    domains = sorted({(d or "").strip().lower() for d in (email_domains or [])
                      if (d or "").strip()})
    now = _now()
    try:
        with closing(_get_conn()) as conn:
            conn.execute(
                """INSERT INTO buyer_companies
                   (id, name, facility_ids_json, email_domains_json,
                    auto_approval_limit, allow_admin_override, status,
                    created_at, updated_at, is_test)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (cid, nm, json.dumps(facilities), json.dumps(domains),
                 DEFAULT_AUTO_APPROVAL_LIMIT, 1 if DEFAULT_ALLOW_ADMIN_OVERRIDE else 0,
                 COMPANY_ACTIVE, now, now, 1 if is_test else 0),
            )
            conn.commit()
    except sqlite3.IntegrityError as exc:
        raise BuyerAccountsError("company_exists",
                                 f"company {cid!r} already exists") from exc
    except Exception as exc:
        print(f"[BuyerAccounts] create_company failed for {cid!r}: {exc}")
        return None
    audit("company_created", company_id=cid, actor=created_by or "system",
          detail={"name": nm, "facility_ids": facilities,
                  "email_domains": domains}, is_test=is_test)
    return get_company(cid)


def get_company(company_id: str) -> Optional[dict]:
    """One company by id, or None (fail-soft). Not flag-gated (a read)."""
    if not company_id:
        return None
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            r = conn.execute("SELECT * FROM buyer_companies WHERE id = ?",
                             (company_id,)).fetchone()
            return _company_row(r) if r else None
    except Exception as exc:
        print(f"[BuyerAccounts] get_company failed for {company_id!r}: {exc}")
        return None


def list_companies() -> list[dict]:
    """Every company (the Gofer admin read), oldest first. [] on fail-soft."""
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM buyer_companies ORDER BY created_at").fetchall()
            return [_company_row(r) for r in rows]
    except Exception as exc:
        print(f"[BuyerAccounts] list_companies failed: {exc}")
        return []


def company_owns_facility(company: Optional[dict], facility_id: Optional[str]) -> bool:
    """True iff ``facility_id`` is one of ``company``'s facilities. Pure."""
    if not company or not facility_id:
        return False
    return facility_id in (company.get("facility_ids") or [])


# ---------------------------------------------------------------------------
# Approval policy (D5 — stored and audited here; enforced in arc 7)
# ---------------------------------------------------------------------------

def validate_auto_approval_limit(value: Any) -> float:
    """The limit as a float, or ``BuyerAccountsError("invalid_limit")``. A
    number >= 0 and finite; ``0`` is valid (every order needs a second
    approval). Booleans and numeric strings are refused — the setting is a
    number, and "2500" arriving as text is a client bug worth surfacing."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BuyerAccountsError("invalid_limit",
                                 "auto_approval_limit must be a number")
    f = float(value)
    if math.isnan(f) or math.isinf(f) or f < 0:
        raise BuyerAccountsError("invalid_limit",
                                 "auto_approval_limit must be a finite number >= 0")
    return f


def set_approval_policy(company_id: str, *, actor: str,
                        auto_approval_limit: Any = None,
                        allow_admin_override: Optional[bool] = None,
                        ip: Optional[str] = None) -> Optional[dict]:
    """Change one or both policy fields. Each field that actually changes
    writes one ``approval_policy_changed`` audit row carrying the field, the
    old value, the new value and the actor. A no-op change writes nothing.
    Raises ``invalid_limit`` / ``invalid_override`` on bad input; returns the
    updated company, or None on flag-off / unknown company / store failure.
    Permission is the caller's job (the rbac module); this is storage."""
    if _dormant():
        return None
    company = get_company(company_id)
    if company is None:
        return None
    changes: list[tuple[str, Any, Any]] = []
    new_limit = company["auto_approval_limit"]
    new_override = company["allow_admin_override"]
    if auto_approval_limit is not None:
        new_limit = validate_auto_approval_limit(auto_approval_limit)
        if new_limit != company["auto_approval_limit"]:
            changes.append(("auto_approval_limit", company["auto_approval_limit"], new_limit))
    if allow_admin_override is not None:
        if not isinstance(allow_admin_override, bool):
            raise BuyerAccountsError("invalid_override",
                                     "allow_admin_override must be true or false")
        if allow_admin_override != company["allow_admin_override"]:
            changes.append(("allow_admin_override", company["allow_admin_override"],
                            allow_admin_override))
            new_override = allow_admin_override
    if not changes:
        return company
    try:
        with closing(_get_conn()) as conn:
            conn.execute(
                "UPDATE buyer_companies SET auto_approval_limit = ?, "
                "allow_admin_override = ?, updated_at = ? WHERE id = ?",
                (new_limit, 1 if new_override else 0, _now(), company_id))
            conn.commit()
    except Exception as exc:
        print(f"[BuyerAccounts] set_approval_policy failed for {company_id!r}: {exc}")
        return None
    for field, old, new in changes:
        audit("approval_policy_changed", company_id=company_id, actor=actor, ip=ip,
              detail={"field": field, "old": old, "new": new})
    return get_company(company_id)


# ---------------------------------------------------------------------------
# Members (D1, D3)
# ---------------------------------------------------------------------------

def add_member(company_id: str, email: str, *, role: str = DEFAULT_INVITE_ROLE,
               invited_by: Optional[str] = None,
               is_test: bool = True) -> Optional[dict]:
    """Insert one ACTIVE member. A second membership for the same email — in
    this company or any other — raises ``already_member`` (the UNIQUE(email)
    index is the one-company-per-member guarantee). None on flag-off / bad
    input / unknown company / store failure."""
    if _dormant():
        return None
    norm = normalize_email(email)
    if not company_id or not norm or get_company(company_id) is None:
        return None
    if role not in ROLES:
        raise BuyerAccountsError("invalid_role", f"not a role: {role!r}")
    member_id = str(uuid.uuid4())
    now = _now()
    try:
        with closing(_get_conn()) as conn:
            conn.execute(
                """INSERT INTO buyer_members
                   (id, company_id, email, role, status, invited_by,
                    created_at, updated_at, is_test)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (member_id, company_id, norm, role, MEMBER_ACTIVE, invited_by,
                 now, now, 1 if is_test else 0),
            )
            conn.commit()
    except sqlite3.IntegrityError as exc:
        raise BuyerAccountsError(
            "already_member", f"{norm} already belongs to a company") from exc
    except Exception as exc:
        print(f"[BuyerAccounts] add_member failed for {norm!r}: {exc}")
        return None
    return get_member(member_id)


def get_member(member_id: str) -> Optional[dict]:
    if not member_id:
        return None
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            r = conn.execute("SELECT * FROM buyer_members WHERE id = ?",
                             (member_id,)).fetchone()
            return dict(r) if r else None
    except Exception as exc:
        print(f"[BuyerAccounts] get_member failed for {member_id!r}: {exc}")
        return None


def get_member_by_email(email: str) -> Optional[dict]:
    """The one membership for ``email`` (any status), or None."""
    norm = normalize_email(email)
    if not norm:
        return None
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            r = conn.execute("SELECT * FROM buyer_members WHERE email = ?",
                             (norm,)).fetchone()
            return dict(r) if r else None
    except Exception as exc:
        print(f"[BuyerAccounts] get_member_by_email failed: {exc}")
        return None


def list_members(company_id: str) -> list[dict]:
    """Every member of a company (any status), oldest first."""
    if not company_id:
        return []
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM buyer_members WHERE company_id = ? ORDER BY created_at",
                (company_id,)).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        print(f"[BuyerAccounts] list_members failed for {company_id!r}: {exc}")
        return []


def count_active_role(company_id: str, role: str) -> int:
    """How many ACTIVE members of ``company_id`` hold ``role``. Fail-soft to a
    LARGE number is wrong here (it would let the last Admin leave), so a store
    failure returns 0 and the caller's "last Admin" guard refuses."""
    try:
        with closing(_get_conn()) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM buyer_members WHERE company_id = ? "
                "AND role = ? AND status = ?",
                (company_id, role, MEMBER_ACTIVE)).fetchone()
            return int(row[0]) if row else 0
    except Exception as exc:
        print(f"[BuyerAccounts] count_active_role failed: {exc}")
        return 0


def update_member_role(member_id: str, role: str) -> Optional[dict]:
    if _dormant() or not member_id or role not in ROLES:
        return None
    try:
        with closing(_get_conn()) as conn:
            cur = conn.execute(
                "UPDATE buyer_members SET role = ?, updated_at = ? WHERE id = ?",
                (role, _now(), member_id))
            conn.commit()
            if cur.rowcount == 0:
                return None
    except Exception as exc:
        print(f"[BuyerAccounts] update_member_role failed for {member_id!r}: {exc}")
        return None
    return get_member(member_id)


def update_member_status(member_id: str, status: str) -> Optional[dict]:
    """Set a member's status. Revoking also revokes every live session of that
    member, so a revoked person is signed out everywhere at once."""
    if _dormant() or not member_id or status not in MEMBER_STATUSES:
        return None
    try:
        with closing(_get_conn()) as conn:
            cur = conn.execute(
                "UPDATE buyer_members SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), member_id))
            if status == MEMBER_REVOKED:
                conn.execute(
                    "UPDATE buyer_sessions SET revoked_at = ? "
                    "WHERE member_id = ? AND revoked_at IS NULL",
                    (_now(), member_id))
            conn.commit()
            if cur.rowcount == 0:
                return None
    except Exception as exc:
        print(f"[BuyerAccounts] update_member_status failed for {member_id!r}: {exc}")
        return None
    return get_member(member_id)


# ---------------------------------------------------------------------------
# Magic links (single-use, expiring, hashed at rest)
# ---------------------------------------------------------------------------

def mint_magic_link(member_id: str, *,
                    expiry_minutes: int = _LINK_EXPIRY_MINUTES,
                    is_test: bool = True) -> Optional[dict]:
    """Mint one single-use link. The RAW token is returned once; only its hash
    is stored."""
    if _dormant() or not member_id:
        return None
    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    link_id = str(uuid.uuid4())
    expires = (datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes)).isoformat()
    try:
        with closing(_get_conn()) as conn:
            conn.execute(
                """INSERT INTO buyer_magic_links
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
        print(f"[BuyerAccounts] mint_magic_link failed for {member_id!r}: {exc}")
        return None


def verify_magic_link(raw: str) -> Optional[dict]:
    """Verify + CONSUME a link (atomic single-use). Returns
    ``{member_id, company_id, email}`` only when the link is live and its
    member is ACTIVE; every other case is None (one uniform rejection)."""
    if _dormant() or not raw or not isinstance(raw, str):
        return None
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM buyer_magic_links WHERE token_hash = ?",
                               (_hash_token(raw),)).fetchone()
            if not row:
                return None
            d = dict(row)
            if d.get("used_at") or _is_expired(d.get("expires_at")):
                return None
            cur = conn.execute(
                "UPDATE buyer_magic_links SET used_at = ? WHERE id = ? "
                "AND used_at IS NULL", (_now(), d["id"]))
            conn.commit()
            if cur.rowcount == 0:
                return None
    except Exception as exc:
        print(f"[BuyerAccounts] verify_magic_link failed: {exc}")
        return None
    member = get_member(d.get("member_id") or "")
    if not member or member.get("status") != MEMBER_ACTIVE:
        return None
    return {"member_id": member["id"], "company_id": member["company_id"],
            "email": member["email"]}


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def create_session(member_id: str, *, expiry_hours: int = _SESSION_EXPIRY_HOURS,
                   is_test: bool = True) -> Optional[dict]:
    """A session for an ACTIVE member. The RAW token is returned once."""
    if _dormant():
        return None
    member = get_member(member_id)
    if not member or member.get("status") != MEMBER_ACTIVE:
        return None
    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    session_id = str(uuid.uuid4())
    expires = (datetime.now(timezone.utc) + timedelta(hours=expiry_hours)).isoformat()
    try:
        with closing(_get_conn()) as conn:
            conn.execute(
                """INSERT INTO buyer_sessions
                   (id, member_id, company_id, token_hash, expires_at,
                    revoked_at, created_at, is_test)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (session_id, member_id, member["company_id"], _hash_token(raw),
                 expires, None, _now(), 1 if is_test else 0),
            )
            conn.commit()
        return {"token": raw, "session_id": session_id, "member_id": member_id,
                "company_id": member["company_id"], "expires_at": expires}
    except Exception as exc:
        print(f"[BuyerAccounts] create_session failed for {member_id!r}: {exc}")
        return None


def validate_session(raw: str) -> Optional[dict]:
    """``{session_id, member_id, company_id, member, company}`` iff the session
    is known, unexpired, unrevoked, its member ACTIVE and still in the
    session's company — else None."""
    if _dormant() or not raw or not isinstance(raw, str):
        return None
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM buyer_sessions WHERE token_hash = ?",
                               (_hash_token(raw),)).fetchone()
            if not row:
                return None
            d = dict(row)
            if d.get("revoked_at") or _is_expired(d.get("expires_at")):
                return None
    except Exception as exc:
        print(f"[BuyerAccounts] validate_session failed: {exc}")
        return None
    member = get_member(d.get("member_id") or "")
    if (not member or member.get("status") != MEMBER_ACTIVE
            or member.get("company_id") != d.get("company_id")):
        return None
    company = get_company(member["company_id"])
    if not company or company.get("status") != COMPANY_ACTIVE:
        return None
    return {"session_id": d["id"], "member_id": member["id"],
            "company_id": member["company_id"], "member": member,
            "company": company}


def revoke_session(session_id: str) -> bool:
    if _dormant() or not session_id:
        return False
    try:
        with closing(_get_conn()) as conn:
            cur = conn.execute(
                "UPDATE buyer_sessions SET revoked_at = ? "
                "WHERE id = ? AND revoked_at IS NULL", (_now(), session_id))
            conn.commit()
            return cur.rowcount > 0
    except Exception as exc:
        print(f"[BuyerAccounts] revoke_session failed for {session_id!r}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Magic-link mail — the SAME governed send seam supplier auth mail uses (F7)
# ---------------------------------------------------------------------------

_APP_BASE_URL_ENV = "BUYER_APP_BASE_URL"
_DEFAULT_APP_BASE_URL = "https://procurement.arkim.ai"


def magic_link_url(raw_token: str) -> str:
    base = (os.environ.get(_APP_BASE_URL_ENV) or "").strip() or _DEFAULT_APP_BASE_URL
    return f"{base.rstrip('/')}/verify?token={raw_token}"


def send_magic_link_email(email: str, raw_token: str, *, company_id: str,
                          member_id: Optional[str] = None) -> str:
    """Hand the sign-in link to ``GmailSender().send`` — the seam where send
    governance (suppression → allowlist → caps) and the EMAIL_SEND_ENABLED
    delivery gate run, exactly as for supplier magic links. With
    NOTIFICATIONS_V1 on, the message carries the same ``auth_mail`` /
    ``message_class="auth"`` markers supplier auth mail carries, so it rides
    the tracking-off configuration set and the auth cap class. No new
    governance class or exemption exists for buyers. The raw token lives in
    the message body only — never logged, never persisted. Returns the send
    status."""
    from utils.email_sender import EmailMessage, GmailSender
    from utils import notifications
    metadata: dict = {"buyer_company_id": company_id, "magic_link": True,
                      "member_id": member_id}
    if notifications.notifications_active():
        metadata["auth_mail"] = True
        metadata["message_class"] = "auth"
    msg = EmailMessage(
        to=[email],
        subject="Your Gofer sign-in link",
        body=(
            "Hello,\n\n"
            "Use the link below to sign in to Gofer procurement.\n"
            "The link is single-use and expires in 30 minutes.\n\n"
            f"{magic_link_url(raw_token)}\n\n"
            "If you did not request it, you can ignore this email.\n\n"
            "Regards,\nArkim Procurement\nprocurement@arkim.ai"
        ),
        metadata=metadata,
    )
    result = GmailSender().send(msg)
    print(f"[BuyerAccounts] magic-link send {result.status} -> {email}")
    return result.status


# ---------------------------------------------------------------------------
# Audit — every auth event, membership decision, policy change and attributed
# buyer action writes a row.
# ---------------------------------------------------------------------------

def audit(event: str, *, company_id: Optional[str] = None,
          member_id: Optional[str] = None, email: Optional[str] = None,
          actor: Optional[str] = None, ip: Optional[str] = None,
          run_id: Optional[str] = None, detail: Optional[dict] = None,
          is_test: bool = True) -> None:
    """Best-effort but LOUD (a failed audit write must not kill the request).
    Flag-gated like every write: flag-off touches no new table."""
    if _dormant():
        return
    try:
        with closing(_get_conn()) as conn:
            conn.execute(
                """INSERT INTO buyer_audit
                   (id, event, company_id, member_id, email, actor, ip, run_id,
                    detail_json, created_at, is_test)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), event, company_id, member_id, email, actor, ip,
                 run_id, json.dumps(detail) if detail else None, _now(),
                 1 if is_test else 0),
            )
            conn.commit()
    except Exception as exc:
        print(f"[BuyerAccounts] audit write failed for {event!r}: {exc}")


def list_audit(*, company_id: Optional[str] = None, event: Optional[str] = None,
               run_id: Optional[str] = None) -> list[dict]:
    """Audit rows, oldest first, optionally filtered. ``detail`` decoded."""
    clauses, params = [], []
    for col, val in (("company_id", company_id), ("event", event), ("run_id", run_id)):
        if val is not None:
            clauses.append(f"{col} = ?")
            params.append(val)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    try:
        with closing(_get_conn()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT * FROM buyer_audit{where} ORDER BY created_at, rowid",
                params).fetchall()
    except Exception as exc:
        print(f"[BuyerAccounts] list_audit failed: {exc}")
        return []
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["detail"] = json.loads(d.pop("detail_json") or "null")
        except (ValueError, TypeError):
            d["detail"] = None
        out.append(d)
    return out
