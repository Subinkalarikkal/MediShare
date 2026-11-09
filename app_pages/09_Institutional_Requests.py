import uuid

import streamlit as st

from components.dialogs import confirm_dialog
from components.header import badge, notice, page_header, sample_tag
from components.session import act, actor, db
from components.tables import show_table, to_csv
from services import institutional_request_service as irs
from services import medicine_service, transfer_service
from utils.constants import Unit

a = actor()
page_header("Institutional Requests", "Register requirements and review matching recommendations")
notice("Recommendations are suggestions for authorized professional review only. Nothing is reserved, allocated or "
       "transferred automatically, and household medicines are never included.")
pretty = lambda v: v.replace("_", " ").title()  # noqa: E731

mine, new, match, rep = st.tabs(["My Requirements", "New Requirement", "Recommendations", "Transfers & Reports"])

with new, db() as s:
    term = st.text_input("Search medicine master (optional)", key="ir_search")
    meds = medicine_service.search_medicines(s, term, limit=30) if term else []
    mopts = {"": "— Not linked —"} | {str(m.id): f"{m.name} ({m.generic_name or '—'})" for m in meds}
    with st.form("new_ir"):
        mid = st.selectbox("Medicine master entry", list(mopts), format_func=mopts.get)
        c1, c2 = st.columns(2)
        name = c1.text_input("Medicine identification *", help="Brand or generic name you need")
        cat = c2.text_input("Required inventory category *", "Analgesic")
        q1, q2 = st.columns(2)
        qty = q1.number_input("Requested quantity *", min_value=0.0, value=100.0)
        unit = q2.selectbox("Unit *", Unit.values(), format_func=pretty)
        purpose = st.text_area("Purpose *", help="Describe the programme or facility this supports", height=80)
        docs = st.text_area("Documentation you can provide", height=60)
        c3, c4 = st.columns(2)
        submit = c3.form_submit_button("Submit requirement", type="primary", width="stretch")
        draft = c4.form_submit_button("Save draft", width="stretch")
    if submit or draft:
        act(s, irs.create_request, required_category=cat, medicine_name=name, requested_quantity=qty, unit=unit,
            purpose=purpose, medicine_id=uuid.UUID(mid) if mid else None, required_documentation=docs,
            submit=bool(submit), success="Requirement saved")

with mine, db() as s:
    reqs = irs.list_my_requests(s, a)
    if not reqs:
        st.info("No requirements registered yet.")
    for r in reqs:
        with st.container(border=True):
            c1, c2 = st.columns([4, 1])
            c1.markdown(f"**{r.medicine_name}** · {r.required_category} {badge(r.status)} {sample_tag(r.is_sample)}",
                        unsafe_allow_html=True)
            c1.caption(f"{float(r.requested_quantity):g} {r.unit.lower()} · {r.purpose}")
            if r.status == "DRAFT" and c2.button("Submit", key=f"irs_{r.id}"):
                act(s, irs.submit_request, r.id, success="Submitted")
            if r.status not in {"CLOSED", "CANCELLED"} and c2.button("Cancel", key=f"irc_{r.id}"):
                confirm_dialog("Cancel this requirement and withdraw open expressions of interest?", irs.cancel_request,
                               (r.id,), success="Requirement cancelled")
            items = irs.request_items(s, a, r.id)
            if items:
                show_table(items, ["medicine", "batch", "donor", "quantity", "unit", "status"])

with match, db() as s:
    open_reqs = [r for r in irs.list_my_requests(s, a) if r.status in {"SUBMITTED", "UNDER_REVIEW"}]
    if not open_reqs:
        st.info("Submit a requirement to see recommendations.")
    else:
        ropts = {str(r.id): f"{r.medicine_name} – {float(r.requested_quantity):g} {r.unit.lower()}" for r in open_reqs}
        rid = st.selectbox("Requirement", list(ropts), format_func=ropts.get)
        recs = irs.recommendations(s, a, uuid.UUID(rid))
        show_table(recs, ["medicine", "batch", "donor", "district", "quantity", "unit", "expiry", "match_score", "reasons"],
                   empty="No approved inventory currently matches this requirement.")
        if recs:
            with st.form("interest"):
                bopts = {str(x["batch_id"]): f"{x['medicine']} ({x['batch']}) – {x['donor']}" for x in recs}
                bid = st.selectbox("Express interest in", list(bopts), format_func=bopts.get)
                q = st.number_input("Quantity", min_value=0.0, value=float(recs[0]["quantity"]))
                note = st.text_input("Note for reviewer")
                if st.form_submit_button("Submit expression of interest", type="primary"):
                    act(s, irs.express_interest, uuid.UUID(rid), uuid.UUID(bid), q, note,
                        success="Expression of interest sent for authorized review")

with rep, db() as s:
    transfers = transfer_service.list_transfers(s, a)
    show_table(transfers, ["medicine", "batch", "from_org", "to_org", "quantity", "unit", "status",
                           "missing_authorizations", "created", "completed"], empty="No transfer records yet.")
    rows = [dict(medicine=r.medicine_name, category=r.required_category, quantity=float(r.requested_quantity),
                 unit=r.unit, status=r.status, requested=r.request_date) for r in irs.list_my_requests(s, a)]
    if rows:
        st.download_button("Download requirements report (CSV)", to_csv(rows), "medishare_requirements.csv", "text/csv")
    if transfers:
        st.download_button("Download transfer records (CSV)", to_csv(transfers), "medishare_transfers.csv", "text/csv")
