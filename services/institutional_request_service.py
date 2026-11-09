"""Institutional requirements (NGOs / healthcare organizations) and the matching engine.

Matching produces *recommendations for authorized review only*. It never
reserves, allocates or transfers inventory, and it never considers household
registrations.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import InstitutionalRequest, InstitutionalRequestItem, InventoryBatch, Medicine, Organization
from services import notification_service as notif
from services.errors import NotFound, PermissionDenied, SafetyViolation, ValidationError, WorkflowError
from services.permission_service import Actor, active_authorizations, org_is_verified, require
from utils.audit_logger import audit
from utils.constants import EXCLUDED_FROM_PILOT, MEDICINE_HANDLING_AUTHORIZATIONS, Perm, Unit
from utils.date_utils import today
from utils.validators import check_choice, check_quantity, clean_text, raise_if, require as req_field

MIN_REMAINING_SHELF_DAYS = 90
MATCHING_VERSION = "1.0.0"


def _verified_org(session: Session, fresh: Actor) -> Organization:
    if fresh.organization_id is None:
        raise PermissionDenied("Your account is not linked to an organization.")
    org = session.get(Organization, fresh.organization_id)
    if not org_is_verified(org):
        raise PermissionDenied("Only verified organizations can submit institutional requests.")
    return org


def create_request(session: Session, actor: Actor, *, required_category: str, medicine_name: str,
                   requested_quantity, unit: str, purpose: str, medicine_id: uuid.UUID | None = None,
                   required_documentation: str | None = None, submit: bool = True,
                   is_sample: bool = False) -> InstitutionalRequest:
    fresh = require(session, actor, Perm.INSTITUTIONAL_REQUEST_MANAGE)
    org = _verified_org(session, fresh)
    errors: list[str] = []
    cat = clean_text(required_category, 100)
    name = clean_text(medicine_name, 250)
    purpose_c = clean_text(purpose, 2000)
    req_field(cat, "Required inventory category", errors)
    req_field(name, "Medicine identification", errors)
    req_field(purpose_c, "Purpose", errors)
    qty = check_quantity(requested_quantity, errors, "Requested quantity")
    check_choice(unit, Unit.values(), errors, "Unit")
    raise_if(errors)
    if medicine_id:
        med = session.get(Medicine, medicine_id)
        if med is None:
            raise NotFound("Medicine not found.")
        if med.restricted_classification in EXCLUDED_FROM_PILOT:
            raise SafetyViolation("Restricted products are excluded from the pilot.")
    r = InstitutionalRequest(organization_id=org.id, required_category=cat, medicine_id=medicine_id,
                             medicine_name=name, requested_quantity=qty, unit=unit, purpose=purpose_c,
                             required_documentation=clean_text(required_documentation, 2000),
                             status="SUBMITTED" if submit else "DRAFT", created_by=fresh.user_id, is_sample=is_sample)
    session.add(r)
    session.flush()
    audit(session, fresh.user_id, "INSTITUTIONAL_REQUEST_CREATED", "institutional_request", r.id,
          after={"medicine": name, "quantity": str(qty), "status": r.status})
    return r


def _own_request(session: Session, fresh: Actor, req_id: uuid.UUID) -> InstitutionalRequest:
    r = session.get(InstitutionalRequest, req_id)
    if r is None:
        raise NotFound("Request not found.")
    if r.organization_id != fresh.organization_id:
        raise PermissionDenied("You can only access your own organization's requests.")
    return r


def submit_request(session: Session, actor: Actor, req_id: uuid.UUID) -> InstitutionalRequest:
    fresh = require(session, actor, Perm.INSTITUTIONAL_REQUEST_MANAGE)
    _verified_org(session, fresh)
    r = _own_request(session, fresh, req_id)
    if r.status != "DRAFT":
        raise WorkflowError("Only drafts can be submitted.")
    r.status = "SUBMITTED"
    audit(session, fresh.user_id, "INSTITUTIONAL_REQUEST_SUBMITTED", "institutional_request", r.id)
    return r


def cancel_request(session: Session, actor: Actor, req_id: uuid.UUID) -> InstitutionalRequest:
    fresh = require(session, actor, Perm.INSTITUTIONAL_REQUEST_MANAGE)
    r = _own_request(session, fresh, req_id)
    if r.status in {"CLOSED", "CANCELLED"}:
        raise WorkflowError("Request is already closed.")
    r.status = "CANCELLED"
    for item in session.scalars(select(InstitutionalRequestItem)
                                .where(InstitutionalRequestItem.institutional_request_id == r.id)):
        if item.status in {"INTEREST_EXPRESSED", "UNDER_REVIEW"}:
            item.status = "WITHDRAWN"
    audit(session, fresh.user_id, "INSTITUTIONAL_REQUEST_CANCELLED", "institutional_request", r.id)
    return r


def list_my_requests(session: Session, actor: Actor) -> list[InstitutionalRequest]:
    fresh = require(session, actor, Perm.INSTITUTIONAL_REQUEST_MANAGE)
    if fresh.organization_id is None:
        return []
    return list(session.scalars(select(InstitutionalRequest)
                                .where(InstitutionalRequest.organization_id == fresh.organization_id)
                                .order_by(InstitutionalRequest.request_date.desc())))


def _score(r: InstitutionalRequest, b: InventoryBatch, req_org: Organization) -> tuple[float, list[str]]:
    score, reasons = 0.0, []
    name = r.medicine_name.lower()
    if r.medicine_id and r.medicine_id == b.medicine_id:
        score += 40
        reasons.append("exact product match")
    elif name in b.medicine_name.lower() or (b.medicine.generic_name and name in b.medicine.generic_name.lower()):
        score += 25
        reasons.append("name / generic match")
    elif b.medicine.category and r.required_category.lower() == b.medicine.category.lower():
        score += 10
        reasons.append("category match")
    else:
        return 0.0, []
    if b.unit == r.unit:
        cover = min(float(b.quantity) / float(r.requested_quantity), 1.0)
        score += 20 * cover
        reasons.append(f"covers {cover:.0%} of requested quantity")
    days = (b.expiry_date - today()).days
    score += 20 * min(days / 365, 1.0)
    reasons.append(f"{days} days to expiry")
    if b.storage_document_file_id and b.storage_conditions:
        score += 10
        reasons.append("storage documented")
    if b.organization.district == req_org.district:
        score += 10
        reasons.append("same district")
    return round(score, 1), reasons


def recommendations(session: Session, actor: Actor, req_id: uuid.UUID, limit: int = 10) -> list[dict]:
    fresh = require(session, actor, Perm.INSTITUTIONAL_REQUEST_MANAGE)
    org = _verified_org(session, fresh)
    r = _own_request(session, fresh, req_id)
    if r.status not in {"SUBMITTED", "UNDER_REVIEW"}:
        return []
    batches = session.scalars(select(InventoryBatch).where(
        InventoryBatch.status == "APPROVED_FOR_FURTHER_AUTHORIZED_PROCESS",
        InventoryBatch.organization_id != org.id, InventoryBatch.deleted_at.is_(None),
        InventoryBatch.recall_status == "CHECKED_NO_RECALL")).all()
    out = []
    for b in batches:
        if (b.expiry_date - today()).days < MIN_REMAINING_SHELF_DAYS:
            continue
        if b.product_classification in EXCLUDED_FROM_PILOT or b.medicine.restricted_classification in EXCLUDED_FROM_PILOT:
            continue
        if not active_authorizations(session, b.organization_id, MEDICINE_HANDLING_AUTHORIZATIONS):
            continue
        s, reasons = _score(r, b, org)
        if s <= 0:
            continue
        out.append(dict(batch_id=b.id, medicine=b.medicine_name, batch=b.batch_number, donor=b.organization.name,
                        district=b.organization.district, quantity=float(b.quantity), unit=b.unit,
                        expiry=b.expiry_date, match_score=s, reasons="; ".join(reasons),
                        matching_version=MATCHING_VERSION))
    out.sort(key=lambda x: -x["match_score"])
    return out[:limit]


def express_interest(session: Session, actor: Actor, req_id: uuid.UUID, batch_id: uuid.UUID, quantity,
                     notes: str | None = None) -> InstitutionalRequestItem:
    fresh = require(session, actor, Perm.INSTITUTIONAL_REQUEST_MANAGE)
    _verified_org(session, fresh)
    r = _own_request(session, fresh, req_id)
    if r.status not in {"SUBMITTED", "UNDER_REVIEW"}:
        raise WorkflowError("Submit the request before expressing interest.")
    valid_ids = {x["batch_id"] for x in recommendations(session, actor, req_id, limit=1000)}
    if batch_id not in valid_ids:
        raise WorkflowError("This inventory is not an eligible recommendation for your request.")
    errors: list[str] = []
    qty = check_quantity(quantity, errors, "Quantity")
    raise_if(errors)
    batch = session.get(InventoryBatch, batch_id)
    if qty > Decimal(str(batch.quantity)):
        raise ValidationError("Quantity exceeds the available batch quantity.")
    dup = session.scalar(select(InstitutionalRequestItem).where(
        InstitutionalRequestItem.institutional_request_id == r.id, InstitutionalRequestItem.inventory_batch_id == batch_id))
    if dup:
        raise WorkflowError("Interest has already been expressed for this batch.")
    item = InstitutionalRequestItem(institutional_request_id=r.id, inventory_batch_id=batch_id, quantity=qty,
                                    status="INTEREST_EXPRESSED", notes=clean_text(notes, 1000), created_by=fresh.user_id)
    session.add(item)
    r.status = "UNDER_REVIEW"
    session.flush()
    audit(session, fresh.user_id, "INTEREST_EXPRESSED", "institutional_request_item", item.id,
          after={"request": str(r.id), "batch": str(batch_id), "quantity": str(qty)})
    notif.notify_role(session, ("REVIEWER",), notif.GENERAL, "Expression of interest awaiting review",
                      f"{r.organization.name} expressed interest in {batch.medicine_name} ({batch.batch_number}).",
                      "institutional_request_item", item.id)
    return item


def request_items(session: Session, actor: Actor, req_id: uuid.UUID) -> list[dict]:
    fresh = require(session, actor, Perm.INSTITUTIONAL_REQUEST_MANAGE)
    r = _own_request(session, fresh, req_id)
    items = session.scalars(select(InstitutionalRequestItem)
                            .where(InstitutionalRequestItem.institutional_request_id == r.id)).all()
    return [dict(id=i.id, medicine=i.batch.medicine_name, batch=i.batch.batch_number, donor=i.batch.organization.name,
                 quantity=float(i.quantity), unit=i.batch.unit, status=i.status) for i in items]


def admin_list_requests(session: Session, actor: Actor) -> list[dict]:
    require(session, actor, Perm.MONITOR_ALL)
    return [dict(id=r.id, organization=r.organization.name, medicine=r.medicine_name, category=r.required_category,
                 quantity=float(r.requested_quantity), unit=r.unit, status=r.status, date=r.request_date,
                 sample=r.is_sample)
            for r in session.scalars(select(InstitutionalRequest).order_by(InstitutionalRequest.request_date.desc()))]


def accepted_interests(session: Session, actor: Actor) -> list[dict]:
    """For administrators documenting transfers."""
    require(session, actor, Perm.TRANSFER_DOCUMENT)
    items = session.scalars(select(InstitutionalRequestItem)
                            .where(InstitutionalRequestItem.status == "REVIEW_ACCEPTED")).all()
    return [dict(id=i.id, batch_id=i.inventory_batch_id, medicine=i.batch.medicine_name, batch=i.batch.batch_number,
                 donor=i.batch.organization.name, recipient=i.request.organization.name, quantity=float(i.quantity),
                 unit=i.batch.unit) for i in items]
