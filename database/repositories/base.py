"""Generic repository helpers."""
from __future__ import annotations

import uuid
from typing import TypeVar

from sqlalchemy.orm import Session

T = TypeVar("T")


def get_or_raise(session: Session, model: type[T], obj_id: uuid.UUID, label: str = "Record") -> T:
    from services.errors import NotFound

    obj = session.get(model, obj_id)
    if obj is None:
        raise NotFound(f"{label} not found.")
    return obj


def get_active(session: Session, model: type[T], obj_id: uuid.UUID, label: str = "Record") -> T:
    """Like get_or_raise but treats soft-deleted rows as missing."""
    from services.errors import NotFound

    obj = get_or_raise(session, model, obj_id, label)
    if getattr(obj, "deleted_at", None) is not None:
        raise NotFound(f"{label} not found.")
    return obj
