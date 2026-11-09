"""MediShare – Streamlit entry point.

Authentication: Streamlit OIDC (st.login, Google) in production; a clearly
labelled development login when AUTH_MODE=dev and APP_ENV != production.
Authorization: role-based navigation here, enforced again in every service.
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="MediShare", page_icon="💊", layout="wide", initial_sidebar_state="expanded")

from components.header import apply_theme  # noqa: E402
from components.session import clear_auth, current_actor, db, show_flash, start_session  # noqa: E402
from components.sidebar import logout, render_sidebar  # noqa: E402
from config import get_settings  # noqa: E402
from database.connection import init_db  # noqa: E402
from services import auth_service  # noqa: E402

settings = get_settings()


@st.cache_resource(show_spinner=False)
def _bootstrap() -> bool:
    # Production schema is managed by Alembic; create_all is a dev/test convenience.
    init_db(create_all=not settings.is_production)
    if settings.demo_mode:  # public showcase: load clearly-labelled sample data into an empty database
        from database.connection import session_scope
        from database.models import User
        from scripts.seed_demo import seed

        with session_scope() as s:
            if s.query(User).count() == 0:
                seed(s)
    return True


_bootstrap()
apply_theme()
if settings.demo_mode:
    st.info("**Public demo** – all accounts and records are SAMPLE data and reset when the app restarts. "
            "Do not enter real personal or medical information.", icon=":material/science:")


def _sync_oidc() -> None:
    """Map a Streamlit OIDC identity to a MediShare account (once per session)."""
    if settings.auth_mode != "oidc":
        return
    try:
        logged_in = st.user.is_logged_in
    except Exception:
        return
    if not logged_in:
        if st.session_state.get("auth_provider") == "oidc":
            clear_auth()
        return
    if st.session_state.get("auth_user_id"):
        return
    email = st.user.get("email")
    with db() as s:
        res = auth_service.resolve_login(s, email, settings.oidc_provider, st.user.get("email_verified"))
    if res.outcome in {"OK", "PENDING"}:
        start_session(res.actor.user_id, res.actor.email, "oidc")
    elif res.outcome == "UNREGISTERED":
        st.session_state["signup_email"] = str(email).lower()
        st.session_state["signup_name"] = st.user.get("name") or ""
    else:
        st.session_state["login_error"] = res.message
        st.logout()


_sync_oidc()
actor = current_actor()

# Session expiry and status re-check on every run.
if actor is not None:
    if auth_service.is_session_expired(st.session_state.get("auth_login_time"), settings.session_timeout_minutes):
        st.session_state["login_error"] = "Your session expired. Please sign in again."
        logout(actor, "SESSION_EXPIRED")
    if actor.status in {"SUSPENDED", "DEACTIVATED"}:
        st.session_state["login_error"] = f"Your account is {actor.status.lower()}."
        logout(actor, "LOGOUT_STATUS_CHANGE")


def P(path: str, title: str, icon: str, url: str, default: bool = False) -> st.Page:
    return st.Page(f"app_pages/{path}", title=title, icon=f":material/{icon}:", url_path=url, default=default)


def build_pages(a) -> dict[str, list[st.Page]]:
    public = [
        P("01_Home.py", "Home", "home", "home", default=a is None),
        P("public_about.py", "About MediShare", "info", "about"),
        P("public_how_it_works.py", "How It Works", "conversion_path", "how-it-works"),
        P("05_Collection_Centres.py", "Find Collection Centres", "location_on", "collection-centres"),
        P("public_organizations.py", "Participating Organizations", "corporate_fare", "organizations"),
        P("public_impact.py", "Environmental Impact", "eco", "impact"),
        P("public_safety.py", "Safety and Compliance", "verified_user", "safety"),
        P("public_contact.py", "Contact", "mail", "contact"),
    ]
    if a is None:
        if st.session_state.get("signup_email"):
            return {"Get started": [P("onboarding.py", "Create your account", "person_add", "onboarding", default=True)],
                    "MediShare": public[1:]}
        return {"MediShare": public, "Account": [P("login.py", "Login", "login", "login")]}

    account = [P("notifications.py", "Notifications", "notifications", "notifications"),
               P("14_Profile.py", "Profile", "person", "profile")]
    explore = [P("01_Home.py", "Home", "home", "home"),
               P("05_Collection_Centres.py", "Collection Centres", "location_on", "collection-centres"),
               P("public_safety.py", "Safety and Compliance", "verified_user", "safety")]
    roles = a.roles
    sections: dict[str, list] = {}

    if a.status == "PENDING":
        if roles & {"INSTITUTIONAL_DONOR", "NGO", "COLLECTION_CENTRE"}:
            sections["Organization onboarding"] = [
                P("07_Organization_Dashboard.py", "Organization Dashboard", "domain", "organization", default=True)]
            if "COLLECTION_CENTRE" in roles:
                sections["Organization onboarding"].append(
                    P("10_Collection_Centre_Portal.py", "Centre Setup", "recycling", "centre-portal"))
        else:
            sections["Account status"] = [P("pending.py", "Account under review", "hourglass_top", "pending", default=True)]
        sections["Account"] = account
        return sections

    if "INDIVIDUAL" in roles:
        sections["My Medicines"] = [
            P("02_Individual_Dashboard.py", "Overview", "dashboard", "overview", default=True),
            P("03_Medicine_Registration.py", "Register Medicine", "add_circle", "register-medicine"),
            P("04_My_Registrations.py", "My Registrations", "medication", "my-registrations"),
            P("06_Collection_Requests.py", "My Requests", "local_shipping", "my-requests"),
        ]
    if roles & {"INSTITUTIONAL_DONOR", "NGO", "COLLECTION_CENTRE"}:
        org_pages = [P("07_Organization_Dashboard.py", "Organization Dashboard", "domain", "organization",
                       default=not sections)]
        if "INSTITUTIONAL_DONOR" in roles:
            org_pages.append(P("08_Organization_Inventory.py", "Inventory", "inventory_2", "inventory"))
        if "NGO" in roles:
            org_pages.append(P("09_Institutional_Requests.py", "Institutional Requests", "assignment", "institutional-requests"))
        sections["Organization"] = org_pages
    if "COLLECTION_CENTRE" in roles:
        sections["Collection Centre"] = [P("10_Collection_Centre_Portal.py", "Centre Portal", "recycling", "centre-portal",
                                           default=not sections)]
    if "REVIEWER" in roles:
        sections["Review"] = [P("11_Reviewer_Portal.py", "Reviewer Portal", "fact_check", "reviewer", default=not sections)]
    if roles & {"ADMIN", "SUPER_ADMIN"}:
        admin = [
            P("12_Admin_Dashboard.py", "Admin Dashboard", "admin_panel_settings", "admin", default=not sections),
            P("admin_users.py", "Users", "group", "admin-users"),
            P("admin_organizations.py", "Organizations & Centres", "apartment", "admin-organizations"),
            P("admin_records.py", "Records & Reviews", "folder_open", "admin-records"),
            P("admin_medicines.py", "Medicine Master", "pill", "admin-medicines"),
            P("admin_risk.py", "AI Risk Monitoring", "monitor_heart", "admin-risk"),
            P("13_Analytics.py", "Analytics & AI Assistant", "insights", "analytics"),
            P("admin_notifications.py", "Notifications", "campaign", "admin-notifications"),
            P("admin_audit.py", "Audit Logs", "history", "admin-audit"),
        ]
        if "SUPER_ADMIN" in roles:
            admin.append(P("admin_settings.py", "System Settings", "settings", "admin-settings"))
        sections["Administration"] = admin
    if not sections:
        sections["Account status"] = [P("pending.py", "Account status", "hourglass_top", "pending", default=True)]
    sections["Account"] = account
    sections["Explore"] = explore
    return sections


render_sidebar(actor)
show_flash()
navigation = st.navigation(build_pages(actor))
navigation.run()
