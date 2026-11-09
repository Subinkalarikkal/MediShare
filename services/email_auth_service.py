"""Passwordless sign-in with an emailed one-time code (proves the user owns the email address).

* 6-digit codes, valid for 10 minutes, single use.
* Stored only as a salted SHA-256 hash; compared in constant time.
* At most 5 wrong attempts per code; at most 3 codes per email per 15 minutes and 10 per day.
* Requesting a code for an unknown email behaves exactly like a known one (no account enumeration).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import EmailLoginCode, LoginEvent
from services import email_service
from services.errors import ServiceError, ValidationError
from utils.date_utils import utcnow
from utils.validators import check_email

CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5
MAX_PER_15_MIN = 3
MAX_PER_DAY = 10


class RateLimited(ServiceError):
    pass


class CodeInvalid(ServiceError):
    pass


def _hash(code: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()


def _normalise(email: str) -> str:
    errors: list[str] = []
    norm = check_email(email, errors)
    if errors:
        raise ValidationError(errors)
    return norm


def _event(session: Session, email: str, event: str) -> None:
    session.add(LoginEvent(email=email, event_type=event, provider="email"))


SUBJECTS = {"login": "Your MediShare sign-in code: {code}",
            "reset": "Your MediShare password reset code: {code}"}
INTROS = {"login": "Your MediShare verification code is: {code}",
          "reset": "Your MediShare password reset code is: {code}"}


def request_code(session: Session, email: str, purpose: str = "login") -> None:
    email = _normalise(email)
    now = utcnow()
    recent = session.scalar(select(func.count(EmailLoginCode.id)).where(
        EmailLoginCode.email == email, EmailLoginCode.created_at > now - timedelta(minutes=15))) or 0
    daily = session.scalar(select(func.count(EmailLoginCode.id)).where(
        EmailLoginCode.email == email, EmailLoginCode.created_at > now - timedelta(days=1))) or 0
    if recent >= MAX_PER_15_MIN or daily >= MAX_PER_DAY:
        _event(session, email, "EMAIL_CODE_RATE_LIMITED")
        raise RateLimited("Too many codes requested. Please wait 15 minutes and try again.")
    if purpose not in SUBJECTS:
        raise ValueError("unknown code purpose")
    for old in session.scalars(select(EmailLoginCode).where(EmailLoginCode.email == email,
                                                            EmailLoginCode.purpose == purpose,
                                                            EmailLoginCode.consumed_at.is_(None))):
        old.consumed_at = now  # only the newest code is valid
    code = f"{secrets.randbelow(1_000_000):06d}"
    salt = secrets.token_hex(16)
    session.add(EmailLoginCode(email=email, purpose=purpose, code_hash=_hash(code, salt), salt=salt,
                               expires_at=now + CODE_TTL))
    session.flush()
    email_service.send_email(
        email, SUBJECTS[purpose].format(code=code),
        INTROS[purpose].format(code=code) + "\n\n"
        f"It expires in {int(CODE_TTL.total_seconds() // 60)} minutes and can be used once.\n"
        "If you did not request this, you can ignore this email – your account is unchanged.\n\n"
        "MediShare will never ask you for this code by phone or message.")
    _event(session, email, "EMAIL_CODE_SENT" if purpose == "login" else "RESET_CODE_SENT")


def verify_code(session: Session, email: str, code: str, purpose: str = "login") -> str:
    """Return the verified, normalised email or raise CodeInvalid."""
    email = _normalise(email)
    code = (code or "").strip().replace(" ", "")
    now = utcnow()
    rec = session.scalar(select(EmailLoginCode).where(EmailLoginCode.email == email,
                                                      EmailLoginCode.purpose == purpose,
                                                      EmailLoginCode.consumed_at.is_(None))
                         .order_by(EmailLoginCode.created_at.desc()))
    if rec is None or rec.expires_at < now:
        _event(session, email, "EMAIL_CODE_EXPIRED_OR_MISSING")
        raise CodeInvalid("This code has expired or was already used. Request a new code.")
    if not (code.isdigit() and len(code) == 6) or not hmac.compare_digest(rec.code_hash, _hash(code, rec.salt)):
        rec.attempts += 1
        if rec.attempts >= MAX_ATTEMPTS:
            rec.consumed_at = now
        _event(session, email, "EMAIL_CODE_WRONG")
        left = MAX_ATTEMPTS - rec.attempts
        raise CodeInvalid(f"Incorrect code. {left} attempt(s) left." if left > 0 else
                          "Too many wrong attempts. Request a new code.")
    rec.consumed_at = now
    _event(session, email, "EMAIL_VERIFIED")
    return email
