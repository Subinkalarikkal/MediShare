"""System settings, permissions management, audit-log access and integration status."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from config import get_settings
from database.models import AuditLog, Permission, Role, RolePermission, SystemSetting
from services.errors import NotFound, PermissionDenied, ValidationError
from services.permission_service import Actor, require
from utils.audit_logger import audit
from utils.constants import OrgType, Perm

PROTECTED_PERMISSIONS = {(r, p) for r in ("SUPER_ADMIN",) for p in
                         (Perm.SETTINGS_MANAGE.value, Perm.PERMISSIONS_MANAGE.value, Perm.ADMINS_MANAGE.value)}
FORBIDDEN_GRANTS = {  # safety: these roles may never hold these permissions
    "INDIVIDUAL": {Perm.INVENTORY_MANAGE.value, Perm.REVIEW_INVENTORY.value, Perm.TRANSFER_DOCUMENT.value,
                   Perm.INSTITUTIONAL_REQUEST_MANAGE.value},
    "NGO": {Perm.MEDICINE_REGISTER.value, Perm.COLLECTION_REQUEST_CREATE.value},
    "REVIEWER": {Perm.SETTINGS_MANAGE.value, Perm.PERMISSIONS_MANAGE.value, Perm.ADMINS_MANAGE.value},
}


def get_value(session: Session, key: str, default=None):
    s = session.scalar(select(SystemSetting).where(SystemSetting.key == key))
    return s.value if s else default


def list_settings(session: Session, actor: Actor) -> list[SystemSetting]:
    require(session, actor, Perm.SETTINGS_MANAGE)
    return list(session.scalars(select(SystemSetting).order_by(SystemSetting.key)))


def _validate(key: str, value):
    if key == "risk.weights":
        if not isinstance(value, dict) or set(value) != {"T", "Q", "D", "C"}:
            raise ValidationError("Weights must contain exactly T, Q, D and C.")
        if any(not isinstance(v, (int, float)) or v < 0 for v in value.values()):
            raise ValidationError("Weights must be non-negative numbers.")
        if abs(sum(value.values()) - 1.0) > 1e-6:
            raise ValidationError("Weights must sum to 1.0.")
    elif key in {"risk.quantity_reference", "risk.delay_reference_days", "risk.time_horizon_days"}:
        if not isinstance(value, (int, float)) or value <= 0:
            raise ValidationError("Value must be a positive number.")
    elif key == "risk.level_thresholds":
        if not (isinstance(value, dict) and 0 < value.get("MODERATE", 0) < value.get("HIGH", 0) < value.get("CRITICAL", 0) <= 100):
            raise ValidationError("Thresholds must satisfy 0 < MODERATE < HIGH < CRITICAL ≤ 100.")
    elif key == "expiry.category_thresholds":
        if not (isinstance(value, dict) and 0 < value.get("URGENT", 0) < value.get("ATTENTION", 0) < value.get("MONITOR", 0)):
            raise ValidationError("Thresholds must satisfy 0 < URGENT < ATTENTION < MONITOR.")
    elif key == "organizations.enabled_types":
        if not isinstance(value, list) or not value or any(v not in OrgType.values() for v in value):
            raise ValidationError("Enabled types must be a non-empty list of known organization types.")
    elif key == "notifications.email_enabled":
        if not isinstance(value, bool):
            raise ValidationError("Value must be true or false.")


def update_setting(session: Session, actor: Actor, key: str, value) -> SystemSetting:
    fresh = require(session, actor, Perm.SETTINGS_MANAGE)
    s = session.scalar(select(SystemSetting).where(SystemSetting.key == key))
    if s is None:
        raise NotFound("Setting not found.")
    _validate(key, value)
    before = {"value": s.value}
    s.value, s.updated_by = value, fresh.user_id
    audit(session, fresh.user_id, "SETTING_UPDATED", "system_setting", key, before, {"value": value})
    return s


def role_permission_matrix(session: Session, actor: Actor) -> dict[str, list[str]]:
    require(session, actor, Perm.PERMISSIONS_MANAGE)
    rows = session.execute(select(Role.code, Permission.code).join(RolePermission, RolePermission.role_id == Role.id)
                           .join(Permission, Permission.id == RolePermission.permission_id)).all()
    out: dict[str, list[str]] = {r.code: [] for r in session.scalars(select(Role))}
    for role, perm in rows:
        out[role].append(perm)
    return {k: sorted(v) for k, v in out.items()}


def set_role_permission(session: Session, actor: Actor, role_code: str, perm_code: str, granted: bool) -> None:
    fresh = require(session, actor, Perm.PERMISSIONS_MANAGE)
    if not granted and (role_code, perm_code) in PROTECTED_PERMISSIONS:
        raise PermissionDenied("This permission is protected and cannot be removed from the super administrator.")
    if granted and perm_code in FORBIDDEN_GRANTS.get(role_code, set()):
        raise PermissionDenied("This grant would break a mandatory safety separation and is not allowed.")
    role = session.scalar(select(Role).where(Role.code == role_code))
    perm = session.scalar(select(Permission).where(Permission.code == perm_code))
    if role is None or perm is None:
        raise NotFound("Role or permission not found.")
    rp = session.scalar(select(RolePermission).where(RolePermission.role_id == role.id,
                                                     RolePermission.permission_id == perm.id))
    if granted and rp is None:
        session.add(RolePermission(role_id=role.id, permission_id=perm.id))
    elif not granted and rp is not None:
        session.delete(rp)
    else:
        return
    audit(session, fresh.user_id, "PERMISSION_GRANTED" if granted else "PERMISSION_REVOKED", "role", role_code,
          after={"permission": perm_code})


def audit_logs(session: Session, actor: Actor, action: str | None = None, entity_type: str | None = None,
               actor_id: uuid.UUID | None = None, limit: int = 1000) -> list[dict]:
    require(session, actor, Perm.AUDIT_VIEW)
    q = select(AuditLog)
    if action:
        q = q.where(AuditLog.action.ilike(f"%{action}%"))
    if entity_type:
        q = q.where(AuditLog.entity_type == entity_type)
    if actor_id:
        q = q.where(AuditLog.actor_user_id == actor_id)
    return [dict(at=a.created_at, actor=str(a.actor_user_id) if a.actor_user_id else "system", action=a.action,
                 entity=a.entity_type, entity_id=a.entity_id, details=a.details or "",
                 before=a.before, after=a.after) for a in session.scalars(q.order_by(AuditLog.created_at.desc()).limit(limit))]


def integration_status(session: Session, actor: Actor) -> list[dict]:
    """Shows whether integrations are configured – never shows secret values."""
    require(session, actor, Perm.INTEGRATIONS_MANAGE)
    s = get_settings()
    from ai.ocr_service import ocr_available
    return [
        dict(integration="Database", configured=True, detail=s.database_url.split(":", 1)[0]),
        dict(integration="Claude API", configured=bool(s.anthropic_api_key), detail=s.claude_model),
        dict(integration="External AI processing", configured=s.ai_external_processing_enabled, detail="AI_EXTERNAL_PROCESSING_ENABLED"),
        dict(integration="OCR (Tesseract/EasyOCR)", configured=ocr_available(), detail=""),
        dict(integration="Private object storage (S3)", configured=s.s3_enabled, detail=s.storage_endpoint or "AWS default"),
        dict(integration="Email provider", configured=bool(s.email_provider_api_key), detail=""),
        dict(integration="Google Maps", configured=bool(s.google_maps_api_key), detail="OpenStreetMap used otherwise"),
        dict(integration="Authentication mode", configured=True, detail=s.auth_mode),
    ]
