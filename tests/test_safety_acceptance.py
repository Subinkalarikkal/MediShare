"""Critical safety acceptance tests (specification §12)."""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import inspect, select

from database.models import AuditLog, CollectionItem, TransferRecord
from services import (
    collection_service, disposal_service, institutional_request_service as irs, inventory_service, medicine_service,
    organization_service, review_service, transfer_service,
)
from services.errors import PermissionDenied, SafetyViolation, WorkflowError
from tests.conftest import FULL_CHECKLIST, make_org, make_user, ready_batch, reg_data


def _approved_batch(session, donor_actor, medicine, reviewer, **over):
    b = ready_batch(session, donor_actor, medicine, **over)
    inventory_service.submit_batch(session, donor_actor, b.id)
    review_service.record_decision(session, reviewer, b.id, "START_REVIEW", notes="Starting review")
    review_service.record_decision(session, reviewer, b.id, "APPROVE_FOR_FURTHER_AUTHORIZED_PROCESS",
                                   notes="All checks complete", checklist=FULL_CHECKLIST,
                                   authorization_reference="DC/KL/2026/77")
    return b


def _accepted_interest(session, ngo_actor, b, reviewer, qty=100):
    r = irs.create_request(session, ngo_actor, required_category="Analgesic", medicine_name="Paracetamol",
                           medicine_id=b.medicine_id, requested_quantity=200, unit="TABLETS",
                           purpose="Community clinic stock")
    recs = irs.recommendations(session, ngo_actor, r.id)
    assert recs and recs[0]["batch_id"] == b.id
    item = irs.express_interest(session, ngo_actor, r.id, b.id, qty)
    review_service.review_interest(session, reviewer, item.id, True, "Recipient licence and need verified")
    return item


# 1 ---------------------------------------------------------------------------
def test_1_direct_individual_to_individual_transfer_impossible(session, individual, centre):
    other = make_user(session, "friend@test.in", "INDIVIDUAL")
    reg = medicine_service.create_registration(session, individual, **reg_data())
    # No listing/claiming API exists for households; other individuals cannot see or request it.
    with pytest.raises(PermissionDenied):
        medicine_service.get_registration(session, other, reg.id)
    _, _, c = centre
    with pytest.raises(PermissionDenied):
        collection_service.create_request(session, other, centre_id=c.id, registration_ids=[reg.id],
                                          collection_method="DROP_OFF", preferred_date=None)
    # Individuals hold no transfer / institutional permissions at all.
    assert not {"transfer.document", "institutional_request.manage", "inventory.manage"} & other.permissions


# 2 ---------------------------------------------------------------------------
def test_2_expired_medicines_never_enter_reuse(session, donor, ngo, medicine, reviewer, admin):
    donor_actor, _ = donor
    ngo_actor, _ = ngo
    expired = ready_batch(session, donor_actor, medicine, batch_number="OLD1",
                          manufacturing_date=date.today() - timedelta(days=900), expiry_date=date.today() - timedelta(days=1))
    with pytest.raises(SafetyViolation):
        inventory_service.submit_batch(session, donor_actor, expired.id)
    b = _approved_batch(session, donor_actor, medicine, reviewer)
    item = _accepted_interest(session, ngo_actor, b, reviewer)
    b.expiry_date = date.today()  # batch expires after approval
    with pytest.raises(SafetyViolation):
        transfer_service.create_transfer(session, admin, inventory_batch_id=b.id, institutional_request_item_id=item.id,
                                         quantity=10)
    inventory_service.quarantine_expired(session, admin)
    assert b.status == "QUARANTINED"
    assert irs.recommendations(session, ngo_actor, item.institutional_request_id) == []


# 3 ---------------------------------------------------------------------------
def test_3_household_inventory_never_enters_institutional_transfer(session, individual, admin):
    reg = medicine_service.create_registration(session, individual, **reg_data())
    # Schema level: transfer tables have no foreign key to medicine_registrations.
    for table in ("transfer_records", "transfer_authorizations", "institutional_request_items", "review_records"):
        fks = {fk.column.table.name for fk in TransferRecord.metadata.tables[table].foreign_keys}
        assert "medicine_registrations" not in fks
    # Service level: passing a household id is refused explicitly.
    with pytest.raises(SafetyViolation):
        transfer_service.create_transfer(session, admin, inventory_batch_id=reg.id,
                                         institutional_request_item_id=reg.id, quantity=1)
    # Collection items reference household registrations only.
    assert {fk.column.table.name for fk in CollectionItem.__table__.foreign_keys} == \
        {"collection_requests", "medicine_registrations"}


# 4 ---------------------------------------------------------------------------
def test_4_unverified_orgs_cannot_submit_requests(session, admin):
    ngo_actor, org = make_org(session, admin, "unv-ngo@test.in", "NGO", "NGO", verify=False)
    with pytest.raises(PermissionDenied):
        irs.create_request(session, ngo_actor, required_category="Analgesic", medicine_name="Paracetamol",
                           requested_quantity=10, unit="TABLETS", purpose="Clinic")


# 5 ---------------------------------------------------------------------------
def test_5_ai_cannot_approve_suitability(session, donor, medicine, reviewer):
    donor_actor, _ = donor
    b = ready_batch(session, donor_actor, medicine)
    inventory_service.submit_batch(session, donor_actor, b.id)
    with pytest.raises(SafetyViolation):
        review_service.record_decision(session, reviewer, b.id, "APPROVE_FOR_FURTHER_AUTHORIZED_PROCESS",
                                       notes="model output", checklist=FULL_CHECKLIST, authorization_reference="x",
                                       ai_generated=True)
    from ai.analytics_assistant import validate_response
    assert validate_response("I approve this medicine; it is safe to use for patients.")[0] is False


# 6 ---------------------------------------------------------------------------
def test_6_transfers_require_all_authorizations(session, donor, ngo, medicine, reviewer, admin):
    donor_actor, _ = donor
    ngo_actor, _ = ngo
    b = _approved_batch(session, donor_actor, medicine, reviewer)
    item = _accepted_interest(session, ngo_actor, b, reviewer)
    t = transfer_service.create_transfer(session, admin, inventory_batch_id=b.id,
                                         institutional_request_item_id=item.id, quantity=100)
    with pytest.raises(SafetyViolation):
        transfer_service.mark_authorized(session, admin, t.id)
    with pytest.raises(SafetyViolation):
        transfer_service.mark_completed(session, admin, t.id, "done")
    transfer_service.add_authorization(session, admin, t.id, "REGULATORY", "DCD/KL/TR/2026/1", "Drugs Control Dept")
    with pytest.raises(PermissionDenied):  # admins cannot record the pharmacist's quality authorization
        transfer_service.add_authorization(session, admin, t.id, "QUALITY", "Q-1")
    transfer_service.add_authorization(session, reviewer, t.id, "QUALITY", "QA-2026-9")
    with pytest.raises(SafetyViolation):
        transfer_service.mark_authorized(session, admin, t.id)
    transfer_service.add_authorization(session, reviewer, t.id, "PROFESSIONAL", "KSPC-12345")
    transfer_service.mark_authorized(session, admin, t.id)
    transfer_service.mark_completed(session, admin, t.id, "Handover documented")
    assert t.status == "COMPLETED"
    # Recipient org sees the record, an unrelated org does not.
    assert transfer_service.list_transfers(session, ngo_actor)[0]["status"] == "COMPLETED"
    other, _ = make_org(session, admin, "x-org@test.in", "NGO", "NGO")
    assert transfer_service.list_transfers(session, other) == []


def test_6b_transfer_quantity_cannot_exceed_batch(session, donor, ngo, medicine, reviewer, admin):
    donor_actor, _ = donor
    ngo_actor, _ = ngo
    b = _approved_batch(session, donor_actor, medicine, reviewer)
    item = _accepted_interest(session, ngo_actor, b, reviewer)
    from services.errors import ValidationError
    with pytest.raises(ValidationError):
        transfer_service.create_transfer(session, admin, inventory_batch_id=b.id,
                                         institutional_request_item_id=item.id, quantity=10_000)


def test_6c_recipient_needs_licence(session, admin, donor, medicine, reviewer):
    donor_actor, _ = donor
    ngo_actor, _ = make_org(session, admin, "nolic-ngo@test.in", "NGO", "NGO", auth_type=None)
    b = _approved_batch(session, donor_actor, medicine, reviewer)
    r = irs.create_request(session, ngo_actor, required_category="Analgesic", medicine_name="Paracetamol",
                           medicine_id=medicine.id, requested_quantity=50, unit="TABLETS", purpose="Clinic")
    item = irs.express_interest(session, ngo_actor, r.id, b.id, 50)
    review_service.review_interest(session, reviewer, item.id, True, "Need verified by reviewer")
    with pytest.raises(SafetyViolation):
        transfer_service.create_transfer(session, admin, inventory_batch_id=b.id,
                                         institutional_request_item_id=item.id, quantity=50)


# 7 ---------------------------------------------------------------------------
def test_7_unauthorized_users_cannot_access_private_records(session, admin, individual, donor, medicine):
    donor_actor, org = donor
    stranger = make_user(session, "s@test.in", "INDIVIDUAL")
    reg = medicine_service.create_registration(session, individual, **reg_data())
    b = ready_batch(session, donor_actor, medicine)
    with pytest.raises(PermissionDenied):
        medicine_service.get_registration(session, stranger, reg.id)
    with pytest.raises(PermissionDenied):
        inventory_service.get_batch(session, stranger, b.id)
    with pytest.raises(PermissionDenied):
        organization_service.get_organization(session, stranger, org.id)
    with pytest.raises(PermissionDenied):
        organization_service.read_document(session, stranger, b.source_document_file_id)
    with pytest.raises(PermissionDenied):
        disposal_service.trace_registration(session, stranger, reg.id)


# 8 ---------------------------------------------------------------------------
def test_8_disposal_records_traceable(session, centre, individual):
    actor, _, c = centre
    reg = medicine_service.create_registration(session, individual, **reg_data())
    req = collection_service.create_request(session, individual, centre_id=c.id, registration_ids=[reg.id],
                                            collection_method="DROP_OFF", preferred_date=None)
    collection_service.accept_request(session, actor, req.id)
    collection_service.schedule_request(session, actor, req.id, datetime.now() + timedelta(hours=1))
    collection_service.confirm_receipt(session, actor, req.id, {reg.id: 10})
    collection_service.initiate_disposal(session, actor, req.id)
    disposal_service.record_disposal(session, actor, req.id, disposal_method="INCINERATION", disposal_date=date.today(),
                                     quantity_disposed=10, unit="TABLETS", disposal_facility="CBWTF",
                                     reference_number="MAN-9")
    trace = disposal_service.trace_registration(session, individual, reg.id)
    assert trace["chain"][0]["disposals"][0]["reference"] == "MAN-9"
    assert session.scalar(select(AuditLog).where(AuditLog.action == "DISPOSAL_RECORDED"))


# 9 ---------------------------------------------------------------------------
def test_9_administrative_decisions_are_audited(session, admin, donor):
    _, org = donor
    actions = set(session.scalars(select(AuditLog.action)))
    assert {"ORG_REGISTERED", "ORG_UNDER_REVIEW", "ORG_VERIFIED", "AUTHORIZATION_VERIFIED"} <= actions
    organization_service.decide_verification(session, admin, org.id, "SUSPENDED", "Audit test suspension")
    log = session.scalar(select(AuditLog).where(AuditLog.action == "ORG_SUSPENDED"))
    assert log.actor_user_id == admin.user_id and log.details == "Audit test suspension"


def test_restricted_products_never_recommended(session, donor, ngo, medicine, reviewer, admin):
    donor_actor, _ = donor
    ngo_actor, _ = ngo
    b = _approved_batch(session, donor_actor, medicine, reviewer)
    medicine.restricted_classification = "PSYCHOTROPIC"  # master reclassified after approval
    r = irs.create_request(session, ngo_actor, required_category="Analgesic", medicine_name="Paracetamol",
                           requested_quantity=5, unit="TABLETS", purpose="Clinic")
    assert irs.recommendations(session, ngo_actor, r.id) == []
