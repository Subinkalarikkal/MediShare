"""Append-only audit logging."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from database.models import AuditLog


def _jsonable(value: Any) -> Any:
    if isinstance(value, (uuid.UUID, Decimal)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return value


def snapshot(obj, fields: list[str]) -> dict:
    return {f: _jsonable(getattr(obj, f, None)) for f in fields}


def audit(session: Session, actor_id: uuid.UUID | None, action: str, entity_type: str,
          entity_id: Any = None, before: dict | None = None, after: dict | None = None,
          details: str | None = None) -> AuditLog:
    entry = AuditLog(
        actor_user_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        before=_jsonable(before) if before else None,
        after=_jsonable(after) if after else None,
        details=details,
    )
    session.add(entry)
    session.flush()
    return entry
