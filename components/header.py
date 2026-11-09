"""Global styling, page headers and status badges."""
from __future__ import annotations

import html

import streamlit as st

PRIMARY, SECONDARY, SUCCESS, WARNING, DANGER = "#1976D2", "#009688", "#2E9D63", "#F59E0B", "#DC4444"
BG, TEXT, MUTED = "#F5F8FC", "#172B4D", "#5E6C84"

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"], .stMarkdown, .stText, button, input, textarea {{
  font-family: 'Inter', 'Manrope', system-ui, -apple-system, 'Segoe UI', sans-serif;
}}
.block-container {{ padding-top: 2rem; padding-bottom: 3rem; max-width: 1240px; }}
h1, h2, h3 {{ color: {TEXT}; letter-spacing: -0.01em; }}
.ms-hero {{
  background: linear-gradient(135deg, {PRIMARY} 0%, {SECONDARY} 100%);
  color: #fff; border-radius: 18px; padding: 2.6rem 2.4rem; margin-bottom: 1.4rem;
}}
.ms-hero h1 {{ color: #fff; font-size: 2.4rem; margin: 0 0 .4rem 0; line-height: 1.15; }}
.ms-hero p {{ font-size: 1.1rem; opacity: .95; max-width: 760px; margin: 0; }}
.ms-card {{
  background: #fff; border: 1px solid #E3E9F2; border-radius: 14px; padding: 1.1rem 1.25rem;
  height: 100%; box-shadow: 0 1px 2px rgba(23,43,77,.04);
}}
.ms-card h4 {{ margin: .1rem 0 .4rem 0; color: {TEXT}; font-size: 1.02rem; }}
.ms-card p {{ color: {MUTED}; margin: 0; font-size: .93rem; line-height: 1.5; }}
.ms-step {{ display:inline-flex; align-items:center; justify-content:center; width:28px; height:28px;
  border-radius:50%; background:{PRIMARY}; color:#fff; font-weight:600; font-size:.85rem; margin-bottom:.5rem; }}
.ms-page-sub {{ color: {MUTED}; margin-top: -.6rem; margin-bottom: 1.2rem; }}
.ms-badge {{ display:inline-block; padding: 2px 10px; border-radius: 999px; font-size: .78rem; font-weight: 600;
  letter-spacing: .02em; white-space: nowrap; }}
.ms-sample {{ display:inline-block; padding:1px 8px; border-radius:6px; background:#FFF4E5; color:#8A5300;
  font-size:.72rem; font-weight:600; }}
.ms-footer {{ color:{MUTED}; font-size:.85rem; border-top:1px solid #E3E9F2; margin-top:2.5rem; padding-top:1rem; }}
div[data-testid="stMetric"] {{ background:#fff; border:1px solid #E3E9F2; border-radius:14px; padding: .9rem 1rem; }}
div[data-testid="stMetricLabel"] p {{ color:{MUTED}; font-weight:500; }}
.ms-notice {{ background:#EAF2FD; border-left:4px solid {PRIMARY}; border-radius:8px; padding:.8rem 1rem;
  color:{TEXT}; font-size:.92rem; }}
</style>
"""

_BADGE = {
    "success": ("#E7F6EE", SUCCESS), "warning": ("#FEF4E2", "#A15C00"), "danger": ("#FDECEC", DANGER),
    "info": ("#E8F1FC", PRIMARY), "neutral": ("#EEF1F5", MUTED), "teal": ("#E0F4F2", "#00796B"),
}
STATUS_TONE = {
    "ACTIVE": "success", "VERIFIED": "success", "CLOSED": "neutral", "DISPOSED": "success", "COMPLETED": "success",
    "APPROVED_FOR_FURTHER_AUTHORIZED_PROCESS": "teal", "REVIEW_ACCEPTED": "teal", "AUTHORIZED": "teal",
    "SUBMITTED": "info", "REQUESTED": "info", "ACCEPTED": "info", "SCHEDULED": "info", "UNDER_REVIEW": "info",
    "COLLECTION_PENDING": "info", "COLLECTED": "teal", "DISPOSAL_PENDING": "warning", "INTEREST_EXPRESSED": "info",
    "PENDING": "warning", "PENDING_AUTHORIZATION": "warning", "ON_HOLD": "warning", "DRAFT": "neutral",
    "QUARANTINED": "danger", "REJECTED": "danger", "SUSPENDED": "danger", "CANCELLED": "neutral",
    "REVIEW_DECLINED": "danger", "WITHDRAWN": "neutral", "INACTIVE": "neutral", "REVOKED": "danger",
    "EXPIRED": "danger", "URGENT": "danger", "ATTENTION": "warning", "MONITOR": "info", "NORMAL": "success",
    "CRITICAL": "danger", "HIGH": "warning", "MODERATE": "info", "LOW": "success",
}


def apply_theme() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def badge(status: str | None, tone: str | None = None) -> str:
    status = status or "—"
    bg, fg = _BADGE[tone or STATUS_TONE.get(status, "neutral")]
    label = html.escape(status.replace("_", " ").title())
    return f'<span class="ms-badge" style="background:{bg};color:{fg}">{label}</span>'


def show_badge(status: str | None) -> None:
    st.markdown(badge(status), unsafe_allow_html=True)


def sample_tag(is_sample: bool) -> str:
    return '<span class="ms-sample">SAMPLE DATA</span>' if is_sample else ""


def page_header(title: str, subtitle: str | None = None) -> None:
    st.title(title)
    if subtitle:
        st.markdown(f'<p class="ms-page-sub">{html.escape(subtitle)}</p>', unsafe_allow_html=True)


def card(title: str, body: str, step: int | None = None) -> None:
    step_html = f'<div class="ms-step">{step}</div>' if step else ""
    st.markdown(f'<div class="ms-card">{step_html}<h4>{html.escape(title)}</h4><p>{html.escape(body)}</p></div>',
                unsafe_allow_html=True)


def notice(text: str) -> None:
    st.markdown(f'<div class="ms-notice">{html.escape(text)}</div>', unsafe_allow_html=True)


def footer() -> None:
    st.markdown(
        '<div class="ms-footer">MediShare – Smart Medicine Waste Reduction &amp; Authorized Donation Coordination '
        'Platform · Pilot: Kerala, India · MediShare documents workflows; it does not itself grant regulatory '
        'authorization to collect, transfer or redistribute medicines.</div>', unsafe_allow_html=True)
