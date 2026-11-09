import uuid

import streamlit as st

from ai import claude_client, ocr_service
from ai.medicine_extraction import extract_from_image
from components.forms import RESTRICTED_DECLARATIONS, registration_fields
from components.header import notice, page_header
from components.session import act, actor, db, flash
from database.models import User
from services import medicine_service
from services.errors import ServiceError, ValidationError
from utils.date_utils import today

a = actor()
page_header("Register a medicine", "For authorized collection and disposal only")
notice("Controlled, narcotic, psychotropic and cold-chain (refrigerated) medicines are excluded from the pilot. "
       "Ask your pharmacist about safe disposal of those products.")

st.subheader("1 · Photograph the packaging (optional)")
photo = st.file_uploader("Upload a clear photo of the pack or strip showing name, batch and expiry",
                         type=["png", "jpg", "jpeg", "webp"])
ai_on = claude_client.is_enabled()
consent = False
if photo is not None:
    st.image(photo, width=260)
    if ai_on:
        consent = st.checkbox("Allow MediShare to send this photo to its AI provider (Anthropic Claude) to read the label. "
                              "Only the packaging image and OCR text are sent.", value=False)
    else:
        st.caption("AI label reading is not configured; on-device OCR will be used." if ocr_service.ocr_available()
                   else "AI and OCR are not available; please type the details.")
    if st.button("Read label", icon=":material/document_scanner:"):
        with st.status("Reading packaging…", expanded=False) as status:
            try:
                with db() as s:
                    res = extract_from_image(s, a, photo.getvalue(), photo.name, allow_external_ai=consent)
                st.session_state["extraction"] = dict(fields=res.fields, confidence=res.confidence, errors=res.errors,
                                                      record_id=str(res.record_id), source=res.source)
                status.update(label="Label read – please check every field", state="complete")
            except (ServiceError, ValidationError) as exc:
                status.update(label="Could not read the label", state="error")
                st.error(str(exc))

ext = st.session_state.get("extraction")
if ext:
    st.info(f"Details suggested by {'AI + OCR' if ext['source'] == 'claude+ocr' else 'OCR'}. They may be wrong – "
            "review and correct every field before saving. Confidence labels are the model's own estimate and are "
            "not verified probabilities.")
    for e in ext["errors"]:
        st.caption(f"⚠️ {e}")
    if ext["confidence"]:
        st.caption("Model-reported confidence: " + ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in ext["confidence"].items()))

st.subheader("2 · Confirm the details")
prefill = dict(ext["fields"]) if ext else {}
with db() as s:
    me = s.get(User, a.user_id)
    prefill.setdefault("district", me.district)
    prefill.setdefault("pin_code", me.pin_code)
    prefill.setdefault("location", me.location)
    matches = medicine_service.search_medicines(s, prefill.get("medicine_name") or "", limit=20) if prefill.get("medicine_name") else []
    master = {str(m.id): f"{m.name} · {m.manufacturer or '—'} · {m.dosage_form.title()}" for m in matches}

with st.form("register_medicine"):
    data = registration_fields(prefill, key="regform")
    link = st.selectbox("Match to medicine master (optional)", [""] + list(master), format_func=lambda k: master.get(k, "— None —"))
    declared = st.selectbox("Is this a restricted or temperature-sensitive product?", RESTRICTED_DECLARATIONS,
                            format_func=lambda v: "No" if v == "NONE" else v.replace("_", " ").title())
    confirm = st.checkbox("I confirm these details are correct and that this medicine is for disposal.")
    c1, c2 = st.columns(2)
    submit = c1.form_submit_button("Submit for collection", type="primary", width="stretch")
    draft = c2.form_submit_button("Save as draft", width="stretch")

if submit or draft:
    if not confirm:
        st.error("Please confirm the details before saving.")
    else:
        with db() as s:
            result = act(s, medicine_service.create_registration, rerun=False, submit=bool(submit),
                         declared_restricted=declared, medicine_id=uuid.UUID(link) if link else None,
                         photo=(photo.getvalue(), photo.name) if photo is not None else None,
                         ai_extraction_id=uuid.UUID(ext["record_id"]) if ext else None, **data)
        if result is not None:
            st.session_state.pop("extraction", None)
            expired = data["expiry_date"] and data["expiry_date"] <= today()
            flash(("Medicine registered" if submit else "Draft saved")
                  + (" – expired medicines go to authorized disposal" if expired else ""))
            st.switch_page("app_pages/04_My_Registrations.py")
