"""Sidebar user card and logout."""
from __future__ import annotations

import html

import streamlit as st

from components.header import badge
from components.session import clear_auth, db
from config import get_settings
from database.models import Organization
from services import auth_service, notification_service
from services.permission_service import Actor
from utils.constants import ROLE_LABELS, Role


def logout(actor: Actor | None, reason: str = "LOGOUT") -> None:
    if actor:
        with db() as s:
            auth_service.record_logout(s, actor, reason)
    clear_auth()
    if get_settings().auth_mode == "oidc":
        try:
            if st.user.is_logged_in:
                st.logout()
        except Exception:
            pass
    st.rerun()


def render_sidebar(actor: Actor | None) -> None:
    with st.sidebar:
        st.markdown("### 💊 MediShare")
        st.caption("Reducing Medicine Waste. Supporting Communities.")
        if actor is None:
            return
        with db() as s:
            unread = notification_service.unread_count(s, actor)
            org = s.get(Organization, actor.organization_id) if actor.organization_id else None
            org_line = (f"{html.escape(org.name)}<br>{badge(org.verification_status)}" if org else "")
        role = actor.primary_role
        label = ROLE_LABELS.get(Role(role), role) if role else "No role"
        st.markdown(
            f'<div class="ms-card"><h4>{html.escape(actor.full_name)}</h4>'
            f'<p>{html.escape(actor.email)}<br>{html.escape(label)} {badge(actor.status)}</p>'
            f'{"<p style=margin-top:.4rem>" + org_line + "</p>" if org_line else ""}</div>',
            unsafe_allow_html=True)
        if unread:
            st.caption(f"🔔 {unread} unread notification(s)")
        if get_settings().dev_login_enabled:
            st.caption("⚠️ Development login mode – not for production use.")
        if st.button("Log out", icon=":material/logout:", width="stretch"):
            logout(actor)
