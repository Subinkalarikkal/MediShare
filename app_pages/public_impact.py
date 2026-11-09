import streamlit as st

from components.charts import bar
from components.header import footer, notice, page_header
from components.metric_cards import metric_row
from components.session import db
from components.tables import show_table
from services import analytics_service

page_header("Environmental Impact", "What has been registered, collected and disposed of through authorized channels")
with db() as s:
    stats = analytics_service.public_stats(s)
    q = analytics_service.quantities_by_unit(s)

metric_row([("Medicines registered", stats["registrations"]), ("Completed collections", stats["completed_collections"]),
            ("Disposal records", stats["disposal_records"]), ("Districts served", stats["districts_served"])])
st.subheader("Quantities by unit")
st.caption("Registered quantities are what households reported. Collected quantities were confirmed on receipt by "
           "a centre. Disposed quantities come from recorded disposal certificates/manifests. They are reported "
           "separately and never combined.")
show_table(q, empty="No quantities recorded yet.")
if not q.empty:
    long = q.melt(id_vars="unit", var_name="stage", value_name="quantity")
    bar(long, "unit", "quantity", "Registered vs collected vs disposed", color="stage")
notice("Unsafe disposal of medicines – flushing, burning or mixing with household waste – can contaminate soil and "
       "water. Authorized collection routes medicines to licensed treatment facilities.")
footer()
