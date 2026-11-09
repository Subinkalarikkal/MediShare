import streamlit as st

from components.forms import district_select
from components.header import badge, page_header
from components.session import act, actor, db
from services import user_service
from utils.constants import ROLE_LABELS, Role

a = actor()
page_header("My Profile")
with db() as s:
    u = user_service.get_profile(s, a)
    st.markdown(f"**{u.email}** · " + ", ".join(ROLE_LABELS[Role(r)] for r in sorted(a.roles)) + f" {badge(u.status)}",
                unsafe_allow_html=True)
    st.caption(f"Member since {u.created_at:%d %b %Y} · Last login {u.last_login_at:%d %b %Y %H:%M} UTC"
               if u.last_login_at else f"Member since {u.created_at:%d %b %Y}")
    with st.form("profile"):
        name = st.text_input("Full name *", u.full_name)
        c1, c2, c3 = st.columns(3)
        phone = c1.text_input("Phone", u.phone or "")
        with c2:
            district = district_select(value=u.district)
        pin = c3.text_input("PIN code", u.pin_code or "", max_chars=6)
        location = st.text_input("Locality / address", u.location or "")
        if st.form_submit_button("Save profile", type="primary"):
            act(s, user_service.update_profile, full_name=name, phone=phone or None, district=district,
                pin_code=pin or None, location=location, success="Profile updated")
    if "REVIEWER" in a.roles:
        st.info(f"Professional registration: {u.professional_registration_number or '—'} "
                f"({u.professional_council or '—'}) · {'Verified' if u.professional_verified else 'Not yet verified'}")
    st.caption("Email and roles are managed through your sign-in provider and administrators. Your data is used only "
               "to coordinate collection and disposal; see the Safety page for details.")
