"""Collection centre directory and household collection-request workflow."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import (
    CollectionCentre, CollectionEvent, CollectionItem, CollectionRequest, MedicineRegistration, Organization,
)
from services import medicine_service
from services import notification_service as notif
from services.errors import NotFound, PermissionDenied, SafetyViolation, ValidationError, WorkflowError
from services.permission_service import (
    Actor, active_authorizations, org_is_verified, require, require_active,
)
from utils.audit_logger import audit, snapshot
from utils.constants import (
    ACTIVE_COLLECTION_STATUSES, COLLECTION_AUTHORIZATIONS, COLLECTION_TRANSITIONS, KERALA_DISTRICTS,
    CollectionMethod, Perm, WasteCategory,
)
from utils.date_utils import today, utcnow
from utils.location_utils import district_centroid, haversine_km
from utils.validators import (
    check_choice, check_email, check_phone, check_pin, check_quantity, clean_text, raise_if, require as req_field,
)

CENTRE_FIELDS = ["name", "address", "latitude", "longitude", "district", "pin_code", "contact_phone",
                 "contact_email", "operating_hours", "accepted_categories", "collection_available",
                 "service_radius_km", "daily_capacity", "status", "verification_status"]


# ================================================================ centres
def _validate_centre(data: dict, errors: list[str]) -> dict:
    out: dict = {}
    out["name"] = clean_text(data.get("name"), 250)
    req_field(out["name"], "Centre name", errors)
    out["address"] = clean_text(data.get("address"), 1000)
    req_field(out["address"], "Address", errors)
    out["district"] = check_choice(data.get("district"), KERALA_DISTRICTS, errors, "District")
    out["pin_code"] = check_pin(data.get("pin_code"), errors)
    out["contact_phone"] = check_phone(data.get("contact_phone"), errors)
    email = data.get("contact_email")
    out["contact_email"] = check_email(email, errors, "Contact email") if email else None
    out["operating_hours"] = clean_text(data.get("operating_hours"), 200)
    cats = list(data.get("accepted_categories") or [])
    if not cats:
        errors.append("Select at least one accepted waste category.")
    bad = [c for c in cats if c not in WasteCategory.values()]
    if bad:
        errors.append(f"Unknown waste categories: {', '.join(bad)}")
    out["accepted_categories"] = cats
    for key, lo, hi in (("latitude", -90, 90), ("longitude", -180, 180)):
        v = data.get(key)
        if v in (None, ""):
            out[key] = None
            continue
        try:
            v = float(v)
            if not lo <= v <= hi:
                raise ValueError
            out[key] = v
        except (TypeError, ValueError):
            errors.append(f"{key.title()} is invalid.")
    try:
        out["service_radius_km"] = float(data.get("service_radius_km", 10))
        if not 0 < out["service_radius_km"] <= 200:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("Service radius must be between 0 and 200 km.")
    try:
        out["daily_capacity"] = int(data.get("daily_capacity", 20))
        if out["daily_capacity"] < 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("Daily capacity must be a non-negative whole number.")
    out["collection_available"] = bool(data.get("collection_available", True))
    return out


def register_centre(session: Session, actor: Actor, is_sample: bool = False, **data) -> CollectionCentre:
    fresh = require(session, actor, Perm.CENTRE_OPERATE, allow_pending=True)
    if fresh.organization_id is None:
        raise WorkflowError("Register your organization before adding a collection centre.")
    errors: list[str] = []
    clean = _validate_centre(data, errors)
    raise_if(errors)
    if clean["latitude"] is None:
        c = district_centroid(clean["district"])
        if c:
            clean["latitude"], clean["longitude"] = c
    centre = CollectionCentre(organization_id=fresh.organization_id, status="INACTIVE",
                              verification_status="PENDING", is_sample=is_sample, **clean)
    session.add(centre)
    session.flush()
    audit(session, fresh.user_id, "CENTRE_REGISTERED", "collection_centre", centre.id,
          after=snapshot(centre, CENTRE_FIELDS))
    notif.notify_role(session, ("ADMIN", "SUPER_ADMIN"), notif.GENERAL, "Collection centre awaiting verification",
                      f"{centre.name} ({centre.district}) was registered.", "collection_centre", centre.id)
    return centre


def _own_centre(session: Session, fresh: Actor, centre_id: uuid.UUID) -> CollectionCentre:
    c = session.get(CollectionCentre, centre_id)
    if c is None or c.deleted_at is not None:
        raise NotFound("Collection centre not found.")
    if c.organization_id != fresh.organization_id:
        raise PermissionDenied("You can only manage your own organization's centres.")
    return c


def update_centre(session: Session, actor: Actor, centre_id: uuid.UUID, **data) -> CollectionCentre:
    fresh = require(session, actor, Perm.CENTRE_OPERATE, allow_pending=True)
    c = _own_centre(session, fresh, centre_id)
    errors: list[str] = []
    clean = _validate_centre(data, errors)
    raise_if(errors)
    before = snapshot(c, CENTRE_FIELDS)
    for k, v in clean.items():
        if v is not None or k not in {"latitude", "longitude"}:
            setattr(c, k, v)
    audit(session, fresh.user_id, "CENTRE_UPDATED", "collection_centre", c.id, before, snapshot(c, CENTRE_FIELDS))
    return c


def set_operational_status(session: Session, actor: Actor, centre_id: uuid.UUID, active: bool) -> CollectionCentre:
    fresh = require(session, actor, Perm.CENTRE_OPERATE)
    c = _own_centre(session, fresh, centre_id)
    if active and c.verification_status != "VERIFIED":
        raise WorkflowError("A centre can only be activated after verification.")
    before = {"status": c.status}
    c.status = "ACTIVE" if active else "INACTIVE"
    audit(session, fresh.user_id, "CENTRE_STATUS_CHANGED", "collection_centre", c.id, before, {"status": c.status})
    return c


def is_authorized_centre(session: Session, c: CollectionCentre) -> bool:
    org = session.get(Organization, c.organization_id)
    return (c.deleted_at is None and c.verification_status == "VERIFIED" and c.status == "ACTIVE"
            and org_is_verified(org)
            and bool(active_authorizations(session, c.organization_id, COLLECTION_AUTHORIZATIONS)))


def decide_centre(session: Session, actor: Actor, centre_id: uuid.UUID, decision: str, notes: str) -> CollectionCentre:
    fresh = require(session, actor, Perm.CENTRE_VERIFY)
    if decision not in {"VERIFIED", "REJECTED", "SUSPENDED", "UNDER_REVIEW"}:
        raise ValidationError("Invalid decision.")
    if not notes or len(notes.strip()) < 5:
        raise ValidationError("A decision note is required.")
    c = session.get(CollectionCentre, centre_id)
    if c is None:
        raise NotFound("Centre not found.")
    if decision == "VERIFIED":
        org = session.get(Organization, c.organization_id)
        if not org_is_verified(org):
            raise WorkflowError("The operating organization must be verified first.")
        if not active_authorizations(session, c.organization_id, COLLECTION_AUTHORIZATIONS):
            raise WorkflowError("The organization has no verified, in-date collection / waste authorization.")
    before = {"verification_status": c.verification_status, "status": c.status}
    c.verification_status = decision
    c.status = "ACTIVE" if decision == "VERIFIED" else ("SUSPENDED" if decision == "SUSPENDED" else "INACTIVE")
    audit(session, fresh.user_id, f"CENTRE_{decision}", "collection_centre", c.id, before,
          {"verification_status": decision, "status": c.status}, details=notes.strip())
    notif.notify_organization(session, c.organization_id, notif.ORG_VERIFICATION_COMPLETED,
                              f"Collection centre {decision.lower()}", f"{c.name}: {notes.strip()}",
                              "collection_centre", c.id)
    return c


def my_centres(session: Session, actor: Actor) -> list[CollectionCentre]:
    fresh = require(session, actor, Perm.CENTRE_OPERATE, allow_pending=True)
    if fresh.organization_id is None:
        return []
    return list(session.scalars(select(CollectionCentre).where(
        CollectionCentre.organization_id == fresh.organization_id, CollectionCentre.deleted_at.is_(None))))


def search_centres(session: Session, *, district: str | None = None, pin_code: str | None = None,
                   name: str | None = None, available_only: bool = False, near: tuple[float, float] | None = None,
                   max_km: float | None = None) -> list[dict]:
    """Public directory – only verified & authorized centres are returned."""
    q = select(CollectionCentre).where(CollectionCentre.deleted_at.is_(None),
                                       CollectionCentre.verification_status == "VERIFIED",
                                       CollectionCentre.status == "ACTIVE")
    if district:
        q = q.where(CollectionCentre.district == district)
    if pin_code:
        q = q.where(CollectionCentre.pin_code.like(f"{pin_code.strip()}%"))
    if name:
        q = q.where(CollectionCentre.name.ilike(f"%{name.strip()}%"))
    if available_only:
        q = q.where(CollectionCentre.collection_available.is_(True))
    out = []
    for c in session.scalars(q.order_by(CollectionCentre.name)):
        if not is_authorized_centre(session, c):
            continue
        dist = None
        if near and c.latitude is not None:
            dist = round(haversine_km(near[0], near[1], c.latitude, c.longitude), 1)
            if max_km and dist > max_km:
                continue
        out.append(dict(id=c.id, name=c.name, address=c.address, district=c.district, pin_code=c.pin_code,
                        latitude=c.latitude, longitude=c.longitude, phone=c.contact_phone,
                        email=c.contact_email or "", hours=c.operating_hours or "",
                        categories=", ".join(x.replace("_", " ").title() for x in c.accepted_categories),
                        available=c.collection_available, radius_km=c.service_radius_km,
                        capacity=c.daily_capacity, distance_km=dist, sample=c.is_sample))
    if near:
        out.sort(key=lambda r: (r["distance_km"] is None, r["distance_km"] or 0))
    return out


def admin_list_centres(session: Session, actor: Actor) -> list[dict]:
    require(session, actor, Perm.CENTRE_VERIFY)
    rows = session.execute(select(CollectionCentre, Organization)
                           .join(Organization, Organization.id == CollectionCentre.organization_id)
                           .where(CollectionCentre.deleted_at.is_(None))).all()
    return [dict(id=c.id, name=c.name, organization=o.name, org_status=o.verification_status, district=c.district,
                 verification_status=c.verification_status, status=c.status, capacity=c.daily_capacity,
                 authorized=is_authorized_centre(session, c), sample=c.is_sample) for c, o in rows]


# ================================================================ requests
def _event(session: Session, req: CollectionRequest, event_type: str, frm: str | None, to: str | None,
           actor_id: uuid.UUID | None, notes: str | None = None) -> None:
    session.add(CollectionEvent(request_id=req.id, event_type=event_type, from_status=frm, to_status=to,
                                actor_id=actor_id, notes=notes))


def _move(session: Session, fresh: Actor, req: CollectionRequest, to_status: str, notes: str | None = None) -> None:
    if to_status not in COLLECTION_TRANSITIONS.get(req.status, set()):
        raise WorkflowError(f"Request cannot move from {req.status} to {to_status}.")
    frm = req.status
    req.status = to_status
    _event(session, req, f"STATUS_{to_status}", frm, to_status, fresh.user_id, notes)
    audit(session, fresh.user_id, f"COLLECTION_{to_status}", "collection_request", req.id, {"status": frm},
          {"status": to_status}, details=notes)


def _move_registrations(session: Session, fresh: Actor, req: CollectionRequest, to_status: str, note: str) -> None:
    for item in req.items:
        reg = item.registration
        if reg.status != to_status:
            medicine_service.transition_internal(session, fresh, reg, to_status, note)


def create_request(session: Session, actor: Actor, *, centre_id: uuid.UUID, registration_ids: list[uuid.UUID],
                   collection_method: str, preferred_date: date | None, pickup_address: str | None = None,
                   notes: str | None = None, is_sample: bool = False) -> CollectionRequest:
    fresh = require(session, actor, Perm.COLLECTION_REQUEST_CREATE)
    errors: list[str] = []
    check_choice(collection_method, CollectionMethod.values(), errors, "Collection method")
    if not registration_ids:
        errors.append("Select at least one registered medicine.")
    if preferred_date and preferred_date < today():
        errors.append("Preferred date cannot be in the past.")
    if collection_method == "PICKUP" and not clean_text(pickup_address):
        errors.append("A pickup address is required for pickup requests.")
    raise_if(errors)
    centre = session.get(CollectionCentre, centre_id)
    if centre is None or not is_authorized_centre(session, centre):
        raise ValidationError("The selected collection centre is not an authorized, active centre.")
    if not centre.collection_available:
        raise WorkflowError("This centre is not accepting new collection requests right now.")
    regs = []
    for rid in dict.fromkeys(registration_ids):
        reg = session.get(MedicineRegistration, rid)
        if reg is None or reg.deleted_at is not None:
            raise NotFound("A selected registration was not found.")
        if reg.user_id != fresh.user_id:
            raise PermissionDenied("You can only request collection for your own registrations.")
        if reg.status != "SUBMITTED":
            raise WorkflowError(f"'{reg.medicine_name}' is not available for a new request (status {reg.status}).")
        dup = session.scalar(select(CollectionItem).join(CollectionRequest)
                             .where(CollectionItem.registration_id == reg.id,
                                    CollectionRequest.status.in_(ACTIVE_COLLECTION_STATUSES)))
        if dup:
            raise WorkflowError(f"'{reg.medicine_name}' is already part of an active collection request.")
        regs.append(reg)
    req = CollectionRequest(user_id=fresh.user_id, centre_id=centre.id, preferred_date=preferred_date,
                            collection_method=collection_method, pickup_address=clean_text(pickup_address, 1000),
                            notes=clean_text(notes, 2000), status="REQUESTED", is_sample=is_sample)
    session.add(req)
    session.flush()
    for reg in regs:
        session.add(CollectionItem(request_id=req.id, registration_id=reg.id))
        medicine_service.transition_internal(session, fresh, reg, "COLLECTION_PENDING", "Collection requested")
    session.flush()
    session.refresh(req)
    _event(session, req, "CREATED", None, "REQUESTED", fresh.user_id)
    audit(session, fresh.user_id, "COLLECTION_REQUESTED", "collection_request", req.id,
          after={"centre": str(centre.id), "registrations": [str(r.id) for r in regs]})
    notif.notify_organization(session, centre.organization_id, notif.COLLECTION_REQUEST_RECEIVED,
                              "New collection request", f"{len(regs)} item(s) – {collection_method.replace('_', ' ').lower()}"
                              f" requested at {centre.name}.", "collection_request", req.id)
    notif.notify(session, fresh.user_id, notif.GENERAL, "Collection request sent",
                 f"Your request was sent to {centre.name}. You will be notified when it is accepted.",
                 "collection_request", req.id)
    return req


def _get_request(session: Session, req_id: uuid.UUID) -> CollectionRequest:
    req = session.get(CollectionRequest, req_id)
    if req is None:
        raise NotFound("Collection request not found.")
    return req


def _centre_request(session: Session, fresh: Actor, req_id: uuid.UUID) -> CollectionRequest:
    req = _get_request(session, req_id)
    centre = session.get(CollectionCentre, req.centre_id)
    if fresh.organization_id is None or centre.organization_id != fresh.organization_id:
        raise PermissionDenied("This request belongs to another collection centre.")
    return req


def cancel_request(session: Session, actor: Actor, req_id: uuid.UUID, reason: str = "") -> CollectionRequest:
    fresh = require(session, actor, Perm.COLLECTION_REQUEST_CREATE)
    req = _get_request(session, req_id)
    if req.user_id != fresh.user_id:
        raise PermissionDenied("You can only cancel your own requests.")
    _move(session, fresh, req, "CANCELLED", reason or "Cancelled by user")
    _move_registrations(session, fresh, req, "SUBMITTED", "Collection request cancelled")
    return req


def accept_request(session: Session, actor: Actor, req_id: uuid.UUID, notes: str = "") -> CollectionRequest:
    fresh = require(session, actor, Perm.CENTRE_OPERATE)
    req = _centre_request(session, fresh, req_id)
    _move(session, fresh, req, "ACCEPTED", notes or None)
    notif.notify(session, req.user_id, notif.COLLECTION_ACCEPTED, "Collection request accepted",
                 f"{req.centre.name} accepted your request. A schedule will follow.", "collection_request", req.id)
    return req


def reject_request(session: Session, actor: Actor, req_id: uuid.UUID, reason: str) -> CollectionRequest:
    fresh = require(session, actor, Perm.CENTRE_OPERATE)
    if not reason or len(reason.strip()) < 5:
        raise ValidationError("A rejection reason is required.")
    req = _centre_request(session, fresh, req_id)
    _move(session, fresh, req, "REJECTED", reason.strip())
    req.rejection_reason = reason.strip()
    _move_registrations(session, fresh, req, "SUBMITTED", "Request rejected by centre")
    notif.notify(session, req.user_id, notif.COLLECTION_REJECTED, "Collection request declined",
                 f"{req.centre.name} declined your request: {reason.strip()}. You may choose another centre.",
                 "collection_request", req.id)
    return req


def schedule_request(session: Session, actor: Actor, req_id: uuid.UUID, when: datetime, notes: str = "") -> CollectionRequest:
    fresh = require(session, actor, Perm.CENTRE_OPERATE)
    req = _centre_request(session, fresh, req_id)
    if when.date() < today():
        raise ValidationError("Scheduled date cannot be in the past.")
    _move(session, fresh, req, "SCHEDULED", notes or f"Scheduled for {when:%d %b %Y %H:%M}")
    req.scheduled_date = when
    _move_registrations(session, fresh, req, "SCHEDULED", "Collection scheduled")
    verb = "pickup" if req.collection_method == "PICKUP" else "drop-off"
    notif.notify(session, req.user_id, notif.COLLECTION_SCHEDULED, "Collection scheduled",
                 f"Your {verb} at {req.centre.name} is scheduled for {when:%d %b %Y, %H:%M}.",
                 "collection_request", req.id)
    return req


def confirm_receipt(session: Session, actor: Actor, req_id: uuid.UUID, received: dict[uuid.UUID, float],
                    notes: str = "") -> CollectionRequest:
    """Centre confirms physical receipt of medicines, with per-item received quantity."""
    fresh = require(session, actor, Perm.CENTRE_OPERATE)
    req = _centre_request(session, fresh, req_id)
    errors: list[str] = []
    for item in req.items:
        q = received.get(item.registration_id)
        if q is None:
            errors.append(f"Received quantity missing for {item.registration.medicine_name}.")
            continue
        try:
            qf = float(q)
        except (TypeError, ValueError):
            errors.append(f"Received quantity for {item.registration.medicine_name} must be a number.")
            continue
        if qf < 0:
            errors.append(f"Received quantity for {item.registration.medicine_name} must not be negative.")
    raise_if(errors)
    _move(session, fresh, req, "COLLECTED", notes or "Receipt confirmed")
    for item in req.items:
        item.quantity_received = float(received[item.registration_id])
    req.receipt_confirmed, req.receipt_confirmed_by = True, fresh.user_id
    req.actual_collection_date = utcnow()
    _move_registrations(session, fresh, req, "COLLECTED", "Received at collection centre")
    notif.notify(session, req.user_id, notif.COLLECTION_COMPLETED, "Medicines received",
                 f"{req.centre.name} confirmed receipt of your medicines. Authorized disposal will follow.",
                 "collection_request", req.id)
    return req


def initiate_disposal(session: Session, actor: Actor, req_id: uuid.UUID) -> CollectionRequest:
    fresh = require(session, actor, Perm.DISPOSAL_RECORD)
    req = _centre_request(session, fresh, req_id)
    _move(session, fresh, req, "DISPOSAL_PENDING", "Authorized disposal process initiated")
    req.disposal_status = "PENDING"
    _move_registrations(session, fresh, req, "DISPOSAL_PENDING", "Awaiting authorized disposal")
    return req


def close_request(session: Session, actor: Actor, req_id: uuid.UUID) -> CollectionRequest:
    fresh = require(session, actor, Perm.CENTRE_OPERATE)
    req = _centre_request(session, fresh, req_id)
    _move(session, fresh, req, "CLOSED", "Request closed after disposal confirmation")
    req.closure_date = utcnow()
    _move_registrations(session, fresh, req, "CLOSED", "Closed after disposal")
    notif.notify(session, req.user_id, notif.REQUEST_CLOSED, "Collection request closed",
                 "Your medicines were disposed of through an authorized process. Thank you for disposing responsibly.",
                 "collection_request", req.id)
    return req


# ================================================================ queries
def request_to_dict(session: Session, r: CollectionRequest) -> dict:
    return dict(id=r.id, centre=r.centre.name, user=r.user.full_name, method=r.collection_method, status=r.status,
                items=len(r.items), medicines=", ".join(i.registration.medicine_name for i in r.items),
                requested=r.request_date, preferred=r.preferred_date, scheduled=r.scheduled_date,
                collected=r.actual_collection_date, disposal_status=r.disposal_status, closed=r.closure_date,
                sample=r.is_sample)


def list_my_requests(session: Session, actor: Actor) -> list[CollectionRequest]:
    fresh = require(session, actor, Perm.COLLECTION_REQUEST_CREATE)
    return list(session.scalars(select(CollectionRequest).where(CollectionRequest.user_id == fresh.user_id)
                                .order_by(CollectionRequest.request_date.desc())))


def centre_requests(session: Session, actor: Actor, statuses: list[str] | None = None) -> list[CollectionRequest]:
    fresh = require(session, actor, Perm.CENTRE_OPERATE)
    if fresh.organization_id is None:
        return []
    q = select(CollectionRequest).join(CollectionCentre, CollectionCentre.id == CollectionRequest.centre_id)\
        .where(CollectionCentre.organization_id == fresh.organization_id)
    if statuses:
        q = q.where(CollectionRequest.status.in_(statuses))
    return list(session.scalars(q.order_by(CollectionRequest.request_date.desc())))


def get_request(session: Session, actor: Actor, req_id: uuid.UUID) -> CollectionRequest:
    fresh = require_active(session, actor)
    req = _get_request(session, req_id)
    if req.user_id == fresh.user_id or fresh.can(Perm.MONITOR_ALL):
        return req
    if fresh.can(Perm.CENTRE_OPERATE):
        return _centre_request(session, fresh, req_id)
    raise PermissionDenied("You do not have access to this request.")


def request_events(session: Session, actor: Actor, req_id: uuid.UUID) -> list[CollectionEvent]:
    get_request(session, actor, req_id)
    return list(session.scalars(select(CollectionEvent).where(CollectionEvent.request_id == req_id)
                                .order_by(CollectionEvent.created_at)))


def centre_handles_registration(session: Session, fresh: Actor, reg_id: uuid.UUID) -> bool:
    if fresh.organization_id is None:
        return False
    return session.scalar(select(func.count(CollectionItem.id)).join(CollectionRequest)
                          .join(CollectionCentre, CollectionCentre.id == CollectionRequest.centre_id)
                          .where(CollectionItem.registration_id == reg_id,
                                 CollectionCentre.organization_id == fresh.organization_id)) > 0


def admin_list_requests(session: Session, actor: Actor, status: str | None = None) -> list[dict]:
    require(session, actor, Perm.MONITOR_ALL)
    q = select(CollectionRequest)
    if status:
        q = q.where(CollectionRequest.status == status)
    return [request_to_dict(session, r) for r in session.scalars(q.order_by(CollectionRequest.request_date.desc()))]


def delayed_requests(session: Session, days: int = 3) -> list[CollectionRequest]:
    """Requests not accepted within `days`, or scheduled collections that are overdue."""
    cutoff = utcnow() - timedelta(days=days)
    q1 = select(CollectionRequest).where(CollectionRequest.status == "REQUESTED",
                                         CollectionRequest.request_date < cutoff)
    q2 = select(CollectionRequest).where(CollectionRequest.status == "SCHEDULED",
                                         CollectionRequest.scheduled_date < utcnow() - timedelta(days=1))
    return list(session.scalars(q1)) + list(session.scalars(q2))
