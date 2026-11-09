from datetime import timedelta

import pytest

from services import auth_service
from services.errors import PermissionDenied, ValidationError
from tests.conftest import make_user
from utils.date_utils import utcnow


def test_valid_login_returns_actor_with_roles(session, individual):
    res = auth_service.resolve_login(session, "PERSON@test.in")
    assert res.outcome == "OK"
    assert "INDIVIDUAL" in res.actor.roles
    assert "medicine.register" in res.actor.permissions


def test_missing_email_is_invalid_login(session):
    with pytest.raises(PermissionDenied):
        auth_service.resolve_login(session, None)


def test_unverified_provider_email_is_rejected(session, individual):
    assert auth_service.resolve_login(session, "person@test.in", email_verified=False).outcome == "UNVERIFIED_EMAIL"


def test_unregistered_authenticated_user(session):
    res = auth_service.resolve_login(session, "stranger@test.in")
    assert res.outcome == "UNREGISTERED" and res.actor is None


def test_suspended_account_denied(session):
    make_user(session, "bad@test.in", "INDIVIDUAL", status="SUSPENDED")
    assert auth_service.resolve_login(session, "bad@test.in").outcome == "SUSPENDED"


def test_org_signup_is_pending_and_individual_is_active(session):
    u1 = auth_service.register_user(session, email="a@test.in", full_name="A", account_type="INDIVIDUAL")
    u2 = auth_service.register_user(session, email="b@test.in", full_name="B", account_type="INSTITUTIONAL_DONOR")
    assert u1.status == "ACTIVE" and u2.status == "PENDING"
    assert auth_service.resolve_login(session, "b@test.in").outcome == "PENDING"


def test_cannot_self_register_as_admin(session):
    with pytest.raises(ValidationError):
        auth_service.register_user(session, email="x@test.in", full_name="X", account_type="ADMIN")


def test_reviewer_signup_requires_credentials(session):
    with pytest.raises(ValidationError):
        auth_service.register_user(session, email="r@test.in", full_name="R", account_type="REVIEWER")


def test_duplicate_registration_rejected(session, individual):
    with pytest.raises(ValidationError):
        auth_service.register_user(session, email="person@test.in", full_name="Again", account_type="INDIVIDUAL")


def test_session_expiry():
    now = utcnow()
    assert not auth_service.is_session_expired(now - timedelta(minutes=10), 60, now)
    assert auth_service.is_session_expired(now - timedelta(minutes=61), 60, now)
    assert auth_service.is_session_expired(None, 60, now)


def test_logout_recorded(session, individual):
    from sqlalchemy import select
    from database.models import LoginEvent
    auth_service.record_logout(session, individual)
    assert session.scalar(select(LoginEvent).where(LoginEvent.event_type == "LOGOUT")) is not None


def test_argon2id_password_hashing():
    h = auth_service.hash_password("correct horse battery")
    assert h.startswith("$argon2id$")
    assert auth_service.verify_password(h, "correct horse battery")
    assert not auth_service.verify_password(h, "wrong password!!")
    with pytest.raises(ValidationError):
        auth_service.hash_password("short")


def test_bootstrap_super_admin_only_once(session):
    auth_service.bootstrap_super_admin(session, "root@test.in", "Root")
    assert "SUPER_ADMIN" in auth_service.resolve_login(session, "root@test.in").actor.roles
    with pytest.raises(PermissionDenied):
        auth_service.bootstrap_super_admin(session, "other@test.in", "Other")


def test_dev_login_never_enabled_in_production(monkeypatch):
    from config import Settings
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.setattr("config.load_dotenv", None, raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "dev")
    assert Settings().dev_login_enabled is False
    monkeypatch.setenv("APP_ENV", "development")
    assert Settings().dev_login_enabled is True
    monkeypatch.delenv("AUTH_MODE")
    assert Settings().auth_mode == "oidc"  # safe default
