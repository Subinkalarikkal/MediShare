"""AI-assisted extraction of medicine details from packaging photographs.

Pipeline: validate image → store privately → OCR → (optional) Claude vision
with a strict JSON schema → validation/normalisation → user confirmation.

The output is always a *candidate* that requires manual review. Model
self-reported confidence values are stored for reference only and are not
treated as calibrated probabilities. The model is instructed to extract
printed text only – it is never asked for (and its output is never used for)
clinical, dosage, substitution or approval decisions.
"""
from __future__ import annotations

import base64
import json
import re
import uuid
from dataclasses import dataclass, field

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from ai import claude_client, ocr_service
from config import get_settings
from database.models import AIExtractionRecord
from database.schemas import MedicineExtractionOutput as _ModelOutput
from services import storage_service
from services.permission_service import Actor, require
from utils.constants import Perm
from utils.date_utils import parse_flexible_date
from utils.image_utils import to_jpeg_for_model, validate_upload
from utils.validators import BATCH_RE

FIELDS = ["medicine_name", "generic_name", "manufacturer", "batch_number", "manufacturing_date", "expiry_date"]
FORBIDDEN_KEYS = {"dosage", "dose", "indication", "indications", "substitute", "substitution", "approved",
                  "approval", "suitable", "suitability", "prescription", "recommendation", "safe_to_use"}

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "medicine_name": {"type": ["string", "null"]},
        "generic_name": {"type": ["string", "null"]},
        "manufacturer": {"type": ["string", "null"]},
        "batch_number": {"type": ["string", "null"]},
        "manufacturing_date": {"type": ["string", "null"]},
        "expiry_date": {"type": ["string", "null"]},
        "confidence": {
            "type": "object",
            "properties": {k: {"type": "string", "enum": ["high", "medium", "low"]} for k in FIELDS},
            "required": FIELDS,
            "additionalProperties": False,
        },
    },
    "required": FIELDS + ["confidence"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You transcribe text printed on medicine packaging for a medicine waste-disposal registry. "
    "Extract only what is visibly printed: brand/medicine name, generic name, manufacturer, batch/lot number, "
    "manufacturing date and expiry date. Copy dates exactly as printed (e.g. 'EXP 05/2027'). If a field is not "
    "clearly legible, return null for it and mark its confidence 'low'. Do not infer, guess or add any clinical "
    "information such as uses, dosage, substitutes, or whether the medicine is safe or suitable to use."
)


@dataclass
class ExtractionResult:
    fields: dict
    confidence: dict
    errors: list[str] = field(default_factory=list)
    requires_manual_review: bool = True
    source: str = "none"
    record_id: uuid.UUID | None = None
    ocr_text: str = ""

    def as_spec_dict(self) -> dict:
        d = {k: self.fields.get(k) for k in FIELDS}
        for k in ("manufacturing_date", "expiry_date"):
            d[k] = d[k].isoformat() if d[k] else None
        d["confidence"] = self.confidence
        d["requires_manual_review"] = True
        return d


def _strip_json(text: str) -> str:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    return m.group(0) if m else text


def validate_model_output(raw: str) -> tuple[dict, dict, list[str]]:
    """Parse and validate raw model text. Returns (fields, confidence, errors)."""
    errors: list[str] = []
    empty = {k: None for k in FIELDS}
    try:
        data = json.loads(_strip_json(raw))
    except (json.JSONDecodeError, TypeError):
        return empty, {}, ["AI response was not valid JSON; please enter details manually."]
    if not isinstance(data, dict):
        return empty, {}, ["AI response had an unexpected structure."]
    bad = sorted(k for k in data if k.lower() in FORBIDDEN_KEYS)
    if bad:
        errors.append(f"Ignored non-permitted AI output fields: {', '.join(bad)}.")
        data = {k: v for k, v in data.items() if k.lower() not in FORBIDDEN_KEYS}
    try:
        parsed = _ModelOutput.model_validate(data)
    except PydanticValidationError:
        return empty, {}, ["AI response failed schema validation; please enter details manually."]
    out = dict(empty)
    for k in ("medicine_name", "generic_name", "manufacturer"):
        v = getattr(parsed, k)
        if v and isinstance(v, str) and v.strip():
            out[k] = v.strip()[:250]
    b = (parsed.batch_number or "").strip().upper()
    b = re.sub(r"^(B\.?\s*NO\.?|BATCH(\s*NO\.?)?|LOT(\s*NO\.?)?)[:\s]*", "", b).strip()
    if b:
        if BATCH_RE.match(b):
            out["batch_number"] = b
        else:
            errors.append("Extracted batch number had invalid characters and was discarded.")
    for k, eom in (("manufacturing_date", False), ("expiry_date", True)):
        raw_date = getattr(parsed, k)
        if raw_date:
            cleaned = re.sub(r"^(EXP(IRY)?\.?|MFG\.?|MFD\.?|MANUFACTURED|USE BEFORE|BEST BEFORE)[:\s]*", "",
                             raw_date.strip().upper())
            d = parse_flexible_date(cleaned, end_of_month=eom)
            if d is None:
                errors.append(f"Could not interpret {k.replace('_', ' ')} '{raw_date}'.")
            out[k] = d
    if out["manufacturing_date"] and out["expiry_date"] and out["manufacturing_date"] > out["expiry_date"]:
        errors.append("Extracted manufacturing date is after expiry date; dates discarded.")
        out["manufacturing_date"] = out["expiry_date"] = None
    if not out["medicine_name"]:
        errors.append("Medicine name could not be extracted.")
    if not out["expiry_date"]:
        errors.append("Expiry date could not be extracted.")
    conf = {}
    if isinstance(parsed.confidence, dict):
        conf = {k: v for k, v in parsed.confidence.items() if k in FIELDS and v in {"high", "medium", "low"}}
    return out, conf, errors


_EXP_RE = re.compile(r"(?:EXP(?:IRY)?|USE\s*BEFORE)[.:\s]*([A-Z]{3,9}[\s./-]*\d{2,4}|\d{1,2}[./-]\d{2,4}(?:[./-]\d{2,4})?)", re.I)
_MFG_RE = re.compile(r"(?:MFG|MFD|MANUFACTURED|MFG\.?\s*DATE)[.:\s]*([A-Z]{3,9}[\s./-]*\d{2,4}|\d{1,2}[./-]\d{2,4}(?:[./-]\d{2,4})?)", re.I)
_BATCH_RE = re.compile(r"(?:B\.?\s*NO|BATCH(?:\s*NO)?|LOT(?:\s*NO)?)[.:\s]*([A-Z0-9][A-Z0-9/\-]{2,20})", re.I)
_MFR_RE = re.compile(r"(?:MFD\.?\s*BY|MANUFACTURED\s*BY|MKT\.?\s*BY|MARKETED\s*BY)[.:\s]*([^\n]{3,80})", re.I)


def extract_from_ocr_text(text: str) -> tuple[dict, list[str]]:
    """Deterministic fallback extraction from OCR text."""
    out = {k: None for k in FIELDS}
    errors: list[str] = []
    if not text.strip():
        return out, ["No readable text was found in the image."]
    if m := _EXP_RE.search(text):
        out["expiry_date"] = parse_flexible_date(m.group(1), end_of_month=True)
    if m := _MFG_RE.search(text):
        out["manufacturing_date"] = parse_flexible_date(m.group(1), end_of_month=False)
    if m := _BATCH_RE.search(text):
        out["batch_number"] = m.group(1).upper()
    if m := _MFR_RE.search(text):
        out["manufacturer"] = m.group(1).strip()
    for line in text.splitlines():
        line = line.strip()
        if len(line) >= 3 and re.search(r"[A-Za-z]{3,}", line) and not re.search(r"EXP|MFG|MFD|BATCH|LOT|B\.NO", line, re.I):
            out["medicine_name"] = line[:250]
            break
    if out["manufacturing_date"] and out["expiry_date"] and out["manufacturing_date"] > out["expiry_date"]:
        out["manufacturing_date"] = out["expiry_date"] = None
        errors.append("OCR dates were inconsistent and were discarded.")
    if not out["expiry_date"]:
        errors.append("Expiry date could not be read – please enter it manually.")
    return out, errors


def extract_from_image(session: Session, actor: Actor, data: bytes, filename: str, *,
                       allow_external_ai: bool) -> ExtractionResult:
    fresh = require(session, actor, Perm.MEDICINE_REGISTER)
    vf = validate_upload(data, filename, max_mb=get_settings().max_upload_mb, images_only=True)
    rec_file = storage_service.store_upload(session, owner_id=fresh.user_id, data=vf.data, filename=vf.filename,
                                            purpose="extraction_images", images_only=True)
    ocr_text = ocr_service.extract_text(vf.data)
    result: ExtractionResult
    raw_response = None
    model_name = None
    if allow_external_ai and claude_client.is_enabled():
        model_name = get_settings().claude_model
        img_b64 = base64.standard_b64encode(to_jpeg_for_model(vf.data)).decode()
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
            {"type": "text", "text": "OCR text (may contain errors):\n" + (ocr_text[:4000] or "(none)")
             + "\n\nReturn the printed packaging fields as JSON."},
        ]
        try:
            raw_response = claude_client.create_message(system=SYSTEM_PROMPT,
                                                        messages=[{"role": "user", "content": content}],
                                                        max_tokens=2000, output_schema=EXTRACTION_SCHEMA)
            fields, conf, errors = validate_model_output(raw_response)
            result = ExtractionResult(fields, conf, errors, source="claude+ocr")
        except claude_client.ClaudeUnavailable as exc:
            fields, errors = extract_from_ocr_text(ocr_text)
            result = ExtractionResult(fields, {}, [f"AI service unavailable ({exc}); used OCR only."] + errors,
                                      source="ocr")
    else:
        fields, errors = extract_from_ocr_text(ocr_text)
        note = [] if ocr_text else ["OCR engine unavailable or no text found."]
        result = ExtractionResult(fields, {}, note + errors, source="ocr")
    status = "SUCCESS" if not result.errors else ("PARTIAL" if any(result.fields.values()) else "FAILED")
    rec = AIExtractionRecord(user_id=fresh.user_id, file_id=rec_file.id, ocr_text=ocr_text[:20000], model=model_name,
                             raw_response=(raw_response or "")[:20000] or None,
                             extracted=result.as_spec_dict(), validation_errors=result.errors,
                             status=f"{status}_{result.source.upper().replace('+', '_')}",
                             requires_manual_review=True)
    session.add(rec)
    session.flush()
    result.record_id, result.ocr_text = rec.id, ocr_text
    return result
