"""Institutional inventory review by verified reviewers / pharmacists.

Every decision is a human decision by a professionally verified reviewer and
is stored as an immutable review_records row. AI output is never accepted
as a review decision.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import (
    InstitutionalRequestItem, InventoryBatch, Organization, ReviewRecord, UploadedFile,
)
from services import inventory_service
from services import notification_service as notif
from services.errors import NotFound, SafetyViolation, ValidationError, WorkflowError
from services.permission_service import Actor, require, require_verified_professional
from utils.audit_logger import audit
from utils.constants import Perm, ReviewDecision
from utils.date_utils import today

CHECKLIST_KEYS = {
    "batch_verified": "Batch number and product identity verified against documents",
    "expiry_verified": "Expiry date verified",
    "storage_verified": "Storage conditions and storage history reviewed",
    "recall_checked": "Recall status independently checked",
    "documents_verified": "Purchase / source documentation reviewed",
    "packaging_verified": "Sealed original packaging confirmed",
}

DECISION_TARGET = {
    ReviewDecision.START_REVIEW.value: "UNDER_REVIEW",
    ReviewDecision.APPROVE_FOR_FURTHER_AUTHORIZED_PROCESS.value: "APPROVED_FOR_FURTHER_AUTHORIZED_PROCESS",
    ReviewDecision.REJECT.value: "REJECTED",
    ReviewDecision.HOLD.value: "ON_HOLD",
    ReviewDecision.QUARANTINE.value: "QUARANTINED",
    ReviewDecision.RESUME.value: "UNDER_REVIEW",
}


def _reviewer(session: Session, actor: Actor) -> Actor:
    fresh = require(session, actor, Perm.REVIEW_INVENTORY)
    require_verified_professional(fresh)
    return fresh


def review_queue(session: Session, actor: Actor, statuses: list[str] | None = None) -> list[dict]:
    _reviewer(session, actor)
    statuses = statuses or ["SUBMITTED", "UNDER_REVIEW", "ON_HOLD"]
    rows = session.execute(select(InventoryBatch, Organization)
                           .join(Organization, Organization.id == InventoryBatch.organization_id)
                           .where(InventoryBatch.status.in_(statuses), InventoryBatch.deleted_at.is_(None))
                           .order_by(InventoryBatch.expiry_date)).all()
    return [dict(id=b.id, organization=o.name, district=o.district, medicine=b.medicine_name, batch=b.batch_number,
                 expiry=b.expiry_date, days_left=(b.expiry_date - today()).days, quantity=float(b.quantity),
                 unit=b.unit, status=b.status, submitted=b.submitted_at, sample=b.is_sample) for b, o in rows]


def batch_details(session: Session, actor: Actor, batch_id: uuid.UUID) -> dict:
    _reviewer(session, actor)
    b = session.get(InventoryBatch, batch_id)
    if b is None:
        raise NotFound("Batch not found.")
    org = session.get(Organization, b.organization_id)
    docs = {}
    for kind, fid in (("source", b.source_document_file_id), ("storage", b.storage_document_file_id)):
        f = session.get(UploadedFile, fid) if fid else None
        docs[kind] = dict(file_id=f.id, filename=f.original_filename, sha256=f.sha256) if f else None
    history = session.scalars(select(ReviewRecord).where(ReviewRecord.inventory_batch_id == b.id)
                              .order_by(ReviewRecord.created_at.desc())).all()
    return dict(batch=b, organization=org, medicine=b.medicine, documents=docs,
                blockers=inventory_service.submission_blockers(session, b) if b.status != "DRAFT" else [],
                history=history)


def record_decision(session: Session, actor: Actor, batch_id: uuid.UUID, decision: str, *,
                    notes: str, checklist: dict[str, bool] | None = None,
                    authorization_reference: str | None = None, ai_generated: bool = False) -> ReviewRecord:
    if ai_generated:
        raise SafetyViolation("AI output cannot be recorded as a review decision.")
    fresh = _reviewer(session, actor)
    if decision not in DECISION_TARGET:
        raise ValidationError("Unknown review decision.")
    if not notes or len(notes.strip()) < 5:
        raise ValidationError("Review notes (at least 5 characters) are required.")
    b = session.get(InventoryBatch, batch_id)
    if b is None:
        raise NotFound("Batch not found.")
    if b.organization_id == fresh.organization_id and fresh.organization_id is not None:
        raise WorkflowError("Reviewers cannot review their own organization's inventory (conflict of interest).")
    checklist = {k: bool((checklist or {}).get(k)) for k in CHECKLIST_KEYS}
    target = DECISION_TARGET[decision]
    if decision == ReviewDecision.APPROVE_FOR_FURTHER_AUTHORIZED_PROCESS.value:
        if not all(checklist.values()):
            missing = [CHECKLIST_KEYS[k] for k, v in checklist.items() if not v]
            raise WorkflowError("Complete every checklist item before approval: " + "; ".join(missing))
        blockers = inventory_service.submission_blockers(session, b)
        if blockers:
            raise SafetyViolation("Cannot approve: " + "; ".join(blockers))
        if not authorization_reference or not authorization_reference.strip():
            raise ValidationError("An authorization reference is required for approval.")
    if decision == ReviewDecision.RESUME.value and b.status not in {"ON_HOLD", "QUARANTINED"}:
        raise WorkflowError("Only held or quarantined inventory can be resumed.")
    frm = b.status
    inventory_service.transition_internal(session, fresh, b, target, notes.strip())
    b.reviewer_status = decision
    rec = ReviewRecord(inventory_batch_id=b.id, reviewer_id=fresh.user_id, decision=decision, from_status=frm,
                       to_status=target, checklist=checklist,
                       authorization_reference=(authorization_reference or "").strip() or None, notes=notes.strip())
    session.add(rec)
    session.flush()
    audit(session, fresh.user_id, f"REVIEW_{decision}", "inventory_batch", b.id, {"status": frm},
          {"status": target, "review_record": str(rec.id)}, details=notes.strip())
    if decision != ReviewDecision.START_REVIEW.value:
        extra = (" Approval does not authorize any transfer; separate documented regulatory and quality "
                 "authorization is still required.") if target.startswith("APPROVED") else ""
        notif.notify_organization(session, b.organization_id, notif.REVIEW_DECISION,
                                  f"Review decision: {target.replace('_', ' ').title()}",
                                  f"{b.medicine_name} batch {b.batch_number}: {notes.strip()}.{extra}",
                                  "inventory_batch", b.id)
    return rec


def review_interest(session: Session, actor: Actor, item_id: uuid.UUID, accept: bool, notes: str) -> InstitutionalRequestItem:
    """Reviewer assesses an NGO expression of interest against an approved batch."""
    fresh = _reviewer(session, actor)
    if not notes or len(notes.strip()) < 5:
        raise ValidationError("Review notes are required.")
    item = session.get(InstitutionalRequestItem, item_id)
    if item is None:
        raise NotFound("Expression of interest not found.")
    if item.status not in {"INTEREST_EXPRESSED", "UNDER_REVIEW"}:
        raise WorkflowError(f"This expression of interest is already {item.status}.")
    batch = session.get(InventoryBatch, item.inventory_batch_id)
    if accept and batch.status != "APPROVED_FOR_FURTHER_AUTHORIZED_PROCESS":
        raise WorkflowError("The inventory batch is no longer approved for further process.")
    if accept and batch.expiry_date <= today():
        raise SafetyViolation("Expired inventory cannot proceed.")
    frm = item.status
    item.status = "REVIEW_ACCEPTED" if accept else "REVIEW_DECLINED"
    session.add(ReviewRecord(inventory_batch_id=batch.id, institutional_request_item_id=item.id,
                             reviewer_id=fresh.user_id, decision=f"INTEREST_{item.status}", from_status=frm,
                             to_status=item.status, notes=notes.strip()))
    audit(session, fresh.user_id, f"INTEREST_{item.status}", "institutional_request_item", item.id,
          {"status": frm}, {"status": item.status}, details=notes.strip())
    notif.notify_organization(session, item.request.organization_id, notif.REVIEW_DECISION,
                              f"Expression of interest {item.status.replace('_', ' ').lower()}",
                              f"{batch.medicine_name}: {notes.strip()}", "institutional_request_item", item.id)
    return item


def pending_interests(session: Session, actor: Actor) -> list[dict]:
    _reviewer(session, actor)
    items = session.scalars(select(InstitutionalRequestItem)
                            .where(InstitutionalRequestItem.status.in_(["INTEREST_EXPRESSED", "UNDER_REVIEW"]))).all()
    return [dict(id=i.id, requesting_org=i.request.organization.name, purpose=i.request.purpose,
                 medicine=i.batch.medicine_name, batch=i.batch.batch_number, donor=i.batch.organization.name,
                 quantity=float(i.quantity), unit=i.batch.unit, expiry=i.batch.expiry_date, status=i.status)
            for i in items]


def my_review_history(session: Session, actor: Actor) -> list[dict]:
    fresh = require(session, actor, Perm.REVIEW_INVENTORY)
    rows = session.execute(select(ReviewRecord, InventoryBatch)
                           .join(InventoryBatch, InventoryBatch.id == ReviewRecord.inventory_batch_id)
                           .where(ReviewRecord.reviewer_id == fresh.user_id)
                           .order_by(ReviewRecord.created_at.desc())).all()
    return [dict(at=r.created_at, medicine=b.medicine_name, batch=b.batch_number, decision=r.decision,
                 from_status=r.from_status, to_status=r.to_status, reference=r.authorization_reference or "",
                 notes=r.notes) for r, b in rows]


def org_review_history(session: Session, actor: Actor) -> list[dict]:
    """Organization view of review decisions on its own inventory."""
    fresh = require(session, actor, Perm.INVENTORY_MANAGE)
    rows = session.execute(select(ReviewRecord, InventoryBatch)
                           .join(InventoryBatch, InventoryBatch.id == ReviewRecord.inventory_batch_id)
                           .where(InventoryBatch.organization_id == fresh.organization_id)
                           .order_by(ReviewRecord.created_at.desc())).all()
    return [dict(at=r.created_at, medicine=b.medicine_name, batch=b.batch_number, decision=r.decision,
                 to_status=r.to_status, notes=r.notes) for r, b in rows]


def admin_review_overview(session: Session, actor: Actor) -> list[dict]:
    require(session, actor, Perm.MONITOR_ALL)
    rows = session.execute(select(ReviewRecord, InventoryBatch)
                           .join(InventoryBatch, InventoryBatch.id == ReviewRecord.inventory_batch_id)
                           .order_by(ReviewRecord.created_at.desc()).limit(1000)).all()
    return [dict(at=r.created_at, medicine=b.medicine_name, batch=b.batch_number, decision=r.decision,
                 to_status=r.to_status, reference=r.authorization_reference or "", notes=r.notes) for r, b in rows]
