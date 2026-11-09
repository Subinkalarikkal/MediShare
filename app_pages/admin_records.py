import uuid

import streamlit as st

from components.dialogs import reason_dialog
from components.header import notice, page_header
from components.session import act, actor, db
from components.tables import show_table, to_csv
from services import (
    collection_service, disposal_service, institutional_request_service as irs, inventory_service, medicine_service,
    review_service, transfer_service,
)
from utils.constants import CollectionStatus, InventoryStatus, KERALA_DISTRICTS, RegistrationStatus

a = actor()
page_header("Records & Reviews", "Monitor every workflow across the platform")
pretty = lambda v: v.replace("_", " ").title()  # noqa: E731
tabs = st.tabs(["Medicine Registrations", "Institutional Inventory", "Collection Requests", "Disposal Records",
                "Institutional Reviews", "Institutional Requests", "Transfers"])

with tabs[0], db() as s:
    c1, c2 = st.columns(2)
    status = c1.selectbox("Status", [""] + RegistrationStatus.values(), format_func=lambda v: pretty(v) if v else "All")
    district = c2.selectbox("District", [""] + KERALA_DISTRICTS, format_func=lambda v: v or "All")
    rows = medicine_service.admin_list_registrations(s, a, status or None, district or None)
    idx = show_table(rows, ["medicine", "quantity", "unit", "expiry", "district", "status", "created_at", "sample"],
                     selectable=True, key="regs")
    if idx is not None:
        r = rows[idx]
        c1, c2, c3 = st.columns(3)
        if r["status"] not in {"ON_HOLD", "CLOSED", "CANCELLED", "REJECTED", "DISPOSED"} and c1.button("Place on hold"):
            reason_dialog("Place registration on hold", medicine_service.admin_set_status, (r["id"], "ON_HOLD"),
                          success="Placed on hold")
        if r["status"] == "ON_HOLD" and c2.button("Release hold"):
            reason_dialog("Release hold", medicine_service.admin_set_status, (r["id"], "SUBMITTED"), success="Hold released")
        if r["status"] in {"SUBMITTED", "ON_HOLD"} and c3.button("Reject"):
            reason_dialog("Reject registration", medicine_service.admin_set_status, (r["id"], "REJECTED"), success="Rejected")
        trace = disposal_service.trace_registration(s, a, r["id"])
        for link in trace["chain"]:
            st.markdown(f"**{link['centre']}** – {pretty(link['status'])}")
            show_table(link["events"])
            show_table(link["disposals"], empty="No disposal recorded yet.")
    if rows:
        st.download_button("Export CSV", to_csv(rows), "registrations.csv", "text/csv")

with tabs[1], db() as s:
    status = st.selectbox("Inventory status", [""] + InventoryStatus.values(), format_func=lambda v: pretty(v) if v else "All")
    rows = inventory_service.admin_list_inventory(s, a, status or None)
    show_table(rows, ["organization", "medicine", "batch", "expiry", "days_left", "quantity", "unit", "status", "recall",
                      "sample"], empty="No inventory recorded.")
    if rows:
        st.download_button("Export CSV", to_csv(rows), "inventory.csv", "text/csv", key="inv_csv")

with tabs[2], db() as s:
    status = st.selectbox("Request status", [""] + CollectionStatus.values(), format_func=lambda v: pretty(v) if v else "All")
    rows = collection_service.admin_list_requests(s, a, status or None)
    show_table(rows, ["centre", "user", "method", "status", "items", "medicines", "requested", "scheduled", "collected",
                      "disposal_status", "closed", "sample"], empty="No collection requests.")

with tabs[3], db() as s:
    rows = disposal_service.admin_disposal_records(s, a)
    show_table(rows, ["date", "centre", "method", "quantity", "unit", "facility", "reference", "certificate", "sample"],
               empty="No disposal records.")
    if rows:
        st.download_button("Export CSV", to_csv(rows), "disposals.csv", "text/csv", key="disp_csv")

with tabs[4], db() as s:
    show_table(review_service.admin_review_overview(s, a), empty="No review decisions yet.")

with tabs[5], db() as s:
    show_table(irs.admin_list_requests(s, a), ["organization", "medicine", "category", "quantity", "unit", "status",
                                                "date", "sample"], empty="No institutional requests.")

with tabs[6], db() as s:
    notice("Transfers can only be documented for reviewer-accepted expressions of interest. A transfer becomes "
           "AUTHORIZED only when regulatory (admin), quality and professional (reviewer) authorizations are all recorded.")
    accepted = irs.accepted_interests(s, a)
    if accepted:
        with st.form("new_transfer"):
            opts = {str(x["id"]): f"{x['medicine']} ({x['batch']}) {x['donor']} → {x['recipient']} · {x['quantity']:g} {x['unit'].lower()}"
                    for x in accepted}
            iid = st.selectbox("Accepted expression of interest", list(opts), format_func=opts.get)
            sel = next(x for x in accepted if str(x["id"]) == iid)
            qty = st.number_input("Quantity", min_value=0.0, value=float(sel["quantity"]))
            notes = st.text_input("Notes")
            if st.form_submit_button("Create transfer record", type="primary"):
                act(s, transfer_service.create_transfer, inventory_batch_id=sel["batch_id"],
                    institutional_request_item_id=uuid.UUID(iid), quantity=qty, notes=notes,
                    success="Transfer record created – pending authorizations")
    transfers = transfer_service.list_transfers(s, a)
    show_table(transfers, ["medicine", "batch", "from_org", "to_org", "quantity", "unit", "status", "missing_authorizations",
                           "created", "completed"], empty="No transfer records.")
    for t in [t for t in transfers if t["status"] in {"PENDING_AUTHORIZATION", "AUTHORIZED"}]:
        with st.expander(f"{t['medicine']} ({t['batch']}) → {t['to_org']} · {pretty(t['status'])}"):
            if t["status"] == "PENDING_AUTHORIZATION":
                with st.form(f"reg_{t['id']}"):
                    c1, c2 = st.columns(2)
                    ref = c1.text_input("Regulatory authorization reference *", key=f"rr_{t['id']}")
                    auth = c2.text_input("Issuing authority", "Drugs Control Department, Kerala", key=f"ra_{t['id']}")
                    if st.form_submit_button("Record regulatory authorization"):
                        act(s, transfer_service.add_authorization, t["id"], "REGULATORY", ref, auth,
                            success="Regulatory authorization recorded")
                c1, c2 = st.columns(2)
                if c1.button("Mark authorized", key=f"ma_{t['id']}", type="primary"):
                    act(s, transfer_service.mark_authorized, t["id"], success="Transfer authorized")
                if c2.button("Cancel transfer", key=f"ct_{t['id']}"):
                    reason_dialog("Cancel transfer record", transfer_service.cancel_transfer, (t["id"],),
                                  success="Transfer cancelled")
            else:
                with st.form(f"done_{t['id']}"):
                    note = st.text_input("Completion note (handover documentation) *", key=f"dn_{t['id']}")
                    if st.form_submit_button("Mark completed", type="primary"):
                        act(s, transfer_service.mark_completed, t["id"], note, success="Transfer completed")
