"""In-app notification centre."""
from __future__ import annotations

import streamlit as st

from components.session import act
from services import notification_service


def render_notifications(s, actor, limit: int = 100) -> None:
    items = notification_service.list_my_notifications(s, actor, limit=limit)
    unread = [n for n in items if n.read_at is None]
    c1, c2 = st.columns([3, 1])
    c1.caption(f"{len(unread)} unread · {len(items)} shown")
    if unread and c2.button("Mark all as read", key="mark_all"):
        act(s, notification_service.mark_all_read, success="All notifications marked as read")
    if not items:
        st.info("You have no notifications yet.")
        return
    for n in items:
        with st.container(border=True):
            a, b = st.columns([5, 1])
            dot = "🔵 " if n.read_at is None else ""
            a.markdown(f"**{dot}{n.title}**")
            a.write(n.message)
            a.caption(f"{n.created_at:%d %b %Y, %H:%M} UTC · {n.type.replace('_', ' ').title()}")
            if n.read_at is None and b.button("Mark read", key=f"read_{n.id}"):
                act(s, notification_service.mark_read, n.id)
