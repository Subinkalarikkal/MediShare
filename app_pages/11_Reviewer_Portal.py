import uuid

import streamlit as st

from components.header import badge, notice, page_header
from components.metric_cards import metric_row
from components.session import act, actor, db, guarded
from components.tables import show_table
from services import analytics_service, organization_service, review_service, transfer_service
from services.review_service import CHECKLIST_KEYS
from utils.constants import ReviewDecision

a = actor()
page_header("Reviewer Portal", "Professional review of institutional inventory")
if not a.professional_verified:
    st.error("Your professional credentials have not been verified yet. Review actions are disabled until an "
             "administrator verifies your registration.")
    st.stop()
notice("You are the decision-maker. Approval means 'approved for further authorized process' only – it never authorizes "
       "a transfer. Do not rely on any AI output for review decisions.")
pretty = lambda v: v.replace("_", " ").title()  # noqa: E731

tabs = st.tabs(["Dashboard", "Review Queue", "Inventory Review", "Expressions of Interest", "Transfer Authorizations",
                "Review History"])

with tabs[0], db() as s:
    ov = guarded(analytics_service.reviewer_overview, s, a)
    metric_row([("Submitted", ov["submitted"]), ("Under review", ov["under_review"]), ("On hold", ov["on_hold"]),
                ("Interests awaiting review", ov["interests"]), ("My decisions", ov["my_decisions"])])

with tabs[1], db() as s:
    queue = review_service.review_queue(s, a)
    show_table(queue, ["organization", "district", "medicine", "batch", "expiry", "days_left", "quantity", "unit",
                       "status", "submitted", "sample"], empty="The review queue is empty.")

with tabs[2], db() as s:
    queue = review_service.review_queue(s, a, ["SUBMITTED", "UNDER_REVIEW", "ON_HOLD", "QUARANTINED"])
    if not queue:
        st.info("Nothing to review.")
    else:
        opts = {str(q["id"]): f"{q['medicine']} ({q['batch']}) – {q['organization']} · {pretty(q['status'])}" for q in queue}
        bid = uuid.UUID(st.selectbox("Select inventory", list(opts), format_func=opts.get))
        d = review_service.batch_details(s, a, bid)
        b, org, med = d["batch"], d["organization"], d["medicine"]
        st.markdown(f"### {b.medicine_name} · `{b.batch_number}` {badge(b.status)}", unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("**Product**")
            st.write(f"Generic: {med.generic_name or '—'}  \nManufacturer: {med.manufacturer or '—'}  \n"
                     f"Form: {pretty(med.dosage_form)}  \nMaster classification: {pretty(med.restricted_classification)}")
        with c2:
            st.markdown("**Batch**")
            st.write(f"Manufactured: {b.manufacturing_date or '—'}  \nExpiry: {b.expiry_date}  \n"
                     f"Quantity: {float(b.quantity):g} {b.unit.lower()}  \nPackaging: {pretty(b.original_packaging_status)}  \n"
                     f"Recall (declared): {pretty(b.recall_status)}  \nClassification: {pretty(b.product_classification)}")
        with c3:
            st.markdown("**Organization**")
            st.write(f"{org.name}  \n{pretty(org.org_type)} · {org.district}  \nVerification: {org.verification_status}  \n"
                     f"Pharmacist: {org.authorized_professional_name or '—'} ({org.authorized_professional_registration or '—'})")
        st.markdown("**Storage**")
        st.write(f"Conditions: {b.storage_conditions or '—'}  \nHistory: {b.storage_history or '—'}")
        st.markdown("**Documentation review**")
        for kind, doc in d["documents"].items():
            label = "Purchase / source documentation" if kind == "source" else "Storage records"
            if doc:
                cA, cB = st.columns([3, 1])
                cA.write(f"{label}: {doc['filename']} · SHA-256 `{doc['sha256'][:16]}…`")
                if cB.button("Open", key=f"open_{kind}"):
                    rec, data = organization_service.read_document(s, a, doc["file_id"])
                    st.download_button(f"Save {rec.original_filename}", data, rec.original_filename, rec.content_type,
                                       key=f"save_{kind}")
            else:
                st.write(f"{label}: ❌ missing")
        if d["blockers"]:
            st.warning("Automated eligibility checks:\n\n" + "\n".join(f"- {x}" for x in d["blockers"]))
        else:
            st.success("Automated eligibility checks passed (documentation, expiry, packaging, licence, classification).")

        st.markdown("**Review decision**")
        allowed = {
            "SUBMITTED": ["START_REVIEW", "QUARANTINE"],
            "UNDER_REVIEW": ["APPROVE_FOR_FURTHER_AUTHORIZED_PROCESS", "REJECT", "HOLD", "QUARANTINE"],
            "ON_HOLD": ["RESUME", "REJECT", "QUARANTINE"],
            "QUARANTINED": ["RESUME"],
        }.get(b.status, [])
        with st.form("decision"):
            decision = st.selectbox("Decision", allowed, format_func=pretty)
            st.caption("Verification checklist (all required for approval)")
            checks = {k: st.checkbox(v, key=f"chk_{k}") for k, v in CHECKLIST_KEYS.items()}
            ref = st.text_input("Authorization reference (required for approval)")
            notes = st.text_area("Review notes *", height=90)
            if st.form_submit_button("Record decision", type="primary"):
                act(s, review_service.record_decision, b.id, decision, notes=notes, checklist=checks,
                    authorization_reference=ref, success="Decision recorded")
        if d["history"]:
            st.markdown("**History**")
            show_table([dict(at=h.created_at, decision=h.decision, from_status=h.from_status, to_status=h.to_status,
                             notes=h.notes) for h in d["history"]])

with tabs[3], db() as s:
    items = review_service.pending_interests(s, a)
    if not items:
        st.info("No expressions of interest awaiting review.")
    for it in items:
        with st.container(border=True):
            st.markdown(f"**{it['requesting_org']}** requests {it['quantity']:g} {it['unit'].lower()} of "
                        f"**{it['medicine']}** ({it['batch']}) from {it['donor']}")
            st.caption(f"Purpose: {it['purpose']} · Expiry {it['expiry']}")
            with st.form(f"int_{it['id']}"):
                notes = st.text_input("Review notes *", key=f"in_{it['id']}")
                c1, c2 = st.columns(2)
                if c1.form_submit_button("Accept for authorization", type="primary"):
                    act(s, review_service.review_interest, it["id"], True, notes, success="Accepted for authorization")
                if c2.form_submit_button("Decline"):
                    act(s, review_service.review_interest, it["id"], False, notes, success="Declined")

with tabs[4], db() as s:
    transfers = [t for t in transfer_service.list_transfers(s, a) if t["status"] == "PENDING_AUTHORIZATION"]
    st.caption("Record the quality and professional authorizations you have personally verified. Regulatory "
               "authorization is recorded by an administrator.")
    if not transfers:
        st.info("No transfers awaiting authorization.")
    for t in transfers:
        with st.container(border=True):
            st.markdown(f"**{t['medicine']}** ({t['batch']}) · {t['from_org']} → {t['to_org']} · "
                        f"{t['quantity']:g} {t['unit'].lower()}")
            st.caption(f"Missing: {t['missing_authorizations']}")
            with st.form(f"ta_{t['id']}"):
                c1, c2 = st.columns(2)
                kind = c1.selectbox("Authorization", ["QUALITY", "PROFESSIONAL"], key=f"tk_{t['id']}")
                ref = c2.text_input("Reference number *", key=f"tr_{t['id']}")
                notes = st.text_input("Notes", key=f"tn_{t['id']}")
                if st.form_submit_button("Record authorization", type="primary"):
                    act(s, transfer_service.add_authorization, t["id"], kind, ref, None, notes,
                        success="Authorization recorded")

with tabs[5], db() as s:
    show_table(review_service.my_review_history(s, a), empty="You have not recorded any decisions yet.")
