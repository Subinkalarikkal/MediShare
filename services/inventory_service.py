"""Institutional surplus inventory.

Only identity-verified organizations holding a verified, in-date
medicine-handling authorization may submit inventory. Submission requires
complete documentation and passes a set of hard safety gates. Approval by a
reviewer means only "approved for further authorized process" – it never
authorizes a transfer by itself.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import InventoryBatch, Organization, UploadedFile
from services import notification_service as notif
from services import storage_service
from services.errors import NotFound, PermissionDenied, SafetyViolation, ValidationError, WorkflowError
from services.medicine_service import get_medicine
from services.permission_service import (
    Actor, active_authorizations, org_is_verified, require, require_active, require_org_member,
)
from utils.audit_logger import audit, snapshot
from utils.constants import (
    EXCLUDED_FROM_PILOT, INVENTORY_TRANSITIONS, MEDICINE_HANDLING_AUTHORIZATIONS, PackagingCondition, Perm,
    RecallStatus, RestrictedClass, Unit,
)
from utils.date_utils import today, utcnow
from utils.validators import (
    check_batch, check_choice, check_dates, check_quantity, clean_text, raise_if,
)

INV_FIELDS = ["medicine_name", "batch_number", "manufacturing_date", "expiry_date", "quantity", "unit",
              "original_packaging_status", "storage_conditions", "storage_history", "recall_status",
              "product_classification", "status", "reviewer_status"]
EDITABLE_STATUSES = {"DRAFT"}


def _own_verified_org(session: Session, fresh: Actor) -> Organization:
    if fresh.organization_id is None:
        raise PermissionDenied("Your account is not linked to an organization.")
    org = session.get(Organization, fresh.organization_id)
    if not org_is_verified(org):
        status = org.verification_status if org else "MISSING"
        raise PermissionDenied(f"Only verified organizations can manage institutional inventory (status: {status}).")
    return org


def _validate(session: Session, data: dict, errors: list[str]) -> dict:
    out: dict = {}
    out["batch_number"] = check_batch(data.get("batch_number"), errors, required=True)
    check_dates(data.get("manufacturing_date"), data.get("expiry_date"), errors)
    out["manufacturing_date"], out["expiry_date"] = data.get("manufacturing_date"), data.get("expiry_date")
    out["quantity"] = check_quantity(data.get("quantity"), errors, max_value=1_000_000)
    out["unit"] = check_choice(data.get("unit"), Unit.values(), errors, "Unit")
    out["original_packaging_status"] = check_choice(data.get("original_packaging_status", "UNKNOWN"),
                                                    PackagingCondition.values(), errors, "Packaging status")
    out["storage_conditions"] = clean_text(data.get("storage_conditions"), 1000)
    out["storage_history"] = clean_text(data.get("storage_history"), 3000)
    out["recall_status"] = check_choice(data.get("recall_status", "NOT_CHECKED"), RecallStatus.values(),
                                        errors, "Recall verification status")
    out["product_classification"] = check_choice(data.get("product_classification", "NONE"),
                                                 RestrictedClass.values(), errors, "Product classification")
    return out


def create_batch(session: Session, actor: Actor, *, medicine_id: uuid.UUID, is_sample: bool = False,
                 **data) -> InventoryBatch:
    fresh = require(session, actor, Perm.INVENTORY_MANAGE)
    org = _own_verified_org(session, fresh)
    med = get_medicine(session, medicine_id)
    if med.restricted_classification in EXCLUDED_FROM_PILOT:
        raise SafetyViolation("Restricted, controlled and cold-chain products are excluded from the pilot.")
    errors: list[str] = []
    clean = _validate(session, data, errors)
    raise_if(errors)
    if clean["product_classification"] in EXCLUDED_FROM_PILOT:
        raise SafetyViolation("Restricted, controlled and cold-chain products are excluded from the pilot.")
    dup = session.scalar(select(InventoryBatch).where(InventoryBatch.organization_id == org.id,
                                                      InventoryBatch.medicine_id == med.id,
                                                      InventoryBatch.batch_number == clean["batch_number"]))
    if dup:
        raise ValidationError("This batch is already registered for your organization.")
    batch = InventoryBatch(organization_id=org.id, medicine_id=med.id, medicine_name=med.name, status="DRAFT",
                           created_by=fresh.user_id, is_sample=is_sample, **clean)
    session.add(batch)
    session.flush()
    audit(session, fresh.user_id, "INVENTORY_CREATED", "inventory_batch", batch.id, after=snapshot(batch, INV_FIELDS))
    return batch


def _get_batch(session: Session, batch_id: uuid.UUID) -> InventoryBatch:
    b = session.get(InventoryBatch, batch_id)
    if b is None or b.deleted_at is not None:
        raise NotFound("Inventory batch not found.")
    return b


def _own_batch(session: Session, fresh: Actor, batch_id: uuid.UUID) -> InventoryBatch:
    b = _get_batch(session, batch_id)
    if fresh.organization_id != b.organization_id:
        raise PermissionDenied("You can only access your own organization's inventory.")
    return b


def update_draft(session: Session, actor: Actor, batch_id: uuid.UUID, **data) -> InventoryBatch:
    fresh = require(session, actor, Perm.INVENTORY_MANAGE)
    _own_verified_org(session, fresh)
    b = _own_batch(session, fresh, batch_id)
    if b.status not in EDITABLE_STATUSES:
        raise WorkflowError("Only draft inventory can be edited.")
    errors: list[str] = []
    clean = _validate(session, data, errors)
    raise_if(errors)
    if clean["product_classification"] in EXCLUDED_FROM_PILOT:
        raise SafetyViolation("Restricted, controlled and cold-chain products are excluded from the pilot.")
    before = snapshot(b, INV_FIELDS)
    for k, v in clean.items():
        setattr(b, k, v)
    audit(session, fresh.user_id, "INVENTORY_UPDATED", "inventory_batch", b.id, before, snapshot(b, INV_FIELDS))
    return b


def attach_document(session: Session, actor: Actor, batch_id: uuid.UUID, kind: str, data: bytes,
                    filename: str) -> UploadedFile:
    """kind: 'source' (purchase / source documentation) or 'storage' (storage records)."""
    fresh = require(session, actor, Perm.INVENTORY_MANAGE)
    _own_verified_org(session, fresh)
    b = _own_batch(session, fresh, batch_id)
    if b.status not in EDITABLE_STATUSES | {"ON_HOLD"}:
        raise WorkflowError("Documents can only be attached to draft or on-hold inventory.")
    if kind not in {"source", "storage"}:
        raise ValidationError("Unknown document kind.")
    rec = storage_service.store_upload(session, owner_id=fresh.user_id, data=data, filename=filename,
                                       purpose="inventory_documents", images_only=False,
                                       organization_id=b.organization_id)
    if kind == "source":
        b.source_document_file_id = rec.id
    else:
        b.storage_document_file_id = rec.id
    audit(session, fresh.user_id, "INVENTORY_DOCUMENT_ATTACHED", "inventory_batch", b.id,
          after={"kind": kind, "file": rec.original_filename, "sha256": rec.sha256})
    return rec


def submission_blockers(session: Session, b: InventoryBatch) -> list[str]:
    """Every reason this batch cannot be submitted for review (empty list = OK)."""
    blockers = []
    org = session.get(Organization, b.organization_id)
    if not org_is_verified(org):
        blockers.append("Organization is not verified.")
    elif not active_authorizations(session, org.id, MEDICINE_HANDLING_AUTHORIZATIONS):
        blockers.append("No verified, in-date drug licence / medicine-handling authorization on file.")
    if b.expiry_date <= today():
        blockers.append("Batch is expired – it must go to the disposal workflow, not review.")
    if b.original_packaging_status != PackagingCondition.SEALED_ORIGINAL.value:
        blockers.append("Only sealed, original-packaging inventory can enter review.")
    if b.product_classification in EXCLUDED_FROM_PILOT:
        blockers.append("Restricted / cold-chain products are excluded from the pilot.")
    med = b.medicine or get_medicine(session, b.medicine_id)
    if med.restricted_classification in EXCLUDED_FROM_PILOT:
        blockers.append("The medicine is classified as restricted in the master database.")
    if b.recall_status == RecallStatus.RECALLED.value:
        blockers.append("Batch is subject to a recall.")
    elif b.recall_status != RecallStatus.CHECKED_NO_RECALL.value:
        blockers.append("Recall status has not been checked.")
    if not b.source_document_file_id:
        blockers.append("Purchase / source documentation is missing.")
    if not b.storage_document_file_id:
        blockers.append("Storage documentation is missing.")
    if not b.storage_conditions:
        blockers.append("Storage conditions are missing.")
    return blockers


def submit_batch(session: Session, actor: Actor, batch_id: uuid.UUID) -> InventoryBatch:
    fresh = require(session, actor, Perm.INVENTORY_MANAGE)
    _own_verified_org(session, fresh)
    b = _own_batch(session, fresh, batch_id)
    if b.status != "DRAFT":
        raise WorkflowError("Only draft inventory can be submitted.")
    blockers = submission_blockers(session, b)
    if blockers:
        if b.expiry_date <= today():
            raise SafetyViolation("; ".join(blockers))
        missing_docs = [x for x in blockers if "documentation" in x.lower()]
        if missing_docs:
            notif.notify_organization(session, b.organization_id, notif.DOCUMENTATION_MISSING,
                                      f"Documentation missing: {b.medicine_name} ({b.batch_number})",
                                      "; ".join(missing_docs), "inventory_batch", b.id)
        raise WorkflowError("Cannot submit: " + "; ".join(blockers))
    auth = active_authorizations(session, b.organization_id, MEDICINE_HANDLING_AUTHORIZATIONS)[0]
    b.regulatory_authorization_id = auth.id
    b.submitted_at = utcnow()
    transition_internal(session, fresh, b, "SUBMITTED", "Submitted for authorized review")
    notif.notify_role(session, ("REVIEWER",), notif.INVENTORY_SUBMITTED, "Inventory submitted for review",
                      f"{b.medicine_name} batch {b.batch_number} is awaiting review.", "inventory_batch", b.id)
    notif.notify_organization(session, b.organization_id, notif.INVENTORY_SUBMITTED, "Inventory submitted",
                              f"{b.medicine_name} batch {b.batch_number} was submitted for authorized review.",
                              "inventory_batch", b.id)
    return b


def transition_internal(session: Session, fresh: Actor, b: InventoryBatch, to_status: str, note: str = "") -> None:
    if to_status not in INVENTORY_TRANSITIONS.get(b.status, set()):
        raise WorkflowError(f"Inventory cannot move from {b.status} to {to_status}.")
    frm = b.status
    b.status = to_status
    audit(session, fresh.user_id, f"INVENTORY_{to_status}", "inventory_batch", b.id, {"status": frm},
          {"status": to_status}, details=note or None)


def withdraw_batch(session: Session, actor: Actor, batch_id: uuid.UUID) -> InventoryBatch:
    """Organization withdraws a submitted (not yet under review) batch back to draft."""
    fresh = require(session, actor, Perm.INVENTORY_MANAGE)
    b = _own_batch(session, fresh, batch_id)
    if b.status != "SUBMITTED":
        raise WorkflowError("Only submitted batches that are not yet under review can be withdrawn.")
    transition_internal(session, fresh, b, "DRAFT", "Withdrawn by organization")
    return b


def close_batch(session: Session, actor: Actor, batch_id: uuid.UUID, note: str) -> InventoryBatch:
    fresh = require(session, actor, Perm.INVENTORY_MANAGE)
    b = _own_batch(session, fresh, batch_id)
    transition_internal(session, fresh, b, "CLOSED", note or "Closed by organization")
    return b


def list_org_inventory(session: Session, actor: Actor, statuses: list[str] | None = None) -> list[InventoryBatch]:
    fresh = require(session, actor, Perm.INVENTORY_MANAGE)
    if fresh.organization_id is None:
        return []
    q = select(InventoryBatch).where(InventoryBatch.organization_id == fresh.organization_id,
                                     InventoryBatch.deleted_at.is_(None))
    if statuses:
        q = q.where(InventoryBatch.status.in_(statuses))
    return list(session.scalars(q.order_by(InventoryBatch.expiry_date)))


def get_batch(session: Session, actor: Actor, batch_id: uuid.UUID) -> InventoryBatch:
    fresh = require_active(session, actor)
    b = _get_batch(session, batch_id)
    if fresh.can(Perm.REVIEW_INVENTORY) or fresh.can(Perm.MONITOR_ALL):
        return b
    require_org_member(fresh, b.organization_id)
    return b


def admin_list_inventory(session: Session, actor: Actor, status: str | None = None) -> list[dict]:
    require(session, actor, Perm.MONITOR_ALL)
    q = select(InventoryBatch, Organization).join(Organization, Organization.id == InventoryBatch.organization_id)\
        .where(InventoryBatch.deleted_at.is_(None))
    if status:
        q = q.where(InventoryBatch.status == status)
    return [dict(id=b.id, organization=o.name, medicine=b.medicine_name, batch=b.batch_number,
                 expiry=b.expiry_date, days_left=(b.expiry_date - today()).days, quantity=float(b.quantity),
                 unit=b.unit, status=b.status, recall=b.recall_status, sample=b.is_sample)
            for b, o in session.execute(q.order_by(InventoryBatch.expiry_date)).all()]


def quarantine_expired(session: Session, system_actor: Actor) -> int:
    """Move expired batches in active statuses to QUARANTINED (→ disposal workflow)."""
    active = ["SUBMITTED", "UNDER_REVIEW", "ON_HOLD", "APPROVED_FOR_FURTHER_AUTHORIZED_PROCESS"]
    rows = session.scalars(select(InventoryBatch).where(InventoryBatch.status.in_(active),
                                                        InventoryBatch.expiry_date <= today())).all()
    for b in rows:
        transition_internal(session, system_actor, b, "QUARANTINED",
                            "Expired – removed from review and directed to the disposal workflow")
        b.reviewer_status = "EXPIRED_QUARANTINED"
        notif.notify_organization(session, b.organization_id, notif.INVENTORY_EXPIRED, "Inventory expired",
                                  f"{b.medicine_name} batch {b.batch_number} expired and was quarantined. "
                                  "Arrange authorized disposal.", "inventory_batch", b.id)
    return len(rows)
