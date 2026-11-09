"""Server-side role-based access control.

The `Actor` object passed between pages and services only *identifies* a
user. Every `require*` call reloads the user's status, roles and permissions
from the database, so values held in st.session_state or submitted by a
browser are never trusted for authorization decisions.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import (
    Organization, OrganizationAuthorization, Permission, Role, RolePermission, User, UserRole,
)
from services.errors import NotFound, PermissionDenied
from utils.constants import AccountStatus, Perm
from utils.date_utils import today


@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID
    email: str = ""
    full_name: str = ""
    status: str = ""
    roles: frozenset = frozenset()
    permissions: frozenset = frozenset()
    organization_id: uuid.UUID | None = None
    professional_verified: bool = False

    def has_role(self, *codes: str) -> bool:
        return bool(self.roles & {str(c) for c in codes})

    def can(self, perm: str) -> bool:
        return str(perm) in self.permissions

    @property
    def is_admin(self) -> bool:
        return self.has_role("ADMIN", "SUPER_ADMIN")

    @property
    def primary_role(self) -> str | None:
        order = ["SUPER_ADMIN", "ADMIN", "REVIEWER", "COLLECTION_CENTRE", "INSTITUTIONAL_DONOR",
                 "NGO", "INDIVIDUAL"]
        for r in order:
            if r in self.roles:
                return r
        return None


def load_actor(session: Session, user_id: uuid.UUID) -> Actor:
    user = session.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise NotFound("User not found.")
    role_rows = session.execute(
        select(Role.id, Role.code).join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user.id)
    ).all()
    role_ids = [r.id for r in role_rows]
    perms: set[str] = set()
    if role_ids:
        perms = set(session.scalars(
            select(Permission.code).join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id.in_(role_ids))
        ))
    return Actor(
        user_id=user.id, email=user.email, full_name=user.full_name, status=user.status,
        roles=frozenset(r.code for r in role_rows), permissions=frozenset(perms),
        organization_id=user.organization_id, professional_verified=user.professional_verified,
    )


def require_active(session: Session, actor: Actor, *, allow_pending: bool = False) -> Actor:
    fresh = load_actor(session, actor.user_id)
    allowed = {AccountStatus.ACTIVE.value}
    if allow_pending:
        allowed.add(AccountStatus.PENDING.value)
    if fresh.status not in allowed:
        raise PermissionDenied(f"Your account is {fresh.status.lower()}; this action is not available.")
    return fresh


def require(session: Session, actor: Actor, *perms: Perm | str, allow_pending: bool = False) -> Actor:
    """Require ALL given permissions. Returns a freshly loaded Actor."""
    fresh = require_active(session, actor, allow_pending=allow_pending)
    missing = [str(p) for p in perms if str(p) not in fresh.permissions]
    if missing:
        raise PermissionDenied("You do not have permission to perform this action.")
    return fresh


def require_any(session: Session, actor: Actor, *perms: Perm | str) -> Actor:
    fresh = require_active(session, actor)
    if not any(str(p) in fresh.permissions for p in perms):
        raise PermissionDenied("You do not have permission to perform this action.")
    return fresh


def require_role(session: Session, actor: Actor, *roles: str, allow_pending: bool = False) -> Actor:
    fresh = require_active(session, actor, allow_pending=allow_pending)
    if not fresh.has_role(*roles):
        raise PermissionDenied("Your role does not allow this action.")
    return fresh


def require_org_member(fresh: Actor, organization_id: uuid.UUID) -> None:
    if fresh.is_admin:
        return
    if fresh.organization_id is None or fresh.organization_id != organization_id:
        raise PermissionDenied("You can only access your own organization's records.")


def require_verified_professional(fresh: Actor) -> None:
    if not fresh.professional_verified:
        raise PermissionDenied(
            "Your professional qualifications have not been verified; review actions are disabled.")


def org_is_verified(org: Organization | None) -> bool:
    return org is not None and org.deleted_at is None and org.verification_status == "VERIFIED"


def active_authorizations(session: Session, organization_id: uuid.UUID, types: set[str],
                          on: date | None = None) -> list[OrganizationAuthorization]:
    on = on or today()
    rows = session.scalars(
        select(OrganizationAuthorization).where(
            OrganizationAuthorization.organization_id == organization_id,
            OrganizationAuthorization.status == "VERIFIED",
            OrganizationAuthorization.authorization_type.in_(types),
        )
    ).all()
    return [a for a in rows
            if (a.valid_from is None or a.valid_from <= on) and (a.valid_until is None or a.valid_until >= on)]
