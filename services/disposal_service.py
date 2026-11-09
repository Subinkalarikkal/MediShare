"""Authorized disposal records and end-to-end traceability."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import (
    CollectionCentre, CollectionEvent, CollectionItem, CollectionRequest, DisposalRecord, MedicineRegistration,
)
from services import collection_service
from services import notification_service as notif
from services import storage_service
from services.errors import NotFound, PermissionDenied, WorkflowError
from services.permission_service import Actor, require, require_active
from utils.audit_logger import audit
from utils.constants import DisposalMethod, Perm, Unit
from utils.date_utils import today
from utils.validators import check_choice, check_quantity, clean_text, raise_if, require as req_field


def record_disposal(session: Session, actor: Actor, req_id: uuid.UUID, *, disposal_method: str,
                    disposal_date: date, quantity_disposed: float, unit: str, disposal_facility: str,
                    reference_number: str, certificate: tuple[bytes, str] | None = None,
                    notes: str | None = None, is_sample: bool = False) -> DisposalRecord:
    fresh = require(session, actor, Perm.DISPOSAL_RECORD)
    req = collection_service._centre_request(session, fresh, req_id)
    if req.status != "DISPOSAL_PENDING":
        raise WorkflowError("Disposal can only be recorded for requests awaiting disposal.")
    errors: list[str] = []
    check_choice(disposal_method, DisposalMethod.values(), errors, "Disposal method")
    check_choice(unit, Unit.values(), errors, "Unit")
    qty = check_quantity(quantity_disposed, errors, "Disposed quantity")
    facility = clean_text(disposal_facility, 250)
    ref = clean_text(reference_number, 100)
    req_field(facility, "Disposal facility", errors)
    req_field(ref, "Disposal reference / manifest number", errors)
    if not isinstance(disposal_date, date):
        errors.append("Disposal date is required.")
    elif disposal_date > today():
        errors.append("Disposal date cannot be in the future.")
    elif req.actual_collection_date and disposal_date < req.actual_collection_date.date():
        errors.append("Disposal date cannot be before the collection date.")
    raise_if(errors)
    cert_id = None
    if certificate:
        rec = storage_service.store_upload(session, owner_id=fresh.user_id, data=certificate[0],
                                           filename=certificate[1], purpose="disposal_certificates",
                                           images_only=False, organization_id=fresh.organization_id)
        cert_id = rec.id
    record = DisposalRecord(request_id=req.id, centre_id=req.centre_id, disposal_method=disposal_method,
                            disposal_date=disposal_date, quantity_disposed=qty, unit=unit,
                            disposal_facility=facility, reference_number=ref, certificate_file_id=cert_id,
                            recorded_by=fresh.user_id, notes=clean_text(notes, 2000), is_sample=is_sample)
    session.add(record)
    session.flush()
    collection_service._move(session, fresh, req, "DISPOSED", f"Disposal recorded – ref {ref}")
    req.disposal_status = "COMPLETED"
    collection_service._move_registrations(session, fresh, req, "DISPOSED", f"Disposed – ref {ref}")
    audit(session, fresh.user_id, "DISPOSAL_RECORDED", "disposal_record", record.id,
          after={"request": str(req.id), "method": disposal_method, "quantity": str(qty), "unit": unit,
                 "facility": facility, "reference": ref})
    notif.notify(session, req.user_id, notif.DISPOSAL_COMPLETED, "Disposal completed",
                 f"Your medicines were disposed of via {disposal_method.replace('_', ' ').lower()} "
                 f"(reference {ref}).", "collection_request", req.id)
    return record


def centre_disposal_records(session: Session, actor: Actor) -> list[dict]:
    fresh = require(session, actor, Perm.DISPOSAL_RECORD)
    rows = session.execute(select(DisposalRecord, CollectionCentre)
                           .join(CollectionCentre, CollectionCentre.id == DisposalRecord.centre_id)
                           .where(CollectionCentre.organization_id == fresh.organization_id)
                           .order_by(DisposalRecord.disposal_date.desc())).all()
    return [_to_dict(d, c) for d, c in rows]


def admin_disposal_records(session: Session, actor: Actor) -> list[dict]:
    require(session, actor, Perm.MONITOR_ALL)
    rows = session.execute(select(DisposalRecord, CollectionCentre)
                           .join(CollectionCentre, CollectionCentre.id == DisposalRecord.centre_id)
                           .order_by(DisposalRecord.disposal_date.desc())).all()
    return [_to_dict(d, c) for d, c in rows]


def _to_dict(d: DisposalRecord, c: CollectionCentre) -> dict:
    return dict(id=d.id, request_id=d.request_id, centre=c.name, method=d.disposal_method, date=d.disposal_date,
                quantity=float(d.quantity_disposed), unit=d.unit, facility=d.disposal_facility,
                reference=d.reference_number, certificate=bool(d.certificate_file_id), sample=d.is_sample)


def trace_registration(session: Session, actor: Actor, reg_id: uuid.UUID) -> dict:
    """Full chain: household registration → collection request → events → disposal record."""
    fresh = require_active(session, actor)
    reg = session.get(MedicineRegistration, reg_id)
    if reg is None:
        raise NotFound("Registration not found.")
    allowed = (reg.user_id == fresh.user_id or fresh.can(Perm.MONITOR_ALL)
               or (fresh.can(Perm.CENTRE_OPERATE) and collection_service.centre_handles_registration(session, fresh, reg.id)))
    if not allowed:
        raise PermissionDenied("You do not have access to this record.")
    items = session.scalars(select(CollectionItem).where(CollectionItem.registration_id == reg.id)).all()
    chain = []
    for it in items:
        req = session.get(CollectionRequest, it.request_id)
        events = session.scalars(select(CollectionEvent).where(CollectionEvent.request_id == req.id)
                                 .order_by(CollectionEvent.created_at)).all()
        disposals = session.scalars(select(DisposalRecord).where(DisposalRecord.request_id == req.id)).all()
        chain.append(dict(
            request_id=req.id, centre=req.centre.name, status=req.status, quantity_received=it.quantity_received,
            events=[dict(event=e.event_type, to=e.to_status, at=e.created_at, notes=e.notes) for e in events],
            disposals=[dict(method=d.disposal_method, date=d.disposal_date, reference=d.reference_number,
                            facility=d.disposal_facility, quantity=float(d.quantity_disposed), unit=d.unit)
                       for d in disposals]))
    return dict(registration_id=reg.id, medicine=reg.medicine_name, status=reg.status, chain=chain)
