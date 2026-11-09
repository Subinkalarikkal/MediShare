import pandas as pd
import streamlit as st

from ai import expiry_risk_engine as ere
from components.charts import LEVEL_COLORS, bar
from components.header import notice, page_header
from components.session import act, actor, db
from components.tables import show_table

a = actor()
page_header("AI Risk Monitoring", "Operational expiry-risk prioritisation – not a medicine-safety assessment")
notice("Risk Score = 0.50·T + 0.20·Q + 0.20·D + 0.10·C (weights configurable by the super administrator). "
       "T: time to expiry · Q: quantity exposure · D: processing delay · C: collection / review capacity pressure. "
       "Expired items are routed to disposal; expired institutional batches are quarantined automatically.")

with db() as s:
    if st.button("Run risk assessment now", type="primary", icon=":material/play_arrow:"):
        res = act(s, ere.run_assessment, rerun=False)
        if res:
            st.success(f"Assessed {res['assessed']} item(s) · {res['new_alerts']} new alert(s) · "
                       f"{res['resolved_alerts']} resolved · {res['quarantined_expired']} expired batch(es) quarantined · "
                       f"algorithm v{res['algorithm_version']}")
    rows = ere.latest_assessments(s, a)
    alerts = ere.open_alerts(s, a)

if rows:
    df = pd.DataFrame(rows)
    c1, c2 = st.columns(2)
    with c1:
        dist = df.groupby(["risk_level", "entity_type"]).size().reset_index(name="count")
        bar(dist, "risk_level", "count", "Risk level distribution", color="entity_type")
    with c2:
        cat = df.groupby("expiry_category").size().reset_index(name="count")
        bar(cat, "expiry_category", "count", "Expiry categories")
    level = st.multiselect("Filter risk level", list(LEVEL_COLORS), default=["CRITICAL", "HIGH"])
    show_table(df[df.risk_level.isin(level)] if level else df,
               ["item", "entity_type", "risk_score", "risk_level", "expiry_category", "days_remaining", "T", "Q", "D", "C",
                "assessed", "version"])
else:
    st.info("No assessments yet – run the assessment to populate this page.")

st.subheader("Open expiry alerts")
for al in alerts[:50]:
    c1, c2 = st.columns([5, 1])
    c1.write(f"{al['category']} · {al['entity_type'].replace('_', ' ').title()} · {al['days_remaining']} day(s) remaining")
    if c2.button("Acknowledge", key=f"ack_{al['id']}"):
        with db() as s:
            act(s, ere.acknowledge_alert, al["id"], success="Alert acknowledged")
if not alerts:
    st.caption("No open alerts.")
