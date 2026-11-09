"""Authentication ↔ identity mapping.

Authentication (who you are) is done by the OIDC provider via Streamlit's
st.login. This module performs *authorization bootstrap*: it maps the
authenticated email to a database user, checks account status and returns an
Actor. A successful Google login never grants institutional access by itself.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import LoginEvent, Role, User, UserRole
from services.errors import PermissionDenied, ValidationError
from services.permission_service import Actor, load_actor
from utils.audit_logger import audit
from utils.constants import KERALA_DISTRICTS, AccountStatus, Role as RoleCode
from utils.date_utils import utcnow
from utils.validators import check_email, check_phone, check_pin, clean_text, raise_if, require

# Argon2id parameters (OWASP recommended baseline) for any locally managed password.
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)

SELF_REGISTERABLE = {
    RoleCode.INDIVIDUAL.value, RoleCode.INSTITUTIONAL_DONOR.value, RoleCode.NGO.value,
    RoleCode.COLLECTION_CENTRE.value, RoleCode.REVIEWER.value,
}


@dataclass
class LoginResult:
    outcome: str  # OK | UNREGISTERED | SUSPENDED | DEACTIVATED | PENDING
    actor: Actor | None = None
    message: str = ""


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValidationError("Password must be at least 10 characters.")
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def get_user_by_email(session: Session, email: str) -> User | None:
    return session.scalar(select(User).where(User.email == email.strip().lower(),
                                             User.deleted_at.is_(None)))


def _log(session: Session, email: str, event: str, user_id=None, provider=None, details=None):
    session.add(LoginEvent(user_id=user_id, email=email.strip().lower(), event_type=event,
                           provider=provider, details=details))
    session.flush()


def resolve_login(session: Session, email: str | None, provider: str = "google",
                  email_verified: bool | None = True) -> LoginResult:
    """Map an authenticated identity to a database user and check status."""
    if not email:
        raise PermissionDenied("The identity provider did not return an email address.")
    if email_verified is False:
        _log(session, email, "LOGIN_DENIED_UNVERIFIED_EMAIL", provider=provider)
        return LoginResult("UNVERIFIED_EMAIL", message="Your email address is not verified with the provider.")
    user = get_user_by_email(session, email)
    if user is None:
        _log(session, email, "LOGIN_UNREGISTERED", provider=provider)
        return LoginResult("UNREGISTERED", message="No MediShare account exists for this email yet.")
    if user.status == AccountStatus.SUSPENDED.value:
        _log(session, email, "LOGIN_DENIED_SUSPENDED", user.id, provider)
        return LoginResult("SUSPENDED", message="This account is suspended. Contact the administrator.")
    if user.status == AccountStatus.DEACTIVATED.value:
        _log(session, email, "LOGIN_DENIED_DEACTIVATED", user.id, provider)
        return LoginResult("DEACTIVATED", message="This account has been deactivated.")
    user.last_login_at = utcnow()
    _log(session, email, "LOGIN_SUCCESS", user.id, provider)
    actor = load_actor(session, user.id)
    if user.status == AccountStatus.PENDING.value:
        return LoginResult("PENDING", actor, "Your account is awaiting review. Access is restricted.")
    return LoginResult("OK", actor)


def record_logout(session: Session, actor: Actor, reason: str = "LOGOUT") -> None:
    _log(session, actor.email, reason, actor.user_id)


def is_session_expired(login_time: datetime | None, timeout_minutes: int,
                       now: datetime | None = None) -> bool:
    if login_time is None:
        return True
    return (now or utcnow()) - login_time > timedelta(minutes=timeout_minutes)


def _assign_role(session: Session, user: User, role_code: str, assigned_by=None) -> None:
    role = session.scalar(select(Role).where(Role.code == role_code))
    if role is None:
        raise ValidationError(f"Unknown role {role_code}.")
    exists = session.scalar(select(UserRole).where(UserRole.user_id == user.id, UserRole.role_id == role.id))
    if not exists:
        session.add(UserRole(user_id=user.id, role_id=role.id, assigned_by=assigned_by))
        session.flush()


def register_user(session: Session, *, email: str, full_name: str, account_type: str,
                  phone: str | None = None, location: str | None = None, district: str | None = None,
                  pin_code: str | None = None, professional_registration_number: str | None = None,
                  professional_council: str | None = None, is_sample: bool = False,
                  username: str | None = None, password: str | None = None,
                  password_confirm: str | None = None) -> User:
    """Self-registration after OIDC authentication.

    Individuals become ACTIVE immediately. Organization and reviewer accounts
    start as PENDING (restricted) until an administrator completes review.
    The `account_type` is validated against an allow-list; administrator roles
    can never be self-assigned.
    """
    errors: list[str] = []
    norm_email = check_email(email, errors)
    full_name = clean_text(full_name, 200)
    require(full_name, "Full name", errors)
    if account_type not in SELF_REGISTERABLE:
        errors.append("Invalid account type.")
    phone = check_phone(phone, errors, required=False)
    pin_code = check_pin(pin_code, errors, required=False)
    if district and district not in KERALA_DISTRICTS:
        errors.append("Please select a valid district.")
    if account_type == RoleCode.REVIEWER.value:
        require(professional_registration_number, "Professional registration number", errors)
        require(professional_council, "Registering council", errors)
    raise_if(errors)
    if get_user_by_email(session, norm_email):
        raise ValidationError("An account already exists for this email.")
    password_hash = None
    if username is not None or password is not None:
        from services import password_service
        username = password_service.validate_username(session, username or "")
        password_service._check_new_password(password or "", password_confirm, norm_email, username)
        password_hash = hash_password(password)
    status = (AccountStatus.ACTIVE.value if account_type == RoleCode.INDIVIDUAL.value
              else AccountStatus.PENDING.value)
    user = User(
        email=norm_email, full_name=full_name, phone=phone, location=clean_text(location, 300),
        district=district, pin_code=pin_code, status=status,
        professional_registration_number=clean_text(professional_registration_number, 100),
        professional_council=clean_text(professional_council, 200), is_sample=is_sample,
        username=username, password_hash=password_hash,
    )
    session.add(user)
    session.flush()
    _assign_role(session, user, account_type)
    audit(session, user.id, "USER_SELF_REGISTERED", "user", user.id,
          after={"email": norm_email, "account_type": account_type, "status": status})
    return user


def bootstrap_super_admin(session: Session, email: str, full_name: str) -> User:
    """Secure bootstrap: only works while no SUPER_ADMIN exists. Run from the CLI script."""
    existing = session.scalar(
        select(UserRole).join(Role, Role.id == UserRole.role_id).where(Role.code == RoleCode.SUPER_ADMIN.value)
    )
    if existing:
        raise PermissionDenied("A super administrator already exists; bootstrap is disabled.")
    errors: list[str] = []
    norm_email = check_email(email, errors)
    raise_if(errors)
    user = get_user_by_email(session, norm_email)
    if user is None:
        user = User(email=norm_email, full_name=full_name, status=AccountStatus.ACTIVE.value)
        session.add(user)
        session.flush()
    user.status = AccountStatus.ACTIVE.value
    _assign_role(session, user, RoleCode.SUPER_ADMIN.value)
    audit(session, user.id, "SUPER_ADMIN_BOOTSTRAPPED", "user", user.id, after={"email": norm_email})
    return user


def assign_role_internal(session: Session, user: User, role_code: str, assigned_by: uuid.UUID | None):
    _assign_role(session, user, role_code, assigned_by)
