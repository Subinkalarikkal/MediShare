"""Notification creation and delivery (in-app, email, optional SMS)."""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from config import get_settings
from database.models import Notification, Role, SystemSetting, User, UserRole
from services.errors import NotFound, PermissionDenied
from services.permission_service import Actor, require, require_active
from utils.audit_logger import audit
from utils.constants import Perm
from utils.date_utils import utcnow

log = logging.getLogger(__name__)

# Notification types (triggers listed in the specification)
REGISTRATION_SUBMITTED = "REGISTRATION_SUBMITTED"
COLLECTION_REQUEST_RECEIVED = "COLLECTION_REQUEST_RECEIVED"
COLLECTION_ACCEPTED = "COLLECTION_ACCEPTED"
COLLECTION_REJECTED = "COLLECTION_REJECTED"
COLLECTION_SCHEDULED = "COLLECTION_SCHEDULED"
COLLECTION_COMPLETED = "COLLECTION_COMPLETED"
DISPOSAL_COMPLETED = "DISPOSAL_COMPLETED"
REQUEST_CLOSED = "REQUEST_CLOSED"
ORG_VERIFICATION_COMPLETED = "ORG_VERIFICATION_COMPLETED"
INVENTORY_SUBMITTED = "INVENTORY_SUBMITTED"
REVIEW_DECISION = "REVIEW_DECISION"
EXPIRY_APPROACHING = "EXPIRY_APPROACHING"
INVENTORY_EXPIRED = "INVENTORY_EXPIRED"
DOCUMENTATION_MISSING = "DOCUMENTATION_MISSING"
ACCOUNT_STATUS = "ACCOUNT_STATUS"
CONTACT_MESSAGE = "CONTACT_MESSAGE"
GENERAL = "GENERAL"


def _email_enabled(session: Session) -> bool:
    setting = session.scalar(select(SystemSetting).where(SystemSetting.key == "notifications.email_enabled"))
    return bool(setting and setting.value) and bool(get_settings().email_provider_api_key)


def _send_email(to: str, subject: str, body: str) -> bool:
    """Email provider hook. Returns True only when a provider actually accepted it.

    No provider SDK is bundled; integrate the chosen provider here. Without a
    configured provider nothing is sent and delivery is recorded as SKIPPED.
    """
    log.info("Email provider integration not configured; email to %s not sent.", to)
    return False


def notify(session: Session, user_id: uuid.UUID, ntype: str, title: str, message: str,
           entity_type: str | None = None, entity_id: uuid.UUID | None = None) -> Notification:
    n = Notification(user_id=user_id, type=ntype, title=title[:250], message=message,
                     entity_type=entity_type, entity_id=entity_id, channel="IN_APP",
                     delivery_status="DELIVERED")
    session.add(n)
    if _email_enabled(session):
        user = session.get(User, user_id)
        if user:
            sent = _send_email(user.email, title, message)
            session.add(Notification(user_id=user_id, type=ntype, title=title[:250], message=message,
                                     entity_type=entity_type, entity_id=entity_id, channel="EMAIL",
                                     delivery_status="SENT" if sent else "SKIPPED_NO_PROVIDER",
                                     read_at=utcnow()))
    session.flush()
    return n


def users_with_role(session: Session, *role_codes: str) -> list[User]:
    return list(session.scalars(
        select(User).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id)
        .where(Role.code.in_(role_codes), User.status == "ACTIVE", User.deleted_at.is_(None)).distinct()
    ))


def notify_role(session: Session, role_codes: tuple[str, ...], ntype: str, title: str, message: str,
                entity_type=None, entity_id=None) -> int:
    users = users_with_role(session, *role_codes)
    for u in users:
        notify(session, u.id, ntype, title, message, entity_type, entity_id)
    return len(users)


def notify_organization(session: Session, organization_id: uuid.UUID, ntype: str, title: str,
                        message: str, entity_type=None, entity_id=None) -> int:
    users = session.scalars(select(User).where(User.organization_id == organization_id,
                                               User.deleted_at.is_(None))).all()
    for u in users:
        notify(session, u.id, ntype, title, message, entity_type, entity_id)
    return len(users)


# ---------------------------------------------------------------- user-facing API
def list_my_notifications(session: Session, actor: Actor, unread_only: bool = False, limit: int = 100):
    fresh = require_active(session, actor, allow_pending=True)
    q = select(Notification).where(Notification.user_id == fresh.user_id, Notification.channel == "IN_APP")
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    return list(session.scalars(q.order_by(Notification.created_at.desc()).limit(limit)))


def unread_count(session: Session, actor: Actor) -> int:
    return session.scalar(select(func.count(Notification.id)).where(
        Notification.user_id == actor.user_id, Notification.read_at.is_(None),
        Notification.channel == "IN_APP")) or 0


def mark_read(session: Session, actor: Actor, notification_id: uuid.UUID) -> None:
    fresh = require_active(session, actor, allow_pending=True)
    n = session.get(Notification, notification_id)
    if n is None:
        raise NotFound("Notification not found.")
    if n.user_id != fresh.user_id:
        raise PermissionDenied("You cannot modify another user's notifications.")
    n.read_at = n.read_at or utcnow()


def mark_all_read(session: Session, actor: Actor) -> int:
    fresh = require_active(session, actor, allow_pending=True)
    res = session.execute(update(Notification).where(
        Notification.user_id == fresh.user_id, Notification.read_at.is_(None)).values(read_at=utcnow()))
    return res.rowcount or 0


def broadcast(session: Session, actor: Actor, role_codes: list[str], title: str, message: str) -> int:
    fresh = require(session, actor, Perm.NOTIFICATIONS_MANAGE)
    if not title.strip() or not message.strip():
        from services.errors import ValidationError
        raise ValidationError("Title and message are required.")
    count = notify_role(session, tuple(role_codes), GENERAL, title.strip(), message.strip())
    audit(session, fresh.user_id, "NOTIFICATION_BROADCAST", "notification", None,
          after={"roles": role_codes, "title": title, "recipients": count})
    return count


def admin_notification_stats(session: Session, actor: Actor):
    require(session, actor, Perm.NOTIFICATIONS_MANAGE)
    rows = session.execute(select(Notification.type, Notification.channel, Notification.delivery_status,
                                  func.count(Notification.id))
                           .group_by(Notification.type, Notification.channel, Notification.delivery_status)).all()
    return [dict(type=r[0], channel=r[1], delivery_status=r[2], count=r[3]) for r in rows]


def submit_contact_message(session: Session, name: str, email: str, message: str) -> int:
    """Public contact form → in-app notification to administrators."""
    from utils.validators import check_email, clean_text, raise_if
    errors: list[str] = []
    name = clean_text(name, 200)
    email = check_email(email, errors)
    message = clean_text(message, 4000)
    if not name:
        errors.append("Name is required.")
    if not message or len(message) < 10:
        errors.append("Please enter a message of at least 10 characters.")
    raise_if(errors)
    return notify_role(session, ("ADMIN", "SUPER_ADMIN"), CONTACT_MESSAGE,
                       f"Contact form: {name}", f"From {name} <{email}>:\n\n{message}")
