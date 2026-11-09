"""Username/password sign-in, password management and email-verified password reset.

* Passwords are hashed with Argon2id (see auth_service.hash_password); plain passwords are never stored or logged.
* Sign-in accepts a username or the account email. Errors never reveal whether an account exists.
* 5 failed attempts within 15 minutes lock password sign-in for that account for 15 minutes
  (the emailed-code sign-in remains available).
* Password reset requires a one-time code emailed to the account address (purpose "reset", separate
  from sign-in codes).
"""
from __future__ import annotations

import re
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import LoginEvent, User
from services import email_auth_service as eas
from services import email_service
from services.auth_service import LoginResult, _hasher, hash_password, resolve_login, verify_password
from services.errors import PermissionDenied, ServiceError, ValidationError
from services.permission_service import Actor, require_active
from utils.audit_logger import audit
from utils.date_utils import utcnow

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._]{2,29}$")
MIN_LENGTH = 10
MAX_FAILURES = 5
LOCK_WINDOW = timedelta(minutes=15)
COMMON = {"password", "password1", "password123", "1234567890", "qwertyuiop", "medishare", "medishare123",
          "iloveyou12", "admin12345", "letmein123", "welcome123", "abcdefghij"}
GENERIC_FAIL = "Incorrect username/email or password."
_DUMMY_HASH = _hasher.hash("dummy-password-for-constant-time")


class LoginLocked(ServiceError):
    pass


# ---------------------------------------------------------------- validation
def validate_username(session: Session, username: str, exclude_user_id: uuid.UUID | None = None) -> str:
    u = (username or "").strip().lower()
    if not USERNAME_RE.match(u):
        raise ValidationError("Username must be 3–30 characters: lowercase letters, numbers, dots or underscores, "
                              "starting with a letter or number.")
    q = select(User.id).where(func.lower(User.username) == u)
    if exclude_user_id:
        q = q.where(User.id != exclude_user_id)
    if session.scalar(q):
        raise ValidationError("This username is already taken.")
    return u


def password_problems(password: str, email: str = "", username: str = "") -> list[str]:
    p = password or ""
    problems = []
    if len(p) < MIN_LENGTH:
        problems.append(f"Password must be at least {MIN_LENGTH} characters.")
    if len(p) > 128:
        problems.append("Password must be at most 128 characters.")
    if not re.search(r"[A-Za-z]", p) or not re.search(r"\d", p):
        problems.append("Password must contain both letters and numbers.")
    low = p.lower()
    if low in COMMON or len(set(p)) < 4:
        problems.append("This password is too easy to guess.")
    local = (email or "").split("@")[0].lower()
    if (local and len(local) >= 4 and local in low) or (username and username.lower() in low):
        problems.append("Password must not contain your email name or username.")
    return problems


def _check_new_password(password: str, confirm: str | None, email: str, username: str = "") -> None:
    problems = password_problems(password, email, username)
    if confirm is not None and password != confirm:
        problems.append("Passwords do not match.")
    if problems:
        raise ValidationError(problems)


# ---------------------------------------------------------------- sign-in
def _find_user(session: Session, identifier: str) -> User | None:
    ident = (identifier or "").strip().lower()
    if not ident:
        return None
    col = User.email if "@" in ident else func.lower(User.username)
    return session.scalar(select(User).where(col == ident, User.deleted_at.is_(None)))


def _recent_failures(session: Session, key: str) -> int:
    return session.scalar(select(func.count(LoginEvent.id)).where(
        LoginEvent.email == key, LoginEvent.event_type == "LOGIN_PASSWORD_FAILED",
        LoginEvent.created_at > utcnow() - LOCK_WINDOW)) or 0


def login_with_password(session: Session, identifier: str, password: str) -> LoginResult:
    user = _find_user(session, identifier)
    key = user.email if user else (identifier or "").strip().lower()[:320]
    if _recent_failures(session, key) >= MAX_FAILURES:
        raise LoginLocked("Too many failed attempts. Try again in 15 minutes, or sign in with an email code.")
    if user is None or not user.password_hash:
        verify_password(_DUMMY_HASH, password or "")  # same work whether or not the account exists
        ok = False
    else:
        ok = verify_password(user.password_hash, password or "")
    if not ok:
        session.add(LoginEvent(user_id=user.id if user else None, email=key, event_type="LOGIN_PASSWORD_FAILED",
                               provider="password"))
        session.flush()
        raise ValidationError(GENERIC_FAIL)
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return resolve_login(session, user.email, provider="password", email_verified=True)


# ---------------------------------------------------------------- set / change
def has_password(session: Session, actor: Actor) -> bool:
    fresh = require_active(session, actor, allow_pending=True)
    return bool(session.get(User, fresh.user_id).password_hash)


def set_password(session: Session, actor: Actor, new_password: str, confirm: str,
                 current_password: str | None = None) -> None:
    fresh = require_active(session, actor, allow_pending=True)
    user = session.get(User, fresh.user_id)
    if user.password_hash and not verify_password(user.password_hash, current_password or ""):
        raise PermissionDenied("Your current password is incorrect.")
    _check_new_password(new_password, confirm, user.email, user.username or "")
    user.password_hash = hash_password(new_password)
    audit(session, user.id, "PASSWORD_CHANGED" if current_password else "PASSWORD_SET", "user", user.id)
    _notify_change(user.email)


def set_username(session: Session, actor: Actor, username: str) -> str:
    fresh = require_active(session, actor, allow_pending=True)
    user = session.get(User, fresh.user_id)
    u = validate_username(session, username, exclude_user_id=user.id)
    before = {"username": user.username}
    user.username = u
    audit(session, user.id, "USERNAME_SET", "user", user.id, before, {"username": u})
    return u


def _notify_change(email: str) -> None:
    try:
        email_service.send_email(email, "Your MediShare password was changed",
                                 "The password for your MediShare account was just set or changed.\n\n"
                                 "If this wasn't you, reset your password immediately using 'Forgot password' "
                                 "and contact the MediShare administrators.")
    except (email_service.EmailNotConfigured, email_service.EmailSendError):
        pass


# ---------------------------------------------------------------- forgot password
def request_reset(session: Session, email: str) -> None:
    """Send a reset code if an account exists. The caller always shows the same message."""
    errors: list[str] = []
    from utils.validators import check_email
    norm = check_email(email, errors)
    if errors:
        raise ValidationError(errors)
    user = session.scalar(select(User).where(User.email == norm, User.deleted_at.is_(None)))
    if user is None or user.status in {"SUSPENDED", "DEACTIVATED"}:
        session.add(LoginEvent(email=norm, event_type="PASSWORD_RESET_UNKNOWN", provider="password"))
        return
    eas.request_code(session, norm, purpose="reset")


def reset_password(session: Session, email: str, code: str, new_password: str, confirm: str) -> None:
    norm = eas.verify_code(session, email, code, purpose="reset")
    user = session.scalar(select(User).where(User.email == norm, User.deleted_at.is_(None)))
    if user is None:
        raise eas.CodeInvalid("This code has expired or was already used. Request a new code.")
    _check_new_password(new_password, confirm, user.email, user.username or "")
    user.password_hash = hash_password(new_password)
    session.add(LoginEvent(user_id=user.id, email=norm, event_type="PASSWORD_RESET", provider="password"))
    audit(session, user.id, "PASSWORD_RESET", "user", user.id)
    _notify_change(user.email)
