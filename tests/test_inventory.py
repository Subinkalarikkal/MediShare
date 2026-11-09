from datetime import date, timedelta

import pytest

from services import inventory_service, review_service, transfer_service
from services.errors import PermissionDenied, SafetyViolation, ValidationError, WorkflowError
from tests.conftest import FULL_CHECKLIST, make_org, ready_batch


def test_unverified_org_cannot_submit_inventory(session, admin, medicine):
    actor, _ = make_org(session, admin, "new@test.in", "INSTITUTIONAL_DONOR", "HOSPITAL", verify=False,
                        auth_type="HOSPITAL_PHARMACY_LICENCE")
    with pytest.raises(PermissionDenied):
        inventory_service.create_batch(session, actor, medicine_id=medicine.id, batch_number="X1",
                                       expiry_date=date.today() + timedelta(days=300), quantity=5, unit="TABLETS")


def test_verified_org_without_licence_cannot_submit(session, admin, medicine):
    actor, _ = make_org(session, admin, "nolic@test.in", "INSTITUTIONAL_DONOR", "HOSPITAL", auth_type=None)
    b = ready_batch(session, actor, medicine)
    with pytest.raises(WorkflowError, match="authorization"):
        inventory_service.submit_batch(session, actor, b.id)


def test_missing_documentation_blocks_submission_and_notifies(session, donor, medicine):
    actor, _ = donor
    b = inventory_service.create_batch(session, actor, medicine_id=medicine.id, batch_number="ND1",
                                       expiry_date=date.today() + timedelta(days=300), quantity=5, unit="TABLETS",
                                       original_packaging_status="SEALED_ORIGINAL", storage_conditions="Cool",
                                       recall_status="CHECKED_NO_RECALL")
    with pytest.raises(WorkflowError, match="documentation"):
        inventory_service.submit_batch(session, actor, b.id)
    from services import notification_service
    assert any(n.type == "DOCUMENTATION_MISSING" for n in notification_service.list_my_notifications(session, actor))


def test_expired_batch_cannot_enter_review(session, donor, medicine):
    actor, _ = donor
    b = ready_batch(session, actor, medicine, batch_number="EXP1", manufacturing_date=date.today() - timedelta(days=800),
                    expiry_date=date.today() - timedelta(days=1))
    with pytest.raises(SafetyViolation):
        inventory_service.submit_batch(session, actor, b.id)


def test_opened_packaging_cannot_enter_review(session, donor, medicine):
    actor, _ = donor
    b = ready_batch(session, actor, medicine, batch_number="OP1", original_packaging_status="OPENED")
    with pytest.raises(WorkflowError, match="sealed"):
        inventory_service.submit_batch(session, actor, b.id)


def test_restricted_product_rejected(session, donor, restricted_medicine, medicine):
    actor, _ = donor
    with pytest.raises(SafetyViolation):
        ready_batch(session, actor, restricted_medicine)
    with pytest.raises(SafetyViolation):
        ready_batch(session, actor, medicine, batch_number="CC1", product_classification="COLD_CHAIN")


def test_duplicate_batch_within_org_rejected(session, donor, medicine):
    actor, _ = donor
    ready_batch(session, actor, medicine)
    with pytest.raises(ValidationError):
        ready_batch(session, actor, medicine)


def test_full_review_rejection_and_quarantine(session, donor, medicine, reviewer):
    actor, _ = donor
    b1 = ready_batch(session, actor, medicine, batch_number="R1")
    inventory_service.submit_batch(session, actor, b1.id)
    review_service.record_decision(session, reviewer, b1.id, "START_REVIEW", notes="Starting review")
    review_service.record_decision(session, reviewer, b1.id, "REJECT", notes="Storage log incomplete")
    assert b1.status == "REJECTED"
    b2 = ready_batch(session, actor, medicine, batch_number="Q1")
    inventory_service.submit_batch(session, actor, b2.id)
    review_service.record_decision(session, reviewer, b2.id, "START_REVIEW", notes="Starting review")
    review_service.record_decision(session, reviewer, b2.id, "QUARANTINE", notes="Packaging tamper suspected")
    assert b2.status == "QUARANTINED"
    assert len(review_service.org_review_history(session, actor)) == 4


def test_approval_requires_checklist_and_reference(session, donor, medicine, reviewer):
    actor, _ = donor
    b = ready_batch(session, actor, medicine)
    inventory_service.submit_batch(session, actor, b.id)
    review_service.record_decision(session, reviewer, b.id, "START_REVIEW", notes="Starting review")
    with pytest.raises(WorkflowError):
        review_service.record_decision(session, reviewer, b.id, "APPROVE_FOR_FURTHER_AUTHORIZED_PROCESS",
                                       notes="Looks fine", checklist={"batch_verified": True}, authorization_reference="R")
    with pytest.raises(ValidationError):
        review_service.record_decision(session, reviewer, b.id, "APPROVE_FOR_FURTHER_AUTHORIZED_PROCESS",
                                       notes="Looks fine", checklist=FULL_CHECKLIST)
    review_service.record_decision(session, reviewer, b.id, "APPROVE_FOR_FURTHER_AUTHORIZED_PROCESS",
                                   notes="All checks complete", checklist=FULL_CHECKLIST, authorization_reference="DC/2026/1")
    assert b.status == "APPROVED_FOR_FURTHER_AUTHORIZED_PROCESS"


def test_unverified_reviewer_cannot_review(session, donor, medicine):
    from tests.conftest import make_user
    r = make_user(session, "unv@test.in", "REVIEWER", professional_verified=False)
    actor, _ = donor
    b = ready_batch(session, actor, medicine)
    inventory_service.submit_batch(session, actor, b.id)
    with pytest.raises(PermissionDenied):
        review_service.record_decision(session, r, b.id, "START_REVIEW", notes="Starting review")


def test_org_cannot_bypass_reviewer(session, donor, medicine):
    actor, _ = donor
    b = ready_batch(session, actor, medicine)
    inventory_service.submit_batch(session, actor, b.id)
    with pytest.raises(PermissionDenied):
        review_service.record_decision(session, actor, b.id, "APPROVE_FOR_FURTHER_AUTHORIZED_PROCESS",
                                       notes="self approve", checklist=FULL_CHECKLIST, authorization_reference="x")


def test_expired_approved_batches_get_quarantined(session, donor, medicine, reviewer, admin):
    actor, _ = donor
    b = ready_batch(session, actor, medicine)
    inventory_service.submit_batch(session, actor, b.id)
    b.expiry_date = date.today()  # simulate time passing
    assert inventory_service.quarantine_expired(session, admin) == 1
    assert b.status == "QUARANTINED"


def test_unauthorized_transfer_attempt(session, donor, medicine, individual):
    actor, _ = donor
    b = ready_batch(session, actor, medicine)
    with pytest.raises(PermissionDenied):
        transfer_service.create_transfer(session, actor, inventory_batch_id=b.id,
                                         institutional_request_item_id=b.id, quantity=1)
    with pytest.raises(PermissionDenied):
        transfer_service.create_transfer(session, individual, inventory_batch_id=b.id,
                                         institutional_request_item_id=b.id, quantity=1)
