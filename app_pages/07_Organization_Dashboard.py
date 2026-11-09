import uuid

import streamlit as st

from components.forms import authorization_fields, organization_fields
from components.header import badge, notice, page_header
from components.metric_cards import metric_row
from components.session import act, actor, db
from components.tables import show_table
from services import analytics_service, organization_service
from services.organization_service import ROLE_ORG_TYPES
from utils.constants import DocumentType

a = actor()


def doc_download(s, file_id, key):
    if st.button("Download", key=f"dl_btn_{key}"):
        rec, data = organization_service.read_document(s, a, file_id)
        st.download_button("Save file", data, file_name=rec.original_filename, mime=rec.content_type, key=f"dl_{key}")


with db() as s:
    org = organization_service.get_my_organization(s, a)
    if org is None:
        page_header("Register your organization", "Step 1 of onboarding – your account stays restricted until verification")
        allowed = sorted(set().union(*[ROLE_ORG_TYPES.get(r, set()) for r in a.roles]))
        with st.form("org_register"):
            data = organization_fields(allowed, key="neworg")
            ok = st.form_submit_button("Register organization", type="primary")
        if ok:
            act(s, organization_service.register_organization, success="Organization registered", **data)
        notice("Next steps: upload your registration certificate and licences, then submit for verification.")
        st.stop()

    page_header(org.name, f"{org.org_type.replace('_', ' ').title()} · {org.district}")
    st.markdown(f"Identity verification: {badge(org.verification_status)}", unsafe_allow_html=True)
    if org.status_reason:
        st.caption(f"Latest note: {org.status_reason}")
    gaps = organization_service.documentation_gaps(s, org)

    dash, profile, docs, auths, verify = st.tabs(["Dashboard", "Organization Profile", "Documentation",
                                                  "Licences & Authorizations", "Verification"])
    with dash:
        ov = analytics_service.organization_overview(s, a)
        if "INSTITUTIONAL_DONOR" in a.roles:
            metric_row([("Inventory entries", ov.get("total", 0)),
                        ("Under review", ov.get("by_status", {}).get("UNDER_REVIEW", 0) + ov.get("by_status", {}).get("SUBMITTED", 0)),
                        ("Approved for further process", ov.get("by_status", {}).get("APPROVED_FOR_FURTHER_AUTHORIZED_PROCESS", 0)),
                        ("Expiring ≤ 90 days", ov.get("expiring_90", 0)), ("Expired", ov.get("expired", 0))])
        if "NGO" in a.roles:
            metric_row([("Institutional requests", ov.get("requests", 0)), ("Expressions of interest", ov.get("interests", 0))])
        if gaps:
            st.warning("**Outstanding requirements**\n\n" + "\n".join(f"- {g}" for g in gaps))
        else:
            st.success("All required documentation and authorizations are on file.")
        notice("Identity verification and regulatory authorization are separate. A verified organization still needs a "
               "verified, in-date licence before it can handle medicines on MediShare.")

    with profile:
        locked = org.verification_status == "VERIFIED"
        if locked:
            st.caption("Name, type and registration number are locked after verification.")
        with st.form("org_profile"):
            data = organization_fields([org.org_type], prefill={c: getattr(org, c) for c in organization_service.ORG_FIELDS},
                                       key="editorg", lock_identity=locked)
            if st.form_submit_button("Save changes", type="primary"):
                act(s, organization_service.update_organization, org.id, success="Profile updated", **data)

    with docs:
        with st.form("upload_doc", clear_on_submit=True):
            c1, c2 = st.columns(2)
            dtype = c1.selectbox("Document type", DocumentType.values(), format_func=lambda v: v.replace("_", " ").title())
            note = c2.text_input("Notes")
            f = st.file_uploader("File (PDF, PNG or JPG, max 8 MB)", type=["pdf", "png", "jpg", "jpeg"])
            if st.form_submit_button("Upload", type="primary"):
                if f is None:
                    st.error("Choose a file to upload.")
                else:
                    act(s, organization_service.upload_document, org.id, dtype, f.getvalue(), f.name, note,
                        success="Document uploaded")
        rows = organization_service.list_documents(s, a, org.id)
        show_table(rows, ["document_type", "filename", "size_kb", "uploaded_at", "notes"], empty="No documents uploaded yet.")
        if rows:
            pick = st.selectbox("Download a document", range(len(rows)), format_func=lambda i, r=rows: f"{r[i]['document_type']} – {r[i]['filename']}")
            doc_download(s, rows[pick]["file_id"], "orgdoc")

    with auths:
        with st.form("add_auth", clear_on_submit=True):
            data = authorization_fields()
            f = st.file_uploader("Licence copy (optional)", type=["pdf", "png", "jpg", "jpeg"], key="authfile")
            if st.form_submit_button("Add authorization", type="primary"):
                act(s, organization_service.add_authorization, org.id, data=f.getvalue() if f else None,
                    filename=f.name if f else None, success="Authorization submitted for verification", **data)
        rows = [dict(type=x.authorization_type, reference=x.reference_number, authority=x.issuing_authority,
                     valid_from=x.valid_from, valid_until=x.valid_until, status=x.status, notes=x.notes or "")
                for x in organization_service.list_authorizations(s, a, org.id)]
        show_table(rows, empty="No licences or authorizations recorded.")

    with verify:
        if org.verification_status in {"PENDING", "REJECTED"}:
            st.write("When your registration certificate (and licences, where applicable) are uploaded, submit your "
                     "organization for administrator verification.")
            if st.button("Submit for verification", type="primary"):
                act(s, organization_service.submit_for_verification, org.id, success="Submitted for verification")
        hist = [dict(at=h.created_at, from_status=h.from_status or "", to_status=h.to_status, notes=h.notes or "")
                for h in organization_service.verification_history(s, a, org.id)]
        show_table(hist, empty="No verification history.")
