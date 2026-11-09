import streamlit as st

from components.header import notice, page_header, show_badge
from components.session import actor

a = actor()
page_header("Account under review", "Your access is restricted until your application is reviewed")
show_badge(a.status)
if "REVIEWER" in a.roles:
    st.write("An administrator will verify your professional registration against the council register. "
             "Review features become available once your credentials are verified.")
else:
    st.write("Your account does not yet have an active role. An administrator will review it.")
notice("You will receive an in-app notification when the review is complete.")
