import streamlit as st

from components.header import card, footer, notice, page_header

page_header("How It Works", "Two independent workflows that never mix")

st.subheader("Workflow A – Household medicine collection and disposal")
st.markdown("""
1. **Register** unused or expired medicines (photo upload with AI label reading, which you confirm).
2. **Select** a verified, authorized collection centre and choose pickup or drop-off.
3. The **centre accepts or declines** and schedules your collection.
4. You receive a **confirmation**; the centre records **receipt** of your medicines.
5. The centre initiates **authorized disposal** and records the method, facility and manifest reference.
6. You receive a **closure notification** with a fully traceable disposal record.
""")
st.code("DRAFT → SUBMITTED → COLLECTION_PENDING → SCHEDULED → COLLECTED → DISPOSAL_PENDING → DISPOSED → CLOSED",
        language=None)

st.subheader("Workflow B – Institutional surplus coordination")
st.markdown("""
1. An organization registers and uploads documents; an administrator verifies its **identity**.
2. Its **drug licence / authorizations** are verified separately – identity alone grants no handling rights.
3. The organization registers **sealed, in-date, documented** surplus inventory and submits it for review.
4. A **verified pharmacist/reviewer** checks batch, expiry, storage, documentation and recall status.
5. Approval means *approved for further authorized process* only.
6. Verified NGOs register requirements; the matching engine suggests relevant inventory **for review only**.
7. Any transfer requires recorded **regulatory, quality and professional authorizations** and is fully audited.
""")
st.code("DRAFT → SUBMITTED → UNDER_REVIEW → APPROVED_FOR_FURTHER_AUTHORIZED_PROCESS | REJECTED | QUARANTINED | ON_HOLD → CLOSED",
        language=None)
notice("Household registrations can never be moved into the institutional workflow, and no patient-level "
       "dispensing happens on MediShare.")

st.subheader("Where AI helps")
c1, c2, c3 = st.columns(3)
with c1:
    card("Label reading", "OCR and Claude vision suggest medicine name, batch and dates from a photo. You always confirm.")
with c2:
    card("Expiry-risk prioritisation", "An operational score highlights inventory that needs attention first.")
with c3:
    card("Admin insights", "A read-only assistant summarises aggregated platform metrics for administrators.")
footer()
