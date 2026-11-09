from datetime import date, datetime, timedelta

import pytest

from services import collection_service, medicine_service
from services.errors import PermissionDenied, ValidationError, WorkflowError
from tests.conftest import make_org, reg_data


def _request(session, individual, centre_id, n=1):
    regs = [medicine_service.create_registration(session, individual, **reg_data(medicine_name=f"Med {i}"))
            for i in range(n)]
    req = collection_service.create_request(session, individual, centre_id=centre_id,
                                            registration_ids=[r.id for r in regs], collection_method="PICKUP",
                                            preferred_date=date.today() + timedelta(days=2),
                                            pickup_address="12 Lake View, Kochi")
    return req, regs


def test_valid_request_moves_registrations(session, centre, individual):
    _, _, c = centre
    req, regs = _request(session, individual, c.id, 2)
    assert req.status == "REQUESTED" and len(req.items) == 2
    assert all(r.status == "COLLECTION_PENDING" for r in regs)


def test_public_directory_lists_only_authorized_centres(session, admin, centre):
    actor, _ = make_org(session, admin, "c2@test.in", "COLLECTION_CENTRE", "COLLECTION_CENTRE",
                        auth_type="BIOMEDICAL_WASTE_AUTHORIZATION")
    collection_service.register_centre(session, actor, name="Unverified Centre", address="X", district="Ernakulam",
                                       pin_code="682011", contact_phone="+91 9000000001",
                                       accepted_categories=["EXPIRED_MEDICINES"])
    names = [c["name"] for c in collection_service.search_centres(session)]
    assert names == ["Kochi Collection Point"]
    assert collection_service.search_centres(session, district="Kollam") == []
    assert collection_service.search_centres(session, pin_code="6820")
    near = collection_service.search_centres(session, near=(9.98, 76.30))
    assert near[0]["distance_km"] is not None


def test_centre_verification_requires_collection_authorization(session, admin):
    actor, _ = make_org(session, admin, "noauth@test.in", "COLLECTION_CENTRE", "COLLECTION_CENTRE", auth_type=None)
    c = collection_service.register_centre(session, actor, name="No Auth", address="X", district="Kollam",
                                           pin_code="691001", contact_phone="+91 9000000002",
                                           accepted_categories=["EXPIRED_MEDICINES"])
    with pytest.raises(WorkflowError):
        collection_service.decide_centre(session, admin, c.id, "VERIFIED", "Trying to verify")


def test_invalid_collection_centre(session, admin, individual):
    actor, _ = make_org(session, admin, "c3@test.in", "COLLECTION_CENTRE", "COLLECTION_CENTRE",
                        auth_type="BIOMEDICAL_WASTE_AUTHORIZATION")
    c = collection_service.register_centre(session, actor, name="Pending Centre", address="X", district="Kollam",
                                           pin_code="691001", contact_phone="+91 9000000003",
                                           accepted_categories=["EXPIRED_MEDICINES"])
    reg = medicine_service.create_registration(session, individual, **reg_data())
    with pytest.raises(ValidationError):
        collection_service.create_request(session, individual, centre_id=c.id, registration_ids=[reg.id],
                                          collection_method="DROP_OFF", preferred_date=None)


def test_duplicate_request_rejected(session, centre, individual):
    _, _, c = centre
    req, regs = _request(session, individual, c.id)
    with pytest.raises(WorkflowError):
        collection_service.create_request(session, individual, centre_id=c.id, registration_ids=[regs[0].id],
                                          collection_method="DROP_OFF", preferred_date=None)


def test_pickup_requires_address_and_future_date(session, centre, individual):
    _, _, c = centre
    reg = medicine_service.create_registration(session, individual, **reg_data())
    with pytest.raises(ValidationError):
        collection_service.create_request(session, individual, centre_id=c.id, registration_ids=[reg.id],
                                          collection_method="PICKUP", preferred_date=date.today() - timedelta(days=1))


def test_cannot_request_for_someone_elses_registration(session, centre, individual):
    from tests.conftest import make_user
    _, _, c = centre
    other = make_user(session, "o@test.in", "INDIVIDUAL")
    reg = medicine_service.create_registration(session, other, **reg_data())
    with pytest.raises(PermissionDenied):
        collection_service.create_request(session, individual, centre_id=c.id, registration_ids=[reg.id],
                                          collection_method="DROP_OFF", preferred_date=None)


def test_accept_schedule_receive(session, centre, individual):
    actor, _, c = centre
    req, regs = _request(session, individual, c.id)
    collection_service.accept_request(session, actor, req.id)
    with pytest.raises(ValidationError):
        collection_service.schedule_request(session, actor, req.id, datetime.now() - timedelta(days=2))
    collection_service.schedule_request(session, actor, req.id, datetime.now() + timedelta(days=1))
    assert regs[0].status == "SCHEDULED"
    with pytest.raises(ValidationError):
        collection_service.confirm_receipt(session, actor, req.id, {})
    collection_service.confirm_receipt(session, actor, req.id, {regs[0].id: 10})
    assert req.status == "COLLECTED" and req.receipt_confirmed and regs[0].status == "COLLECTED"
    events = collection_service.request_events(session, individual, req.id)
    assert [e.to_status for e in events][-3:] == ["ACCEPTED", "SCHEDULED", "COLLECTED"]


def test_reject_returns_registrations(session, centre, individual):
    actor, _, c = centre
    req, regs = _request(session, individual, c.id)
    with pytest.raises(ValidationError):
        collection_service.reject_request(session, actor, req.id, "")
    collection_service.reject_request(session, actor, req.id, "Outside service area")
    assert req.status == "REJECTED" and regs[0].status == "SUBMITTED"


def test_user_cancel(session, centre, individual):
    _, _, c = centre
    req, regs = _request(session, individual, c.id)
    collection_service.cancel_request(session, individual, req.id)
    assert req.status == "CANCELLED" and regs[0].status == "SUBMITTED"


def test_invalid_transition(session, centre, individual):
    actor, _, c = centre
    req, regs = _request(session, individual, c.id)
    with pytest.raises(WorkflowError):
        collection_service.confirm_receipt(session, actor, req.id, {regs[0].id: 1})
