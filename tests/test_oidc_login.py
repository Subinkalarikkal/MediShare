"""Google (OIDC) sign-in page: renders the button when configured, never crashes or leaks details."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tests.test_app import APP, _use_seeded, seeded_db  # noqa: F401  (reuse seeded DB fixtures)

ROOT = Path(__file__).resolve().parents[1]
AUTH = {
    "redirect_uri": "http://localhost:8501/oauth2callback",
    "cookie_secret": "test-cookie-secret-0123456789abcdef",
    "google": {
        "client_id": "test.apps.googleusercontent.com",
        "client_secret": "test-secret",
        "server_metadata_url": "https://accounts.google.com/.well-known/openid-configuration",
    },
}


@pytest.fixture
def oidc_mode(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "oidc")
    monkeypatch.setenv("APP_ENV", "production")


def _login_page(with_auth: bool) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    # Always set [auth] explicitly so a local .streamlit/secrets.toml can't influence the test.
    at.secrets["auth"] = AUTH if with_auth else {"redirect_uri": "", "cookie_secret": ""}
    at.run()
    at.switch_page("app_pages/login.py").run()
    return at


def test_google_button_shown_when_configured(oidc_mode, monkeypatch):
    import streamlit.auth_util as au
    # Streamlit's own validator reads .streamlit/secrets.toml from disk; our page checks the values itself.
    monkeypatch.setattr(au, "validate_auth_credentials", lambda provider: None)
    at = _login_page(with_auth=True)
    assert not at.exception
    labels = [b.label for b in at.button]
    assert "Sign in with Google" in labels
    assert "Sign in" not in labels and not at.selectbox  # no development account picker
    assert not any("Development login" in w.value for w in at.warning)


def test_missing_config_shows_friendly_message_not_traceback(oidc_mode):
    at = _login_page(with_auth=False)
    assert not at.exception
    assert [e.value for e in at.error] == ["Sign-in is temporarily unavailable. Please try again later or use the Contact page."]
    assert not any(b.label == "Sign in with Google" for b in at.button)


def test_missing_authlib_shows_friendly_message(oidc_mode, monkeypatch):
    import streamlit.auth_util as au
    monkeypatch.setattr(au, "is_authlib_installed", lambda: False)
    at = _login_page(with_auth=True)
    assert not at.exception
    assert any("temporarily unavailable" in e.value for e in at.error)


def test_authlib_installed_and_required():
    from streamlit.auth_util import is_authlib_installed
    assert is_authlib_installed()
    assert any(l.lower().startswith("authlib==") for l in (ROOT / "requirements.txt").read_text().splitlines())


def test_tracebacks_hidden_from_visitors():
    import tomllib
    cfg = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert cfg["client"]["showErrorDetails"] == "none"


def test_demo_mode_never_in_production(monkeypatch):
    from config import Settings
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("APP_ENV", "production")
    assert Settings().demo_mode is False
    monkeypatch.setenv("APP_ENV", "demo")
    assert Settings().demo_mode is True


def test_demo_mode_seeds_empty_database_and_shows_banner(monkeypatch, tmp_path):
    """Simulates a fresh Streamlit Cloud start: empty DB → sample data loaded, demo banner shown."""
    from database import connection
    from database.models import User
    from services import storage_service
    url = f"sqlite:///{(tmp_path / 'demo.db').as_posix()}"
    for k, v in {"DATABASE_URL": url, "APP_ENV": "demo", "AUTH_MODE": "dev", "DEMO_MODE": "true",
                 "LOCAL_STORAGE_DIR": str(tmp_path / "files")}.items():
        monkeypatch.setenv(k, v)
    storage_service.set_backend(storage_service.LocalBackend(str(tmp_path / "files")))
    connection.configure(url)
    import streamlit as st
    st.cache_resource.clear()  # force the app's one-time bootstrap to run again
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    assert any("Public demo" in i.value for i in at.info)
    with connection.session_scope() as s:
        assert s.query(User).filter(User.email.like("%@sample.medishare.in")).count() >= 10
    at.switch_page("app_pages/login.py").run()
    assert at.selectbox and any(b.label == "Sign in" for b in at.button)
    connection.get_engine().dispose()


def test_committed_profile_is_production_with_email_verification(monkeypatch):
    """The hosted site has no demo data/login: production mode with email-code sign-in."""
    from config import Settings
    for k in ("APP_ENV", "AUTH_MODE", "DEMO_MODE", "MEDISHARE_PROFILE", "DATABASE_URL"):
        monkeypatch.delenv(k, raising=False)
    s = Settings()
    assert (s.app_env, s.auth_mode) == ("production", "email")
    assert s.demo_mode is False and s.dev_login_enabled is False
    assert s.database_url.endswith("/medishare.db")  # never the dev/demo database with SAMPLE data
    monkeypatch.setenv("AUTH_MODE", "dev")  # even if someone sets dev login, production refuses it
    assert Settings().dev_login_enabled is False


def test_unexpected_page_error_shows_friendly_message(monkeypatch):
    """Any unhandled page error is caught: visitors see a friendly message, never a traceback."""
    from services import analytics_service

    def boom(*a, **k):
        raise RuntimeError("database exploded with secret details")
    monkeypatch.setattr(analytics_service, "public_stats", boom)
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    msgs = [e.value for e in at.error]
    assert any("Something went wrong on this page" in m for m in msgs)
    assert not any("secret details" in m for m in msgs)
