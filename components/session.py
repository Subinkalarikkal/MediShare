"""Bridges Streamlit pages and the service layer.

st.session_state only stores *who* is logged in (user id, email, login time).
Roles and permissions are reloaded from the database on every run and
re-checked again inside every service call.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from typing import Callable, Iterator

import streamlit as st
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import get_settings
from database.connection import get_sessionmaker
from services.errors import NotFound, PermissionDenied, ServiceError, ValidationError
from services.permission_service import Actor, load_actor

log = logging.getLogger(__name__)

AUTH_KEYS = ("auth_user_id", "auth_email", "auth_login_time", "auth_provider")


@contextmanager
def db() -> Iterator[Session]:
    """Page-level session. Commits on normal exit, rolls back on error."""
    s = get_sessionmaker()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def current_actor() -> Actor | None:
    uid = st.session_state.get("auth_user_id")
    if not uid:
        return None
    try:
        with db() as s:
            return load_actor(s, uuid.UUID(str(uid)))
    except NotFound:
        clear_auth()
        return None


def actor() -> Actor:
    a = current_actor()
    if a is None:
        st.warning("Please sign in to continue.")
        st.stop()
    return a


def start_session(user_id, email: str, provider: str) -> None:
    from utils.date_utils import utcnow
    st.session_state["auth_user_id"] = str(user_id)
    st.session_state["auth_email"] = email
    st.session_state["auth_login_time"] = utcnow()
    st.session_state["auth_provider"] = provider
    st.session_state.pop("signup_email", None)
    st.session_state.pop("signup_name", None)
    st.session_state.pop("signup_verified", None)


def clear_auth() -> None:
    for k in AUTH_KEYS:
        st.session_state.pop(k, None)


def flash(message: str, icon: str = "✅") -> None:
    st.session_state.setdefault("_flash", []).append((message, icon))


def show_flash() -> None:
    for msg, icon in st.session_state.pop("_flash", []):
        st.toast(msg, icon=icon)


def _error_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "Please correct the following:\n\n" + "\n".join(f"- {e}" for e in exc.errors)
    if isinstance(exc, PermissionDenied):
        return f"Not permitted: {exc}"
    if isinstance(exc, ServiceError):
        return str(exc)
    if isinstance(exc, IntegrityError):
        return "This change conflicts with existing data (duplicate or invalid reference)."
    return "An unexpected error occurred. The problem has been logged."


def act(s: Session, fn: Callable, *args, success: str | None = None, rerun: bool = True, **kwargs):
    """Run a service write inside session `s`, commit, flash, and rerun. Shows errors inline."""
    a = actor()
    try:
        result = fn(s, a, *args, **kwargs)
        s.commit()
    except (ServiceError, IntegrityError) as exc:
        s.rollback()
        st.error(_error_message(exc))
        return None
    except Exception as exc:  # pragma: no cover - logged, not shown in detail
        s.rollback()
        log.exception("Unexpected error in %s", getattr(fn, "__name__", fn))
        st.error(_error_message(exc))
        if not get_settings().is_production:
            st.exception(exc)
        return None
    if success:
        flash(success)
    if rerun:
        st.rerun()
    return result


def guarded(fn: Callable, *args, **kwargs):
    """Run a read-only service call and render permission / not-found errors."""
    try:
        return fn(*args, **kwargs)
    except (ServiceError, IntegrityError) as exc:
        st.error(_error_message(exc))
        st.stop()
