import streamlit as st

from components.header import card, footer, page_header

page_header("About MediShare", "Smart Medicine Waste Reduction & Authorized Donation Coordination Platform")

st.markdown("""
### Our vision
MediShare is a healthcare technology platform that helps individuals, healthcare institutions, NGOs and
authorized medicine collection centres coordinate the responsible management of unused and expired medicines.

### The problem
Unused medicines accumulate when treatment is discontinued, prescriptions change, patients recover, stock is
over-purchased or expiry dates are not tracked. Without accessible collection points and coordination between
organizations, many of these medicines are discarded unsafely – harming the environment and wasting resources.

### Our approach
""")
c1, c2, c3 = st.columns(3)
with c1:
    card("Safe household disposal", "Households register unused medicines and hand them to verified, authorized "
         "collection centres that record every step through to certified disposal.")
with c2:
    card("Institutional coordination", "Verified, licensed institutions can register eligible surplus for review "
         "by qualified pharmacists. Approval never equals transfer – documented authorization is always required.")
with c3:
    card("Helpful, bounded AI", "AI reads packaging labels and prioritises inventory nearing expiry. It never "
         "prescribes, recommends doses or substitutions, or approves anything.")

st.markdown("""
### Who it is for
Households · Hospitals · Licensed pharmacies · Healthcare institutions · NGOs and charitable healthcare
organizations · Authorized medicine collection centres · Pharmacists and authorized reviewers ·
Government and regulatory stakeholders.

### Where we operate
The pilot covers **Kerala, India**. Locations, languages, organization types and regulatory rules are
configurable to support future expansion.
""")
footer()
