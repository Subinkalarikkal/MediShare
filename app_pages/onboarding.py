import streamlit as st

from components.forms import district_select
from components.header import notice, page_header
from components.session import clear_auth, db, start_session
from config import get_settings
from services import auth_service
from services.errors import ValidationError
from utils.constants import ROLE_LABELS, Role

email = st.session_state.get("signup_email")
if not email or st.session_state.get("signup_verified") is not True:
    # Never create an account for an email address whose ownership hasn't been proven.
    st.session_state.pop("signup_email", None)
    st.error("Please verify your email address first.")
    if st.button("Go to sign-in", type="primary"):
        st.rerun()
    st.stop()
page_header("Create your MediShare account", f"Verified email: {email}")
notice("Individuals can start registering medicines immediately. Organization, collection-centre and reviewer "
       "accounts are restricted until an administrator reviews your application.")

types = [Role.INDIVIDUAL, Role.INSTITUTIONAL_DONOR, Role.NGO, Role.COLLECTION_CENTRE, Role.REVIEWER]
account_type = st.radio("I am registering as", [t.value for t in types], format_func=lambda v: ROLE_LABELS[Role(v)],
                        horizontal=True)
with st.form("signup"):
    full_name = st.text_input("Full name *", st.session_state.get("signup_name", ""))
    c1, c2, c3 = st.columns(3)
    phone = c1.text_input("Phone")
    with c2:
        district = district_select()
    pin = c3.text_input("PIN code", max_chars=6)
    location = st.text_input("Locality")
    reg_no = council = None
    if account_type == Role.REVIEWER.value:
        st.markdown("**Professional credentials** (verified by an administrator before review access is enabled)")
        reg_no = st.text_input("Pharmacist / professional registration number *")
        council = st.text_input("Registering council *", "Kerala State Pharmacy Council")
    agree = st.checkbox("I understand MediShare is for authorized collection, disposal and documented coordination "
                        "only, and is not a medicine marketplace.")
    submitted = st.form_submit_button("Create account", type="primary")
if submitted:
    if not agree:
        st.error("Please confirm that you understand how MediShare works.")
    else:
        try:
            with db() as s:
                user = auth_service.register_user(
                    s, email=email, full_name=full_name, account_type=account_type, phone=phone or None,
                    location=location, district=district, pin_code=pin or None,
                    professional_registration_number=reg_no, professional_council=council)
                uid, uemail = user.id, user.email
            start_session(uid, uemail, get_settings().auth_mode)
            st.rerun()
        except ValidationError as e:
            st.error("\n".join(f"- {x}" for x in e.errors))
if st.button("Cancel"):
    clear_auth()
    st.session_state.pop("signup_email", None)
    st.session_state.pop("signup_verified", None)
    st.rerun()
