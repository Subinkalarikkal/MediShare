import uuid
from datetime import date, timedelta

import streamlit as st

from ai import expiry_risk_engine
from components.dialogs import reason_dialog
from components.header import badge, notice, page_header, sample_tag
from components.session import act, actor, db
from components.tables import show_table, to_csv
from services import inventory_service, medicine_service, review_service, transfer_service
from utils.constants import InventoryStatus, PackagingCondition, RecallStatus, RestrictedClass, Unit
from utils.date_utils import today

a = actor()
page_header("Institutional Inventory", "Sealed, in-date, documented surplus for authorized professional review")
pretty = lambda v: v.replace("_", " ").title()  # noqa: E731

inv_tab, add_tab, doc_tab, hist_tab, risk_tab, rep_tab = st.tabs(
    ["Inventory", "Add Inventory", "Documentation", "Review History", "Expiry Risk", "Reports"])

with inv_tab, db() as s:
    status = st.multiselect("Status", InventoryStatus.values(), format_func=pretty)
    batches = inventory_service.list_org_inventory(s, a, status or None)
    if not batches:
        st.info("No inventory registered yet.")
    for b in batches:
        days = (b.expiry_date - today()).days
        with st.container(border=True):
            c1, c2 = st.columns([4, 2])
            c1.markdown(f"**{b.medicine_name}** · batch `{b.batch_number}` {badge(b.status)} {sample_tag(b.is_sample)}",
                        unsafe_allow_html=True)
            c1.caption(f"{float(b.quantity):g} {b.unit.lower()} · Expiry {b.expiry_date:%d %b %Y} "
                       f"({'expired' if days <= 0 else f'{days} days'}) · Packaging {pretty(b.original_packaging_status)} · "
                       f"Recall {pretty(b.recall_status)}")
            if b.status == "DRAFT":
                blockers = inventory_service.submission_blockers(s, b)
                if blockers:
                    c1.caption("Before submitting: " + "; ".join(blockers))
                if days <= 0:
                    c1.error("Expired – this batch cannot enter review. Close it and arrange authorized disposal.")
            with c2:
                k1, k2 = st.columns(2)
                if b.status == "DRAFT" and k1.button("Submit", key=f"s_{b.id}", type="primary"):
                    act(s, inventory_service.submit_batch, b.id, success="Submitted for review")
                if b.status == "SUBMITTED" and k1.button("Withdraw", key=f"w_{b.id}"):
                    act(s, inventory_service.withdraw_batch, b.id, success="Withdrawn to draft")
                if b.status in {"DRAFT", "REJECTED", "QUARANTINED", "APPROVED_FOR_FURTHER_AUTHORIZED_PROCESS"} and \
                        k2.button("Close", key=f"c_{b.id}"):
                    reason_dialog(f"Close {b.medicine_name} ({b.batch_number})", inventory_service.close_batch, (b.id,),
                                  reason_kw="note", label="Closing note", success="Inventory closed")
            if b.status.startswith("APPROVED"):
                st.caption("Approved for further authorized process only – this does not authorize any transfer.")

with add_tab, db() as s:
    term = st.text_input("Search the medicine master", key="inv_search")
    meds = medicine_service.search_medicines(s, term, limit=50)
    if not meds:
        st.info("No matching medicine in the master database. Ask an administrator to add it.")
    else:
        mopts = {str(m.id): f"{m.name} · {m.generic_name or '—'} · {m.manufacturer or '—'} · {pretty(m.dosage_form)}"
                 + (f" · RESTRICTED ({m.restricted_classification})" if m.restricted_classification != "NONE" else "")
                 for m in meds}
        with st.form("add_inventory"):
            mid = st.selectbox("Medicine *", list(mopts), format_func=mopts.get)
            c1, c2, c3 = st.columns(3)
            batch = c1.text_input("Batch number *")
            mfg = c2.date_input("Manufacturing date", date.today() - timedelta(days=180), max_value=date.today())
            exp = c3.date_input("Expiry date *", date.today() + timedelta(days=365))
            q1, q2, q3 = st.columns(3)
            qty = q1.number_input("Quantity *", min_value=0.0, value=100.0, step=1.0)
            unit = q2.selectbox("Unit *", Unit.values(), format_func=pretty)
            pack = q3.selectbox("Original packaging status *", PackagingCondition.values(), format_func=pretty)
            r1, r2 = st.columns(2)
            recall = r1.selectbox("Recall verification status *", RecallStatus.values(), format_func=pretty)
            cls = r2.selectbox("Product classification *", RestrictedClass.values(), format_func=pretty)
            storage = st.text_input("Storage conditions *", "Stored below 25°C, protected from light and moisture")
            history = st.text_area("Storage history", height=70)
            if st.form_submit_button("Save draft", type="primary"):
                act(s, inventory_service.create_batch, medicine_id=uuid.UUID(mid), batch_number=batch,
                    manufacturing_date=mfg, expiry_date=exp, quantity=qty, unit=unit, original_packaging_status=pack,
                    recall_status=recall, product_classification=cls, storage_conditions=storage,
                    storage_history=history, success="Inventory saved as draft – attach documentation next")
    notice("Restricted, controlled and cold-chain products, opened packs and expired stock cannot be submitted.")

with doc_tab, db() as s:
    drafts = inventory_service.list_org_inventory(s, a, ["DRAFT", "ON_HOLD"])
    if not drafts:
        st.info("Documentation can be attached to draft or on-hold inventory.")
    else:
        opts = {str(b.id): f"{b.medicine_name} ({b.batch_number})" for b in drafts}
        bid = st.selectbox("Inventory entry", list(opts), format_func=opts.get)
        b = next(x for x in drafts if str(x.id) == bid)
        st.write(f"Purchase / source documentation: {'✅ on file' if b.source_document_file_id else '❌ missing'} · "
                 f"Storage documentation: {'✅ on file' if b.storage_document_file_id else '❌ missing'}")
        with st.form("inv_doc", clear_on_submit=True):
            kind = st.radio("Document", ["source", "storage"], horizontal=True,
                            format_func=lambda k: "Purchase / source documentation" if k == "source" else "Storage records")
            f = st.file_uploader("File (PDF, PNG or JPG)", type=["pdf", "png", "jpg", "jpeg"])
            if st.form_submit_button("Attach", type="primary"):
                if f is None:
                    st.error("Choose a file.")
                else:
                    act(s, inventory_service.attach_document, b.id, kind, f.getvalue(), f.name, success="Document attached")

with hist_tab, db() as s:
    show_table(review_service.org_review_history(s, a), empty="No review decisions yet.")
    st.markdown("**Authorized transfer history**")
    show_table(transfer_service.list_transfers(s, a), ["medicine", "batch", "from_org", "to_org", "quantity", "unit",
                                                        "status", "missing_authorizations", "created", "completed"],
               empty="No transfer records involve your organization.")

with risk_tab, db() as s:
    st.caption("Operational priority score (time to expiry, quantity, processing delay, review capacity). "
               "It is not a measure of medicine safety.")
    show_table(expiry_risk_engine.org_assessments(s, a), empty="No risk assessments have been run yet.")

with rep_tab, db() as s:
    rows = [dict(medicine=b.medicine_name, batch=b.batch_number, manufacturing_date=b.manufacturing_date,
                 expiry_date=b.expiry_date, quantity=float(b.quantity), unit=b.unit, status=b.status,
                 recall=b.recall_status, packaging=b.original_packaging_status, sample=b.is_sample)
            for b in inventory_service.list_org_inventory(s, a)]
    show_table(rows, empty="Nothing to report yet.")
    if rows:
        st.download_button("Download inventory report (CSV)", to_csv(rows), "medishare_inventory.csv", "text/csv")
