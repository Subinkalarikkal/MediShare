"""Streamlit AppTest smoke tests: every page renders for every role without exceptions."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

from database import connection  # noqa: E402
from database.models import User  # noqa: E402
from utils.date_utils import utcnow  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "app.py")
DOMAIN = "sample.medishare.in"

PUBLIC = ["01_Home.py", "public_about.py", "public_how_it_works.py", "05_Collection_Centres.py",
          "public_organizations.py", "public_impact.py", "public_safety.py", "public_contact.py", "login.py"]
ACCOUNT = ["notifications.py", "14_Profile.py"]
ROLE_PAGES = {
    "anjali": ["02_Individual_Dashboard.py", "03_Medicine_Registration.py", "04_My_Registrations.py",
               "06_Collection_Requests.py", "05_Collection_Centres.py"],
    "hospital": ["07_Organization_Dashboard.py", "08_Organization_Inventory.py"],
    "ngo": ["07_Organization_Dashboard.py", "09_Institutional_Requests.py"],
    "centre": ["07_Organization_Dashboard.py", "10_Collection_Centre_Portal.py"],
    "pharmacist.reviewer": ["11_Reviewer_Portal.py"],
    "reviewer.pending": ["pending.py"],
    "clinic": ["07_Organization_Dashboard.py"],  # pending organization
    "admin": ["12_Admin_Dashboard.py", "admin_users.py", "admin_organizations.py", "admin_records.py",
              "admin_medicines.py", "admin_risk.py", "13_Analytics.py", "admin_notifications.py", "admin_audit.py"],
    "super.admin": ["12_Admin_Dashboard.py", "admin_settings.py"],
}


@pytest.fixture(scope="module")
def seeded_db():
    tmp = tempfile.mkdtemp()
    url = f"sqlite:///{Path(tmp, 'apptest.db').as_posix()}"
    os.environ["DATABASE_URL"] = url
    os.environ["AUTH_MODE"] = "dev"
    os.environ["LOCAL_STORAGE_DIR"] = str(Path(tmp, "storage"))
    from services import storage_service
    storage_service.set_backend(storage_service.LocalBackend(os.environ["LOCAL_STORAGE_DIR"]))
    connection.configure(url)
    connection.init_db(create_all=True)
    import scripts.seed_demo as seed_demo
    with connection.session_scope() as s:
        seed_demo.seed(s)
    yield url
    connection.get_engine().dispose()
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(autouse=True)
def _use_seeded(seeded_db, _isolation):
    from services import storage_service
    connection.configure(seeded_db)
    storage_service.set_backend(storage_service.LocalBackend(os.environ["LOCAL_STORAGE_DIR"]))
    yield


def _uid(email):
    with connection.session_scope() as s:
        return str(s.query(User).filter(User.email == email).one().id)


def _app(login: str | None = None) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    if login:
        at.session_state["auth_user_id"] = _uid(f"{login}@{DOMAIN}")
        at.session_state["auth_email"] = f"{login}@{DOMAIN}"
        at.session_state["auth_login_time"] = utcnow()
        at.session_state["auth_provider"] = "dev"
    return at


def _assert_ok(at: AppTest, where: str):
    assert not at.exception, f"{where}: {[e.message for e in at.exception]}"
    errors = [e.value for e in at.error]
    assert not errors, f"{where}: error messages shown: {errors}"


@pytest.mark.parametrize("page", PUBLIC)
def test_public_pages_render(page):
    at = _app()
    at.run()
    at.switch_page(f"app_pages/{page}").run()
    _assert_ok(at, page)


def test_home_shows_live_stats():
    at = _app().run()
    _assert_ok(at, "home")
    labels = {m.label: m.value for m in at.metric}
    assert int(labels["Verified collection centres"]) == 3
    assert int(labels["Disposal records"]) == 3


@pytest.mark.parametrize("login,page", [(u, p) for u, pages in ROLE_PAGES.items() for p in pages + ACCOUNT])
def test_role_pages_render(login, page):
    at = _app(login).run()
    _assert_ok(at, f"{login} default page")
    at.switch_page(f"app_pages/{page}").run()
    _assert_ok(at, f"{login}:{page}")


def test_dev_login_flow():
    at = _app().run()
    at.switch_page("app_pages/login.py").run()
    at.selectbox[0].select(f"anjali@{DOMAIN}").run()
    next(b for b in at.button if b.label == "Sign in").click().run()
    assert at.session_state["auth_user_id"] == _uid(f"anjali@{DOMAIN}")
    _assert_ok(at, "after login")


def test_expired_session_logs_out():
    from datetime import timedelta
    at = _app("anjali")
    at.session_state["auth_login_time"] = utcnow() - timedelta(hours=5)
    at.run()
    assert "auth_user_id" not in at.session_state


def test_suspended_user_is_logged_out():
    with connection.session_scope() as s:
        u = s.query(User).filter(User.email == f"fathima@{DOMAIN}").one()
        u.status = "SUSPENDED"
    try:
        at = _app("fathima").run()
        assert "auth_user_id" not in at.session_state
    finally:
        with connection.session_scope() as s:
            s.query(User).filter(User.email == f"fathima@{DOMAIN}").one().status = "ACTIVE"


def test_contact_form_submits():
    at = _app().run()
    at.switch_page("app_pages/public_contact.py").run()
    at.text_input[0].input("Asha")
    at.text_input[1].input("asha@example.org")
    at.text_area[0].input("Where can I drop off expired syrup bottles?")
    at.button[0].click().run()
    assert any("delivered" in s.value for s in at.success)


def test_individual_cannot_reach_admin_pages():
    """Admin pages are not even registered in an individual's navigation."""
    at = _app("anjali").run()
    for page in ("12_Admin_Dashboard.py", "admin_users.py", "admin_settings.py", "11_Reviewer_Portal.py",
                 "08_Organization_Inventory.py"):
        with pytest.raises(ValueError, match="Could not find a navigation page"):
            at.switch_page(f"app_pages/{page}")


def test_anonymous_users_only_get_public_pages():
    at = _app().run()
    with pytest.raises(ValueError, match="Could not find a navigation page"):
        at.switch_page("app_pages/02_Individual_Dashboard.py")


# ---------------------------------------------------------------- UI interactions
def test_ui_register_medicine_submits(_use_seeded):
    from database.models import MedicineRegistration
    with connection.session_scope() as s:
        before = s.query(MedicineRegistration).count()
    at = _app("fathima").run()
    at.switch_page("app_pages/03_Medicine_Registration.py").run()
    at.text_input(key="regform_name").input("UI Test Syrup")
    at.checkbox[-1].check()  # confirmation
    next(b for b in at.button if b.label == "Submit for collection").click().run()
    _assert_ok(at, "register submit")
    with connection.session_scope() as s:
        assert s.query(MedicineRegistration).count() == before + 1
        assert s.query(MedicineRegistration).filter_by(medicine_name="UI Test Syrup").one().status == "SUBMITTED"


def test_ui_register_requires_confirmation(_use_seeded):
    at = _app("fathima").run()
    at.switch_page("app_pages/03_Medicine_Registration.py").run()
    at.text_input(key="regform_name").input("Unconfirmed")
    next(b for b in at.button if b.label == "Submit for collection").click().run()
    assert any("confirm" in e.value.lower() for e in at.error)


def test_ui_centre_accepts_request(_use_seeded):
    from database.models import CollectionRequest
    at = _app("centre").run()
    at.switch_page("app_pages/10_Collection_Centre_Portal.py").run()
    accept = [b for b in at.button if b.label == "Accept"]
    assert accept, "expected a new request in the sample data"
    with connection.session_scope() as s:
        before = s.query(CollectionRequest).filter_by(status="ACCEPTED").count()
    accept[0].click().run()
    _assert_ok(at, "accept")
    with connection.session_scope() as s:
        assert s.query(CollectionRequest).filter_by(status="ACCEPTED").count() == before + 1


def test_ui_admin_runs_risk_assessment(_use_seeded):
    at = _app("admin").run()
    at.switch_page("app_pages/admin_risk.py").run()
    next(b for b in at.button if b.label == "Run risk assessment now").click().run()
    _assert_ok(at, "risk run")
    assert any("Assessed" in s.value for s in at.success)


def test_ui_assistant_fallback_answer(_use_seeded):
    at = _app("admin").run()
    at.switch_page("app_pages/13_Analytics.py").run()
    at.text_area[0].input("How many collection requests are pending?")
    next(b for b in at.button if b.label == "Ask").click().run()
    _assert_ok(at, "assistant")
    assert any("Pending collection requests" in m.value for m in at.markdown)
