"""Organization registration, documents, identity verification and regulatory authorizations.

Identity verification (organizations.verification_status) and regulatory
authorization (organization_authorizations) are deliberately separate:
a VERIFIED organization has no medicine-handling rights until a matching,
in-date authorization has also been verified by an administrator.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import (
    Organization, OrganizationAuthorization, OrganizationDocument, OrganizationVerification,
    UploadedFile, User,
)
from services import notification_service as notif
from services import storage_service
from services.errors import NotFound, PermissionDenied, ValidationError, WorkflowError
from services.permission_service import (
    Actor, active_authorizations, require, require_active, require_org_member, require_role,
)
from utils.audit_logger import audit, snapshot
from utils.constants import (
    COLLECTION_AUTHORIZATIONS, KERALA_DISTRICTS, MEDICINE_HANDLING_AUTHORIZATIONS, VERIFICATION_TRANSITIONS,
    AccountStatus, AuthorizationStatus, AuthorizationType, DocumentType, OrgType, Perm,
)
from utils.date_utils import utcnow
from utils.validators import (
    check_choice, check_email, check_phone, check_pin, clean_text, raise_if, require as req_field,
)

ROLE_ORG_TYPES = {
    "INSTITUTIONAL_DONOR": {OrgType.HOSPITAL.value, OrgType.PHARMACY.value, OrgType.HEALTHCARE_INSTITUTION.value},
    "NGO": {OrgType.NGO.value, OrgType.CHARITABLE_HEALTHCARE.value},
    "COLLECTION_CENTRE": {OrgType.COLLECTION_CENTRE.value, OrgType.PHARMACY.value, OrgType.HOSPITAL.value},
}
ORG_FIELDS = ["name", "org_type", "registration_number", "address", "district", "pin_code",
              "contact_person", "contact_email", "phone", "website", "service_area", "description",
              "authorized_professional_name", "authorized_professional_registration", "verification_status"]
LOCKED_AFTER_VERIFICATION = {"name", "org_type", "registration_number"}


def _validate_org_fields(data: dict, errors: list[str], allowed_types: set[str]) -> dict:
    out = {}
    out["name"] = clean_text(data.get("name"), 250)
    req_field(out["name"], "Organization name", errors)
    out["org_type"] = check_choice(data.get("org_type"), allowed_types, errors, "Organization type")
    out["registration_number"] = clean_text(data.get("registration_number"), 100)
    req_field(out["registration_number"], "Registration number", errors)
    out["address"] = clean_text(data.get("address"), 1000)
    req_field(out["address"], "Address", errors)
    out["district"] = check_choice(data.get("district"), KERALA_DISTRICTS, errors, "District")
    out["pin_code"] = check_pin(data.get("pin_code"), errors)
    out["contact_person"] = clean_text(data.get("contact_person"), 200)
    req_field(out["contact_person"], "Contact person", errors)
    out["contact_email"] = check_email(data.get("contact_email"), errors, "Contact email")
    out["phone"] = check_phone(data.get("phone"), errors)
    website = clean_text(data.get("website"), 300)
    if website and not website.startswith(("http://", "https://")):
        errors.append("Website must start with http:// or https://")
    out["website"] = website
    out["service_area"] = clean_text(data.get("service_area"), 1000)
    out["description"] = clean_text(data.get("description"), 3000)
    out["authorized_professional_name"] = clean_text(data.get("authorized_professional_name"), 200)
    out["authorized_professional_registration"] = clean_text(data.get("authorized_professional_registration"), 100)
    return out


def _get_org(session: Session, org_id: uuid.UUID) -> Organization:
    org = session.get(Organization, org_id)
    if org is None or org.deleted_at is not None:
        raise NotFound("Organization not found.")
    return org


def register_organization(session: Session, actor: Actor, **data) -> Organization:
    fresh = require_role(session, actor, "INSTITUTIONAL_DONOR", "NGO", "COLLECTION_CENTRE", allow_pending=True)
    if fresh.organization_id is not None:
        raise WorkflowError("Your account is already linked to an organization.")
    allowed: set[str] = set()
    for r in fresh.roles:
        allowed |= ROLE_ORG_TYPES.get(r, set())
    from services.settings_service import get_value
    enabled = set(get_value(session, "organizations.enabled_types", OrgType.values()))
    allowed &= enabled
    errors: list[str] = []
    clean = _validate_org_fields(data, errors, allowed)
    raise_if(errors)
    dup = session.scalar(select(Organization).where(Organization.org_type == clean["org_type"],
                                                    Organization.registration_number == clean["registration_number"]))
    if dup:
        raise ValidationError("An organization with this type and registration number is already registered.")
    org = Organization(**clean, verification_status="PENDING", created_by=fresh.user_id,
                       is_sample=bool(data.get("is_sample", False)))
    session.add(org)
    session.flush()
    session.get(User, fresh.user_id).organization_id = org.id
    session.add(OrganizationVerification(organization_id=org.id, from_status=None, to_status="PENDING",
                                         decided_by=fresh.user_id, notes="Registered"))
    audit(session, fresh.user_id, "ORG_REGISTERED", "organization", org.id, after=snapshot(org, ORG_FIELDS))
    return org


def update_organization(session: Session, actor: Actor, org_id: uuid.UUID, **data) -> Organization:
    fresh = require(session, actor, Perm.ORG_MANAGE_OWN, allow_pending=True)
    org = _get_org(session, org_id)
    require_org_member(fresh, org.id)
    if org.verification_status == "SUSPENDED":
        raise WorkflowError("Suspended organizations cannot be modified.")
    errors: list[str] = []
    data.setdefault("org_type", org.org_type)
    clean = _validate_org_fields(data, errors, {org.org_type})
    raise_if(errors)
    before = snapshot(org, ORG_FIELDS)
    for k, v in clean.items():
        if org.verification_status == "VERIFIED" and k in LOCKED_AFTER_VERIFICATION and getattr(org, k) != v:
            raise WorkflowError(f"'{k.replace('_', ' ')}' cannot be changed after verification. Contact an administrator.")
        setattr(org, k, v)
    audit(session, fresh.user_id, "ORG_UPDATED", "organization", org.id, before, snapshot(org, ORG_FIELDS))
    return org


def upload_document(session: Session, actor: Actor, org_id: uuid.UUID, document_type: str,
                    data: bytes, filename: str, notes: str | None = None) -> OrganizationDocument:
    fresh = require(session, actor, Perm.ORG_MANAGE_OWN, allow_pending=True)
    org = _get_org(session, org_id)
    require_org_member(fresh, org.id)
    errors: list[str] = []
    check_choice(document_type, DocumentType.values(), errors, "Document type")
    raise_if(errors)
    rec = storage_service.store_upload(session, owner_id=fresh.user_id, data=data, filename=filename,
                                       purpose="org_documents", images_only=False, organization_id=org.id)
    doc = OrganizationDocument(organization_id=org.id, document_type=document_type, file_id=rec.id,
                               notes=clean_text(notes, 1000), uploaded_by=fresh.user_id)
    session.add(doc)
    session.flush()
    audit(session, fresh.user_id, "ORG_DOCUMENT_UPLOADED", "organization", org.id,
          after={"document_type": document_type, "file": rec.original_filename, "sha256": rec.sha256})
    return doc


def add_authorization(session: Session, actor: Actor, org_id: uuid.UUID, *, authorization_type: str,
                      reference_number: str, issuing_authority: str | None, valid_from: date | None,
                      valid_until: date | None, data: bytes | None = None, filename: str | None = None,
                      notes: str | None = None) -> OrganizationAuthorization:
    fresh = require(session, actor, Perm.ORG_MANAGE_OWN, allow_pending=True)
    org = _get_org(session, org_id)
    require_org_member(fresh, org.id)
    errors: list[str] = []
    check_choice(authorization_type, AuthorizationType.values(), errors, "Authorization type")
    ref = clean_text(reference_number, 100)
    req_field(ref, "Licence / authorization number", errors)
    if valid_from and valid_until and valid_until < valid_from:
        errors.append("'Valid until' must be on or after 'valid from'.")
    raise_if(errors)
    file_id = None
    if data:
        rec = storage_service.store_upload(session, owner_id=fresh.user_id, data=data, filename=filename or "licence.pdf",
                                           purpose="authorizations", images_only=False, organization_id=org.id)
        file_id = rec.id
    auth = OrganizationAuthorization(
        organization_id=org.id, authorization_type=authorization_type, reference_number=ref,
        issuing_authority=clean_text(issuing_authority, 200), valid_from=valid_from, valid_until=valid_until,
        status="PENDING", document_file_id=file_id, notes=clean_text(notes, 1000))
    session.add(auth)
    session.flush()
    audit(session, fresh.user_id, "ORG_AUTHORIZATION_ADDED", "organization_authorization", auth.id,
          after={"type": authorization_type, "reference": ref})
    return auth


def documentation_gaps(session: Session, org: Organization) -> list[str]:
    docs = set(session.scalars(select(OrganizationDocument.document_type)
                               .where(OrganizationDocument.organization_id == org.id)))
    gaps = []
    if DocumentType.REGISTRATION_CERTIFICATE.value not in docs:
        gaps.append("Registration certificate")
    if org.org_type in {"HOSPITAL", "PHARMACY", "HEALTHCARE_INSTITUTION"}:
        if DocumentType.DRUG_LICENCE.value not in docs:
            gaps.append("Applicable drug licence document")
        if not active_authorizations(session, org.id, MEDICINE_HANDLING_AUTHORIZATIONS):
            gaps.append("Verified, in-date drug licence authorization")
        if not org.authorized_professional_name:
            gaps.append("Authorized professional (pharmacist) information")
    if org.org_type == "COLLECTION_CENTRE":
        if not active_authorizations(session, org.id, COLLECTION_AUTHORIZATIONS):
            gaps.append("Verified collection / bio-medical waste authorization")
    return gaps


def submit_for_verification(session: Session, actor: Actor, org_id: uuid.UUID) -> Organization:
    fresh = require(session, actor, Perm.ORG_MANAGE_OWN, allow_pending=True)
    org = _get_org(session, org_id)
    require_org_member(fresh, org.id)
    docs = set(session.scalars(select(OrganizationDocument.document_type)
                               .where(OrganizationDocument.organization_id == org.id)))
    if DocumentType.REGISTRATION_CERTIFICATE.value not in docs:
        raise WorkflowError("Upload the registration certificate before submitting for verification.")
    if org.verification_status not in {"PENDING", "REJECTED"}:
        raise WorkflowError(f"Organization is already {org.verification_status}.")
    _transition(session, fresh, org, "UNDER_REVIEW", "Submitted by organization")
    notif.notify_role(session, ("ADMIN", "SUPER_ADMIN"), notif.GENERAL, "Organization awaiting verification",
                      f"{org.name} submitted documents for verification.", "organization", org.id)
    return org


def _transition(session: Session, fresh: Actor, org: Organization, to_status: str, notes: str) -> None:
    allowed = VERIFICATION_TRANSITIONS.get(org.verification_status, set())
    if to_status not in allowed:
        raise WorkflowError(f"Cannot move organization from {org.verification_status} to {to_status}.")
    frm = org.verification_status
    org.verification_status = to_status
    org.status_reason = notes
    if to_status == "VERIFIED":
        org.verified_at, org.verified_by = utcnow(), fresh.user_id
    session.add(OrganizationVerification(organization_id=org.id, from_status=frm, to_status=to_status,
                                         decided_by=fresh.user_id, notes=notes))
    audit(session, fresh.user_id, f"ORG_{to_status}", "organization", org.id,
          {"verification_status": frm}, {"verification_status": to_status}, details=notes)


def decide_verification(session: Session, actor: Actor, org_id: uuid.UUID, decision: str, notes: str) -> Organization:
    """Admin decision: VERIFIED / REJECTED / SUSPENDED / UNDER_REVIEW / PENDING."""
    fresh = require(session, actor, Perm.ORG_VERIFY)
    if not notes or len(notes.strip()) < 5:
        raise ValidationError("A decision note (at least 5 characters) is required.")
    org = _get_org(session, org_id)
    _transition(session, fresh, org, decision, notes.strip())
    members = session.scalars(select(User).where(User.organization_id == org.id)).all()
    if decision == "VERIFIED":
        for u in members:
            if u.status == AccountStatus.PENDING.value:
                u.status = AccountStatus.ACTIVE.value
    notif.notify_organization(session, org.id, notif.ORG_VERIFICATION_COMPLETED,
                              f"Organization verification: {decision}",
                              f"{org.name} verification status is now {decision}. Note: {notes.strip()}",
                              "organization", org.id)
    return org


def decide_authorization(session: Session, actor: Actor, authorization_id: uuid.UUID, decision: str,
                         notes: str) -> OrganizationAuthorization:
    fresh = require(session, actor, Perm.ORG_VERIFY)
    if decision not in {AuthorizationStatus.VERIFIED.value, AuthorizationStatus.REJECTED.value,
                        AuthorizationStatus.REVOKED.value}:
        raise ValidationError("Invalid decision.")
    if not notes or len(notes.strip()) < 5:
        raise ValidationError("A decision note (at least 5 characters) is required.")
    auth = session.get(OrganizationAuthorization, authorization_id)
    if auth is None:
        raise NotFound("Authorization not found.")
    if decision == "VERIFIED" and auth.valid_until and auth.valid_until < date.today():
        raise WorkflowError("This authorization has already expired and cannot be verified.")
    before = {"status": auth.status}
    auth.status = decision
    auth.verified_by, auth.verified_at = fresh.user_id, utcnow()
    auth.notes = notes.strip()
    audit(session, fresh.user_id, f"AUTHORIZATION_{decision}", "organization_authorization", auth.id,
          before, {"status": decision}, details=notes.strip())
    notif.notify_organization(session, auth.organization_id, notif.ORG_VERIFICATION_COMPLETED,
                              f"Authorization {auth.reference_number}: {decision}", notes.strip(),
                              "organization", auth.organization_id)
    return auth


# ---------------------------------------------------------------- queries
def get_my_organization(session: Session, actor: Actor) -> Organization | None:
    fresh = require_active(session, actor, allow_pending=True)
    if fresh.organization_id is None:
        return None
    return session.get(Organization, fresh.organization_id)


def get_organization(session: Session, actor: Actor, org_id: uuid.UUID) -> Organization:
    fresh = require_active(session, actor, allow_pending=True)
    org = _get_org(session, org_id)
    if not (fresh.can(Perm.ORG_VERIFY) or fresh.can(Perm.MONITOR_ALL)):
        require_org_member(fresh, org.id)
    return org


def list_documents(session: Session, actor: Actor, org_id: uuid.UUID) -> list[dict]:
    get_organization(session, actor, org_id)
    rows = session.execute(select(OrganizationDocument, UploadedFile)
                           .join(UploadedFile, UploadedFile.id == OrganizationDocument.file_id)
                           .where(OrganizationDocument.organization_id == org_id)
                           .order_by(OrganizationDocument.created_at.desc())).all()
    return [dict(id=d.id, document_type=d.document_type, filename=f.original_filename, size_kb=round(f.size_bytes / 1024, 1),
                 uploaded_at=d.created_at, file_id=f.id, notes=d.notes) for d, f in rows]


def list_authorizations(session: Session, actor: Actor, org_id: uuid.UUID) -> list[OrganizationAuthorization]:
    get_organization(session, actor, org_id)
    return list(session.scalars(select(OrganizationAuthorization)
                                .where(OrganizationAuthorization.organization_id == org_id)
                                .order_by(OrganizationAuthorization.created_at.desc())))


def read_document(session: Session, actor: Actor, file_id: uuid.UUID) -> tuple[UploadedFile, bytes]:
    """Permission-checked file read. Owners, org members, admins and reviewers may read."""
    fresh = require_active(session, actor, allow_pending=True)
    rec = session.get(UploadedFile, file_id)
    if rec is None or rec.deleted_at is not None:
        raise NotFound("File not found.")
    allowed = (rec.owner_user_id == fresh.user_id
               or (rec.organization_id and rec.organization_id == fresh.organization_id)
               or fresh.can(Perm.ORG_VERIFY) or fresh.can(Perm.REVIEW_INVENTORY))
    if not allowed:
        raise PermissionDenied("You do not have access to this file.")
    audit(session, fresh.user_id, "FILE_ACCESSED", "uploaded_file", rec.id)
    return rec, storage_service.read_file(rec)


def list_organizations(session: Session, actor: Actor, status: str | None = None,
                       org_type: str | None = None, text: str = "") -> list[dict]:
    require(session, actor, Perm.ORG_VERIFY)
    q = select(Organization).where(Organization.deleted_at.is_(None))
    if status:
        q = q.where(Organization.verification_status == status)
    if org_type:
        q = q.where(Organization.org_type == org_type)
    if text:
        q = q.where(Organization.name.ilike(f"%{text.strip()}%"))
    out = []
    for o in session.scalars(q.order_by(Organization.created_at.desc())):
        out.append(dict(id=o.id, name=o.name, type=o.org_type, district=o.district,
                        registration_number=o.registration_number, verification_status=o.verification_status,
                        documentation_gaps=", ".join(documentation_gaps(session, o)) or "None",
                        created_at=o.created_at, sample=o.is_sample))
    return out


def pending_authorizations(session: Session, actor: Actor) -> list[dict]:
    require(session, actor, Perm.ORG_VERIFY)
    rows = session.execute(select(OrganizationAuthorization, Organization)
                           .join(Organization, Organization.id == OrganizationAuthorization.organization_id)
                           .where(OrganizationAuthorization.status == "PENDING")).all()
    return [dict(id=a.id, organization=o.name, type=a.authorization_type, reference=a.reference_number,
                 authority=a.issuing_authority, valid_from=a.valid_from, valid_until=a.valid_until,
                 file_id=a.document_file_id) for a, o in rows]


def public_verified_organizations(session: Session) -> list[dict]:
    """Public listing – identity-verified organizations only, no private details."""
    rows = session.scalars(select(Organization).where(Organization.verification_status == "VERIFIED",
                                                      Organization.deleted_at.is_(None))
                           .order_by(Organization.name)).all()
    return [dict(name=o.name, type=o.org_type.replace("_", " ").title(), district=o.district,
                 website=o.website or "", sample=o.is_sample) for o in rows]


def verification_history(session: Session, actor: Actor, org_id: uuid.UUID) -> list[OrganizationVerification]:
    get_organization(session, actor, org_id)
    return list(session.scalars(select(OrganizationVerification)
                                .where(OrganizationVerification.organization_id == org_id)
                                .order_by(OrganizationVerification.created_at.desc())))


def count_verified(session: Session) -> int:
    return session.scalar(select(func.count(Organization.id))
                          .where(Organization.verification_status == "VERIFIED")) or 0
