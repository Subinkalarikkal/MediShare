"""Consistent dataframe rendering."""
from __future__ import annotations

import pandas as pd
import streamlit as st


def _label(col: str) -> str:
    return col.replace("_", " ").strip().capitalize()


def show_table(rows, columns: list[str] | None = None, empty: str = "No records yet.", height: int | None = None,
               hide: tuple[str, ...] = ("id",), key: str | None = None, selectable: bool = False):
    """Render rows (list of dicts or DataFrame). Returns the selected row index when selectable."""
    df = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    if df.empty:
        st.info(empty)
        return None
    if columns:
        df = df[[c for c in columns if c in df.columns]]
    df = df.drop(columns=[c for c in hide if c in df.columns])
    for c in df.columns:
        if df[c].dtype == object or pd.api.types.is_string_dtype(df[c]):  # pandas 3 uses a "str" dtype
            df[c] = df[c].apply(lambda v: v.replace("_", " ") if isinstance(v, str) and v.isupper() else v)
    if "sample" in df.columns:
        df["sample"] = df["sample"].map({True: "Sample", False: ""})
    df.columns = [_label(c) for c in df.columns]
    kwargs = dict(width="stretch", hide_index=True)
    if height:
        kwargs["height"] = height
    if selectable:
        event = st.dataframe(df, on_select="rerun", selection_mode="single-row", key=key, **kwargs)
        sel = event.selection.rows if event and hasattr(event, "selection") else []
        return sel[0] if sel else None
    st.dataframe(df, **kwargs)
    return None


def to_csv(rows) -> bytes:
    df = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    return df.to_csv(index=False).encode("utf-8")
