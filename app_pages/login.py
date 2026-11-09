import logging

import streamlit as st
from sqlalchemy import select

from components.header import notice, page_header
from components.session import db, start_session
from config import get_settings
from database.models import User
from services import auth_service, user_service
from utils.constants import ROLE_LABELS, Role

log = logging.getLogger(__name__)
settings = get_settings()


def oidc_ready(provider: str) -> tuple[bool, str]:
    """Check everything st.login needs, so a visitor never hits a crash on click.

    Configuration problems are written to the server log only – visitors see a generic message.
    """
    from streamlit.auth_util import is_authlib_installed, validate_auth_credentials

    if not is_authlib_installed():
        return False, "Authlib is not installed (pip install -r requirements.txt)."
    try:
        auth = st.secrets.get("auth") or {}
    except Exception:
        auth = {}
    prov = auth.get(provider) or {}
    missing = [k for k in ("redirect_uri", "cookie_secret") if not str(auth.get(k) or "").strip()]
    missing += [f"{provider}.{k}" for k in ("client_id", "client_secret", "server_metadata_url")
                if not str(prov.get(k) or "").strip()]
    if missing:
        return False, f"[auth] secrets missing or empty: {', '.join(missing)}"
    try:
        validate_auth_credentials(provider)
    except Exception as exc:  # missing/invalid [auth] secrets
        return False, f"OIDC configuration invalid: {exc}"
    return True, ""


page_header("Sign in to MediShare", "Secure sign-in with your Google account")

if err := st.session_state.pop("login_error", None):
    st.error(err)

col, side = st.columns([3, 2])
with col:
    if settings.auth_mode == "oidc":
        st.write("MediShare uses Google sign-in (OpenID Connect). We only receive your verified email and name.")
        ready, problem = oidc_ready(settings.oidc_provider)
        if ready:
            st.button("Sign in with Google", type="primary", icon=":material/login:",
                      on_click=st.login, args=[settings.oidc_provider])
        else:
            log.error("Sign-in unavailable: %s", problem)
            st.error("Sign-in is temporarily unavailable. Please try again later or use the Contact page.")
    elif settings.dev_login_enabled:
        st.warning("**Development login mode.** Identity is not verified. Use only with seeded sample data. "
                   "Set AUTH_MODE=oidc for real deployments – this mode is refused when APP_ENV=production.")
        with db() as s:
            users = s.scalars(select(User).where(User.deleted_at.is_(None)).order_by(User.full_name)).all()
            options = {}
            for u in users:
                roles = user_service.user_roles(s, u.id)
                label = ", ".join(ROLE_LABELS[Role(r)] for r in roles) or "No role"
                options[u.email] = f"{u.full_name} — {label} ({u.status.lower()})"
        if options:
            email = st.selectbox("Choose an account", list(options), format_func=lambda e: options[e])
            if st.button("Sign in", type="primary", icon=":material/login:"):
                with db() as s:
                    res = auth_service.resolve_login(s, email, provider="dev")
                if res.outcome in {"OK", "PENDING"}:
                    start_session(res.actor.user_id, res.actor.email, "dev")
                    st.rerun()
                st.error(res.message)
        else:
            st.info("No accounts exist yet. Run `python scripts/seed_demo.py` or create a new account below.")
        st.divider()
        st.markdown("**New to MediShare?**")
        new_email = st.text_input("Email address for a new account")
        if st.button("Continue to sign-up"):
            if "@" not in new_email:
                st.error("Enter a valid email address.")
            else:
                st.session_state["signup_email"] = new_email.strip().lower()
                st.rerun()
    else:
        st.error("Development login is disabled in production. Configure AUTH_MODE=oidc.")
with side:
    notice("Signing in does not grant institutional access by itself. Organization, collection-centre and reviewer "
           "accounts stay restricted until an administrator verifies documents and credentials.")
