"""Metric card rows."""
from __future__ import annotations

import streamlit as st


def metric_row(items: list[tuple], columns: int | None = None) -> None:
    """items: (label, value) or (label, value, help)."""
    cols = st.columns(columns or len(items))
    for i, item in enumerate(items):
        label, value, *rest = item
        with cols[i % len(cols)]:
            st.metric(label, value if value is not None else "—", help=rest[0] if rest else None)
