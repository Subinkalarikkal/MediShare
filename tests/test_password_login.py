"""Username/password sign-in, password management and forgot-password reset."""
from __future__ import annotations

import re

import pytest
from streamlit.testing.v1 import AppTest

from database.models import User
from services import email_auth_service as eas
from services import email_service, password_service as ps
from services.errors import PermissionDenied, ValidationError
from tests.conftest import make_user

OUTBOX: list[tuple[str, str, str]] = []
GOOD = "Medicine2026safe"


@pytest.fixture(autouse=True)
def fake_mail():
    OUTBOX.clear()
    email_service.set_sender(lambda to, subject, body: OUTBOX.append((to, subject, body)))
    yield
    email_service.set_sender(None)


def last_code(to: str) -> str:
    body = next(b for t, _, b in reversed(OUTBOX) if t == to)
    return re.search(r"\b(\d{6})\b", body).group(1)


def user_with_password(session, email="pat@example.org", username="pat.k"):
    actor = make_user(session, email, "INDIVIDUAL")
    ps.set_username(session, actor, username)
    ps.set_password(session, actor, GOOD, GOOD)
    return actor


# ---------------------------------------------------------------- service
def test_login_with_username_or_email(session):
    user_with_password(session)
    assert ps.login_with_password(session, "pat.k", GOOD).outcome == "OK"
    assert ps.login_with_password(session, "PAT@Example.org", GOOD).outcome == "OK"
    u = session.query(User).filter_by(email="pat@example.org").one()
    assert u.password_hash.startswith("$argon2id$") and GOOD not in u.password_hash


def test_wrong_password_and_unknown_user_give_same_message(session):
    user_with_password(session)
    with pytest.raises(ValidationError) as e1:
        ps.login_with_password(session, "pat.k", "wrong-password-1")
    with pytest.raises(ValidationError) as e2:
        ps.login_with_password(session, "nobody", "wrong-password-1")
    assert e1.value.errors == e2.value.errors == [ps.GENERIC_FAIL]


def test_account_without_password_cannot_use_password_login(session):
    make_user(session, "codeonly@example.org", "INDIVIDUAL")
    with pytest.raises(ValidationError):
        ps.login_with_password(session, "codeonly@example.org", "")


def test_lockout_after_five_failures(session):
    user_with_password(session)
    for _ in range(5):
        with pytest.raises(ValidationError):
            ps.login_with_password(session, "pat.k", "nope-nope-1")
    with pytest.raises(ps.LoginLocked):
        ps.login_with_password(session, "pat.k", GOOD)  # even the right password is refused while locked


def test_suspended_user_cannot_sign_in_with_password(session, admin):
    from services import user_service
    a = user_with_password(session)
    user_service.suspend_user(session, admin, a.user_id, "Policy violation")
    assert ps.login_with_password(session, "pat.k", GOOD).outcome == "SUSPENDED"


@pytest.mark.parametrize("pw", ["short1", "onlyletterslong", "1234567890123", "password123", "aaaaaaaaaa1",
                                "patrick2026x"])
def test_weak_passwords_rejected(pw):
    assert ps.password_problems(pw, "patrick@example.org", "pk")


def test_change_password_requires_current(session):
    a = user_with_password(session)
    with pytest.raises(PermissionDenied):
        ps.set_password(session, a, "Another2026pass", "Another2026pass", "wrong-current-1")
    with pytest.raises(ValidationError):
        ps.set_password(session, a, "Another2026pass", "Mismatch2026pass", GOOD)
    ps.set_password(session, a, "Another2026pass", "Another2026pass", GOOD)
    assert ps.login_with_password(session, "pat.k", "Another2026pass").outcome == "OK"
    assert any("password was changed" in s for _, s, _ in OUTBOX)


def test_username_rules_and_uniqueness(session):
    user_with_password(session)
    other = make_user(session, "other@example.org", "INDIVIDUAL")
    with pytest.raises(ValidationError, match="taken"):
        ps.set_username(session, other, "PAT.K")
    with pytest.raises(ValidationError):
        ps.set_username(session, other, "x!")


def test_forgot_password_flow(session):
    user_with_password(session)
    ps.request_reset(session, "pat@example.org")
    code = last_code("pat@example.org")
    assert "password reset code" in OUTBOX[-1][1]
    ps.reset_password(session, "pat@example.org", code, "Brandnew2026pass", "Brandnew2026pass")
    assert ps.login_with_password(session, "pat.k", "Brandnew2026pass").outcome == "OK"
    with pytest.raises(ValidationError):
        ps.login_with_password(session, "pat.k", GOOD)
    with pytest.raises(eas.CodeInvalid):  # reset code is single use
        ps.reset_password(session, "pat@example.org", code, "Other2026passwd", "Other2026passwd")


def test_reset_for_unknown_email_sends_nothing(session):
    ps.request_reset(session, "ghost@example.org")
    assert OUTBOX == []


def test_login_and_reset_codes_are_not_interchangeable(session):
    user_with_password(session)
    eas.request_code(session, "pat@example.org", purpose="login")
    login_code = last_code("pat@example.org")
    with pytest.raises(eas.CodeInvalid):
        ps.reset_password(session, "pat@example.org", login_code, "Brandnew2026pass", "Brandnew2026pass")
    ps.request_reset(session, "pat@example.org")
    reset_code = last_code("pat@example.org")
    with pytest.raises(eas.CodeInvalid):
        eas.verify_code(session, "pat@example.org", reset_code, purpose="login")


def test_signup_with_username_and_password(session):
    from services import auth_service
    with pytest.raises(ValidationError):
        auth_service.register_user(session, email="n@example.org", full_name="N", account_type="INDIVIDUAL",
                                   username="nn", password=GOOD, password_confirm=GOOD)  # username too short
    auth_service.register_user(session, email="n@example.org", full_name="N", account_type="INDIVIDUAL",
                               username="new.user", password=GOOD, password_confirm=GOOD)
    assert ps.login_with_password(session, "new.user", GOOD).outcome == "OK"


# ---------------------------------------------------------------- UI
from tests.test_app import APP, _use_seeded, seeded_db  # noqa: E402,F401


@pytest.fixture
def email_mode(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AUTH_MODE", "email")


def _login() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.switch_page("app_pages/login.py").run()
    return at


def _set_pw(email, username, pw):
    from database import connection
    from services.permission_service import load_actor
    with connection.session_scope() as s:
        u = s.query(User).filter_by(email=email).one()
        a = load_actor(s, u.id)
        if not u.username:
            ps.set_username(s, a, username)
        if u.password_hash:
            u.password_hash = None
        ps.set_password(s, a, pw, pw)


def test_ui_login_page_has_three_tabs(email_mode):
    at = _login()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Password", "Email code / New account", "Forgot password"]


def test_ui_password_sign_in(email_mode):
    _set_pw("rahul@sample.medishare.in", "rahul.m", GOOD)
    at = _login()
    at.text_input[0].input("rahul.m")
    at.text_input[1].input("wrong-password-9")
    next(b for b in at.button if b.label == "Sign in").click().run()
    assert any(ps.GENERIC_FAIL in e.value for e in at.error)
    at.text_input[0].input("rahul.m")
    at.text_input[1].input(GOOD)
    next(b for b in at.button if b.label == "Sign in").click().run()
    assert not at.exception and at.session_state["auth_provider"] == "password"
    assert any(t.value.startswith("Welcome, Rahul") for t in at.title)


def test_ui_forgot_password(email_mode):
    _set_pw("fathima@sample.medishare.in", "fathima.r", GOOD)
    at = _login()
    reset_inputs = lambda: [t for t in at.text_input if t.label in  # noqa: E731
                            {"Account email", "Reset code", "New password", "Confirm new password"}]
    reset_inputs()[0].input("fathima@sample.medishare.in")
    next(b for b in at.button if b.label == "Send reset code").click().run()
    assert any("we've emailed it a reset code" in s.value for s in at.success)
    code = last_code("fathima@sample.medishare.in")
    f = {t.label: t for t in reset_inputs()}
    f["Reset code"].input(code)
    f["New password"].input("Resetdone2026ok")
    f["Confirm new password"].input("Resetdone2026ok")
    next(b for b in at.button if b.label == "Set new password").click().run()
    assert not at.exception
    assert any("password has been changed" in s.value for s in at.success)
    from database import connection
    with connection.session_scope() as s:
        assert ps.login_with_password(s, "fathima.r", "Resetdone2026ok").outcome == "OK"


def test_ui_profile_set_password(email_mode):
    at = AppTest.from_file(APP, default_timeout=60)
    from database import connection
    from utils.date_utils import utcnow
    with connection.session_scope() as s:
        u = s.query(User).filter_by(email="admin@sample.medishare.in").one()
        u.password_hash, u.username, uid = None, None, str(u.id)
    at.session_state["auth_user_id"] = uid
    at.session_state["auth_email"] = "admin@sample.medishare.in"
    at.session_state["auth_login_time"] = utcnow()
    at.session_state["auth_provider"] = "email"
    at.run()
    at.switch_page("app_pages/14_Profile.py").run()
    assert not at.exception
    f = {t.label: t for t in at.text_input}
    f["Username"].input("platform.admin")
    next(b for b in at.button if b.label == "Save username").click().run()
    f = {t.label: t for t in at.text_input}
    f["New password"].input("Adminpass2026x")  # contains the email name "admin" -> friendly error, no crash
    f["Confirm new password"].input("Adminpass2026x")
    next(b for b in at.button if b.label == "Set password").click().run()
    assert not at.exception
    assert any("must not contain your email name" in e.value for e in at.error)
    f = {t.label: t for t in at.text_input}
    f["New password"].input("Platform2026key")
    f["Confirm new password"].input("Platform2026key")
    next(b for b in at.button if b.label == "Set password").click().run()
    assert not at.exception
    with connection.session_scope() as s:
        assert ps.login_with_password(s, "platform.admin", "Platform2026key").outcome == "OK"
