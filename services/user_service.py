"""User profile management and administrative user operations."""
from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from database.models import AuditLog, LoginEvent, Role, User, UserRole
from services import notification_service as notif
from services.auth_service import assign_role_internal
from services.errors import NotFound, PermissionDenied, ValidationError
from services.permission_service import Actor, load_actor, require, require_active
from utils.audit_logger import audit, snapshot
from utils.constants import KERALA_DISTRICTS, AccountStatus, Perm, Role as RoleCode
from utils.date_utils import utcnow
from utils.validators import check_phone, check_pin, clean_text, raise_if

PROFILE_FIELDS = ["full_name", "phone", "location", "district", "pin_code"]


def get_profile(session: Session, actor: Actor) -> User:
    fresh = require_active(session, actor, allow_pending=True)
    return session.get(User, fresh.user_id)


def update_profile(session: Session, actor: Actor, **fields) -> User:
    fresh = require_active(session, actor, allow_pending=True)
    user = session.get(User, fresh.user_id)
    before = snapshot(user, PROFILE_FIELDS)
    errors: list[str] = []
    name = clean_text(fields.get("full_name"), 200)
    if not name:
        errors.append("Full name is required.")
    phone = check_phone(fields.get("phone"), errors, required=False)
    pin = check_pin(fields.get("pin_code"), errors, required=False)
    district = fields.get("district") or None
    if district and district not in KERALA_DISTRICTS:
        errors.append("Please select a valid district.")
    raise_if(errors)
    user.full_name, user.phone, user.pin_code, user.district = name, phone, pin, district
    user.location = clean_text(fields.get("location"), 300)
    audit(session, fresh.user_id, "PROFILE_UPDATED", "user", user.id, before, snapshot(user, PROFILE_FIELDS))
    return user


def user_roles(session: Session, user_id: uuid.UUID) -> list[str]:
    return list(session.scalars(select(Role.code).join(UserRole, UserRole.role_id == Role.id)
                                .where(UserRole.user_id == user_id)))


def search_users(session: Session, actor: Actor, text: str = "", role: str | None = None,
                 status: str | None = None, limit: int = 500) -> list[dict]:
    require(session, actor, Perm.USERS_MANAGE)
    q = select(User).where(User.deleted_at.is_(None))
    if text:
        like = f"%{text.strip()}%"
        q = q.where(or_(User.full_name.ilike(like), User.email.ilike(like)))
    if status:
        q = q.where(User.status == status)
    if role:
        q = q.join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id).where(Role.code == role)
    users = session.scalars(q.order_by(User.created_at.desc()).limit(limit)).all()
    out = []
    for u in users:
        out.append(dict(id=u.id, full_name=u.full_name, email=u.email, roles=", ".join(user_roles(session, u.id)),
                        status=u.status, district=u.district, created_at=u.created_at,
                        last_login_at=u.last_login_at, professional_verified=u.professional_verified,
                        sample=u.is_sample))
    return out


def _target(session: Session, user_id: uuid.UUID) -> User:
    u = session.get(User, user_id)
    if u is None or u.deleted_at is not None:
        raise NotFound("User not found.")
    return u


def _guard_admin_target(session: Session, fresh: Actor, target: User) -> None:
    roles = set(user_roles(session, target.id))
    if target.id == fresh.user_id:
        raise PermissionDenied("You cannot change your own account status.")
    if roles & {"ADMIN", "SUPER_ADMIN"} and not fresh.has_role("SUPER_ADMIN"):
        raise PermissionDenied("Only a super administrator can manage administrator accounts.")


def set_user_status(session: Session, actor: Actor, user_id: uuid.UUID, new_status: str, reason: str) -> User:
    fresh = require(session, actor, Perm.USERS_MANAGE)
    if new_status not in AccountStatus.values():
        raise ValidationError("Invalid status.")
    if not reason or len(reason.strip()) < 5:
        raise ValidationError("Please provide a reason (at least 5 characters).")
    target = _target(session, user_id)
    _guard_admin_target(session, fresh, target)
    before = {"status": target.status}
    target.status = new_status
    audit(session, fresh.user_id, f"USER_STATUS_{new_status}", "user", target.id, before,
          {"status": new_status}, details=reason.strip())
    notif.notify(session, target.id, notif.ACCOUNT_STATUS, "Account status updated",
                 f"Your account status is now {new_status}. Reason: {reason.strip()}", "user", target.id)
    return target


def suspend_user(session, actor, user_id, reason):
    return set_user_status(session, actor, user_id, AccountStatus.SUSPENDED.value, reason)


def reactivate_user(session, actor, user_id, reason):
    return set_user_status(session, actor, user_id, AccountStatus.ACTIVE.value, reason)


def verify_professional(session: Session, actor: Actor, user_id: uuid.UUID, notes: str) -> User:
    """Admin verifies a reviewer's professional credentials and activates the account."""
    fresh = require(session, actor, Perm.USERS_MANAGE)
    target = _target(session, user_id)
    if "REVIEWER" not in user_roles(session, target.id):
        raise ValidationError("Only reviewer accounts carry professional credentials.")
    if not target.professional_registration_number:
        raise ValidationError("The reviewer has not supplied a professional registration number.")
    target.professional_verified = True
    target.professional_verified_at = utcnow()
    if target.status == AccountStatus.PENDING.value:
        target.status = AccountStatus.ACTIVE.value
    audit(session, fresh.user_id, "PROFESSIONAL_VERIFIED", "user", target.id,
          after={"registration": target.professional_registration_number}, details=notes)
    notif.notify(session, target.id, notif.ACCOUNT_STATUS, "Professional credentials verified",
                 "Your reviewer credentials were verified. Review features are now enabled.")
    return target


def assign_role(session: Session, actor: Actor, user_id: uuid.UUID, role_code: str) -> None:
    """Admins may assign non-admin roles; only super admins may grant ADMIN/SUPER_ADMIN."""
    fresh = require(session, actor, Perm.USERS_MANAGE)
    if role_code not in RoleCode.values():
        raise ValidationError("Unknown role.")
    if role_code in {"ADMIN", "SUPER_ADMIN"}:
        fresh = require(session, actor, Perm.ADMINS_MANAGE)
    target = _target(session, user_id)
    assign_role_internal(session, target, role_code, fresh.user_id)
    audit(session, fresh.user_id, "ROLE_ASSIGNED", "user", target.id, after={"role": role_code})


def revoke_role(session: Session, actor: Actor, user_id: uuid.UUID, role_code: str) -> None:
    fresh = require(session, actor, Perm.USERS_MANAGE)
    if role_code in {"ADMIN", "SUPER_ADMIN"}:
        fresh = require(session, actor, Perm.ADMINS_MANAGE)
    target = _target(session, user_id)
    if target.id == fresh.user_id:
        raise PermissionDenied("You cannot revoke your own roles.")
    row = session.scalar(select(UserRole).join(Role, Role.id == UserRole.role_id)
                         .where(UserRole.user_id == target.id, Role.code == role_code))
    if row is None:
        raise NotFound("The user does not have this role.")
    session.delete(row)
    audit(session, fresh.user_id, "ROLE_REVOKED", "user", target.id, before={"role": role_code})


def activity_history(session: Session, actor: Actor, user_id: uuid.UUID) -> dict:
    require(session, actor, Perm.USERS_MANAGE)
    logins = session.scalars(select(LoginEvent).where(LoginEvent.user_id == user_id)
                             .order_by(LoginEvent.created_at.desc()).limit(100)).all()
    audits = session.scalars(select(AuditLog).where(
        or_(AuditLog.actor_user_id == user_id, AuditLog.entity_id == str(user_id)))
        .order_by(AuditLog.created_at.desc()).limit(200)).all()
    return {
        "logins": [dict(event=e.event_type, provider=e.provider, at=e.created_at) for e in logins],
        "audit": [dict(action=a.action, entity=a.entity_type, entity_id=a.entity_id, details=a.details,
                       at=a.created_at) for a in audits],
    }


def view_user(session: Session, actor: Actor, user_id: uuid.UUID) -> dict:
    require(session, actor, Perm.USERS_MANAGE)
    u = _target(session, user_id)
    a = load_actor(session, u.id)
    return dict(id=u.id, full_name=u.full_name, email=u.email, phone=u.phone, location=u.location,
                district=u.district, pin_code=u.pin_code, status=u.status, roles=sorted(a.roles),
                organization_id=u.organization_id, created_at=u.created_at, last_login_at=u.last_login_at,
                professional_registration_number=u.professional_registration_number,
                professional_council=u.professional_council, professional_verified=u.professional_verified)
