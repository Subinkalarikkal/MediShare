import streamlit as st

from components.header import footer, notice, page_header

page_header("Safety and Compliance", "The rules MediShare enforces in software – and what it cannot do for you")

st.subheader("Household safety")
st.markdown("""
- Individuals **cannot** transfer medicines to other individuals or list them for others to claim.
- Household registrations are **only** for authorized collection and disposal.
- Expired medicines are **never** recommended for reuse.
- Opened, damaged or improperly stored medicines never enter a reuse workflow.
- **Controlled, narcotic, psychotropic and other restricted products** and **cold-chain / temperature-sensitive
  products** are excluded from the pilot.
""")
st.subheader("Institutional safety")
st.markdown("""
- Only identity-verified organizations can register inventory, and **verification ≠ medicine-handling
  authorization** – drug licences and professional credentials are verified separately.
- Inventory needs purchase/source and storage documentation, a checked recall status and sealed original packaging.
- Every batch is reviewed by a professionally verified reviewer; approval does **not** authorize transfer.
- Transfers require documented regulatory, quality and professional authorization, and every step is audited.
""")
st.subheader("AI safety")
st.markdown("""
AI on MediShare **never** prescribes medicines, recommends dosages or substitutions, determines clinical
suitability, approves redistribution, overrides a reviewer, or makes legal or regulatory decisions. It only helps
with label reading (always confirmed by a person), operational prioritisation and administrative reporting.
Medicine photos are only sent to the external AI provider when you choose to allow it.
""")
st.subheader("Compliance status")
notice("Before real-world operation, MediShare requires an appropriate regulatory assessment under applicable "
       "Indian drug laws (including the Drugs and Cosmetics framework), bio-medical waste rules and data protection "
       "law. The existence of a software workflow does not mean MediShare holds any authorization to collect, "
       "transfer or redistribute medicines. The pilot supports safe collection and disposal workflows.")
footer()
