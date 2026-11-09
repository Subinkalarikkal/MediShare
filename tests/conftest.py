"""Shared fixtures: isolated in-memory database, in-memory storage and role factories."""
from __future__ import annotations

import io
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("APP_ENV", "test")
os.environ["ANTHROPIC_API_KEY"] = ""  # never call the real API from tests

from PIL import Image, ImageDraw  # noqa: E402

from ai import claude_client  # noqa: E402
from database import connection  # noqa: E402
from database.models import Base, User  # noqa: E402
from database.seed import seed_reference_data  # noqa: E402
from services import auth_service, organization_service, storage_service  # noqa: E402
from services.permission_service import load_actor  # noqa: E402


class MemoryBackend(storage_service.StorageBackend):
    name = "memory"

    def __init__(self):
        self.blobs = {}

    def put(self, key, data, content_type):
        self.blobs[key] = data

    def get(self, key):
        return self.blobs[key]


@pytest.fixture(autouse=True)
def _isolation():
    storage_service.set_backend(MemoryBackend())
    claude_client.set_client(None)
    yield
    claude_client.set_client(None)


@pytest.fixture
def session():
    engine = connection.configure("sqlite://")
    Base.metadata.create_all(engine)
    s = connection.get_sessionmaker()()
    seed_reference_data(s)
    s.commit()
    yield s
    s.rollback()
    s.close()
    engine.dispose()


def png_bytes(w=200, h=120, text="PARACETAMOL 500\nB.No PCM2401\nEXP 05/2027") -> bytes:
    img = Image.new("RGB", (w, h), "white")
    ImageDraw.Draw(img).multiline_text((8, 8), text, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


@pytest.fixture
def png():
    return png_bytes()


def make_user(session, email, role, *, status="ACTIVE", professional_verified=False):
    kwargs = {}
    if role == "REVIEWER":
        kwargs = dict(professional_registration_number="KSPC-12345", professional_council="Kerala State Pharmacy Council")
    if role in {"ADMIN", "SUPER_ADMIN"}:
        u = User(email=email, full_name=email.split("@")[0].title(), status=status)
        session.add(u)
        session.flush()
        auth_service.assign_role_internal(session, u, role, None)
    else:
        u = auth_service.register_user(session, email=email, full_name=email.split("@")[0].title(),
                                       account_type=role, district="Ernakulam", pin_code="682001", **kwargs)
        u.status = status
    u.professional_verified = professional_verified
    session.flush()
    return load_actor(session, u.id)


@pytest.fixture
def admin(session):
    return make_user(session, "admin@test.in", "ADMIN")


@pytest.fixture
def super_admin(session):
    return make_user(session, "super@test.in", "SUPER_ADMIN")


ORG_DATA = dict(address="1 MG Road", district="Ernakulam", pin_code="682001", contact_person="Contact",
                contact_email="org@test.in", phone="+91 9876543210",
                authorized_professional_name="Pharm A", authorized_professional_registration="KSPC-1")


def make_org(session, admin, email, role, org_type, *, verify=True, auth_type=None, regno=None):
    actor = make_user(session, email, role, status="PENDING")
    org = organization_service.register_organization(session, actor, name=f"Org {email}", org_type=org_type,
                                                     registration_number=regno or f"REG-{email}", **ORG_DATA)
    organization_service.upload_document(session, load_actor(session, actor.user_id), org.id,
                                         "REGISTRATION_CERTIFICATE", PDF_BYTES, "reg.pdf")
    if auth_type:
        a = organization_service.add_authorization(
            session, load_actor(session, actor.user_id), org.id, authorization_type=auth_type,
            reference_number=f"LIC-{email}", issuing_authority="State Drugs Control",
            valid_from=date.today() - timedelta(days=365), valid_until=date.today() + timedelta(days=365))
    if verify:
        organization_service.submit_for_verification(session, load_actor(session, actor.user_id), org.id)
        organization_service.decide_verification(session, admin, org.id, "VERIFIED", "All documents valid")
        if auth_type:
            organization_service.decide_authorization(session, admin, a.id, "VERIFIED", "Licence checked")
    session.flush()
    return load_actor(session, actor.user_id), org


@pytest.fixture
def donor(session, admin):
    return make_org(session, admin, "donor@test.in", "INSTITUTIONAL_DONOR", "HOSPITAL",
                    auth_type="HOSPITAL_PHARMACY_LICENCE")


@pytest.fixture
def ngo(session, admin):
    return make_org(session, admin, "ngo@test.in", "NGO", "CHARITABLE_HEALTHCARE",
                    auth_type="DRUG_LICENCE_RETAIL")


@pytest.fixture
def centre(session, admin):
    from services import collection_service
    actor, org = make_org(session, admin, "centre@test.in", "COLLECTION_CENTRE", "COLLECTION_CENTRE",
                          auth_type="BIOMEDICAL_WASTE_AUTHORIZATION")
    c = collection_service.register_centre(session, actor, name="Kochi Collection Point", address="Market Rd",
                                           district="Ernakulam", pin_code="682011", contact_phone="+91 9000000000",
                                           operating_hours="9-5", accepted_categories=["EXPIRED_MEDICINES", "UNUSED_MEDICINES"],
                                           service_radius_km=15, daily_capacity=10)
    collection_service.decide_centre(session, admin, c.id, "VERIFIED", "Site inspected")
    session.flush()
    return actor, org, c


@pytest.fixture
def individual(session):
    return make_user(session, "person@test.in", "INDIVIDUAL")


@pytest.fixture
def reviewer(session):
    return make_user(session, "reviewer@test.in", "REVIEWER", professional_verified=True)


@pytest.fixture
def medicine(session, admin):
    from services import medicine_service
    return medicine_service.create_medicine(session, admin, name="Paracetamol 500mg", generic_name="Paracetamol",
                                            manufacturer="Acme Pharma", category="Analgesic", dosage_form="TABLET")


@pytest.fixture
def restricted_medicine(session, admin):
    from services import medicine_service
    return medicine_service.create_medicine(session, admin, name="Morphine 10mg", generic_name="Morphine",
                                            manufacturer="Acme Pharma", category="Opioid", dosage_form="TABLET",
                                            restricted_classification="NARCOTIC")


def reg_data(**over):
    d = dict(medicine_name="Paracetamol 500mg", expiry_date=date.today() + timedelta(days=200), quantity=10,
             unit="TABLETS", packaging_condition="OPENED", district="Ernakulam", pin_code="682001")
    d.update(over)
    return d


def ready_batch(session, donor_actor, medicine, **over):
    """Create a fully documented draft batch for the donor organization."""
    from services import inventory_service
    data = dict(batch_number="B123", manufacturing_date=date.today() - timedelta(days=100),
                expiry_date=date.today() + timedelta(days=300), quantity=500, unit="TABLETS",
                original_packaging_status="SEALED_ORIGINAL", storage_conditions="Below 25°C, dry",
                storage_history="Pharmacy store room", recall_status="CHECKED_NO_RECALL")
    data.update(over)
    b = inventory_service.create_batch(session, donor_actor, medicine_id=medicine.id, **data)
    inventory_service.attach_document(session, donor_actor, b.id, "source", PDF_BYTES, "invoice.pdf")
    inventory_service.attach_document(session, donor_actor, b.id, "storage", PDF_BYTES, "storage.pdf")
    return b


FULL_CHECKLIST = {k: True for k in ["batch_verified", "expiry_verified", "storage_verified", "recall_checked",
                                    "documents_verified", "packaging_verified"]}
