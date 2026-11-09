"""Email verification sign-in: one-time codes, abuse limits, UI flow and real SMTP delivery."""
from __future__ import annotations

import re
import socketserver
import threading
from datetime import timedelta

import pytest
from streamlit.testing.v1 import AppTest

from database.models import EmailLoginCode, LoginEvent, User
from services import email_auth_service as eas
from services import email_service
from services.errors import ValidationError
from tests.conftest import make_user
from utils.date_utils import utcnow

OUTBOX: list[tuple[str, str, str]] = []


@pytest.fixture(autouse=True)
def fake_mail():
    OUTBOX.clear()
    email_service.set_sender(lambda to, subject, body: OUTBOX.append((to, subject, body)))
    yield
    email_service.set_sender(None)


def last_code(to: str) -> str:
    body = next(b for t, _, b in reversed(OUTBOX) if t == to)
    return re.search(r"\b(\d{6})\b", body).group(1)


# ---------------------------------------------------------------- service
def test_code_sent_and_verified_once(session):
    eas.request_code(session, "New.Person@Example.org")
    code = last_code("new.person@example.org")
    assert eas.verify_code(session, "new.person@example.org", code) == "new.person@example.org"
    with pytest.raises(eas.CodeInvalid):  # single use
        eas.verify_code(session, "new.person@example.org", code)
    row = session.query(EmailLoginCode).one()
    assert code not in row.code_hash and len(row.code_hash) == 64  # only a salted hash is stored
    assert {e.event_type for e in session.query(LoginEvent)} >= {"EMAIL_CODE_SENT", "EMAIL_VERIFIED"}


def test_wrong_code_attempts_lock_the_code(session):
    eas.request_code(session, "a@example.org")
    code = last_code("a@example.org")
    wrong = "000000" if code != "000000" else "111111"
    for i in range(4):
        with pytest.raises(eas.CodeInvalid, match="attempt"):
            eas.verify_code(session, "a@example.org", wrong)
    with pytest.raises(eas.CodeInvalid, match="Too many"):
        eas.verify_code(session, "a@example.org", wrong)
    with pytest.raises(eas.CodeInvalid):  # even the right code no longer works
        eas.verify_code(session, "a@example.org", code)


def test_expired_code_rejected(session):
    eas.request_code(session, "b@example.org")
    code = last_code("b@example.org")
    session.query(EmailLoginCode).one().expires_at = utcnow() - timedelta(seconds=1)
    with pytest.raises(eas.CodeInvalid, match="expired"):
        eas.verify_code(session, "b@example.org", code)


def test_only_newest_code_is_valid(session):
    eas.request_code(session, "c@example.org")
    first = last_code("c@example.org")
    eas.request_code(session, "c@example.org")
    second = last_code("c@example.org")
    if first != second:
        with pytest.raises(eas.CodeInvalid):
            eas.verify_code(session, "c@example.org", first)
    assert eas.verify_code(session, "c@example.org", second)


def test_rate_limit(session):
    for _ in range(3):
        eas.request_code(session, "d@example.org")
    with pytest.raises(eas.RateLimited):
        eas.request_code(session, "d@example.org")
    assert len(OUTBOX) == 3


def test_code_for_other_email_does_not_work(session):
    eas.request_code(session, "e@example.org")
    with pytest.raises(eas.CodeInvalid):
        eas.verify_code(session, "f@example.org", last_code("e@example.org"))


def test_invalid_email_rejected(session):
    with pytest.raises(ValidationError):
        eas.request_code(session, "not-an-email")
    assert OUTBOX == []


def test_unconfigured_smtp_raises(monkeypatch):
    email_service.set_sender(None)
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_FROM"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(email_service.EmailNotConfigured):
        email_service.send_email("x@example.org", "s", "b")


def test_real_smtp_delivery(monkeypatch):
    """send_email talks real SMTP to a local test mail server."""
    received = {}

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            w = lambda line: self.wfile.write((line + "\r\n").encode())  # noqa: E731
            w("220 test ESMTP")
            data, in_data = [], False
            while True:
                line = self.rfile.readline().decode(errors="replace")
                if not line:
                    return
                if in_data:
                    if line.strip() == ".":
                        in_data = False
                        received["data"] = "".join(data)
                        w("250 OK")
                    else:
                        data.append(line)
                    continue
                cmd = line.strip().upper()
                if cmd.startswith(("EHLO", "HELO")):
                    w("250 localhost")
                elif cmd.startswith("MAIL FROM"):
                    received["from"] = line.strip()
                    w("250 OK")
                elif cmd.startswith("RCPT TO"):
                    received["to"] = line.strip()
                    w("250 OK")
                elif cmd == "DATA":
                    in_data = True
                    w("354 End data with <CR><LF>.<CR><LF>")
                elif cmd == "QUIT":
                    w("221 Bye")
                    return
                else:
                    w("250 OK")

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        email_service.set_sender(None)
        monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
        monkeypatch.setenv("SMTP_PORT", str(server.server_address[1]))
        monkeypatch.setenv("SMTP_FROM", "MediShare <no-reply@medishare.test>")
        monkeypatch.delenv("SMTP_USER", raising=False)
        email_service.send_email("user@example.org", "Your MediShare sign-in code: 123456", "Code 123456")
    finally:
        server.shutdown()
    assert "user@example.org" in received["to"]
    assert "Subject: Your MediShare sign-in code: 123456" in received["data"]


# ---------------------------------------------------------------- UI (as a visitor on the hosted site)
from tests.test_app import APP, _use_seeded, seeded_db  # noqa: E402,F401


@pytest.fixture
def email_mode(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AUTH_MODE", "email")


def _login() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.switch_page("app_pages/login.py").run()
    return at


def _request(at: AppTest, email: str) -> AppTest:
    at.text_input[0].input(email)
    next(b for b in at.button if b.label == "Send verification code").click().run()
    return at


def _verify(at: AppTest, code: str) -> AppTest:
    at.text_input[0].input(code)
    next(b for b in at.button if b.label == "Verify and sign in").click().run()
    return at


def test_ui_no_account_picker_or_unverified_signup(email_mode):
    at = _login()
    assert not at.exception
    labels = [b.label for b in at.button]
    assert "Send verification code" in labels
    assert "Sign in" not in labels and "Continue to sign-up" not in labels and not at.selectbox


def test_ui_new_user_must_verify_before_signup(email_mode):
    from database import connection
    at = _request(_login(), "brand.new@example.org")
    assert any("We sent a 6-digit code" in s.value for s in at.success)
    _verify(at, "999999" if last_code("brand.new@example.org") != "999999" else "888888")
    assert any("Incorrect code" in e.value for e in at.error)
    assert "auth_user_id" not in at.session_state and "signup_email" not in at.session_state
    _verify(at, last_code("brand.new@example.org"))
    assert not at.exception
    assert at.session_state["signup_verified"] is True
    assert any("Verified email: brand.new@example.org" in m.value for m in at.markdown)
    at.text_input[0].input("Brand New")
    at.checkbox[0].check()
    next(b for b in at.button if b.label == "Create account").click().run()
    assert not at.exception
    with connection.session_scope() as s:
        u = s.query(User).filter_by(email="brand.new@example.org").one()
        assert u.status == "ACTIVE" and str(u.id) == at.session_state["auth_user_id"]


def test_ui_existing_user_signs_in_with_code(email_mode):
    at = _request(_login(), "anjali@sample.medishare.in")
    _verify(at, last_code("anjali@sample.medishare.in"))
    assert not at.exception and at.session_state["auth_provider"] == "email"
    assert any(h.value.startswith("Welcome, Anjali") for h in at.title)


def test_ui_onboarding_refuses_unverified_email(email_mode):
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["signup_email"] = "attacker@example.org"  # forged, no verification flag
    at.run()
    assert not any(b.label == "Create account" for b in at.button)
    assert any("verify your email" in e.value.lower() for e in at.error)


def test_ui_smtp_not_configured_is_friendly(email_mode, monkeypatch):
    email_service.set_sender(None)
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_FROM"):
        monkeypatch.delenv(k, raising=False)
    at = _login()
    assert not at.exception
    assert [e.value for e in at.error] == ["Sign-in is temporarily unavailable. Please try again later or use the Contact page."]
