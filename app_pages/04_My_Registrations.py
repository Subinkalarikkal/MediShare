import streamlit as st

from components.dialogs import confirm_dialog, reason_dialog
from components.header import badge, page_header, sample_tag
from components.session import act, actor, db
from services import disposal_service, medicine_service
from utils.constants import RegistrationStatus
from utils.date_utils import today

a = actor()
page_header("My Registrations", "Track each medicine from registration to certified disposal")

statuses = st.multiselect("Filter by status", RegistrationStatus.values(), format_func=lambda v: v.replace("_", " ").title())
with db() as s:
    regs = medicine_service.list_my_registrations(s, a, statuses or None)
    if not regs:
        st.info("No registrations yet.")
        if st.button("Register a medicine", type="primary"):
            st.switch_page("app_pages/03_Medicine_Registration.py")
    for r in regs:
        days = (r.expiry_date - today()).days
        exp = "Expired" if days <= 0 else f"{days} days to expiry"
        with st.container(border=True):
            c1, c2 = st.columns([4, 2])
            c1.markdown(f"**{r.medicine_name}** {badge(r.status)} {sample_tag(r.is_sample)}", unsafe_allow_html=True)
            c1.caption(f"{float(r.quantity):g} {r.unit.lower()} · Expiry {r.expiry_date:%d %b %Y} ({exp}) · "
                       f"Batch {r.batch_number or '—'} · Registered {r.created_at:%d %b %Y}")
            with c2:
                b1, b2, b3 = st.columns(3)
                if r.status == "DRAFT":
                    if b1.button("Submit", key=f"sub_{r.id}"):
                        act(s, medicine_service.submit_registration, r.id, success="Submitted for collection")
                    if b2.button("Delete", key=f"del_{r.id}"):
                        confirm_dialog(f"Delete the draft '{r.medicine_name}'?", medicine_service.delete_draft,
                                       (r.id,), success="Draft deleted")
                if r.status in {"DRAFT", "SUBMITTED"} and b3.button("Cancel", key=f"can_{r.id}"):
                    reason_dialog(f"Cancel '{r.medicine_name}'", medicine_service.cancel_registration, (r.id,),
                                  success="Registration cancelled", min_len=0)
                if r.status == "SUBMITTED" and b1.button("Request", key=f"req_{r.id}", type="primary"):
                    st.switch_page("app_pages/06_Collection_Requests.py")
            if r.status not in {"DRAFT", "SUBMITTED", "CANCELLED", "REJECTED"}:
                with st.expander("Collection & disposal trail"):
                    trace = disposal_service.trace_registration(s, a, r.id)
                    for link in trace["chain"]:
                        st.markdown(f"**{link['centre']}** – request {badge(link['status'])}", unsafe_allow_html=True)
                        for e in link["events"]:
                            st.caption(f"{e['at']:%d %b %Y %H:%M} · {(e['to'] or e['event']).replace('_', ' ').title()}"
                                       + (f" – {e['notes']}" if e["notes"] else ""))
                        for d in link["disposals"]:
                            st.success(f"Disposed {d['date']:%d %b %Y} by {d['method'].replace('_', ' ').lower()} at "
                                       f"{d['facility']} (reference {d['reference']}).")
