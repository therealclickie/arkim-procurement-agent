"""
utils/buyer_accounts_rbac.py
Arc 6 T2 — the buyer permission matrix (D2).

THE ONE module that says what each buyer role may do. Enforcement goes through
``has_permission(member, capability)`` against the declared matrix — no route
compares role names inline (a source-scan test enforces it, as arc 2's does
for suppliers). A fifth role is one row in ``CAPABILITY_MATRIX``.

The matrix is D2's table verbatim:

  | Capability                                   | Req | Buyer | Appr | Admin |
  | raise a request / chat intake                |  ✓  |   ✓   |  ✓   |   ✓   |
  | view the company's runs and orders           |  ✓  |   ✓   |  ✓   |   ✓   |
  | select a candidate and submit an order       |     |   ✓   |  ✓   |   ✓   |
  | approve an order up to the auto limit        |     |   ✓   |  ✓   |   ✓   |
  | give the second approval above the limit     |     |       |  ✓   |   ✓   |
  | override the limit (single-person approval)  |     |       |      |   ✓ ¹ |
  | set the auto-approval limit                  |     |       |      |   ✓   |
  | turn the admin override on or off            |     |       |      |   ✓   |
  | invite, change role, revoke members          |     |       |      |   ✓   |

  ¹ only while the company's ``allow_admin_override`` is on (D5).

The approval capabilities are DEFINED here and the policy is STORED in
utils/buyer_accounts.py; nothing enforces them at order time yet (arc 7).

Member-management rules (D3) live here too, above the matrix: invite defaults
to Requester; a member can only be managed within the actor's company (a
cross-company id is a not-found); the last active Admin of a company can be
neither demoted nor revoked — including by themselves — so a company can never
lock itself out of its own settings.
"""
from __future__ import annotations

from typing import Callable, Optional

from utils.buyer_accounts import (
    DEFAULT_INVITE_ROLE,
    MEMBER_ACTIVE,
    MEMBER_REVOKED,
    ROLE_ADMIN,
    ROLE_APPROVER,
    ROLE_BUYER,
    ROLE_REQUESTER,
    ROLES,
    BuyerAccountsError,
)

# ---------------------------------------------------------------------------
# Capabilities + the matrix (the specification — asserted as DATA by the
# table test in test_buyer_accounts_rbac.py, every role × capability pair)
# ---------------------------------------------------------------------------

RAISE_REQUEST = "raise_request"
VIEW_COMPANY = "view_company"
SELECT_AND_ORDER = "select_and_order"
APPROVE_WITHIN_LIMIT = "approve_within_limit"
SECOND_APPROVAL = "second_approval"
OVERRIDE_LIMIT = "override_limit"
SET_APPROVAL_LIMIT = "set_approval_limit"
TOGGLE_ADMIN_OVERRIDE = "toggle_admin_override"
MANAGE_MEMBERS = "manage_members"

CAPABILITIES: tuple[str, ...] = (
    RAISE_REQUEST, VIEW_COMPANY, SELECT_AND_ORDER, APPROVE_WITHIN_LIMIT,
    SECOND_APPROVAL, OVERRIDE_LIMIT, SET_APPROVAL_LIMIT, TOGGLE_ADMIN_OVERRIDE,
    MANAGE_MEMBERS,
)

_EVERYONE = frozenset({RAISE_REQUEST, VIEW_COMPANY})
_BUYER = _EVERYONE | {SELECT_AND_ORDER, APPROVE_WITHIN_LIMIT}
_APPROVER = _BUYER | {SECOND_APPROVAL}

CAPABILITY_MATRIX: dict[str, frozenset[str]] = {
    ROLE_REQUESTER: _EVERYONE,
    ROLE_BUYER: frozenset(_BUYER),
    ROLE_APPROVER: frozenset(_APPROVER),
    ROLE_ADMIN: frozenset(CAPABILITIES),
}

# Capabilities that hold only while a company setting allows them (footnote ¹).
_CONDITIONAL_ON_OVERRIDE_SETTING = frozenset({OVERRIDE_LIMIT})


def has_permission(member: Optional[dict], capability: str,
                   company: Optional[dict] = None) -> bool:
    """True iff ``member``'s role holds ``capability`` per THE matrix. The
    single enforcement primitive. Fail closed: a missing or non-ACTIVE member,
    an unknown role or an unknown capability holds nothing. OVERRIDE_LIMIT
    additionally needs ``company["allow_admin_override"]`` — without the
    company in hand it is refused, never assumed."""
    if not member or member.get("status") != MEMBER_ACTIVE:
        return False
    if capability not in CAPABILITY_MATRIX.get(member.get("role") or "", frozenset()):
        return False
    if capability in _CONDITIONAL_ON_OVERRIDE_SETTING:
        return bool(company and company.get("allow_admin_override"))
    return True


def permissions_for(member: Optional[dict], company: Optional[dict] = None) -> list[str]:
    """Every capability the member holds right now, sorted — what ``/me``
    reflects to the UI. Not the control: routes re-check has_permission."""
    return sorted(c for c in CAPABILITIES if has_permission(member, c, company))


def capability_dependency(capability: str,
                          session_dependency: Callable[..., dict]) -> Callable[..., dict]:
    """FastAPI dependency factory: ``session_dependency`` authenticates, then
    THE matrix decides. 403 when the role lacks the capability. This is the
    only seam routes use — they never name a role.

    A session dependency that returns ``None`` is saying "identity is not in
    force here" (BUYER_ACCOUNTS_V1 off on a pre-existing route, D8); the
    capability check then passes ``None`` through and the route behaves as
    it did before this arc. Refusing an absent session is the session
    dependency's job, never this one's."""
    from fastapi import Depends, HTTPException

    def dep(session: Optional[dict] = Depends(session_dependency)) -> Optional[dict]:
        if session is None:
            return None
        if not has_permission(session.get("member"), capability, session.get("company")):
            raise HTTPException(status_code=403, detail="Forbidden")
        return session

    dep.buyer_capability = capability  # type: ignore[attr-defined]
    return dep


# ---------------------------------------------------------------------------
# Member-management policy (D3)
# ---------------------------------------------------------------------------

def invite_member(actor: dict, email: str, role: Optional[str] = None) -> dict:
    """Invite ``email`` into the actor's company. MANAGE_MEMBERS required.
    ``role`` defaults to Requester (least privilege). A person already in a
    company — this one or another — is ``already_member`` (one company per
    member). Membership is always explicit: there is no domain auto-join."""
    from utils import buyer_accounts
    if not has_permission(actor, MANAGE_MEMBERS):
        raise BuyerAccountsError("forbidden", "manage_members required")
    role = role or DEFAULT_INVITE_ROLE
    if role not in ROLES:
        raise BuyerAccountsError("invalid_role", f"not a role: {role!r}")
    norm = buyer_accounts.normalize_email(email)
    if not norm:
        raise BuyerAccountsError("invalid_email", "not a usable email")
    member = buyer_accounts.add_member(actor["company_id"], norm, role=role,
                                       invited_by=actor["id"])
    if member is None:
        raise BuyerAccountsError("store_error", "member could not be created")
    return member


def change_member_role(actor: dict, target_member_id: str, new_role: str) -> dict:
    """Change a member's role. MANAGE_MEMBERS required; target in the actor's
    company; the last active Admin cannot be demoted (by anyone, themselves
    included)."""
    from utils import buyer_accounts
    if not has_permission(actor, MANAGE_MEMBERS):
        raise BuyerAccountsError("forbidden", "manage_members required")
    if new_role not in ROLES:
        raise BuyerAccountsError("invalid_role", f"not a role: {new_role!r}")
    target = _target_in_actor_company(actor, target_member_id)
    if target["status"] != MEMBER_ACTIVE:
        raise BuyerAccountsError("member_not_active", "member is not active")
    if new_role == target["role"]:
        return target
    if _is_last_active_admin(target) and new_role != ROLE_ADMIN:
        raise BuyerAccountsError("last_admin", "a company must keep at least one Admin")
    out = buyer_accounts.update_member_role(target["id"], new_role)
    if out is None:
        raise BuyerAccountsError("store_error", "role could not be changed")
    return out


def revoke_member(actor: dict, target_member_id: str) -> dict:
    """Revoke a member (and every session they hold). MANAGE_MEMBERS
    required; target in the actor's company; the last active Admin cannot be
    revoked (by anyone, themselves included)."""
    from utils import buyer_accounts
    if not has_permission(actor, MANAGE_MEMBERS):
        raise BuyerAccountsError("forbidden", "manage_members required")
    target = _target_in_actor_company(actor, target_member_id)
    if target["status"] == MEMBER_REVOKED:
        return target
    if _is_last_active_admin(target):
        raise BuyerAccountsError("last_admin", "a company must keep at least one Admin")
    out = buyer_accounts.update_member_status(target["id"], MEMBER_REVOKED)
    if out is None:
        raise BuyerAccountsError("store_error", "member could not be revoked")
    return out


def _is_last_active_admin(member: dict) -> bool:
    from utils import buyer_accounts
    return (member.get("role") == ROLE_ADMIN
            and member.get("status") == MEMBER_ACTIVE
            and buyer_accounts.count_active_role(member["company_id"], ROLE_ADMIN) <= 1)


def _target_in_actor_company(actor: dict, target_member_id: str) -> dict:
    """The target, verified to belong to the actor's company. A cross-company
    id raises ``member_not_found`` — indistinguishable from an unknown id."""
    from utils import buyer_accounts
    target = buyer_accounts.get_member(target_member_id)
    if not target or target.get("company_id") != actor.get("company_id"):
        raise BuyerAccountsError("member_not_found", "Member not found")
    return target
