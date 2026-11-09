from datetime import date, timedelta

import pytest

from services import medicine_service
from services.errors import PermissionDenied, SafetyViolation, ValidationError, WorkflowError
from tests.conftest import png_bytes, reg_data


def test_valid_registration_submitted_and_notified(session, individual, png):
    reg = medicine_service.create_registration(session, individual, photo=(png, "box.png"), **reg_data())
    assert reg.status == "SUBMITTED" and reg.photo_file_id and reg.submitted_at
    from services import notification_service
    assert notification_service.list_my_notifications(session, individual)[0].type == "REGISTRATION_SUBMITTED"


def test_expired_household_medicine_can_be_registered_for_disposal(session, individual):
    reg = medicine_service.create_registration(session, individual,
                                               **reg_data(expiry_date=date.today() - timedelta(days=30)))
    assert reg.status == "SUBMITTED"


@pytest.mark.parametrize("over", [
    dict(expiry_date=None),
    dict(manufacturing_date=date.today() + timedelta(days=5)),
    dict(manufacturing_date=date.today(), expiry_date=date.today() - timedelta(days=5)),
    dict(expiry_date="2026-13-45"),
])
def test_invalid_dates(session, individual, over):
    with pytest.raises(ValidationError):
        medicine_service.create_registration(session, individual, **reg_data(**over))


def test_missing_medicine_information(session, individual):
    with pytest.raises(ValidationError) as e:
        medicine_service.create_registration(session, individual, **reg_data(medicine_name="  ", unit=None))
    assert any("Medicine name" in x for x in e.value.errors)


@pytest.mark.parametrize("qty", [0, -5, "abc", 10_000_001])
def test_invalid_quantity(session, individual, qty):
    with pytest.raises(ValidationError):
        medicine_service.create_registration(session, individual, **reg_data(quantity=qty))


def test_invalid_file_upload_rejected(session, individual):
    with pytest.raises(ValidationError):
        medicine_service.create_registration(session, individual, photo=(b"MZ\x90\x00 not an image", "x.png"),
                                             **reg_data())
    with pytest.raises(ValidationError):  # pdf disguised as image
        medicine_service.create_registration(session, individual, photo=(b"%PDF-1.4 ...", "x.png"), **reg_data())
    with pytest.raises(ValidationError):  # disallowed extension
        medicine_service.create_registration(session, individual, photo=(png_bytes(), "x.gif"), **reg_data())
    with pytest.raises(ValidationError):  # too small to read
        medicine_service.create_registration(session, individual, photo=(png_bytes(20, 20, ""), "x.png"), **reg_data())


def test_restricted_products_excluded(session, individual, restricted_medicine):
    with pytest.raises(SafetyViolation):
        medicine_service.create_registration(session, individual, medicine_id=restricted_medicine.id, **reg_data())
    with pytest.raises(SafetyViolation):
        medicine_service.create_registration(session, individual, declared_restricted="COLD_CHAIN", **reg_data())


def test_draft_edit_submit_cancel(session, individual):
    reg = medicine_service.create_registration(session, individual, submit=False, **reg_data())
    assert reg.status == "DRAFT"
    medicine_service.update_draft(session, individual, reg.id, **reg_data(quantity=3))
    medicine_service.submit_registration(session, individual, reg.id)
    with pytest.raises(WorkflowError):
        medicine_service.update_draft(session, individual, reg.id, **reg_data())
    medicine_service.cancel_registration(session, individual, reg.id, "No longer needed")
    assert reg.status == "CANCELLED"


def test_users_cannot_see_each_others_registrations(session, individual):
    from tests.conftest import make_user
    other = make_user(session, "other@test.in", "INDIVIDUAL")
    reg = medicine_service.create_registration(session, individual, **reg_data())
    with pytest.raises(PermissionDenied):
        medicine_service.get_registration(session, other, reg.id)
    assert medicine_service.list_my_registrations(session, other) == []


def test_organizations_cannot_register_household_medicines(session, ngo):
    ngo_actor, _ = ngo
    with pytest.raises(PermissionDenied):
        medicine_service.create_registration(session, ngo_actor, **reg_data())


def test_medicine_master_admin_only_and_no_clinical_content(session, admin, individual):
    with pytest.raises(PermissionDenied):
        medicine_service.create_medicine(session, individual, name="X")
    with pytest.raises(ValidationError):
        medicine_service.create_medicine(session, admin, name="Y", category="Adult dosage 2x daily")
    m = medicine_service.create_medicine(session, admin, name="Cetirizine 10mg", dosage_form="TABLET")
    with pytest.raises(ValidationError):
        medicine_service.create_medicine(session, admin, name="cetirizine 10mg", dosage_form="TABLET")
    assert medicine_service.search_medicines(session, "ceti")[0].id == m.id


def test_admin_hold_and_release(session, admin, individual):
    reg = medicine_service.create_registration(session, individual, **reg_data())
    medicine_service.admin_set_status(session, admin, reg.id, "ON_HOLD", "Checking duplicate")
    assert reg.status == "ON_HOLD"
    medicine_service.admin_set_status(session, admin, reg.id, "SUBMITTED", "Duplicate cleared")
    assert reg.status == "SUBMITTED"
