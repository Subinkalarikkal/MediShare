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


def email_code_login() -> None:
    """Two steps: send a one-time code to the email address, then verify it."""
    from services import email_auth_service as eas
    from services import email_service
    from services.errors import ValidationError

    if not email_service.is_configured():
        log.error("Sign-in unavailable: SMTP is not configured (SMTP_HOST/SMTP_USER/SMTP_PASSWORD/SMTP_FROM).")
        st.error("Sign-in is temporarily unavailable. Please try again later or use the Contact page.")
        return
    pending = st.session_state.get("otp_email")
    if not pending:
        st.write("Enter your email address. We'll send you a 6-digit code to confirm it's really you – "
                 "no password needed.")
        with st.form("otp_request"):
            email = st.text_input("Email address", placeholder="you@example.com")
            sent = st.form_submit_button("Send verification code", type="primary", icon=":material/mail:")
        if sent:
            try:
                with db() as s:
                    eas.request_code(s, email)
                st.session_state["otp_email"] = email.strip().lower()
                st.rerun()
            except ValidationError:
                st.error("Please enter a valid email address.")
            except eas.RateLimited as exc:
                st.error(str(exc))
            except (email_service.EmailNotConfigured, email_service.EmailSendError):
                log.exception("Could not send verification email")
                st.error("We couldn't send the email right now. Please try again in a few minutes.")
        return

    st.success(f"We sent a 6-digit code to **{pending}**. It expires in 10 minutes. Check your spam folder if "
               "you don't see it.")
    with st.form("otp_verify"):
        code = st.text_input("Verification code", max_chars=6, placeholder="123456")
        verify = st.form_submit_button("Verify and sign in", type="primary", icon=":material/verified:")
    if verify:
        try:
            with db() as s:
                verified = eas.verify_code(s, pending, code)
                res = auth_service.resolve_login(s, verified, provider="email", email_verified=True)
        except eas.CodeInvalid as exc:
            st.error(str(exc))
            return
        st.session_state.pop("otp_email", None)
        if res.outcome in {"OK", "PENDING"}:
            start_session(res.actor.user_id, res.actor.email, "email")
            st.rerun()
        elif res.outcome == "UNREGISTERED":
            st.session_state["signup_email"] = verified
            st.session_state["signup_verified"] = True  # email ownership proven by the code
            st.rerun()
        else:
            st.error(res.message)
    c1, c2 = st.columns(2)
    if c1.button("Send a new code"):
        try:
            with db() as s:
                eas.request_code(s, pending)
            st.toast("A new code is on its way.")
        except eas.RateLimited as exc:
            st.error(str(exc))
        except (email_service.EmailNotConfigured, email_service.EmailSendError):
            st.error("We couldn't send the email right now. Please try again in a few minutes.")
    if c2.button("Use a different email"):
        st.session_state.pop("otp_email", None)
        st.rerun()


SUBTITLE = {"email": "Secure sign-in with a one-time code sent to your email",
            "oidc": "Secure sign-in with your Google account"}
page_header("Sign in to MediShare", SUBTITLE.get(settings.auth_mode, "Development sign-in"))

if err := st.session_state.pop("login_error", None):
    st.error(err)

col, side = st.columns([3, 2])
with col:
    if settings.auth_mode == "email":
        email_code_login()
    elif settings.auth_mode == "oidc":
        st.write("MediShare uses Google sign-in (OpenID Connect). We only receive your verified email and name.")
        ready, problem = oidc_ready(settings.oidc_provider)
        if ready:
            st.button("Sign in with Google", type="primary", icon=":material/login:",
                      on_click=st.login, args=[settings.oidc_provider])
        else:
            log.error("Sign-in unavailable: %s", problem)
            st.error("Sign-in is temporarily unavailable. Please try again later or use the Contact page.")
    elif settings.dev_login_enabled:
        if settings.demo_mode:
            st.info("**Demo sign-in.** Pick any sample account below to explore that role. No password is needed "
                    "because every account here is sample data.")
        else:
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
                st.session_state["signup_verified"] = True  # local development only (refused in production)
                st.rerun()
    else:
        log.error("No usable sign-in method: AUTH_MODE=%s with APP_ENV=%s", settings.auth_mode, settings.app_env)
        st.error("Sign-in is temporarily unavailable. Please try again later or use the Contact page.")
with side:
    notice("Signing in does not grant institutional access by itself. Organization, collection-centre and reviewer "
           "accounts stay restricted until an administrator verifies documents and credentials.")
