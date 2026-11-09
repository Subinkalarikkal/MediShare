import uuid

import streamlit as st

from components.dialogs import reason_dialog
from components.header import badge, page_header
from components.session import act, actor, db
from components.tables import show_table
from services import user_service
from utils.constants import ROLE_LABELS, AccountStatus, Role

a = actor()
page_header("Users", "Search, review and manage accounts")
c1, c2, c3 = st.columns([2, 1, 1])
text = c1.text_input("Search name or email")
role = c2.selectbox("Role", [""] + Role.values(), format_func=lambda r: ROLE_LABELS[Role(r)] if r else "All roles")
status = c3.selectbox("Status", [""] + AccountStatus.values(), format_func=lambda v: v.title() if v else "All statuses")

with db() as s:
    rows = user_service.search_users(s, a, text, role or None, status or None)
    st.caption(f"{len(rows)} user(s)")
    idx = show_table(rows, ["full_name", "email", "roles", "status", "district", "professional_verified", "last_login_at",
                            "created_at", "sample"], selectable=True, key="users_tbl")
    if idx is None:
        st.caption("Select a row to view the profile and actions.")
        st.stop()
    uid = rows[idx]["id"]
    u = user_service.view_user(s, a, uid)
    st.divider()
    st.markdown(f"### {u['full_name']} {badge(u['status'])}", unsafe_allow_html=True)
    p1, p2 = st.columns(2)
    p1.write(f"**Email:** {u['email']}  \n**Phone:** {u['phone'] or '—'}  \n**Location:** {u['location'] or '—'}, "
             f"{u['district'] or '—'} {u['pin_code'] or ''}")
    p2.write(f"**Roles:** {', '.join(ROLE_LABELS[Role(r)] for r in u['roles'])}  \n**Created:** {u['created_at']:%d %b %Y}"
             f"  \n**Last login:** {u['last_login_at'] or '—'}")
    if "REVIEWER" in u["roles"]:
        st.info(f"Professional registration: {u['professional_registration_number'] or '—'} "
                f"({u['professional_council'] or '—'}) · Verified: {'Yes' if u['professional_verified'] else 'No'}")
        if not u["professional_verified"]:
            with st.form("verify_prof"):
                note = st.text_input("Verification note (e.g. council register checked on …) *")
                if st.form_submit_button("Mark credentials verified", type="primary"):
                    act(s, user_service.verify_professional, uid, note, success="Credentials verified")
    b1, b2, b3 = st.columns(3)
    if u["status"] != "SUSPENDED" and b1.button("Suspend account", type="secondary"):
        reason_dialog(f"Suspend {u['full_name']}", user_service.suspend_user, (uid,), success="Account suspended")
    if u["status"] in {"SUSPENDED", "PENDING", "DEACTIVATED"} and b2.button("Activate account", type="primary"):
        reason_dialog(f"Activate {u['full_name']}", user_service.reactivate_user, (uid,), success="Account activated")
    with b3.popover("Manage roles"):
        add = st.selectbox("Add role", [r for r in Role.values() if r not in u["roles"]],
                           format_func=lambda r: ROLE_LABELS[Role(r)])
        if st.button("Add role"):
            act(s, user_service.assign_role, uid, add, success="Role added")
        if u["roles"]:
            rem = st.selectbox("Remove role", u["roles"], format_func=lambda r: ROLE_LABELS[Role(r)])
            if st.button("Remove role"):
                act(s, user_service.revoke_role, uid, rem, success="Role removed")
    st.subheader("Activity history")
    h = user_service.activity_history(s, a, uid)
    t1, t2 = st.tabs(["Logins", "Audit trail"])
    with t1:
        show_table(h["logins"], empty="No login events.")
    with t2:
        show_table(h["audit"], empty="No audit entries.")
