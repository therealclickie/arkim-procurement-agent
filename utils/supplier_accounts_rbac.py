"""
utils/supplier_accounts_rbac.py
Arc 2 T11 — the supplier-account permission matrix (D7).

THE ONE module that declares roles' capabilities. D7's point is structural:
enforcement goes through ``has_permission(member, capability)`` against the
declared matrix — never scattered ``if role == "OWNER"`` comparisons in route
handlers. A fourth role (a read-only VIEWER, deliberately out of scope for
v1) must be ONE row in ``CAPABILITY_MATRIX``, not a hunt through routes.

Role NAMES are the data-layer vocabulary of utils/supplier_accounts.py (the
store persists them); this module imports them and owns every POLICY over
them: the matrix, the capability check, and the member-management rules
(who may invite / change roles / revoke, and the ownership invariants).
Route handlers call this module and the store — they never name a role.

Rules encoded here (D7):
  - exactly one OWNER per account (the persistence layer's partial unique
    index is the hard guarantee; the policy here never attempts a second);
  - the first member to establish an account becomes OWNER (that rule lives
    in the store's establish_account — it is establishment, not management);
  - a concierge-approved pending member defaults to MEMBER (least
    privilege — store.approve_pending_member);
  - an ADMIN may not promote anyone to OWNER nor demote the OWNER (v1 has no
    ownership-transfer route: ownership changes only via account
    establishment; a dedicated transfer flow is future work);
  - the OWNER cannot leave without transferring ownership — an OWNER target
    can never be revoked, by anyone, including themselves.

Fail-soft discipline: policy violations raise SupplierAccountsError with a
stable ``code`` (the caller maps it to an HTTP status); store I/O failures
remain fail-soft in the store itself.
"""
from __future__ import annotations

from typing import Optional

from utils.supplier_accounts import (
    MEMBER_ACTIVE,
    MEMBER_PENDING,
    MEMBER_REVOKED,
    ROLE_ADMIN,
    ROLE_MEMBER,
    ROLE_OWNER,
    ROLES,
    SupplierAccountsError,
    add_member,
    get_member,
    update_member_role,
    update_member_status,
)

# ---------------------------------------------------------------------------
# Capabilities + the matrix (the specification — asserted as DATA by the
# table test in test_supplier_accounts_rbac.py, every role × capability pair)
# ---------------------------------------------------------------------------

VIEW_REQUESTS = "view_requests"          # see open RFQs / own requests
SUBMIT_QUOTES = "submit_quotes"          # submit structured quotes
PROPOSE_REVISIONS = "propose_revisions"  # propose profile/capability revisions
VIEW_MEMBERS = "view_members"            # see the account's member list
MANAGE_MEMBERS = "manage_members"        # invite / approve / revoke members
CHANGE_ROLES = "change_roles"            # change a member's role
TRANSFER_OWNERSHIP = "transfer_ownership"  # transfer ownership / delete account

CAPABILITIES: tuple[str, ...] = (
    VIEW_REQUESTS, SUBMIT_QUOTES, PROPOSE_REVISIONS, VIEW_MEMBERS,
    MANAGE_MEMBERS, CHANGE_ROLES, TRANSFER_OWNERSHIP,
)

# The matrix (D7's table): role -> the capabilities it holds. Exactly the
# brief's table — every role can view/quote/propose; OWNER+ADMIN manage
# members and roles; ONLY the OWNER transfers ownership / deletes.
CAPABILITY_MATRIX: dict[str, frozenset[str]] = {
    ROLE_OWNER: frozenset(CAPABILITIES),
    ROLE_ADMIN: frozenset({
        VIEW_REQUESTS, SUBMIT_QUOTES, PROPOSE_REVISIONS, VIEW_MEMBERS,
        MANAGE_MEMBERS, CHANGE_ROLES,
    }),
    ROLE_MEMBER: frozenset({
        VIEW_REQUESTS, SUBMIT_QUOTES, PROPOSE_REVISIONS, VIEW_MEMBERS,
    }),
}


def has_permission(member: dict, capability: str) -> bool:
    """True iff ``member``'s role holds ``capability`` per THE matrix. The
    single enforcement primitive — routes depend on it, nothing compares
    roles inline. An unknown role holds nothing (fail closed). A non-ACTIVE
    member holds nothing (sessions already require ACTIVE, belt+braces)."""
    if not member or member.get("status") != MEMBER_ACTIVE:
        return False
    return capability in CAPABILITY_MATRIX.get(member.get("role") or "", frozenset())


# ---------------------------------------------------------------------------
# Member-management policy (the D7 rules ABOVE the matrix — all role logic
# lives HERE, in the matrix module, never in route handlers)
# ---------------------------------------------------------------------------

def invite_member(actor: dict, email: str, role: str) -> dict:
    """Invite ``email`` into the actor's account at ``role``. Policy:
    MANAGE_MEMBERS capability required; role must be valid and NOT OWNER
    (ownership is established, not invited — a second live OWNER is
    impossible by the persistence invariant anyway). A domain-matching email
    lands ACTIVE immediately (D2 — the email proves company membership); a
    non-matching / public-mailbox email lands PENDING for the concierge
    (T10). A REVOKED member may be re-invited (a fresh invitation is the
    path back); an existing ACTIVE/PENDING membership is a 409-class
    conflict. Returns the member dict; raises SupplierAccountsError on a
    policy violation."""
    if not has_permission(actor, MANAGE_MEMBERS):
        raise SupplierAccountsError("forbidden", "manage_members required")
    if role not in ROLES:
        raise SupplierAccountsError("invalid_role", f"not a role: {role!r}")
    if role == ROLE_OWNER:
        raise SupplierAccountsError(
            "owner_transfer_required",
            "ownership is established via the account claim, never invited")
    from utils.supplier_accounts import (
        ensure_member_for_link, get_member_by_email, normalize_email,
        email_registrable_domain, is_public_mailbox_email,
    )
    norm = normalize_email(email)
    if not norm:
        raise SupplierAccountsError("invalid_email", "not a usable email")
    account_id = actor["account_id"]
    existing = get_member_by_email(account_id, norm)
    if existing is not None:
        if existing["status"] == MEMBER_REVOKED:
            # A fresh invitation is the path back for a revoked member.
            out = update_member_role(existing["id"], role)
            revived = update_member_status(existing["id"], MEMBER_PENDING,
                                           updated_by=actor["id"])
            return revived or out or existing
        raise SupplierAccountsError(
            "already_member", f"{norm} is already a member of this account")
    # D2 establishment with the requested (non-OWNER) role.
    auto_active = (
        email_registrable_domain(norm) == (_actor_account_domain(actor) or "")
        and not is_public_mailbox_email(norm)
    )
    member = add_member(
        account_id, norm, role=role,
        status=MEMBER_ACTIVE if auto_active else MEMBER_PENDING,
        invited_by=actor["id"],
    )
    if member is None:
        raise SupplierAccountsError("store_error", "member could not be created")
    return member


def _actor_account_domain(actor: dict) -> Optional[str]:
    """The actor's account domain (for the invite D2 match)."""
    from utils.supplier_accounts import get_account, _normalize_domain
    account = get_account(actor.get("account_id") or "")
    return _normalize_domain((account or {}).get("supplier_domain") or "")


def change_member_role(actor: dict, target_member_id: str, new_role: str) -> dict:
    """Change a member's role. Policy: CHANGE_ROLES capability; the target
    must belong to the ACTOR's account (cross-account is a not-found, never
    an existence reveal); the new role must be valid; nobody may promote to
    OWNER (v1 has no transfer route) and the OWNER may never be demoted
    (ownership transfer is the dedicated path, not built in v1). The
    persistence layer's partial unique index remains the hard one-OWNER
    guarantee. Returns the updated member; raises on a policy violation."""
    if not has_permission(actor, CHANGE_ROLES):
        raise SupplierAccountsError("forbidden", "change_roles required")
    if new_role not in ROLES:
        raise SupplierAccountsError("invalid_role", f"not a role: {new_role!r}")
    target = _target_in_actor_account(actor, target_member_id)
    if new_role == ROLE_OWNER:
        raise SupplierAccountsError(
            "owner_transfer_required",
            "ownership is established via the account claim, never assigned "
            "by role change (a dedicated transfer flow is future work)")
    if target["role"] == ROLE_OWNER:
        raise SupplierAccountsError(
            "cannot_demote_owner",
            "the OWNER cannot be demoted; ownership must transfer first")
    out = update_member_role(target["id"], new_role)
    if out is None:
        raise SupplierAccountsError("store_error", "role could not be changed")
    return out


def revoke_member(actor: dict, target_member_id: str) -> dict:
    """Revoke a member. Policy: MANAGE_MEMBERS capability; the target must
    belong to the actor's account; an OWNER can never be revoked — by
    anyone, including themselves (D7: the OWNER cannot leave without
    transferring ownership; v1 has no transfer route). Returns the updated
    member; raises on a policy violation."""
    if not has_permission(actor, MANAGE_MEMBERS):
        raise SupplierAccountsError("forbidden", "manage_members required")
    target = _target_in_actor_account(actor, target_member_id)
    if target["role"] == ROLE_OWNER:
        raise SupplierAccountsError(
            "cannot_revoke_owner",
            "the OWNER cannot leave without transferring ownership")
    out = update_member_status(target["id"], MEMBER_REVOKED,
                               updated_by=actor["id"])
    if out is None:
        raise SupplierAccountsError("store_error", "member could not be revoked")
    return out


def _target_in_actor_account(actor: dict, target_member_id: str) -> dict:
    """The target member, verified to belong to the actor's account. Raises
    ``member_not_found`` otherwise (a cross-account id is indistinguishable
    from an unknown one — no existence reveal across accounts)."""
    target = get_member(target_member_id)
    if not target or target.get("account_id") != actor.get("account_id"):
        raise SupplierAccountsError("member_not_found", "Member not found")
    return target
