from datetime import date, timedelta

import streamlit as st

from components.dialogs import reason_dialog
from components.header import badge, notice, page_header, sample_tag
from components.session import act, actor, db
from database.models import User
from services import collection_service, medicine_service
from utils.location_utils import district_centroid

a = actor()
page_header("My Collection Requests", "Ask an authorized centre to collect or receive your registered medicines")

new_tab, list_tab = st.tabs(["New request", "My requests"])
with new_tab, db() as s:
    ready = medicine_service.list_my_registrations(s, a, ["SUBMITTED"])
    me = s.get(User, a.user_id)
    if not ready:
        st.info("You have no registered medicines waiting for collection.")
        if st.button("Register a medicine", type="primary"):
            st.switch_page("app_pages/03_Medicine_Registration.py")
    else:
        near = district_centroid(me.district)
        centres = collection_service.search_centres(s, available_only=True, near=near)
        if not centres:
            st.warning("No authorized collection centres are accepting requests right now.")
        else:
            with st.form("new_request"):
                reg_opts = {str(r.id): f"{r.medicine_name} – {float(r.quantity):g} {r.unit.lower()} (exp {r.expiry_date:%b %Y})"
                            for r in ready}
                picked = st.multiselect("Medicines to include *", list(reg_opts), default=list(reg_opts),
                                        format_func=reg_opts.get)
                c_opts = {str(c["id"]): f"{c['name']} – {c['district']}" + (f" ({c['distance_km']} km)" if c["distance_km"] else "")
                          for c in centres}
                centre_id = st.selectbox("Collection centre *", list(c_opts), format_func=c_opts.get)
                method = st.radio("Collection method", ["DROP_OFF", "PICKUP"], horizontal=True,
                                  format_func=lambda m: "I will drop off" if m == "DROP_OFF" else "Request pickup")
                c1, c2 = st.columns(2)
                preferred = c1.date_input("Preferred date", date.today() + timedelta(days=2), min_value=date.today())
                address = c2.text_input("Pickup address (for pickup)", me.location or "")
                notes = st.text_area("Notes for the centre", height=70)
                sent = st.form_submit_button("Send request", type="primary")
            if sent:
                import uuid
                act(s, collection_service.create_request, centre_id=uuid.UUID(centre_id),
                    registration_ids=[uuid.UUID(x) for x in picked], collection_method=method, preferred_date=preferred,
                    pickup_address=address, notes=notes, success="Collection request sent")
    notice("Collection centres listed here are verified and hold an in-date collection authorization.")

with list_tab, db() as s:
    reqs = collection_service.list_my_requests(s, a)
    if not reqs:
        st.info("No collection requests yet.")
    for r in reqs:
        with st.container(border=True):
            c1, c2 = st.columns([4, 1])
            c1.markdown(f"**{r.centre.name}** {badge(r.status)} {sample_tag(r.is_sample)}", unsafe_allow_html=True)
            sched = f" · Scheduled {r.scheduled_date:%d %b %Y %H:%M}" if r.scheduled_date else ""
            c1.caption(f"{'Pickup' if r.collection_method == 'PICKUP' else 'Drop-off'} · Requested "
                       f"{r.request_date:%d %b %Y}{sched} · {len(r.items)} item(s): "
                       + ", ".join(i.registration.medicine_name for i in r.items))
            if r.rejection_reason:
                c1.warning(f"Declined: {r.rejection_reason}")
            if r.status in {"REQUESTED", "ACCEPTED", "SCHEDULED"} and c2.button("Cancel", key=f"cx_{r.id}"):
                reason_dialog("Cancel this collection request?", collection_service.cancel_request, (r.id,),
                              success="Request cancelled", min_len=0)
            with st.expander("Timeline"):
                for e in collection_service.request_events(s, a, r.id):
                    st.caption(f"{e.created_at:%d %b %Y %H:%M} · {(e.to_status or e.event_type).replace('_', ' ').title()}"
                               + (f" – {e.notes}" if e.notes else ""))
