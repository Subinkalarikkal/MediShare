import json

import pandas as pd
import streamlit as st

from components.header import notice, page_header
from components.session import act, actor, db
from components.tables import show_table
from services import settings_service, user_service
from utils.constants import Perm

a = actor()
page_header("System Settings", "Super administrator configuration")
settings_tab, perms_tab, admins_tab, integ_tab = st.tabs(["Settings & thresholds", "Role permissions", "Administrators",
                                                          "Integrations"])

with settings_tab, db() as s:
    notice("Risk weights must sum to 1.0. Changes apply to the next risk assessment and are recorded in the audit log.")
    for item in settings_service.list_settings(s, a):
        with st.expander(f"{item.key} – {item.description or ''}"):
            with st.form(f"set_{item.key}"):
                raw = st.text_area("Value (JSON)", json.dumps(item.value, indent=2), key=f"v_{item.key}", height=120)
                if st.form_submit_button("Save", type="primary"):
                    try:
                        value = json.loads(raw)
                    except json.JSONDecodeError:
                        st.error("Value must be valid JSON.")
                    else:
                        act(s, settings_service.update_setting, item.key, value, success=f"{item.key} updated")
            st.caption(f"Last updated {item.updated_at:%d %b %Y %H:%M} UTC")

with perms_tab, db() as s:
    matrix = settings_service.role_permission_matrix(s, a)
    perms = [p.value for p in Perm]
    df = pd.DataFrame([{**{"permission": p}, **{r: ("✔" if p in matrix[r] else "") for r in matrix}} for p in perms])
    st.dataframe(df, hide_index=True, width="stretch")
    with st.form("perm_change"):
        c1, c2, c3 = st.columns(3)
        role = c1.selectbox("Role", list(matrix))
        perm = c2.selectbox("Permission", perms)
        grant = c3.radio("Action", ["Grant", "Revoke"], horizontal=True)
        if st.form_submit_button("Apply", type="primary"):
            act(s, settings_service.set_role_permission, role, perm, grant == "Grant", success="Permissions updated")
    st.caption("Grants that would break mandatory safety separations (e.g. individuals managing institutional inventory) are refused.")

with admins_tab, db() as s:
    admins = user_service.search_users(s, a, role="ADMIN") + user_service.search_users(s, a, role="SUPER_ADMIN")
    show_table(admins, ["full_name", "email", "roles", "status", "last_login_at"])
    st.caption("Grant or revoke the Administrator role from the Users page (Manage roles). Only super administrators can do this.")

with integ_tab, db() as s:
    show_table(settings_service.integration_status(s, a))
    st.caption("Secrets are configured through environment variables / Streamlit secrets and are never displayed here.")
