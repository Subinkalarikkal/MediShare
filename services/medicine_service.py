"""Medicine master and household medicine registration (collection & disposal only).

Household registrations can never be listed for other individuals, claimed by
organizations, or moved into the institutional workflow. There is no API in
this module (or any other) that converts a registration into inventory.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from database.models import AIExtractionRecord, Medicine, MedicineRegistration
from database.repositories import get_or_raise
from services import notification_service as notif
from services import storage_service
from services.errors import NotFound, PermissionDenied, SafetyViolation, ValidationError, WorkflowError
from services.permission_service import Actor, require, require_active
from utils.audit_logger import audit, snapshot
from utils.constants import (
    EXCLUDED_FROM_PILOT, KERALA_DISTRICTS, REGISTRATION_TRANSITIONS, DosageForm, PackagingCondition, Perm,
    RestrictedClass, Unit,
)
from utils.date_utils import utcnow
from utils.validators import (
    check_batch, check_choice, check_dates, check_pin, check_quantity, clean_text, raise_if,
    require as req_field,
)

MED_FIELDS = ["name", "generic_name", "active_ingredient", "manufacturer", "category", "dosage_form",
              "packaging_type", "restricted_classification"]
REG_FIELDS = ["medicine_name", "generic_name", "manufacturer", "batch_number", "manufacturing_date",
              "expiry_date", "quantity", "unit", "packaging_condition", "storage_info", "location",
              "district", "pin_code", "remarks", "status"]

RESTRICTED_MESSAGE = (
    "Controlled, narcotic, psychotropic, restricted and cold-chain / temperature-sensitive products are "
    "excluded from the MediShare pilot. Please contact your pharmacist or the local drug control office "
    "for safe disposal guidance."
)


# ================================================================ medicine master
def search_medicines(session: Session, text: str = "", limit: int = 50) -> list[Medicine]:
    q = select(Medicine)
    if text:
        like = f"%{text.strip()}%"
        q = q.where(or_(Medicine.name.ilike(like), Medicine.generic_name.ilike(like)))
    return list(session.scalars(q.order_by(Medicine.name).limit(limit)))


def get_medicine(session: Session, medicine_id: uuid.UUID) -> Medicine:
    return get_or_raise(session, Medicine, medicine_id, "Medicine")


def _validate_medicine(data: dict, errors: list[str]) -> dict:
    out = {k: clean_text(data.get(k), 250) for k in
           ["name", "generic_name", "active_ingredient", "manufacturer", "category", "packaging_type"]}
    req_field(out["name"], "Medicine name", errors)
    out["dosage_form"] = check_choice(data.get("dosage_form", "OTHER"), DosageForm.values(), errors, "Dosage form")
    out["restricted_classification"] = check_choice(
        data.get("restricted_classification", "NONE"), RestrictedClass.values(), errors, "Restricted classification")
    for banned in ("dose", "dosage", "indication", "prescribe"):
        for k in ("category", "packaging_type"):
            if out.get(k) and banned in out[k].lower():
                errors.append("The medicine master stores identification data only, not clinical guidance.")
    return out


def create_medicine(session: Session, actor: Actor, **data) -> Medicine:
    fresh = require(session, actor, Perm.MEDICINE_MASTER_MANAGE)
    errors: list[str] = []
    clean = _validate_medicine(data, errors)
    raise_if(errors)
    dup = session.scalar(select(Medicine).where(func.lower(Medicine.name) == clean["name"].lower(),
                                                Medicine.manufacturer == clean["manufacturer"],
                                                Medicine.dosage_form == clean["dosage_form"]))
    if dup:
        raise ValidationError("This medicine already exists in the master database.")
    med = Medicine(**clean, is_sample=bool(data.get("is_sample", False)))
    session.add(med)
    session.flush()
    audit(session, fresh.user_id, "MEDICINE_MASTER_CREATED", "medicine", med.id, after=snapshot(med, MED_FIELDS))
    return med


def update_medicine(session: Session, actor: Actor, medicine_id: uuid.UUID, **data) -> Medicine:
    fresh = require(session, actor, Perm.MEDICINE_MASTER_MANAGE)
    med = get_medicine(session, medicine_id)
    errors: list[str] = []
    clean = _validate_medicine(data, errors)
    raise_if(errors)
    before = snapshot(med, MED_FIELDS)
    for k, v in clean.items():
        setattr(med, k, v)
    audit(session, fresh.user_id, "MEDICINE_MASTER_UPDATED", "medicine", med.id, before, snapshot(med, MED_FIELDS))
    return med


# ================================================================ household registrations
def _validate_registration(data: dict, errors: list[str]) -> dict:
    out: dict = {}
    out["medicine_name"] = clean_text(data.get("medicine_name"), 250)
    req_field(out["medicine_name"], "Medicine name", errors)
    out["generic_name"] = clean_text(data.get("generic_name"), 250)
    out["manufacturer"] = clean_text(data.get("manufacturer"), 250)
    out["batch_number"] = check_batch(data.get("batch_number"), errors)
    mfg, exp = data.get("manufacturing_date"), data.get("expiry_date")
    for label, d in (("Manufacturing date", mfg), ("Expiry date", exp)):
        if d is not None and not isinstance(d, date):
            errors.append(f"{label} is not a valid date.")
    if isinstance(exp, date) or exp is None:
        check_dates(mfg if isinstance(mfg, date) else None, exp, errors)
    out["manufacturing_date"], out["expiry_date"] = mfg, exp
    out["quantity"] = check_quantity(data.get("quantity"), errors, max_value=10_000)
    out["unit"] = check_choice(data.get("unit"), Unit.values(), errors, "Unit")
    out["packaging_condition"] = check_choice(data.get("packaging_condition", "UNKNOWN"),
                                              PackagingCondition.values(), errors, "Packaging condition")
    out["storage_info"] = clean_text(data.get("storage_info"), 1000)
    out["location"] = clean_text(data.get("location"), 300)
    district = data.get("district") or None
    if district and district not in KERALA_DISTRICTS:
        errors.append("Please select a valid district.")
    out["district"] = district
    out["pin_code"] = check_pin(data.get("pin_code"), errors, required=False)
    out["remarks"] = clean_text(data.get("remarks"), 2000)
    return out


def _check_restrictions(session: Session, medicine_id, declared_restricted: str | None) -> Medicine | None:
    med = None
    if medicine_id:
        med = get_medicine(session, medicine_id)
        if med.restricted_classification in EXCLUDED_FROM_PILOT:
            raise SafetyViolation(RESTRICTED_MESSAGE)
    if declared_restricted and declared_restricted in EXCLUDED_FROM_PILOT:
        raise SafetyViolation(RESTRICTED_MESSAGE)
    return med


def create_registration(session: Session, actor: Actor, *, submit: bool = True,
                        medicine_id: uuid.UUID | None = None, declared_restricted: str | None = None,
                        photo: tuple[bytes, str] | None = None, ai_extraction_id: uuid.UUID | None = None,
                        is_sample: bool = False, **data) -> MedicineRegistration:
    fresh = require(session, actor, Perm.MEDICINE_REGISTER)
    errors: list[str] = []
    clean = _validate_registration(data, errors)
    raise_if(errors)
    med = _check_restrictions(session, medicine_id, declared_restricted)
    photo_id = None
    if photo:
        rec = storage_service.store_upload(session, owner_id=fresh.user_id, data=photo[0], filename=photo[1],
                                           purpose="medicine_photos", images_only=True)
        photo_id = rec.id
    if ai_extraction_id:
        ext = session.get(AIExtractionRecord, ai_extraction_id)
        if ext is None or ext.user_id != fresh.user_id:
            raise PermissionDenied("Invalid AI extraction reference.")
        ext.confirmed, ext.confirmed_at = True, utcnow()
    reg = MedicineRegistration(user_id=fresh.user_id, medicine_id=med.id if med else None, photo_file_id=photo_id,
                               ai_extraction_id=ai_extraction_id, status="DRAFT", is_sample=is_sample, **clean)
    session.add(reg)
    session.flush()
    audit(session, fresh.user_id, "REGISTRATION_CREATED", "medicine_registration", reg.id,
          after=snapshot(reg, REG_FIELDS))
    if submit:
        _transition(session, fresh, reg, "SUBMITTED", "Submitted by user")
    return reg


def _transition(session: Session, fresh: Actor, reg: MedicineRegistration, to_status: str, note: str = "") -> None:
    if to_status not in REGISTRATION_TRANSITIONS.get(reg.status, set()):
        raise WorkflowError(f"Registration cannot move from {reg.status} to {to_status}.")
    frm = reg.status
    reg.status = to_status
    if to_status == "SUBMITTED" and reg.submitted_at is None:
        reg.submitted_at = utcnow()
    if to_status in {"CLOSED", "CANCELLED", "REJECTED"}:
        reg.closed_at = utcnow()
    audit(session, fresh.user_id, f"REGISTRATION_{to_status}", "medicine_registration", reg.id,
          {"status": frm}, {"status": to_status}, details=note or None)
    if to_status == "SUBMITTED" and frm == "DRAFT":
        notif.notify(session, reg.user_id, notif.REGISTRATION_SUBMITTED, "Medicine registration submitted",
                     f"'{reg.medicine_name}' is registered for authorized collection and disposal. "
                     "Choose a collection centre to request pickup or drop-off.", "medicine_registration", reg.id)


def transition_internal(session: Session, fresh: Actor, reg: MedicineRegistration, to_status: str, note: str = ""):
    """Used by collection/disposal services after their own authorization checks."""
    _transition(session, fresh, reg, to_status, note)


def _own_registration(session: Session, fresh: Actor, reg_id: uuid.UUID) -> MedicineRegistration:
    reg = session.get(MedicineRegistration, reg_id)
    if reg is None or reg.deleted_at is not None:
        raise NotFound("Registration not found.")
    if reg.user_id != fresh.user_id:
        raise PermissionDenied("You can only access your own registrations.")
    return reg


def update_draft(session: Session, actor: Actor, reg_id: uuid.UUID, **data) -> MedicineRegistration:
    fresh = require(session, actor, Perm.MEDICINE_REGISTER)
    reg = _own_registration(session, fresh, reg_id)
    if reg.status != "DRAFT":
        raise WorkflowError("Only draft registrations can be edited.")
    errors: list[str] = []
    clean = _validate_registration(data, errors)
    raise_if(errors)
    before = snapshot(reg, REG_FIELDS)
    for k, v in clean.items():
        setattr(reg, k, v)
    audit(session, fresh.user_id, "REGISTRATION_UPDATED", "medicine_registration", reg.id, before,
          snapshot(reg, REG_FIELDS))
    return reg


def submit_registration(session: Session, actor: Actor, reg_id: uuid.UUID) -> MedicineRegistration:
    fresh = require(session, actor, Perm.MEDICINE_REGISTER)
    reg = _own_registration(session, fresh, reg_id)
    _transition(session, fresh, reg, "SUBMITTED")
    return reg


def cancel_registration(session: Session, actor: Actor, reg_id: uuid.UUID, reason: str = "") -> MedicineRegistration:
    fresh = require(session, actor, Perm.MEDICINE_REGISTER)
    reg = _own_registration(session, fresh, reg_id)
    if reg.status not in {"DRAFT", "SUBMITTED"}:
        raise WorkflowError("Cancel the related collection request first; this registration is already in a collection workflow.")
    _transition(session, fresh, reg, "CANCELLED", reason)
    return reg


def delete_draft(session: Session, actor: Actor, reg_id: uuid.UUID) -> None:
    fresh = require(session, actor, Perm.MEDICINE_REGISTER)
    reg = _own_registration(session, fresh, reg_id)
    if reg.status != "DRAFT":
        raise WorkflowError("Only drafts can be deleted.")
    reg.deleted_at = utcnow()
    audit(session, fresh.user_id, "REGISTRATION_DELETED", "medicine_registration", reg.id)


def list_my_registrations(session: Session, actor: Actor, statuses: list[str] | None = None) -> list[MedicineRegistration]:
    fresh = require(session, actor, Perm.MEDICINE_REGISTER)
    q = select(MedicineRegistration).where(MedicineRegistration.user_id == fresh.user_id,
                                           MedicineRegistration.deleted_at.is_(None))
    if statuses:
        q = q.where(MedicineRegistration.status.in_(statuses))
    return list(session.scalars(q.order_by(MedicineRegistration.created_at.desc())))


def get_registration(session: Session, actor: Actor, reg_id: uuid.UUID) -> MedicineRegistration:
    fresh = require_active(session, actor)
    reg = session.get(MedicineRegistration, reg_id)
    if reg is None or reg.deleted_at is not None:
        raise NotFound("Registration not found.")
    if reg.user_id == fresh.user_id or fresh.can(Perm.MONITOR_ALL):
        return reg
    if fresh.can(Perm.CENTRE_OPERATE):
        from services.collection_service import centre_handles_registration
        if centre_handles_registration(session, fresh, reg.id):
            return reg
    raise PermissionDenied("You do not have access to this registration.")


def admin_list_registrations(session: Session, actor: Actor, status: str | None = None,
                             district: str | None = None) -> list[dict]:
    require(session, actor, Perm.MONITOR_ALL)
    q = select(MedicineRegistration).where(MedicineRegistration.deleted_at.is_(None))
    if status:
        q = q.where(MedicineRegistration.status == status)
    if district:
        q = q.where(MedicineRegistration.district == district)
    return [dict(id=r.id, medicine=r.medicine_name, quantity=float(r.quantity), unit=r.unit, expiry=r.expiry_date,
                 district=r.district, status=r.status, created_at=r.created_at, sample=r.is_sample)
            for r in session.scalars(q.order_by(MedicineRegistration.created_at.desc()).limit(2000))]


def admin_set_status(session: Session, actor: Actor, reg_id: uuid.UUID, to_status: str, reason: str) -> MedicineRegistration:
    """Admin may place registrations ON_HOLD, release holds, or reject them."""
    fresh = require(session, actor, Perm.MONITOR_ALL)
    if to_status not in {"ON_HOLD", "REJECTED", "SUBMITTED"}:
        raise ValidationError("Administrators may only hold, release or reject registrations.")
    if not reason or len(reason.strip()) < 5:
        raise ValidationError("A reason is required.")
    reg = session.get(MedicineRegistration, reg_id)
    if reg is None:
        raise NotFound("Registration not found.")
    _transition(session, fresh, reg, to_status, reason.strip())
    notif.notify(session, reg.user_id, notif.GENERAL, f"Registration {to_status.replace('_', ' ').lower()}",
                 f"'{reg.medicine_name}': {reason.strip()}", "medicine_registration", reg.id)
    return reg
