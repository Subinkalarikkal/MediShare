"""Built-in administrator account created from a configured Argon2id hash."""
from __future__ import annotations

import pytest

from database.models import User
from services import auth_service, password_service as ps
from services.errors import ValidationError
from services.permission_service import load_actor

PW = "Seedtest2026pass"


@pytest.fixture
def pw_hash():
    return auth_service.hash_password(PW)


def test_seed_admin_created_and_can_sign_in(session, pw_hash):
    u = auth_service.ensure_seed_admin(session, "MediShare.Admin", "superadmin@medishare.invalid", pw_hash)
    assert u is not None and u.username == "medishare.admin" and u.status == "ACTIVE"
    actor = load_actor(session, u.id)
    assert "SUPER_ADMIN" in actor.roles and "settings.manage" in actor.permissions
    res = ps.login_with_password(session, "medishare.admin", PW)
    assert res.outcome == "OK" and res.actor.user_id == u.id
    assert u.password_hash == pw_hash  # only the hash is stored


def test_seed_admin_is_idempotent_and_keeps_changed_password(session, pw_hash):
    u = auth_service.ensure_seed_admin(session, "medishare.admin", "superadmin@medishare.invalid", pw_hash)
    ps.set_password(session, load_actor(session, u.id), "Changed2026pass", "Changed2026pass", PW)
    assert auth_service.ensure_seed_admin(session, "medishare.admin", "superadmin@medishare.invalid", pw_hash) is None
    assert session.query(User).filter_by(username="medishare.admin").count() == 1
    assert ps.login_with_password(session, "medishare.admin", "Changed2026pass").outcome == "OK"


def test_seed_admin_rejects_plain_password(session):
    with pytest.raises(ValidationError):
        auth_service.ensure_seed_admin(session, "medishare.admin", "superadmin@medishare.invalid", "plain-password-1")


def test_seed_admin_not_created_without_config(session):
    assert auth_service.ensure_seed_admin(session, "", "", "") is None


def test_seed_admin_does_not_block_owner_super_admin(session, pw_hash, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "medishare.admin")
    auth_service.ensure_seed_admin(session, "medishare.admin", "superadmin@medishare.invalid", pw_hash)
    owner = auth_service.bootstrap_super_admin(session, "owner@example.org", "Owner")
    assert "SUPER_ADMIN" in load_actor(session, owner.id).roles


def test_committed_profile_contains_hash_not_password():
    from pathlib import Path
    import tomllib
    prof = tomllib.loads((Path(__file__).resolve().parents[1] / "medishare_profile.toml").read_text(encoding="utf-8"))
    assert prof["ADMIN_USERNAME"] == "medishare.admin"
    assert prof["ADMIN_PASSWORD_HASH"].startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert "ADMIN_PASSWORD" not in prof  # never a plain password
