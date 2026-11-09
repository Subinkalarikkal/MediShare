from datetime import date, datetime, time, timedelta

import streamlit as st

from components.dialogs import reason_dialog
from components.forms import centre_fields
from components.header import badge, notice, page_header, sample_tag
from components.metric_cards import metric_row
from components.session import act, actor, db
from components.tables import show_table, to_csv
from services import analytics_service, collection_service, disposal_service
from utils.constants import DisposalMethod, Unit

a = actor()
page_header("Collection Centre Portal", "Manage incoming requests, collections and authorized disposal")
pretty = lambda v: v.replace("_", " ").title()  # noqa: E731


def centre_settings(s):
    if a.organization_id is None:
        st.info("Register your organization first (Organization Dashboard).")
        return
    centres = collection_service.my_centres(s, a)
    for c in centres:
        with st.expander(f"{c.name} – {c.district}", expanded=len(centres) == 1):
            st.markdown(f"Verification {badge(c.verification_status)} · Operation {badge(c.status)}", unsafe_allow_html=True)
            with st.form(f"centre_{c.id}"):
                data = centre_fields({k: getattr(c, k) for k in collection_service.CENTRE_FIELDS}, key=f"c{c.id}")
                if st.form_submit_button("Save centre", type="primary"):
                    act(s, collection_service.update_centre, c.id, success="Centre updated", **data)
            if c.verification_status == "VERIFIED" and a.status == "ACTIVE":
                if c.status == "ACTIVE" and st.button("Pause operations", key=f"pause_{c.id}"):
                    act(s, collection_service.set_operational_status, c.id, False, success="Centre paused")
                if c.status == "INACTIVE" and st.button("Resume operations", key=f"resume_{c.id}"):
                    act(s, collection_service.set_operational_status, c.id, True, success="Centre active")
    st.markdown("**Add a collection centre**")
    with st.form("new_centre"):
        data = centre_fields(key="newc")
        if st.form_submit_button("Register centre", type="primary"):
            act(s, collection_service.register_centre, success="Centre registered – awaiting verification", **data)
    notice("A centre appears in the public directory only after administrator verification, and only while your "
           "organization holds a verified, in-date collection or bio-medical waste authorization.")


def req_card(r, s):
    st.markdown(f"**{r.user.full_name}** → {r.centre.name} {badge(r.status)} {sample_tag(r.is_sample)}",
                unsafe_allow_html=True)
    where = f"Pickup at {r.pickup_address}" if r.collection_method == "PICKUP" else "Drop-off at centre"
    st.caption(f"{where} · Requested {r.request_date:%d %b %Y} · Preferred {r.preferred_date or '—'}"
               + (f" · Scheduled {r.scheduled_date:%d %b %Y %H:%M}" if r.scheduled_date else ""))
    show_table([dict(medicine=i.registration.medicine_name, quantity=float(i.registration.quantity),
                     unit=i.registration.unit, expiry=i.registration.expiry_date, packaging=i.registration.packaging_condition,
                     received=float(i.quantity_received) if i.quantity_received is not None else None) for i in r.items])
    if r.notes:
        st.caption(f"Notes: {r.notes}")


if a.status != "ACTIVE":
    st.warning("Your organization is awaiting verification. You can set up your centre now; request handling is enabled "
               "after verification.")
    with db() as s:
        centre_settings(s)
    st.stop()

tabs = st.tabs(["Dashboard", "Incoming Requests", "Collection Schedule", "Received Medicines", "Disposal Records",
                "Reports", "Centre Settings"])
with tabs[0], db() as s:
    ov = analytics_service.centre_overview(s, a)
    metric_row([("New requests", ov["incoming"]), ("To schedule", ov["to_schedule"]), ("Scheduled", ov["scheduled"]),
                ("Awaiting disposal", ov["awaiting_disposal"]), ("Completed", ov["completed"])])
    st.subheader("Today's collections")
    show_table([dict(user=r.user.full_name, method=r.collection_method, time=r.scheduled_date, items=len(r.items))
                for r in ov["today"]], empty="No collections scheduled for today.")

with tabs[1], db() as s:
    reqs = collection_service.centre_requests(s, a, ["REQUESTED"])
    if not reqs:
        st.info("No new requests.")
    for r in reqs:
        with st.container(border=True):
            req_card(r, s)
            c1, c2, _ = st.columns([1, 1, 4])
            if c1.button("Accept", key=f"acc_{r.id}", type="primary"):
                act(s, collection_service.accept_request, r.id, success="Request accepted")
            if c2.button("Reject", key=f"rej_{r.id}"):
                reason_dialog("Reject this collection request", collection_service.reject_request, (r.id,),
                              success="Request rejected")

with tabs[2], db() as s:
    reqs = collection_service.centre_requests(s, a, ["ACCEPTED", "SCHEDULED"])
    if not reqs:
        st.info("Nothing to schedule.")
    for r in reqs:
        with st.container(border=True):
            req_card(r, s)
            with st.form(f"sched_{r.id}"):
                c1, c2 = st.columns(2)
                d = c1.date_input("Date", r.preferred_date or date.today() + timedelta(days=1), min_value=date.today(),
                                  key=f"d_{r.id}")
                t = c2.time_input("Time", time(10, 0), key=f"t_{r.id}")
                label = "Reschedule" if r.status == "SCHEDULED" else "Schedule collection"
                if st.form_submit_button(label, type="primary"):
                    act(s, collection_service.schedule_request, r.id, datetime.combine(d, t), success="Collection scheduled")
            if r.status == "SCHEDULED":
                with st.form(f"recv_{r.id}"):
                    st.markdown("**Confirm receipt** – enter the quantity actually received for each item")
                    received = {}
                    for i in r.items:
                        received[i.registration_id] = st.number_input(
                            f"{i.registration.medicine_name} ({i.registration.unit.lower()})", min_value=0.0,
                            value=float(i.registration.quantity), key=f"q_{i.id}")
                    note = st.text_input("Receipt note", key=f"rn_{r.id}")
                    if st.form_submit_button("Confirm receipt", type="primary"):
                        act(s, collection_service.confirm_receipt, r.id, received, note, success="Receipt confirmed")

with tabs[3], db() as s:
    reqs = collection_service.centre_requests(s, a, ["COLLECTED"])
    if not reqs:
        st.info("No received medicines awaiting disposal initiation.")
    for r in reqs:
        with st.container(border=True):
            req_card(r, s)
            if st.button("Initiate authorized disposal", key=f"init_{r.id}", type="primary"):
                act(s, collection_service.initiate_disposal, r.id, success="Disposal initiated")

with tabs[4], db() as s:
    pend = collection_service.centre_requests(s, a, ["DISPOSAL_PENDING", "DISPOSED"])
    for r in pend:
        with st.container(border=True):
            req_card(r, s)
            if r.status == "DISPOSAL_PENDING":
                total = sum(float(i.quantity_received or 0) for i in r.items)
                with st.form(f"disp_{r.id}"):
                    c1, c2, c3 = st.columns(3)
                    method = c1.selectbox("Disposal method", DisposalMethod.values(), format_func=pretty, key=f"m_{r.id}")
                    ddate = c2.date_input("Disposal date", date.today(), max_value=date.today(), key=f"dd_{r.id}")
                    qty = c3.number_input("Quantity disposed", min_value=0.0, value=total, key=f"dq_{r.id}")
                    c4, c5, c6 = st.columns(3)
                    unit = c4.selectbox("Unit", Unit.values(), index=Unit.values().index(r.items[0].registration.unit),
                                        format_func=pretty, key=f"du_{r.id}")
                    facility = c5.text_input("Disposal facility *", key=f"df_{r.id}")
                    ref = c6.text_input("Manifest / reference number *", key=f"dr_{r.id}")
                    cert = st.file_uploader("Disposal certificate (PDF/image)", type=["pdf", "png", "jpg", "jpeg"],
                                            key=f"dc_{r.id}")
                    note = st.text_input("Notes", key=f"dn_{r.id}")
                    if st.form_submit_button("Record disposal", type="primary"):
                        act(s, disposal_service.record_disposal, r.id, disposal_method=method, disposal_date=ddate,
                            quantity_disposed=qty, unit=unit, disposal_facility=facility, reference_number=ref,
                            certificate=(cert.getvalue(), cert.name) if cert else None, notes=note,
                            success="Disposal recorded")
            elif st.button("Close request", key=f"close_{r.id}", type="primary"):
                act(s, collection_service.close_request, r.id, success="Request closed – user notified")
    st.subheader("Disposal records")
    recs = disposal_service.centre_disposal_records(s, a)
    show_table(recs, ["date", "centre", "method", "quantity", "unit", "facility", "reference", "certificate", "sample"],
               empty="No disposal records yet.")

with tabs[5], db() as s:
    rows = [collection_service.request_to_dict(s, r) for r in collection_service.centre_requests(s, a)]
    show_table(rows, ["centre", "method", "status", "items", "medicines", "requested", "scheduled", "collected",
                      "disposal_status", "closed", "sample"], empty="No requests yet.")
    if rows:
        st.download_button("Download collection report (CSV)", to_csv(rows), "medishare_collections.csv", "text/csv")
    recs = disposal_service.centre_disposal_records(s, a)
    if recs:
        st.download_button("Download disposal report (CSV)", to_csv(recs), "medishare_disposals.csv", "text/csv")

with tabs[6], db() as s:
    centre_settings(s)
