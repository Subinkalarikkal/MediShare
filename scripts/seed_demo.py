"""Seed clearly-labelled SAMPLE data for demonstrations.

Every record is created through the real service layer (so all validation,
safety rules and audit logging apply) and flagged with is_sample=True.
Organization and centre names start with "[SAMPLE]" and sample users use the
@sample.medishare.in domain.

Usage:  python scripts/seed_demo.py           (idempotent – skips if already seeded)
        python scripts/seed_demo.py --reset   (SQLite only: delete the dev DB first)
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from ai import expiry_risk_engine  # noqa: E402
from config import get_settings  # noqa: E402
from database import connection  # noqa: E402
from database.models import (  # noqa: E402
    CollectionEvent, CollectionRequest, DisposalRecord, InventoryBatch, MedicineRegistration, User,
)
from services import (  # noqa: E402
    auth_service, collection_service, disposal_service, institutional_request_service as irs, inventory_service,
    medicine_service, organization_service, review_service, transfer_service, user_service,
)
from services.permission_service import load_actor  # noqa: E402
from utils.constants import SAMPLE_PREFIX  # noqa: E402

DOMAIN = "sample.medishare.in"
PDF = b"%PDF-1.4\n% MediShare SAMPLE document - not a real certificate\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
TODAY = date.today()
ORG_COMMON = dict(contact_person="Sample Contact", phone="+91 9400000000")
CHECKLIST = {k: True for k in review_service.CHECKLIST_KEYS}


def png(text: str) -> bytes:
    import io
    img = Image.new("RGB", (480, 220), "white")
    ImageDraw.Draw(img).multiline_text((16, 16), text, fill="black", spacing=8)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def user(s, email, name, role, district="Ernakulam", pin="682001", **kw):
    u = auth_service.register_user(s, email=f"{email}@{DOMAIN}", full_name=f"{name} (sample)", account_type=role,
                                   district=district, pin_code=pin, phone="+91 9400000001", is_sample=True, **kw)
    return u


def actor(s, u):
    return load_actor(s, u.id)


def org(s, admin, owner, name, org_type, district, pin, regno, auth_type, verify=True, professional=True):
    a = actor(s, owner)
    o = organization_service.register_organization(
        s, a, name=SAMPLE_PREFIX + name, org_type=org_type, registration_number=regno, address=f"Sample Road, {district}",
        district=district, pin_code=pin, contact_email=owner.email, website="https://example.org",
        authorized_professional_name="Sample Pharmacist" if professional else None,
        authorized_professional_registration="KSPC-SAMPLE-01" if professional else None,
        description="Sample organization for demonstration only.", is_sample=True, **ORG_COMMON)
    a = actor(s, owner)
    organization_service.upload_document(s, a, o.id, "REGISTRATION_CERTIFICATE", PDF, "registration_SAMPLE.pdf")
    auth = None
    if auth_type:
        doc_type = "WASTE_AUTHORIZATION" if "WASTE" in auth_type or "COLLECTION" in auth_type else "DRUG_LICENCE"
        organization_service.upload_document(s, a, o.id, doc_type, PDF, "licence_SAMPLE.pdf")
        auth = organization_service.add_authorization(
            s, a, o.id, authorization_type=auth_type, reference_number=f"SAMPLE-{regno}",
            issuing_authority="Drugs Control Department, Kerala (sample)", valid_from=TODAY - timedelta(days=400),
            valid_until=TODAY + timedelta(days=700), data=PDF, filename="licence_SAMPLE.pdf")
    if verify:
        organization_service.submit_for_verification(s, a, o.id)
        organization_service.decide_verification(s, admin, o.id, "VERIFIED", "Sample: documents checked")
        if auth:
            organization_service.decide_authorization(s, admin, auth.id, "VERIFIED", "Sample: licence checked")
    s.flush()
    return o


def backdate(s, obj, days: int):
    when = datetime.now() - timedelta(days=days)
    for attr in ("created_at", "request_date", "submitted_at"):
        if hasattr(obj, attr) and getattr(obj, attr) is not None:
            setattr(obj, attr, when)


def seed(s) -> None:
    if s.query(User).filter(User.email == f"super.admin@{DOMAIN}").first():
        print("Sample data already present – nothing to do.")
        return
    sup = auth_service.bootstrap_super_admin(s, f"super.admin@{DOMAIN}", "Super Admin (sample)")
    sup.is_sample = True
    adm_u = User(email=f"admin@{DOMAIN}", full_name="Platform Admin (sample)", status="ACTIVE", is_sample=True)
    s.add(adm_u)
    s.flush()
    auth_service.assign_role_internal(s, adm_u, "ADMIN", sup.id)
    admin = actor(s, adm_u)

    # ---------------------------------------------------------------- medicine master
    meds = {}
    for name, generic, cat, form, cls in [
        ("Paracetamol 500 mg", "Paracetamol", "Analgesic", "TABLET", "NONE"),
        ("Amoxicillin 500 mg", "Amoxicillin", "Antibiotic", "CAPSULE", "NONE"),
        ("Cetirizine 10 mg", "Cetirizine", "Antihistamine", "TABLET", "NONE"),
        ("Metformin 500 mg", "Metformin", "Antidiabetic", "TABLET", "NONE"),
        ("Atorvastatin 10 mg", "Atorvastatin", "Cardiovascular", "TABLET", "NONE"),
        ("Amlodipine 5 mg", "Amlodipine", "Cardiovascular", "TABLET", "NONE"),
        ("Pantoprazole 40 mg", "Pantoprazole", "Gastrointestinal", "TABLET", "NONE"),
        ("Oral Rehydration Salts", "ORS", "Electrolyte", "POWDER", "NONE"),
        ("Ibuprofen Suspension 100 mg/5 ml", "Ibuprofen", "Analgesic", "SUSPENSION", "NONE"),
        ("Salbutamol Inhaler 100 mcg", "Salbutamol", "Respiratory", "INHALER", "NONE"),
        ("Insulin Glargine 100 IU/ml", "Insulin glargine", "Antidiabetic", "OTHER", "COLD_CHAIN"),
        ("Tramadol 50 mg", "Tramadol", "Analgesic", "CAPSULE", "CONTROLLED"),
        ("Alprazolam 0.25 mg", "Alprazolam", "Anxiolytic", "TABLET", "PSYCHOTROPIC"),
    ]:
        meds[generic] = medicine_service.create_medicine(s, admin, name=name, generic_name=generic,
                                                          active_ingredient=generic, manufacturer="Sample Pharma Ltd",
                                                          category=cat, dosage_form=form,
                                                          restricted_classification=cls, packaging_type="Blister/Bottle",
                                                          is_sample=True)

    # ---------------------------------------------------------------- people
    rev_u = user(s, "pharmacist.reviewer", "Dr. Meera Pillai", "REVIEWER", professional_registration_number="KSPC-SAMPLE-4411",
                 professional_council="Kerala State Pharmacy Council")
    user_service.verify_professional(s, admin, rev_u.id, "Sample: council register checked")
    user(s, "reviewer.pending", "Arun Varghese", "REVIEWER", professional_registration_number="KSPC-SAMPLE-5520",
         professional_council="Kerala State Pharmacy Council")
    people = [user(s, "anjali", "Anjali Nair", "INDIVIDUAL", "Ernakulam", "682016"),
              user(s, "rahul", "Rahul Menon", "INDIVIDUAL", "Thiruvananthapuram", "695001"),
              user(s, "fathima", "Fathima Rasheed", "INDIVIDUAL", "Kozhikode", "673001")]
    for p in people:
        p.location = f"Sample locality, {p.district}"

    # ---------------------------------------------------------------- organizations
    donor_u = user(s, "hospital", "Hospital Pharmacy Manager", "INSTITUTIONAL_DONOR")
    donor_org = org(s, admin, donor_u, "Periyar Community Hospital", "HOSPITAL", "Ernakulam", "682011", "KL-HOSP-0001",
                    "HOSPITAL_PHARMACY_LICENCE")
    pharm_u = user(s, "pharmacy", "Pharmacy Owner", "INSTITUTIONAL_DONOR", "Kozhikode", "673004")
    org(s, admin, pharm_u, "Malabar Care Pharmacy", "PHARMACY", "Kozhikode", "673004", "KL-PHAR-0002", "DRUG_LICENCE_RETAIL")
    pending_u = user(s, "clinic", "Clinic Administrator", "INSTITUTIONAL_DONOR", "Thrissur", "680001")
    org(s, admin, pending_u, "Thrissur Family Clinic", "HEALTHCARE_INSTITUTION", "Thrissur", "680001", "KL-CLIN-0003",
        "DRUG_LICENCE_RETAIL", verify=False)
    ngo_u = user(s, "ngo", "Outreach Coordinator", "NGO", "Thiruvananthapuram", "695003")
    ngo_org = org(s, admin, ngo_u, "Kerala Health Outreach Trust", "CHARITABLE_HEALTHCARE", "Ernakulam", "682020",
                  "KL-NGO-0004", "DRUG_LICENCE_RETAIL")
    centre_u = user(s, "centre", "Centre Operations Lead", "COLLECTION_CENTRE")
    org(s, admin, centre_u, "GreenCycle Medicine Collection", "COLLECTION_CENTRE", "Ernakulam", "682024", "KL-CBW-0005",
        "BIOMEDICAL_WASTE_AUTHORIZATION", professional=False)
    ca = actor(s, centre_u)
    centres = []
    for name, dist, pin, lat, lon, cap in [("Kochi Collection Point", "Ernakulam", "682024", 9.9894, 76.3013, 25),
                                           ("Thiruvananthapuram Drop-off Hub", "Thiruvananthapuram", "695004", 8.5068, 76.9570, 15),
                                           ("Kozhikode Collection Desk", "Kozhikode", "673002", 11.2566, 75.7804, 10)]:
        c = collection_service.register_centre(
            s, ca, name=SAMPLE_PREFIX + name, address=f"Sample building, {dist}", district=dist, pin_code=pin,
            contact_phone="+91 9400000099", contact_email=f"centre@{DOMAIN}", operating_hours="Mon–Sat 09:00–17:00",
            accepted_categories=["EXPIRED_MEDICINES", "UNUSED_MEDICINES", "LIQUID_MEDICINES", "TOPICALS"],
            latitude=lat, longitude=lon, service_radius_km=20, daily_capacity=cap, is_sample=True)
        collection_service.decide_centre(s, admin, c.id, "VERIFIED", "Sample: site inspected")
        centres.append(c)

    # ---------------------------------------------------------------- household registrations & requests
    def reg(person, med, qty, unit, exp_days, cond="OPENED", days_ago=0, submit=True):
        r = medicine_service.create_registration(
            s, actor(s, person), submit=submit, medicine_id=meds[med].id, medicine_name=meds[med].name,
            generic_name=med, manufacturer="Sample Pharma Ltd", batch_number=f"S{abs(exp_days) % 9000:04d}",
            expiry_date=TODAY + timedelta(days=exp_days), quantity=qty, unit=unit, packaging_condition=cond,
            district=person.district, pin_code=person.pin_code, location=person.location,
            photo=(png(f"{meds[med].name}\nSAMPLE PACK\nEXP {(TODAY + timedelta(days=exp_days)):%m/%Y}"), "sample_pack.png"),
            is_sample=True)
        backdate(s, r, days_ago)
        return r

    anjali, rahul, fathima = people
    def run_request(person, regs, centre, stage, method="DROP_OFF", days_ago=0):
        pa = actor(s, person)
        req = collection_service.create_request(s, pa, centre_id=centre.id, registration_ids=[r.id for r in regs],
                                                collection_method=method, preferred_date=TODAY + timedelta(days=2),
                                                pickup_address=person.location, is_sample=True)
        ca2 = actor(s, centre_u)
        if stage in {"ACCEPTED", "SCHEDULED", "COLLECTED", "DISPOSAL_PENDING", "CLOSED"}:
            collection_service.accept_request(s, ca2, req.id)
        if stage in {"SCHEDULED", "COLLECTED", "DISPOSAL_PENDING", "CLOSED"}:
            collection_service.schedule_request(s, ca2, req.id, datetime.combine(TODAY + timedelta(days=1), datetime.min.time()).replace(hour=11))
        if stage in {"COLLECTED", "DISPOSAL_PENDING", "CLOSED"}:
            collection_service.confirm_receipt(s, ca2, req.id, {r.id: float(r.quantity) for r in regs})
        if stage in {"DISPOSAL_PENDING", "CLOSED"}:
            collection_service.initiate_disposal(s, ca2, req.id)
        if stage == "CLOSED":
            disposal_service.record_disposal(s, ca2, req.id, disposal_method="AUTHORIZED_CBWTF", disposal_date=TODAY,
                                             quantity_disposed=sum(float(r.quantity) for r in regs), unit=regs[0].unit,
                                             disposal_facility="Sample Common Bio-medical Waste Treatment Facility",
                                             reference_number=f"SAMPLE-MAN-{str(req.id)[:6].upper()}",
                                             certificate=(PDF, "disposal_certificate_SAMPLE.pdf"), is_sample=True)
            collection_service.close_request(s, ca2, req.id)
        if days_ago:
            req.request_date = datetime.now() - timedelta(days=days_ago)
            if req.actual_collection_date:
                req.actual_collection_date = req.request_date + timedelta(days=3)
            for d in s.query(DisposalRecord).filter(DisposalRecord.request_id == req.id):
                d.disposal_date = (req.request_date + timedelta(days=6)).date()
            for e in s.query(CollectionEvent).filter(CollectionEvent.request_id == req.id):
                e.created_at = req.request_date
        return req

    k, t, z = centres
    run_request(anjali, [reg(anjali, "Paracetamol", 20, "TABLETS", -40, days_ago=150),
                         reg(anjali, "Amoxicillin", 10, "CAPSULES", -10, days_ago=148)], k, "CLOSED", days_ago=145)
    run_request(rahul, [reg(rahul, "Ibuprofen", 1, "BOTTLES", -60, days_ago=118)], t, "CLOSED", "PICKUP", days_ago=115)
    run_request(fathima, [reg(fathima, "Metformin", 45, "TABLETS", 30, days_ago=92)], z, "CLOSED", days_ago=90)
    run_request(anjali, [reg(anjali, "Cetirizine", 15, "TABLETS", 12, days_ago=60)], k, "DISPOSAL_PENDING", days_ago=55)
    run_request(rahul, [reg(rahul, "Atorvastatin", 28, "TABLETS", 75, days_ago=35)], t, "COLLECTED", days_ago=30)
    run_request(fathima, [reg(fathima, "Pantoprazole", 14, "TABLETS", 120, days_ago=20)], z, "SCHEDULED", "PICKUP", days_ago=12)
    run_request(anjali, [reg(anjali, "Amlodipine", 30, "TABLETS", 200, days_ago=10)], k, "ACCEPTED", days_ago=6)
    run_request(rahul, [reg(rahul, "ORS", 6, "PACKS", 400, cond="SEALED_ORIGINAL", days_ago=7)], t, "REQUESTED", days_ago=5)
    reg(anjali, "Salbutamol", 1, "PACKS", 25, days_ago=3)              # awaiting a request (URGENT expiry)
    reg(fathima, "Paracetamol", 10, "STRIPS", -5, days_ago=2)          # expired, awaiting request
    reg(rahul, "Cetirizine", 10, "TABLETS", 300, submit=False)        # draft

    # ---------------------------------------------------------------- institutional inventory
    da = actor(s, donor_u)
    rva = actor(s, rev_u)

    def batch(med, bno, qty, exp_days, docs=True, **kw):
        b = inventory_service.create_batch(
            s, da, medicine_id=meds[med].id, batch_number=bno, manufacturing_date=TODAY - timedelta(days=200),
            expiry_date=TODAY + timedelta(days=exp_days), quantity=qty, unit=kw.pop("unit", "TABLETS"),
            original_packaging_status=kw.pop("pack", "SEALED_ORIGINAL"),
            storage_conditions="Stored below 25°C in the hospital pharmacy store (sample)",
            storage_history="Received from distributor; temperature log maintained (sample)",
            recall_status=kw.pop("recall", "CHECKED_NO_RECALL"), is_sample=True)
        if docs:
            inventory_service.attach_document(s, da, b.id, "source", PDF, "purchase_invoice_SAMPLE.pdf")
            inventory_service.attach_document(s, da, b.id, "storage", PDF, "storage_log_SAMPLE.pdf")
        return b

    approved = batch("Paracetamol", "SPH-PCM-2401", 2000, 420)
    inventory_service.submit_batch(s, da, approved.id)
    review_service.record_decision(s, rva, approved.id, "START_REVIEW", notes="Sample: review started")
    review_service.record_decision(s, rva, approved.id, "APPROVE_FOR_FURTHER_AUTHORIZED_PROCESS",
                                   notes="Sample: all checks completed", checklist=CHECKLIST,
                                   authorization_reference="SAMPLE-DC-REF-001")
    approved2 = batch("ORS", "SPH-ORS-2402", 600, 300, unit="PACKS")
    inventory_service.submit_batch(s, da, approved2.id)
    review_service.record_decision(s, rva, approved2.id, "START_REVIEW", notes="Sample: review started")
    review_service.record_decision(s, rva, approved2.id, "APPROVE_FOR_FURTHER_AUTHORIZED_PROCESS",
                                   notes="Sample: all checks completed", checklist=CHECKLIST,
                                   authorization_reference="SAMPLE-DC-REF-002")
    under = batch("Amoxicillin", "SPH-AMX-2403", 800, 150, unit="CAPSULES")
    inventory_service.submit_batch(s, da, under.id)
    review_service.record_decision(s, rva, under.id, "START_REVIEW", notes="Sample: checking storage log")
    submitted = batch("Cetirizine", "SPH-CTZ-2404", 1200, 75)
    inventory_service.submit_batch(s, da, submitted.id)
    rejected = batch("Metformin", "SPH-MET-2405", 500, 200)
    inventory_service.submit_batch(s, da, rejected.id)
    review_service.record_decision(s, rva, rejected.id, "START_REVIEW", notes="Sample: review started")
    review_service.record_decision(s, rva, rejected.id, "REJECT", notes="Sample: storage temperature log incomplete")
    near = batch("Amlodipine", "SPH-AML-2406", 300, 20)
    inventory_service.submit_batch(s, da, near.id)
    batch("Atorvastatin", "SPH-ATV-2407", 250, 240, docs=False)  # draft missing documentation
    expired = batch("Pantoprazole", "SPH-PAN-2408", 150, 40)
    inventory_service.submit_batch(s, da, expired.id)
    expired.expiry_date = TODAY - timedelta(days=3)  # sample: batch expired while awaiting review
    for b, days in ((approved, 60), (approved2, 45), (under, 25), (submitted, 9), (rejected, 70), (near, 14), (expired, 50)):
        backdate(s, b, days)

    # ---------------------------------------------------------------- NGO requirement → interest → transfer docs
    na = actor(s, ngo_u)
    req = irs.create_request(s, na, required_category="Analgesic", medicine_name="Paracetamol",
                             medicine_id=meds["Paracetamol"].id, requested_quantity=1000, unit="TABLETS",
                             purpose="Sample: stock for a community health camp run by licensed pharmacists",
                             required_documentation="Licence copy, pharmacist in charge", is_sample=True)
    item = irs.express_interest(s, na, req.id, approved.id, 1000, "Sample expression of interest")
    review_service.review_interest(s, rva, item.id, True, "Sample: recipient licence and need verified")
    tr = transfer_service.create_transfer(s, admin, inventory_batch_id=approved.id, institutional_request_item_id=item.id,
                                          quantity=1000, notes="Sample transfer documentation")
    transfer_service.add_authorization(s, admin, tr.id, "REGULATORY", "SAMPLE-DCD-TR-001", "Drugs Control Department (sample)")
    transfer_service.add_authorization(s, rva, tr.id, "QUALITY", "SAMPLE-QA-001")
    irs.create_request(s, na, required_category="Electrolyte", medicine_name="ORS", requested_quantity=300, unit="PACKS",
                       purpose="Sample: monsoon diarrhoea prevention programme", is_sample=True)

    # ---------------------------------------------------------------- risk assessment
    summary = expiry_risk_engine.run_assessment(s, admin)
    print("Risk assessment:", summary)
    print("Sample data created. Sign in (development mode) with any @sample.medishare.in account.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="Delete the local SQLite dev database first")
    args = parser.parse_args()
    url = get_settings().database_url
    if args.reset:
        if not url.startswith("sqlite:///"):
            sys.exit("--reset is only supported for the local SQLite development database.")
        db_path = Path(url.replace("sqlite:///", ""))
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(db_path) + suffix)
            if p.exists():
                p.unlink()
    connection.configure(url)
    connection.init_db(create_all=True)
    with connection.session_scope() as s:
        seed(s)


if __name__ == "__main__":
    main()
