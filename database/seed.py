"""Idempotent seeding of reference data (roles, permissions, settings)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Permission, Role, RolePermission, SystemSetting
from utils.constants import DEFAULT_ROLE_PERMISSIONS, DEFAULT_SETTINGS, ROLE_LABELS, Perm


def seed_reference_data(session: Session) -> None:
    roles = {r.code: r for r in session.scalars(select(Role))}
    for code, label in ROLE_LABELS.items():
        if code.value not in roles:
            role = Role(code=code.value, name=label)
            session.add(role)
            roles[code.value] = role
    perms = {p.code: p for p in session.scalars(select(Permission))}
    for perm in Perm:
        if perm.value not in perms:
            p = Permission(code=perm.value, description=perm.name.replace("_", " ").title())
            session.add(p)
            perms[perm.value] = p
    session.flush()
    existing = {(rp.role_id, rp.permission_id) for rp in session.scalars(select(RolePermission))}
    for role_code, plist in DEFAULT_ROLE_PERMISSIONS.items():
        role = roles[role_code.value]
        for perm in plist:
            key = (role.id, perms[perm.value].id)
            if key not in existing:
                session.add(RolePermission(role_id=role.id, permission_id=perms[perm.value].id))
                existing.add(key)
    keys = set(session.scalars(select(SystemSetting.key)))
    for key, spec in DEFAULT_SETTINGS.items():
        if key not in keys:
            session.add(SystemSetting(key=key, value=spec["value"], description=spec["description"]))
    session.flush()
