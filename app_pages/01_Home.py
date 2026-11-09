import streamlit as st

from components.header import card, footer, notice
from components.metric_cards import metric_row
from components.session import current_actor, db
from components.tables import show_table
from services import analytics_service, collection_service, organization_service
from utils.constants import KERALA_DISTRICTS

st.markdown(
    '<div class="ms-hero"><h1>Reducing Medicine Waste.<br>Supporting Communities.</h1>'
    '<p>MediShare helps households, healthcare institutions and authorized collection centres in Kerala '
    'manage unused and expired medicines responsibly – from safe household disposal to documented, '
    'professionally reviewed institutional coordination.</p></div>', unsafe_allow_html=True)

a = current_actor()
c1, c2, c3 = st.columns([1, 1, 2])
if a is None:
    if c1.button("Get started", type="primary", icon=":material/login:", width="stretch"):
        st.switch_page("app_pages/login.py")
if c2.button("Find a collection centre", icon=":material/location_on:", width="stretch"):
    st.switch_page("app_pages/05_Collection_Centres.py")

st.subheader("What MediShare does")
st.write(
    "Unused medicines pile up in homes, pharmacies and hospitals when treatments change, prescriptions end or "
    "stock is over-purchased. Poor expiry tracking and a lack of accessible collection points mean many are "
    "thrown away unsafely. MediShare provides one place to record, track and coordinate the responsible "
    "management of these medicines."
)

st.subheader("How it works")
h, i = st.tabs(["Household collection & disposal", "Institutional surplus coordination"])
with h:
    cols = st.columns(4)
    steps = [("Register", "Record unused or expired medicines – snap the pack and let AI read the label."),
             ("Choose a centre", "Pick a verified, authorized collection centre near you."),
             ("Pickup or drop-off", "The centre accepts and schedules your collection."),
             ("Safe disposal", "The centre records authorized disposal; you get a closure confirmation.")]
    for n, (col, (t, b)) in enumerate(zip(cols, steps), 1):
        with col:
            card(t, b, step=n)
    st.caption("Household medicines are only ever collected for disposal. They are never passed on to other people.")
with i:
    cols = st.columns(4)
    steps = [("Verify", "Organizations verify identity and drug-licence authorizations separately."),
             ("Register surplus", "Sealed, in-date, fully documented inventory only."),
             ("Professional review", "Verified pharmacists review batch, expiry, storage and recall status."),
             ("Documented authorization", "Any transfer needs recorded regulatory, quality and professional authorization.")]
    for n, (col, (t, b)) in enumerate(zip(cols, steps), 1):
        with col:
            card(t, b, step=n)

st.subheader("Platform statistics")
with db() as s:
    stats = analytics_service.public_stats(s)
    orgs = organization_service.public_verified_organizations(s)
metric_row([
    ("Verified collection centres", stats["verified_centres"]),
    ("Verified organizations", stats["verified_organizations"]),
    ("Medicines registered", stats["registrations"]),
    ("Completed collections", stats["completed_collections"]),
    ("Disposal records", stats["disposal_records"]),
])
st.caption("Live figures from the MediShare database. Records labelled as sample data are demonstration records.")

left, right = st.columns(2)
with left:
    st.subheader("Participating organizations")
    show_table(orgs[:8], ["name", "type", "district", "sample"], empty="No organizations have been verified yet.")
with right:
    st.subheader("Find a collection centre")
    district = st.selectbox("District", [""] + KERALA_DISTRICTS, format_func=lambda x: x or "All districts", key="home_d")
    with db() as s:
        centres = collection_service.search_centres(s, district=district or None)
    show_table(centres[:8], ["name", "district", "pin_code", "hours", "sample"],
               empty="No authorized centres found for this district yet.")

st.subheader("Safety first")
notice("MediShare is not a medicine marketplace. Individuals cannot give medicines to other individuals; expired, "
       "opened or damaged medicines never enter any reuse workflow; controlled, narcotic, psychotropic and "
       "cold-chain products are excluded from the pilot; and AI never prescribes, recommends doses or approves "
       "redistribution.")
footer()
