import uuid

import streamlit as st

from components.header import badge, notice, page_header
from components.session import act, actor, db
from components.tables import show_table
from services import collection_service, organization_service
from utils.constants import OrgType, VerificationStatus

a = actor()
page_header("Organizations & Collection Centres", "Identity verification, licence verification and centre authorization")
pretty = lambda v: v.replace("_", " ").title()  # noqa: E731
orgs_tab, auth_tab, centre_tab = st.tabs(["Organizations", "Licences & Authorizations", "Collection Centres"])

with orgs_tab, db() as s:
    c1, c2, c3 = st.columns([2, 1, 1])
    text = c1.text_input("Search organizations")
    status = c2.selectbox("Verification status", [""] + VerificationStatus.values(), format_func=lambda v: pretty(v) if v else "All")
    otype = c3.selectbox("Type", [""] + OrgType.values(), format_func=lambda v: pretty(v) if v else "All")
    rows = organization_service.list_organizations(s, a, status or None, otype or None, text)
    idx = show_table(rows, ["name", "type", "district", "registration_number", "verification_status", "documentation_gaps",
                            "created_at", "sample"], selectable=True, key="org_tbl")
    if idx is not None:
        org = organization_service.get_organization(s, a, rows[idx]["id"])
        st.divider()
        st.markdown(f"### {org.name} {badge(org.verification_status)}", unsafe_allow_html=True)
        st.write(f"{pretty(org.org_type)} · Reg. no. {org.registration_number} · {org.address}, {org.district} {org.pin_code}  \n"
                 f"Contact: {org.contact_person} · {org.contact_email} · {org.phone}  \n"
                 f"Authorized professional: {org.authorized_professional_name or '—'} "
                 f"({org.authorized_professional_registration or '—'})")
        docs = organization_service.list_documents(s, a, org.id)
        show_table(docs, ["document_type", "filename", "size_kb", "uploaded_at", "notes"], empty="No documents uploaded.")
        if docs:
            pick = st.selectbox("Open document", range(len(docs)), format_func=lambda i, d=docs: f"{d[i]['document_type']} – {d[i]['filename']}")
            if st.button("Load document"):
                rec, data = organization_service.read_document(s, a, docs[pick]["file_id"])
                st.download_button("Save file", data, rec.original_filename, rec.content_type)
        gaps = organization_service.documentation_gaps(s, org)
        if gaps:
            st.warning("Gaps: " + "; ".join(gaps))
        from utils.constants import VERIFICATION_TRANSITIONS
        options = sorted(VERIFICATION_TRANSITIONS.get(org.verification_status, set()))
        if options:
            with st.form("org_decision"):
                decision = st.selectbox("Decision", options, format_func=pretty)
                note = st.text_area("Decision note *", height=80)
                if st.form_submit_button("Record decision", type="primary"):
                    act(s, organization_service.decide_verification, org.id, decision, note, success="Decision recorded")
        show_table([dict(at=h.created_at, from_status=h.from_status or "", to_status=h.to_status, notes=h.notes or "")
                    for h in organization_service.verification_history(s, a, org.id)])

with auth_tab, db() as s:
    notice("Licences are verified separately from organization identity. Only verify after checking the licence with "
           "the issuing authority.")
    pend = organization_service.pending_authorizations(s, a)
    if not pend:
        st.info("No licences awaiting verification.")
    for p in pend:
        with st.container(border=True):
            st.markdown(f"**{p['organization']}** · {pretty(p['type'])} · `{p['reference']}`")
            st.caption(f"{p['authority'] or '—'} · valid {p['valid_from'] or '—'} → {p['valid_until'] or '—'}")
            if p["file_id"] and st.button("Load licence copy", key=f"lf_{p['id']}"):
                rec, data = organization_service.read_document(s, a, p["file_id"])
                st.download_button("Save file", data, rec.original_filename, rec.content_type, key=f"lfs_{p['id']}")
            with st.form(f"auth_{p['id']}"):
                note = st.text_input("Verification note *", key=f"an_{p['id']}")
                c1, c2 = st.columns(2)
                if c1.form_submit_button("Verify licence", type="primary"):
                    act(s, organization_service.decide_authorization, p["id"], "VERIFIED", note, success="Licence verified")
                if c2.form_submit_button("Reject"):
                    act(s, organization_service.decide_authorization, p["id"], "REJECTED", note, success="Licence rejected")

with centre_tab, db() as s:
    rows = collection_service.admin_list_centres(s, a)
    idx = show_table(rows, ["name", "organization", "org_status", "district", "verification_status", "status", "capacity",
                            "authorized", "sample"], selectable=True, key="centre_tbl")
    if idx is not None:
        c = rows[idx]
        st.markdown(f"### {c['name']} {badge(c['verification_status'])}", unsafe_allow_html=True)
        with st.form("centre_decision"):
            decision = st.selectbox("Decision", ["VERIFIED", "UNDER_REVIEW", "REJECTED", "SUSPENDED"], format_func=pretty)
            note = st.text_area("Decision note *", height=70)
            if st.form_submit_button("Record decision", type="primary"):
                act(s, collection_service.decide_centre, c["id"], decision, note, success="Centre decision recorded")
