import streamlit as st

from components.header import footer, page_header
from components.session import db
from services import notification_service
from services.errors import ValidationError

page_header("Contact", "Questions about collection, organization onboarding or the pilot")
c1, c2 = st.columns([2, 1])
with c1:
    with st.form("contact", clear_on_submit=True):
        name = st.text_input("Your name *")
        email = st.text_input("Email *")
        message = st.text_area("Message *", height=150)
        sent = st.form_submit_button("Send message", type="primary")
    if sent:
        try:
            with db() as s:
                n = notification_service.submit_contact_message(s, name, email, message)
            if n:
                st.success("Thank you – your message was delivered to the MediShare administrators.")
            else:
                st.warning("Your message was saved but no administrator account is active yet.")
        except ValidationError as e:
            st.error("\n".join(f"- {x}" for x in e.errors))
with c2:
    st.markdown("**MediShare pilot – Kerala**")
    st.caption("For medical emergencies or questions about your medicines, contact your doctor or pharmacist. "
               "MediShare does not provide medical advice.")
footer()
