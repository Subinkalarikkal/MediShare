"""Confirmation dialogs (st.dialog). Each dialog opens its own DB session."""
from __future__ import annotations

from typing import Callable

import streamlit as st

from components.session import act, db


@st.dialog("Confirm action")
def reason_dialog(title: str, fn: Callable, args: tuple = (), reason_kw: str = "reason",
                  label: str = "Reason", success: str = "Saved", min_len: int = 5, extra: dict | None = None):
    st.markdown(f"**{title}**")
    reason = st.text_area(label, key="dlg_reason")
    c1, c2 = st.columns(2)
    if c1.button("Confirm", type="primary", width="stretch"):
        if len(reason.strip()) < min_len:
            st.error(f"Please enter at least {min_len} characters.")
            return
        with db() as s:
            act(s, fn, *args, success=success, **{reason_kw: reason.strip()}, **(extra or {}))
    if c2.button("Cancel", width="stretch"):
        st.rerun()


@st.dialog("Please confirm")
def confirm_dialog(message: str, fn: Callable, args: tuple = (), kwargs: dict | None = None, success: str = "Done"):
    st.write(message)
    c1, c2 = st.columns(2)
    if c1.button("Yes, continue", type="primary", width="stretch"):
        with db() as s:
            act(s, fn, *args, success=success, **(kwargs or {}))
    if c2.button("Cancel", width="stretch"):
        st.rerun()
