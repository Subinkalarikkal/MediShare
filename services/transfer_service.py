"""Documentation of authorized institutional transfers.

A transfer record can only reference an institutional inventory batch (the
table has no column that could point at a household registration). A
transfer can only become AUTHORIZED once regulatory, quality and professional
authorizations are all recorded, and only for an in-date, approved batch
moving between two verified, licensed organizations. MediShare documents
these steps – it does not itself grant legal authorization.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import (
    InstitutionalRequestItem, InventoryBatch, MedicineRegistration, Organization, TransferAuthorization,
    TransferRecord,
)
from services import notification_service as notif
from services.errors import NotFound, PermissionDenied, SafetyViolation, ValidationError, WorkflowError
from services.permission_service import (
    Actor, active_authorizations, org_is_verified, require, require_active, require_verified_professional,
)
from utils.audit_logger import audit
from utils.constants import (
    EXCLUDED_FROM_PILOT, MEDICINE_HANDLING_AUTHORIZATIONS, REQUIRED_TRANSFER_AUTHORIZATIONS, Perm,
    TransferAuthorizationType,
)
from utils.date_utils import today, utcnow
from utils.validators import check_quantity, clean_text, raise_if


def _check_batch_eligible(session: Session, batch: InventoryBatch) -> None:
    if batch.status != "APPROVED_FOR_FURTHER_AUTHORIZED_PROCESS":
        raise SafetyViolation("Only reviewer-approved inventory can be documented for transfer.")
    if batch.expiry_date <= today():
        raise SafetyViolation("Expired inventory can never enter a transfer workflow.")
    if batch.product_classification in EXCLUDED_FROM_PILOT or \
            batch.medicine.restricted_classification in EXCLUDED_FROM_PILOT:
        raise SafetyViolation("Restricted products are excluded from the pilot.")
    if batch.recall_status != "CHECKED_NO_RECALL":
        raise SafetyViolation("Recall status must be checked with no recall before transfer.")


def _check_orgs(session: Session, from_org_id: uuid.UUID, to_org_id: uuid.UUID) -> None:
    if from_org_id == to_org_id:
        raise ValidationError("Source and recipient organizations must be different.")
    for oid, label in ((from_org_id, "Source"), (to_org_id, "Recipient")):
        org = session.get(Organization, oid)
        if not org_is_verified(org):
            raise SafetyViolation(f"{label} organization is not verified.")
    if not active_authorizations(session, from_org_id, MEDICINE_HANDLING_AUTHORIZATIONS):
        raise SafetyViolation("Source organization lacks a verified, in-date medicine-handling authorization.")
    if not active_authorizations(session, to_org_id, MEDICINE_HANDLING_AUTHORIZATIONS):
        raise SafetyViolation("Recipient organization lacks a verified, in-date medicine-handling authorization.")


def create_transfer(session: Session, actor: Actor, *, inventory_batch_id: uuid.UUID,
                    institutional_request_item_id: uuid.UUID, quantity, notes: str | None = None) -> TransferRecord:
    fresh = require(session, actor, Perm.TRANSFER_DOCUMENT)
    # Explicit guard: a household registration id must never be used here.
    if session.get(MedicineRegistration, inventory_batch_id) is not None:
        raise SafetyViolation("Household medicine registrations can never enter institutional transfer workflows.")
    batch = session.get(InventoryBatch, inventory_batch_id)
    if batch is None:
        raise NotFound("Inventory batch not found.")
    _check_batch_eligible(session, batch)
    item = session.get(InstitutionalRequestItem, institutional_request_item_id)
    if item is None or item.inventory_batch_id != batch.id:
        raise ValidationError("The expression of interest does not match this batch.")
    if item.status != "REVIEW_ACCEPTED":
        raise WorkflowError("The expression of interest must be accepted by a reviewer first.")
    to_org_id = item.request.organization_id
    _check_orgs(session, batch.organization_id, to_org_id)
    errors: list[str] = []
    qty = check_quantity(quantity, errors, "Transfer quantity")
    raise_if(errors)
    committed = session.scalar(select(func.coalesce(func.sum(TransferRecord.quantity), 0)).where(
        TransferRecord.inventory_batch_id == batch.id, TransferRecord.status != "CANCELLED")) or 0
    if Decimal(str(committed)) + qty > Decimal(str(batch.quantity)):
        raise ValidationError("Transfer quantity exceeds the remaining batch quantity.")
    t = TransferRecord(inventory_batch_id=batch.id, from_organization_id=batch.organization_id,
                       to_organization_id=to_org_id, institutional_request_item_id=item.id, quantity=qty,
                       unit=batch.unit, status="PENDING_AUTHORIZATION", created_by=fresh.user_id,
                       notes=clean_text(notes, 2000))
    session.add(t)
    session.flush()
    audit(session, fresh.user_id, "TRANSFER_DOCUMENTED", "transfer_record", t.id,
          after={"batch": str(batch.id), "to": str(to_org_id), "quantity": str(qty)})
    return t


def add_authorization(session: Session, actor: Actor, transfer_id: uuid.UUID, authorization_type: str,
                      reference_number: str, issuing_authority: str | None = None,
                      notes: str | None = None, ai_generated: bool = False) -> TransferAuthorization:
    """REGULATORY is recorded by an administrator; QUALITY and PROFESSIONAL by a verified reviewer."""
    if ai_generated:
        raise SafetyViolation("AI output cannot be recorded as an authorization.")
    if authorization_type not in TransferAuthorizationType.values():
        raise ValidationError("Unknown authorization type.")
    if authorization_type == "REGULATORY":
        fresh = require(session, actor, Perm.TRANSFER_DOCUMENT)
    else:
        fresh = require(session, actor, Perm.TRANSFER_AUTHORIZE_QUALITY)
        require_verified_professional(fresh)
    ref = clean_text(reference_number, 150)
    if not ref:
        raise ValidationError("An authorization reference number is required.")
    t = session.get(TransferRecord, transfer_id)
    if t is None:
        raise NotFound("Transfer record not found.")
    if t.status != "PENDING_AUTHORIZATION":
        raise WorkflowError("Authorizations can only be added while the transfer is pending authorization.")
    exists = session.scalar(select(TransferAuthorization).where(TransferAuthorization.transfer_id == t.id,
                                                               TransferAuthorization.authorization_type == authorization_type))
    if exists:
        raise WorkflowError(f"A {authorization_type} authorization is already recorded.")
    a = TransferAuthorization(transfer_id=t.id, authorization_type=authorization_type, reference_number=ref,
                              issuing_authority=clean_text(issuing_authority, 200), authorized_by=fresh.user_id,
                              notes=clean_text(notes, 2000))
    session.add(a)
    session.flush()
    audit(session, fresh.user_id, f"TRANSFER_AUTH_{authorization_type}", "transfer_record", t.id,
          after={"reference": ref})
    return a


def missing_authorizations(session: Session, t: TransferRecord) -> set[str]:
    have = set(session.scalars(select(TransferAuthorization.authorization_type)
                               .where(TransferAuthorization.transfer_id == t.id)))
    return REQUIRED_TRANSFER_AUTHORIZATIONS - have


def mark_authorized(session: Session, actor: Actor, transfer_id: uuid.UUID) -> TransferRecord:
    fresh = require(session, actor, Perm.TRANSFER_DOCUMENT)
    t = session.get(TransferRecord, transfer_id)
    if t is None:
        raise NotFound("Transfer record not found.")
    if t.status != "PENDING_AUTHORIZATION":
        raise WorkflowError(f"Transfer is {t.status}.")
    missing = missing_authorizations(session, t)
    if missing:
        raise SafetyViolation("Missing required authorizations: " + ", ".join(sorted(missing)))
    batch = session.get(InventoryBatch, t.inventory_batch_id)
    _check_batch_eligible(session, batch)
    _check_orgs(session, t.from_organization_id, t.to_organization_id)
    t.status = "AUTHORIZED"
    audit(session, fresh.user_id, "TRANSFER_AUTHORIZED", "transfer_record", t.id,
          {"status": "PENDING_AUTHORIZATION"}, {"status": "AUTHORIZED"})
    for oid in (t.from_organization_id, t.to_organization_id):
        notif.notify_organization(session, oid, notif.REVIEW_DECISION, "Transfer documentation authorized",
                                  f"{batch.medicine_name} batch {batch.batch_number}: all required authorizations "
                                  "are on record.", "transfer_record", t.id)
    return t


def mark_completed(session: Session, actor: Actor, transfer_id: uuid.UUID, notes: str) -> TransferRecord:
    fresh = require(session, actor, Perm.TRANSFER_DOCUMENT)
    t = session.get(TransferRecord, transfer_id)
    if t is None:
        raise NotFound("Transfer record not found.")
    if t.status != "AUTHORIZED":
        raise SafetyViolation("Only fully authorized transfers can be marked completed.")
    t.status, t.completed_at = "COMPLETED", utcnow()
    t.notes = ((t.notes or "") + f"\nCompletion: {clean_text(notes, 1000) or ''}").strip()
    audit(session, fresh.user_id, "TRANSFER_COMPLETED", "transfer_record", t.id,
          {"status": "AUTHORIZED"}, {"status": "COMPLETED"}, details=notes)
    return t


def cancel_transfer(session: Session, actor: Actor, transfer_id: uuid.UUID, reason: str) -> TransferRecord:
    fresh = require(session, actor, Perm.TRANSFER_DOCUMENT)
    t = session.get(TransferRecord, transfer_id)
    if t is None:
        raise NotFound("Transfer record not found.")
    if t.status in {"COMPLETED", "CANCELLED"}:
        raise WorkflowError("Closed transfer records cannot be modified.")
    frm = t.status
    t.status = "CANCELLED"
    audit(session, fresh.user_id, "TRANSFER_CANCELLED", "transfer_record", t.id, {"status": frm},
          {"status": "CANCELLED"}, details=reason)
    return t


def list_transfers(session: Session, actor: Actor) -> list[dict]:
    """Admins/reviewers see all; organizations see transfers involving them only."""
    fresh = require_active(session, actor)
    q = select(TransferRecord)
    if not (fresh.can(Perm.TRANSFER_DOCUMENT) or fresh.can(Perm.REVIEW_INVENTORY)):
        if fresh.organization_id is None:
            raise PermissionDenied("No organization linked to your account.")
        q = q.where((TransferRecord.from_organization_id == fresh.organization_id)
                    | (TransferRecord.to_organization_id == fresh.organization_id))
    out = []
    for t in session.scalars(q.order_by(TransferRecord.created_at.desc())):
        b = session.get(InventoryBatch, t.inventory_batch_id)
        out.append(dict(id=t.id, medicine=b.medicine_name, batch=b.batch_number,
                        from_org=session.get(Organization, t.from_organization_id).name,
                        to_org=session.get(Organization, t.to_organization_id).name, quantity=float(t.quantity),
                        unit=t.unit, status=t.status,
                        missing_authorizations=", ".join(sorted(missing_authorizations(session, t))) or "None",
                        created=t.created_at, completed=t.completed_at))
    return out
