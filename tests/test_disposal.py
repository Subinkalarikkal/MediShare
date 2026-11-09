from datetime import date, datetime, timedelta

import pytest

from services import collection_service, disposal_service, medicine_service
from services.errors import PermissionDenied, ValidationError, WorkflowError
from tests.conftest import PDF_BYTES, reg_data


def _collected(session, centre, individual):
    actor, _, c = centre
    reg = medicine_service.create_registration(session, individual, **reg_data())
    req = collection_service.create_request(session, individual, centre_id=c.id, registration_ids=[reg.id],
                                            collection_method="DROP_OFF", preferred_date=None)
    collection_service.accept_request(session, actor, req.id)
    collection_service.schedule_request(session, actor, req.id, datetime.now() + timedelta(hours=2))
    collection_service.confirm_receipt(session, actor, req.id, {reg.id: 10})
    return actor, req, reg


def _dispose(session, actor, req, **over):
    data = dict(disposal_method="AUTHORIZED_CBWTF", disposal_date=date.today(), quantity_disposed=10, unit="TABLETS",
                disposal_facility="Kerala CBWTF, Palakkad", reference_number="MAN-2026-001",
                certificate=(PDF_BYTES, "cert.pdf"))
    data.update(over)
    return disposal_service.record_disposal(session, actor, req.id, **data)


def test_disposal_requires_initiation(session, centre, individual):
    actor, req, _ = _collected(session, centre, individual)
    with pytest.raises(WorkflowError):
        _dispose(session, actor, req)


def test_disposal_completion_and_closure_traceable(session, centre, individual):
    actor, req, reg = _collected(session, centre, individual)
    collection_service.initiate_disposal(session, actor, req.id)
    rec = _dispose(session, actor, req)
    assert req.status == "DISPOSED" and reg.status == "DISPOSED" and rec.certificate_file_id
    collection_service.close_request(session, actor, req.id)
    assert req.status == "CLOSED" and reg.status == "CLOSED" and req.closure_date
    trace = disposal_service.trace_registration(session, individual, reg.id)
    assert trace["chain"][0]["disposals"][0]["reference"] == "MAN-2026-001"
    statuses = [e["to"] for e in trace["chain"][0]["events"]]
    assert statuses == ["REQUESTED", "ACCEPTED", "SCHEDULED", "COLLECTED", "DISPOSAL_PENDING", "DISPOSED", "CLOSED"]
    from services import notification_service
    types = {n.type for n in notification_service.list_my_notifications(session, individual)}
    assert {"COLLECTION_SCHEDULED", "COLLECTION_COMPLETED", "DISPOSAL_COMPLETED", "REQUEST_CLOSED"} <= types


@pytest.mark.parametrize("over", [
    dict(disposal_date=date.today() + timedelta(days=1)),
    dict(reference_number=""),
    dict(quantity_disposed=0),
    dict(disposal_method="FLUSH_DOWN_DRAIN"),
])
def test_disposal_validation(session, centre, individual, over):
    actor, req, _ = _collected(session, centre, individual)
    collection_service.initiate_disposal(session, actor, req.id)
    with pytest.raises(ValidationError):
        _dispose(session, actor, req, **over)


def test_only_handling_centre_can_record_disposal(session, admin, centre, individual):
    from tests.conftest import make_org
    other, _ = make_org(session, admin, "zz@test.in", "COLLECTION_CENTRE", "COLLECTION_CENTRE",
                        auth_type="BIOMEDICAL_WASTE_AUTHORIZATION")
    actor, req, _ = _collected(session, centre, individual)
    collection_service.initiate_disposal(session, actor, req.id)
    with pytest.raises(PermissionDenied):
        _dispose(session, other, req)
    with pytest.raises(PermissionDenied):
        _dispose(session, individual, req)
