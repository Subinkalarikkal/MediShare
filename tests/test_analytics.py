from datetime import date, datetime, timedelta

import pytest

from services import analytics_service, collection_service, disposal_service, medicine_service, organization_service
from services.errors import PermissionDenied, WorkflowError
from tests.conftest import reg_data


def test_kpis_reflect_database(session, admin, centre, individual):
    k0 = analytics_service.admin_kpis(session, admin)
    assert k0["household_registrations"] == 0 and k0["verified_centres"] == 1
    actor, _, c = centre
    reg = medicine_service.create_registration(session, individual, **reg_data(quantity=12))
    req = collection_service.create_request(session, individual, centre_id=c.id, registration_ids=[reg.id],
                                            collection_method="DROP_OFF", preferred_date=None)
    k1 = analytics_service.admin_kpis(session, admin)
    assert k1["household_registrations"] == 1 and k1["pending_collection_requests"] == 1
    collection_service.accept_request(session, actor, req.id)
    collection_service.schedule_request(session, actor, req.id, datetime.now() + timedelta(hours=1))
    collection_service.confirm_receipt(session, actor, req.id, {reg.id: 11})
    collection_service.initiate_disposal(session, actor, req.id)
    disposal_service.record_disposal(session, actor, req.id, disposal_method="INCINERATION", disposal_date=date.today(),
                                     quantity_disposed=11, unit="TABLETS", disposal_facility="F", reference_number="R1")
    q = analytics_service.quantities_by_unit(session).set_index("unit").loc["TABLETS"]
    assert (q.registered, q.collected_received, q.verified_disposed) == (12, 11, 11)
    k2 = analytics_service.admin_kpis(session, admin)
    assert k2["completed_collections"] == 1 and k2["pending_collection_requests"] == 0
    assert analytics_service.public_stats(session)["disposal_records"] == 1
    perf = analytics_service.centre_performance(session, admin)
    assert perf.iloc[0].completed == 1
    assert not analytics_service.monthly_collection_activity(session, admin).empty
    assert not analytics_service.district_registrations(session, admin).empty


def test_analytics_requires_permission(session, individual):
    with pytest.raises(PermissionDenied):
        analytics_service.admin_kpis(session, individual)


def test_incomplete_documentation_report(session, admin):
    from tests.conftest import make_org
    make_org(session, admin, "gap@test.in", "INSTITUTIONAL_DONOR", "HOSPITAL", verify=False)
    df = analytics_service.incomplete_documentation(session, admin)
    assert "drug licence" in " ".join(df.gaps).lower()


def test_org_verification_flow_and_locked_fields(session, admin):
    from tests.conftest import ORG_DATA, make_user
    actor = make_user(session, "flow@test.in", "INSTITUTIONAL_DONOR", status="PENDING")
    org = organization_service.register_organization(session, actor, name="Flow Hospital", org_type="HOSPITAL",
                                                     registration_number="FH-1", **ORG_DATA)
    with pytest.raises(WorkflowError):
        organization_service.submit_for_verification(session, actor, org.id)
    from tests.conftest import PDF_BYTES
    organization_service.upload_document(session, actor, org.id, "REGISTRATION_CERTIFICATE", PDF_BYTES, "r.pdf")
    organization_service.submit_for_verification(session, actor, org.id)
    with pytest.raises(WorkflowError):
        organization_service.decide_verification(session, admin, org.id, "SUSPENDED", "Invalid transition")
    organization_service.decide_verification(session, admin, org.id, "VERIFIED", "Documents verified")
    with pytest.raises(WorkflowError):
        organization_service.update_organization(session, actor, org.id, name="Renamed Hospital",
                                                 registration_number="FH-1", **ORG_DATA)
    hist = organization_service.verification_history(session, actor, org.id)
    assert [h.to_status for h in hist][:2] == ["VERIFIED", "UNDER_REVIEW"]
    assert organization_service.public_verified_organizations(session)[0]["name"] == "Flow Hospital"
