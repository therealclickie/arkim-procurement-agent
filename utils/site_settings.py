"""
utils/site_settings.py
Per-site delivery (ship-to) settings — the durable store behind the customer Delivery
Settings screen and the graduated shipping disclosure at order placement.

Raw-sqlite3 module (mirrors utils/orders.py): its own data/site_settings.sqlite,
idempotent CREATE TABLE, fail-soft, bracket-prefixed logging. One ship-to row per
site_id. Replaces the earlier client-only (localStorage) persistence.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional
from utils import data_dir

_DATA_DIR = data_dir.data_dir()  # R10: $GOFER_DATA_DIR, else <repo>/data (identical when unset)
_DB_PATH = os.path.join(_DATA_DIR, "site_settings.sqlite")

# The fields a caller may write (matches the customer form + order-review ship-to block).
_WRITABLE = ("company", "address", "city", "attention", "hours", "instructions")

_DDL = """
CREATE TABLE IF NOT EXISTS site_shipto (
    site_id       TEXT PRIMARY KEY,
    company       TEXT,
    address       TEXT,
    city          TEXT,
    attention     TEXT,
    hours         TEXT,
    instructions  TEXT,
    updated_at    TEXT NOT NULL
);
"""


# The columns a read returns — exactly the pre-arc-6 row shape, so the arc 6
# company_id column never appears in a response.
_READ_COLUMNS = "site_id, company, address, city, attention, hours, instructions, updated_at"


def _migrate(conn: sqlite3.Connection) -> None:
    """Arc 6 F8: a nullable company_id. Existing rows read NULL — they belong to no
    buyer company, so under BUYER_ACCOUNTS_V1 they are invisible (the F2 stance)."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(site_shipto)")}
    if have and "company_id" not in have:
        conn.execute("ALTER TABLE site_shipto ADD COLUMN company_id TEXT")


def _get_conn() -> sqlite3.Connection:
    os.makedirs(_DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute(_DDL)
    _migrate(conn)
    conn.commit()
    return conn


def _company_key(company_id: str, site_id: str) -> str:
    """Arc 6 F8: under BUYER_ACCOUNTS_V1 a company's ship-to row is stored under a
    company-namespaced key, so two companies' "lamirada" are two rows — isolation by
    construction (site ids are free strings from the client, K1)."""
    return f"{company_id}::{site_id}"


def get_shipto(site_id: str, company_id: Optional[str] = None) -> Optional[dict]:
    """Return the stored ship-to for a site (dict of _WRITABLE fields + updated_at), or
    None when nothing has been saved yet (the UI falls back to its seeded default).
    ``company_id`` (arc 6): read the session company's row only; None = legacy row."""
    try:
        conn = _get_conn()
        conn.row_factory = sqlite3.Row
        if company_id is not None:
            row = conn.execute(
                f"SELECT {_READ_COLUMNS} FROM site_shipto WHERE site_id = ? AND company_id = ?",
                (_company_key(company_id, site_id), company_id)).fetchone()
            if not row:
                return None
            out = dict(row)
            out["site_id"] = site_id
            return out
        row = conn.execute(f"SELECT {_READ_COLUMNS} FROM site_shipto WHERE site_id = ?",
                           (site_id,)).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        print(f"[SiteSettings] get_shipto failed for {site_id!r}: {exc}")
        return None


def upsert_shipto(site_id: str, fields: dict, company_id: Optional[str] = None) -> bool:
    """Insert or replace a site's ship-to. Only _WRITABLE keys are accepted; missing keys
    store as empty strings. Fail-soft: returns False on a write error, never raises.
    ``company_id`` (arc 6): write the session company's row; None = legacy row."""
    if not site_id:
        return False
    values = {k: (fields.get(k) or "") for k in _WRITABLE}
    if company_id is not None:
        key = _company_key(company_id, site_id)
        try:
            conn = _get_conn()
            conn.execute(
                """INSERT INTO site_shipto
                       (site_id, company, address, city, attention, hours, instructions,
                        updated_at, company_id)
                   VALUES (:site_id, :company, :address, :city, :attention, :hours,
                           :instructions, :updated_at, :company_id)
                   ON CONFLICT(site_id) DO UPDATE SET
                       company=excluded.company, address=excluded.address, city=excluded.city,
                       attention=excluded.attention, hours=excluded.hours,
                       instructions=excluded.instructions, updated_at=excluded.updated_at""",
                {"site_id": key, **values, "company_id": company_id,
                 "updated_at": datetime.now(timezone.utc).isoformat()},
            )
            conn.commit()
            return True
        except Exception as exc:
            print(f"[SiteSettings] upsert_shipto failed for {key!r}: {exc}")
            return False
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn = _get_conn()
        conn.execute(
            """INSERT INTO site_shipto
                   (site_id, company, address, city, attention, hours, instructions, updated_at)
               VALUES (:site_id, :company, :address, :city, :attention, :hours, :instructions, :updated_at)
               ON CONFLICT(site_id) DO UPDATE SET
                   company=excluded.company, address=excluded.address, city=excluded.city,
                   attention=excluded.attention, hours=excluded.hours,
                   instructions=excluded.instructions, updated_at=excluded.updated_at""",
            {"site_id": site_id, **values, "updated_at": now},
        )
        conn.commit()
        print(f"[SiteSettings] ship-to saved for {site_id!r}")
        return True
    except Exception as exc:
        print(f"[SiteSettings] upsert_shipto failed for {site_id!r}: {exc}")
        return False
