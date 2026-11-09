import streamlit as st

from ai import analytics_assistant, claude_client
from ai.forecasting import linear_forecast, monthly_counts
from components.charts import LEVEL_COLORS, bar, donut, line
from components.header import notice, page_header
from components.session import actor, db, guarded
from components.tables import show_table, to_csv
from services import analytics_service as an
from services.errors import ServiceError

a = actor()
page_header("Analytics & AI Assistant", "Every figure is computed from the MediShare database")
charts, reports, assistant = st.tabs(["Charts", "Reports", "AI Assistant"])

with charts, db() as s:
    guarded(an.admin_kpis, s, a)
    c1, c2 = st.columns(2)
    with c1:
        line(an.monthly_registrations(s, a), "month", "count", "Monthly household registrations")
    with c2:
        line(an.monthly_collection_activity(s, a), "month", "count", "Monthly collection activity", color="kind")
    c3, c4 = st.columns(2)
    with c3:
        bar(an.district_registrations(s, a), "district", "registrations", "District-wise registrations", horizontal=True,
            height=400)
    with c4:
        donut(an.inventory_category_distribution(s, a), "category", "entries", "Inventory category distribution", height=400)
    c5, c6 = st.columns(2)
    with c5:
        bar(an.expiry_risk_distribution(s, a), "risk_level", "count", "Expiry-risk distribution (latest assessment)",
            color="entity_type")
    with c6:
        donut(an.pending_vs_completed(s, a), "group", "count", "Pending vs completed requests")
    c7, c8 = st.columns(2)
    with c7:
        perf = an.centre_performance(s, a)
        if not perf.empty:
            long = perf[["centre", "pending", "completed", "rejected"]].melt(
                id_vars=["centre"], var_name="stage", value_name="count")
            bar(long, "centre", "count", "Collection-centre performance", color="stage")
        else:
            bar(perf, "centre", "count", "Collection-centre performance")
    with c8:
        line(an.institutional_activity(s, a), "month", "count", "Institutional activity", color="kind")
    st.subheader("Registration volume forecast")
    fc = linear_forecast(monthly_counts(an.registration_dates(s, a)), periods=3)
    if len(fc) and (fc.kind == "forecast").any():
        line(fc, "month", "value", "Monthly registrations – actual and 3-month linear-trend estimate", color="kind")
        st.caption("Forecast is a simple linear trend for capacity planning – an estimate, not a prediction of individual behaviour.")
    else:
        st.caption("At least three months of history are needed for a forecast.")

with reports, db() as s:
    st.subheader("Quantities (registered vs received vs verified disposed)")
    q = an.quantities_by_unit(s)
    show_table(q)
    st.subheader("Collection-centre performance")
    perf = an.centre_performance(s, a)
    show_table(perf)
    st.subheader("Incomplete documentation")
    gaps = an.incomplete_documentation(s, a)
    show_table(gaps, empty="No documentation gaps.")
    st.subheader("Inventory by status")
    show_table(an.inventory_status_distribution(s, a))
    for label, df, name in (("Quantities", q, "quantities.csv"), ("Centre performance", perf, "centre_performance.csv"),
                            ("Documentation gaps", gaps, "documentation_gaps.csv")):
        if not df.empty:
            st.download_button(f"Download {label} (CSV)", to_csv(df), name, "text/csv", key=f"dl_{name}")

with assistant:
    notice("The assistant is read-only. It only sees aggregated, de-identified metrics, never personal data, and cannot "
           "change records, approve inventory or give clinical advice.")
    if not claude_client.is_enabled():
        st.caption("Claude is not configured – answers show the matching database figures without AI narrative.")
    examples = ["Which inventory entries need attention this month?", "How many collection requests are pending?",
                "Which districts have the highest medicine registration volumes?",
                "Which collection centres have delayed requests?", "Which institutions have incomplete documentation?",
                "Summarize this month's collection activity."]
    pick = st.selectbox("Example questions", [""] + examples, format_func=lambda x: x or "— choose an example —")
    with st.form("ask"):
        q_text = st.text_area("Your question", pick, max_chars=500)
        asked = st.form_submit_button("Ask", type="primary")
    if asked:
        with st.spinner("Analysing aggregated metrics…"):
            try:
                with db() as s:
                    ans = analytics_assistant.ask(s, a, q_text)
                if ans.source == "blocked":
                    st.warning(ans.text)
                else:
                    st.markdown(ans.text)
                    st.caption(f"Source: {'Claude (validated)' if ans.source == 'claude' else 'database figures'} · "
                               f"data sections used: {', '.join(ans.snapshot_keys)}")
            except ServiceError as exc:
                st.error(str(exc))
