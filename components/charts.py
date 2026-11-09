"""Plotly chart helpers with a consistent palette."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

SERIES = ["#1976D2", "#009688", "#F59E0B", "#7E57C2", "#DC4444", "#5E6C84"]
LEVEL_COLORS = {"LOW": "#2E9D63", "MODERATE": "#1976D2", "HIGH": "#F59E0B", "CRITICAL": "#DC4444"}


def _style(fig, height=320):
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=36, b=8), paper_bgcolor="white", plot_bgcolor="white",
                      font=dict(family="Inter, system-ui, sans-serif", color="#172B4D", size=12),
                      legend=dict(orientation="h", y=-0.2, title=None), title=dict(font=dict(size=14)))
    fig.update_xaxes(showgrid=False, linecolor="#DDE3EC")
    fig.update_yaxes(gridcolor="#EEF1F5", zeroline=False)
    return fig


def _empty(title: str):
    st.markdown(f"**{title}**")
    st.caption("No data recorded yet.")


def bar(df: pd.DataFrame, x: str, y: str, title: str, color: str | None = None, horizontal=False,
        color_map: dict | None = None, height=320):
    if df is None or df.empty:
        return _empty(title)
    kw = dict(color=color, color_discrete_sequence=SERIES, color_discrete_map=color_map or {}, title=title)
    fig = px.bar(df, x=y if horizontal else x, y=x if horizontal else y, orientation="h" if horizontal else "v", **kw)
    if horizontal:
        fig.update_yaxes(categoryorder="total ascending")
    st.plotly_chart(_style(fig, height), width="stretch")


def line(df: pd.DataFrame, x: str, y: str, title: str, color: str | None = None, height=320, dash: str | None = None):
    if df is None or df.empty:
        return _empty(title)
    fig = px.line(df, x=x, y=y, color=color, markers=True, title=title, color_discrete_sequence=SERIES,
                  line_dash=dash)
    st.plotly_chart(_style(fig, height), width="stretch")


def donut(df: pd.DataFrame, names: str, values: str, title: str, color_map: dict | None = None, height=320):
    if df is None or df.empty or df[values].sum() == 0:
        return _empty(title)
    fig = px.pie(df, names=names, values=values, hole=.55, title=title, color=names,
                 color_discrete_sequence=SERIES, color_discrete_map=color_map or {})
    fig.update_traces(textinfo="value+percent")
    st.plotly_chart(_style(fig, height), width="stretch")
