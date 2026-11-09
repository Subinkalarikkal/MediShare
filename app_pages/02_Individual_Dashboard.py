import streamlit as st

from components.header import notice, page_header
from components.metric_cards import metric_row
from components.session import actor, db, guarded
from components.tables import show_table
from services import analytics_service, medicine_service

a = actor()
page_header(f"Welcome, {a.full_name.split()[0]}", "Your household medicine disposal at a glance")
with db() as s:
    ov = guarded(analytics_service.individual_overview, s, a)
    recent = medicine_service.list_my_registrations(s, a)[:6]
    upcoming = [dict(centre=r.centre.name, method=r.collection_method, scheduled=r.scheduled_date) for r in ov["upcoming"]]
    recent_rows = [dict(medicine=r.medicine_name, expiry=r.expiry_date, quantity=f"{float(r.quantity):g} {r.unit.lower()}",
                        status=r.status) for r in recent]

metric_row([("Registered medicines", ov["registrations"]), ("Awaiting a collection request", ov["awaiting_request"]),
            ("In collection", ov["in_collection"]), ("Disposed", ov["disposed"]),
            ("Active requests", ov["active_requests"])])
if ov["awaiting_request"]:
    st.info(f"You have {ov['awaiting_request']} medicine(s) ready for collection. Create a collection request to "
            "hand them to an authorized centre.")

c1, c2, c3 = st.columns(3)
if c1.button("Register a medicine", type="primary", icon=":material/add_circle:", width="stretch"):
    st.switch_page("app_pages/03_Medicine_Registration.py")
if c2.button("Request collection", icon=":material/local_shipping:", width="stretch"):
    st.switch_page("app_pages/06_Collection_Requests.py")
if c3.button("Find collection centres", icon=":material/location_on:", width="stretch"):
    st.switch_page("app_pages/05_Collection_Centres.py")

l, r = st.columns(2)
with l:
    st.subheader("Upcoming collections")
    show_table(upcoming, empty="No scheduled collections.")
with r:
    st.subheader("Recent registrations")
    show_table(recent_rows, empty="You have not registered any medicines yet.")
notice("Registered medicines are only collected for authorized disposal – they are never given to other people.")
