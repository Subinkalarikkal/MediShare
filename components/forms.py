"""Reusable form field groups. Each returns a dict of raw values for the service layer."""
from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from utils.constants import (
    KERALA_DISTRICTS, AuthorizationType, OrgType, PackagingCondition, RestrictedClass, Unit, WasteCategory,
)


def _idx(options, value, default=0):
    try:
        return list(options).index(value)
    except ValueError:
        return default


def _pretty(v: str) -> str:
    return v.replace("_", " ").title()


def district_select(label="District", value=None, key=None, optional=True):
    opts = ([""] if optional else []) + KERALA_DISTRICTS
    return st.selectbox(label, opts, index=_idx(opts, value), key=key,
                        format_func=lambda x: x or "— Select —") or None


def registration_fields(prefill: dict | None = None, key: str = "reg") -> dict:
    p = prefill or {}
    c1, c2 = st.columns(2)
    out = {}
    out["medicine_name"] = c1.text_input("Medicine name *", p.get("medicine_name") or "", key=f"{key}_name")
    out["generic_name"] = c2.text_input("Generic name", p.get("generic_name") or "", key=f"{key}_gen")
    out["manufacturer"] = c1.text_input("Manufacturer", p.get("manufacturer") or "", key=f"{key}_mfr")
    out["batch_number"] = c2.text_input("Batch number (if available)", p.get("batch_number") or "", key=f"{key}_batch")
    has_mfg = c1.checkbox("Manufacturing date known", value=bool(p.get("manufacturing_date")), key=f"{key}_hasmfg")
    out["manufacturing_date"] = c1.date_input("Manufacturing date", p.get("manufacturing_date") or date.today() - timedelta(days=365),
                                              min_value=date(1990, 1, 1), max_value=date.today(), key=f"{key}_mfg",
                                              disabled=not has_mfg)
    if not has_mfg:
        out["manufacturing_date"] = None
    out["expiry_date"] = c2.date_input("Expiry date *", p.get("expiry_date") or date.today() + timedelta(days=180),
                                       min_value=date(1990, 1, 1), max_value=date(2100, 12, 31), key=f"{key}_exp")
    q1, q2, q3 = st.columns([1, 1, 2])
    out["quantity"] = q1.number_input("Quantity *", min_value=0.0, value=float(p.get("quantity") or 1), step=1.0,
                                      key=f"{key}_qty")
    units = Unit.values()
    out["unit"] = q2.selectbox("Unit *", units, index=_idx(units, p.get("unit")), format_func=_pretty, key=f"{key}_unit")
    conds = PackagingCondition.values()
    out["packaging_condition"] = q3.selectbox("Packaging condition", conds, index=_idx(conds, p.get("packaging_condition", "UNKNOWN")),
                                              format_func=_pretty, key=f"{key}_pack")
    out["storage_info"] = st.text_input("Storage information (if known)", p.get("storage_info") or "", key=f"{key}_store")
    l1, l2, l3 = st.columns([2, 1, 1])
    out["location"] = l1.text_input("Location / locality", p.get("location") or "", key=f"{key}_loc")
    with l2:
        out["district"] = district_select(value=p.get("district"), key=f"{key}_dist")
    out["pin_code"] = l3.text_input("PIN code", p.get("pin_code") or "", key=f"{key}_pin", max_chars=6)
    out["remarks"] = st.text_area("Additional remarks", p.get("remarks") or "", key=f"{key}_rem", height=80)
    return out


def organization_fields(allowed_types: list[str], prefill: dict | None = None, key: str = "org",
                        lock_identity: bool = False) -> dict:
    p = prefill or {}
    out = {}
    c1, c2 = st.columns(2)
    out["name"] = c1.text_input("Organization name *", p.get("name", ""), key=f"{key}_name", disabled=lock_identity)
    out["org_type"] = c2.selectbox("Organization type *", allowed_types, index=_idx(allowed_types, p.get("org_type")),
                                   format_func=_pretty, key=f"{key}_type", disabled=lock_identity)
    out["registration_number"] = c1.text_input("Registration number *", p.get("registration_number", ""),
                                               key=f"{key}_reg", disabled=lock_identity)
    out["website"] = c2.text_input("Website", p.get("website") or "", key=f"{key}_web")
    out["address"] = st.text_area("Address *", p.get("address", ""), key=f"{key}_addr", height=70)
    d1, d2 = st.columns(2)
    with d1:
        out["district"] = district_select("District *", p.get("district"), key=f"{key}_dist")
    out["pin_code"] = d2.text_input("PIN code *", p.get("pin_code", ""), key=f"{key}_pin", max_chars=6)
    e1, e2, e3 = st.columns(3)
    out["contact_person"] = e1.text_input("Contact person *", p.get("contact_person", ""), key=f"{key}_cp")
    out["contact_email"] = e2.text_input("Contact email *", p.get("contact_email", ""), key=f"{key}_ce")
    out["phone"] = e3.text_input("Phone *", p.get("phone", ""), key=f"{key}_ph")
    f1, f2 = st.columns(2)
    out["authorized_professional_name"] = f1.text_input("Authorized professional (pharmacist) name",
                                                        p.get("authorized_professional_name") or "", key=f"{key}_apn")
    out["authorized_professional_registration"] = f2.text_input("Professional registration number",
                                                                p.get("authorized_professional_registration") or "",
                                                                key=f"{key}_apr")
    out["service_area"] = st.text_input("Service area", p.get("service_area") or "", key=f"{key}_sa")
    out["description"] = st.text_area("Description", p.get("description") or "", key=f"{key}_desc", height=80)
    return out


def authorization_fields(key="auth") -> dict:
    c1, c2 = st.columns(2)
    out = {}
    types = AuthorizationType.values()
    out["authorization_type"] = c1.selectbox("Authorization type *", types, format_func=_pretty, key=f"{key}_t")
    out["reference_number"] = c2.text_input("Licence / authorization number *", key=f"{key}_ref")
    out["issuing_authority"] = c1.text_input("Issuing authority", "Drugs Control Department, Kerala", key=f"{key}_auth")
    out["valid_from"] = c2.date_input("Valid from", date.today() - timedelta(days=30), key=f"{key}_vf")
    out["valid_until"] = c1.date_input("Valid until", date.today() + timedelta(days=365 * 2), key=f"{key}_vu")
    return out


def centre_fields(prefill: dict | None = None, key: str = "centre") -> dict:
    p = prefill or {}
    out = {}
    c1, c2 = st.columns(2)
    out["name"] = c1.text_input("Centre name *", p.get("name", ""), key=f"{key}_n")
    out["contact_phone"] = c2.text_input("Contact phone *", p.get("contact_phone", ""), key=f"{key}_ph")
    out["address"] = st.text_area("Address *", p.get("address", ""), key=f"{key}_a", height=70)
    d1, d2, d3 = st.columns(3)
    with d1:
        out["district"] = district_select("District *", p.get("district"), key=f"{key}_d")
    out["pin_code"] = d2.text_input("PIN code *", p.get("pin_code", ""), key=f"{key}_pin", max_chars=6)
    out["contact_email"] = d3.text_input("Contact email", p.get("contact_email") or "", key=f"{key}_e")
    g1, g2 = st.columns(2)
    lat = g1.text_input("Latitude (optional)", "" if p.get("latitude") is None else str(p["latitude"]), key=f"{key}_lat")
    lon = g2.text_input("Longitude (optional)", "" if p.get("longitude") is None else str(p["longitude"]), key=f"{key}_lon")
    out["latitude"], out["longitude"] = lat or None, lon or None
    out["operating_hours"] = st.text_input("Operating hours", p.get("operating_hours") or "Mon–Sat 09:00–17:00", key=f"{key}_h")
    out["accepted_categories"] = st.multiselect("Accepted waste categories *", WasteCategory.values(),
                                                default=p.get("accepted_categories") or ["EXPIRED_MEDICINES", "UNUSED_MEDICINES"],
                                                format_func=_pretty, key=f"{key}_cat")
    h1, h2, h3 = st.columns(3)
    out["service_radius_km"] = h1.number_input("Service radius (km)", 1.0, 200.0, float(p.get("service_radius_km") or 10), key=f"{key}_r")
    out["daily_capacity"] = h2.number_input("Daily capacity (requests)", 0, 1000, int(p.get("daily_capacity") or 20), key=f"{key}_c")
    out["collection_available"] = h3.checkbox("Accepting new requests", value=p.get("collection_available", True), key=f"{key}_av")
    return out


RESTRICTED_DECLARATIONS = ["NONE"] + [c for c in RestrictedClass.values() if c != "NONE"]
ORG_TYPES = OrgType.values()
