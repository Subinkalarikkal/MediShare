"""Outgoing email over SMTP (e.g. Gmail with an app password, or any SMTP provider).

Credentials come only from environment variables / Streamlit secrets.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from config import get_settings

log = logging.getLogger(__name__)


class EmailNotConfigured(Exception):
    pass


class EmailSendError(Exception):
    pass


_sender = None  # tests inject a fake sender: callable(to, subject, body)


def set_sender(fn) -> None:
    global _sender
    _sender = fn


def is_configured() -> bool:
    return _sender is not None or get_settings().smtp_enabled


def send_email(to: str, subject: str, body: str) -> None:
    if _sender is not None:
        _sender(to, subject, body)
        return
    s = get_settings()
    if not s.smtp_enabled:
        raise EmailNotConfigured("SMTP is not configured (SMTP_HOST / SMTP_USER / SMTP_PASSWORD / SMTP_FROM).")
    msg = EmailMessage()
    msg["From"] = s.smtp_from or s.smtp_user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        ctx = ssl.create_default_context()
        if s.smtp_use_ssl or s.smtp_port == 465:
            with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=ctx, timeout=20) as srv:
                if s.smtp_user:
                    srv.login(s.smtp_user, s.smtp_password or "")
                srv.send_message(msg)
        else:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as srv:
                srv.ehlo()
                if srv.has_extn("starttls"):
                    srv.starttls(context=ctx)
                    srv.ehlo()
                if s.smtp_user:
                    srv.login(s.smtp_user, s.smtp_password or "")
                srv.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        log.error("SMTP send to %s failed: %s", to, exc)
        raise EmailSendError("The email could not be sent.") from exc
