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


def test_google_button_shown_when_configured(oidc_mode):
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
