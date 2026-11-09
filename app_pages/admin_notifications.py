import streamlit as st

from components.header import page_header
from components.session import act, actor, db
from components.tables import show_table
from config import get_settings
from services import notification_service
from utils.constants import ROLE_LABELS, Role

a = actor()
page_header("Notifications", "Broadcast announcements and monitor delivery")
with db() as s:
    with st.form("broadcast", clear_on_submit=True):
        roles = st.multiselect("Recipients (roles)", Role.values(), default=["INDIVIDUAL"],
                               format_func=lambda r: ROLE_LABELS[Role(r)])
        title = st.text_input("Title *")
        message = st.text_area("Message *", height=100)
        if st.form_submit_button("Send notification", type="primary"):
            act(s, notification_service.broadcast, roles, title, message, success="Notification sent")
    st.subheader("Delivery statistics")
    if not get_settings().email_provider_api_key:
        st.caption("Email provider not configured – only in-app notifications are delivered. Email attempts are recorded "
                   "as 'SKIPPED_NO_PROVIDER' when email is enabled in settings.")
    show_table(notification_service.admin_notification_stats(s, a), empty="No notifications sent yet.")
