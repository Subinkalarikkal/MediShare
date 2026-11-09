import streamlit as st

from ai import expiry_risk_engine
from components.charts import bar, donut
from components.header import page_header
from components.metric_cards import metric_row
from components.session import actor, db, guarded
from components.tables import show_table
from services import analytics_service, collection_service, organization_service

a = actor()
page_header("Admin Dashboard", "Live platform metrics from the MediShare database")
with db() as s:
    k = guarded(analytics_service.admin_kpis, s, a)
    metric_row([("Total users", k["total_users"]), ("Active users (30 days)", k["active_users"]),
                ("Verified organizations", k["verified_organizations"]), ("Verified centres", k["verified_centres"])])
    metric_row([("Household registrations", k["household_registrations"]), ("Inventory entries", k["inventory_entries"]),
                ("Pending collection requests", k["pending_collection_requests"]),
                ("Completed collections", k["completed_collections"])])
    metric_row([("Expired awaiting disposal", k["expired_awaiting_disposal"], "Expired household items still in progress "
                 "plus quarantined institutional batches"), ("Pending institutional reviews", k["pending_reviews"]),
                ("Disposal records", k["disposal_records"]), ("Open expiry alerts", k["open_expiry_alerts"])])

    c1, c2 = st.columns(2)
    with c1:
        donut(analytics_service.pending_vs_completed(s, a), "group", "count", "Collection requests by stage")
    with c2:
        bar(analytics_service.district_registrations(s, a).head(10), "district", "registrations",
            "Registrations by district", horizontal=True)

    st.subheader("Recorded quantities")
    st.caption("Registered, received and verified-disposed quantities are tracked separately.")
    show_table(analytics_service.quantities_by_unit(s), empty="No quantities recorded yet.")

    l, r = st.columns(2)
    with l:
        st.subheader("Awaiting verification")
        pend = [o for o in organization_service.list_organizations(s, a) if o["verification_status"] in {"PENDING", "UNDER_REVIEW"}]
        show_table(pend, ["name", "type", "district", "verification_status", "documentation_gaps"], empty="Nothing waiting.")
        if pend and st.button("Open verification queue"):
            st.switch_page("app_pages/admin_organizations.py")
    with r:
        st.subheader("Delayed collection requests")
        delayed = [dict(centre=x.centre.name, status=x.status, requested=x.request_date, scheduled=x.scheduled_date)
                   for x in collection_service.delayed_requests(s)]
        show_table(delayed, empty="No delayed requests.")
    st.subheader("Open expiry alerts")
    show_table(expiry_risk_engine.open_alerts(s, a)[:20], ["entity_type", "category", "days_remaining", "created"],
               empty="No open alerts. Run a risk assessment from AI Risk Monitoring.")
