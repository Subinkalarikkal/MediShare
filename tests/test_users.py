import pytest

from services import auth_service, notification_service, user_service
from services.errors import PermissionDenied, ValidationError
from tests.conftest import make_user


def test_profile_update_validates_and_audits(session, individual):
    from sqlalchemy import select
    from database.models import AuditLog
    user_service.update_profile(session, individual, full_name="New Name", phone="+91 9999999999",
                                district="Kollam", pin_code="691001", location="Kollam town")
    assert user_service.get_profile(session, individual).district == "Kollam"
    assert session.scalar(select(AuditLog).where(AuditLog.action == "PROFILE_UPDATED"))
    with pytest.raises(ValidationError):
        user_service.update_profile(session, individual, full_name="X", pin_code="12")


def test_admin_search_filter_suspend_reactivate(session, admin, individual):
    rows = user_service.search_users(session, admin, role="INDIVIDUAL")
    assert [r["email"] for r in rows] == ["person@test.in"]
    user_service.suspend_user(session, admin, individual.user_id, "Policy violation")
    assert auth_service.resolve_login(session, "person@test.in").outcome == "SUSPENDED"
    assert user_service.search_users(session, admin, status="SUSPENDED")
    user_service.reactivate_user(session, admin, individual.user_id, "Resolved issue")
    assert auth_service.resolve_login(session, "person@test.in").outcome == "OK"
    hist = user_service.activity_history(session, admin, individual.user_id)
    assert any(a["action"] == "USER_STATUS_SUSPENDED" for a in hist["audit"])
    assert notification_service.list_my_notifications(session, individual)


def test_suspend_requires_reason(session, admin, individual):
    with pytest.raises(ValidationError):
        user_service.suspend_user(session, admin, individual.user_id, "")


def test_admin_cannot_suspend_other_admin(session, admin):
    other = make_user(session, "admin2@test.in", "ADMIN")
    with pytest.raises(PermissionDenied):
        user_service.suspend_user(session, admin, other.user_id, "Not allowed here")


def test_only_super_admin_grants_admin_role(session, admin, super_admin, individual):
    with pytest.raises(PermissionDenied):
        user_service.assign_role(session, admin, individual.user_id, "ADMIN")
    user_service.assign_role(session, super_admin, individual.user_id, "ADMIN")
    assert "ADMIN" in user_service.user_roles(session, individual.user_id)


def test_reviewer_professional_verification_activates(session, admin):
    r = make_user(session, "rev2@test.in", "REVIEWER", status="PENDING")
    user_service.verify_professional(session, admin, r.user_id, "Council register checked")
    v = user_service.view_user(session, admin, r.user_id)
    assert v["professional_verified"] and v["status"] == "ACTIVE"


def test_notifications_mark_read_and_ownership(session, individual, admin):
    n = notification_service.notify(session, individual.user_id, "GENERAL", "Hi", "Hello")
    assert notification_service.unread_count(session, individual) == 1
    with pytest.raises(PermissionDenied):
        notification_service.mark_read(session, admin, n.id)
    notification_service.mark_read(session, individual, n.id)
    assert notification_service.unread_count(session, individual) == 0


def test_broadcast_requires_permission(session, admin, individual):
    with pytest.raises(PermissionDenied):
        notification_service.broadcast(session, individual, ["INDIVIDUAL"], "t", "m")
    assert notification_service.broadcast(session, admin, ["INDIVIDUAL"], "Notice", "Maintenance tonight") == 1


def test_contact_form_notifies_admins(session, admin):
    assert notification_service.submit_contact_message(session, "Asha", "asha@test.in", "Where is my nearest centre?") == 1
    with pytest.raises(ValidationError):
        notification_service.submit_contact_message(session, "", "bad", "x")
