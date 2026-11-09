import streamlit as st

from components.header import footer, page_header
from components.session import db
from components.tables import show_table
from services import organization_service

page_header("Participating Organizations", "Organizations whose identity has been verified by MediShare administrators")
with db() as s:
    orgs = organization_service.public_verified_organizations(s)
types = sorted({o["type"] for o in orgs})
sel = st.multiselect("Filter by type", types)
rows = [o for o in orgs if not sel or o["type"] in sel]
show_table(rows, ["name", "type", "district", "website", "sample"], empty="No verified organizations yet.")
st.caption("Identity verification does not by itself establish authorization to handle or transfer medicines.")
footer()
