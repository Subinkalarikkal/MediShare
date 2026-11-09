from components.header import page_header
from components.notifications import render_notifications
from components.session import actor, db

a = actor()
page_header("Notifications", "Updates about your registrations, requests, reviews and account")
with db() as s:
    render_notifications(s, a)
