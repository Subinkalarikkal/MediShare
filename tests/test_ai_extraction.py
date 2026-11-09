import json
from datetime import date
from types import SimpleNamespace

import pytest

from ai import analytics_assistant, claude_client, medicine_extraction as mx
from services.errors import PermissionDenied, ValidationError


class FakeClaude:
    """Mimics client.beta.messages.create returning a text block."""

    def __init__(self, text=None, exc=None, stop_reason="end_turn"):
        self.text, self.exc, self.stop_reason, self.calls = text, exc, stop_reason, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return SimpleNamespace(stop_reason=self.stop_reason,
                               content=[SimpleNamespace(type="text", text=self.text)])


GOOD = json.dumps({"medicine_name": "Dolo 650", "generic_name": "Paracetamol", "manufacturer": "Micro Labs",
                   "batch_number": "B.No. DOBS3975", "manufacturing_date": "MFG 06/2025",
                   "expiry_date": "EXP 05/2028",
                   "confidence": {k: "high" for k in mx.FIELDS}})


def test_valid_output_is_normalised():
    fields, conf, errors = mx.validate_model_output(GOOD)
    assert errors == []
    assert fields["batch_number"] == "DOBS3975"
    assert fields["expiry_date"] == date(2028, 5, 31) and fields["manufacturing_date"] == date(2025, 6, 1)
    assert conf["medicine_name"] == "high"


def test_malformed_json():
    fields, _, errors = mx.validate_model_output("Sure! here it is: {not json")
    assert all(v is None for v in fields.values()) and "not valid JSON" in errors[0]


def test_missing_fields_and_bad_dates():
    raw = json.dumps({"medicine_name": None, "expiry_date": "sometime soon", "batch_number": "B@D!!"})
    fields, _, errors = mx.validate_model_output(raw)
    assert fields["expiry_date"] is None and fields["batch_number"] is None
    assert any("Could not interpret expiry" in e for e in errors)
    assert any("Medicine name" in e for e in errors)


def test_incorrect_date_order_discarded():
    raw = json.dumps({"medicine_name": "X", "manufacturing_date": "2028-01-01", "expiry_date": "2026-01-01"})
    fields, _, errors = mx.validate_model_output(raw)
    assert fields["expiry_date"] is None and any("after expiry" in e for e in errors)


def test_ai_approval_or_clinical_fields_are_ignored():
    raw = json.dumps({"medicine_name": "X", "expiry_date": "2027-01-01", "approved": True,
                      "dosage": "2 tablets daily", "safe_to_use": True})
    fields, _, errors = mx.validate_model_output(raw)
    assert "approved" not in fields and "dosage" not in fields
    assert any("non-permitted" in e for e in errors)


def test_low_confidence_still_requires_manual_review():
    raw = json.dumps({"medicine_name": "X", "expiry_date": "2027-01", "confidence": {"medicine_name": "low"}})
    fields, conf, _ = mx.validate_model_output(raw)
    res = mx.ExtractionResult(fields, conf)
    assert res.as_spec_dict()["requires_manual_review"] is True and conf["medicine_name"] == "low"


def test_ocr_fallback_parsing():
    text = "CROCIN ADVANCE\nParacetamol Tablets IP 500 mg\nB.No. CA2405\nMFG. 03/2025\nEXP. 02/2028\nMfd. by GSK Ltd"
    fields, errors = mx.extract_from_ocr_text(text)
    assert fields["medicine_name"] == "CROCIN ADVANCE" and fields["batch_number"] == "CA2405"
    assert fields["expiry_date"] == date(2028, 2, 29) and fields["manufacturer"].startswith("GSK")
    assert mx.extract_from_ocr_text("")[1]


def test_end_to_end_with_fake_claude(session, individual, png, monkeypatch):
    fake = FakeClaude(GOOD)
    claude_client.set_client(fake)
    monkeypatch.setattr(mx.ocr_service, "extract_text", lambda b: "DOLO 650 EXP 05/2028")
    res = mx.extract_from_image(session, individual, png, "box.png", allow_external_ai=True)
    assert res.source == "claude+ocr" and res.fields["medicine_name"] == "Dolo 650" and res.record_id
    call = fake.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["output_config"]["format"]["type"] == "json_schema"
    assert call["messages"][0]["content"][0]["type"] == "image"


def test_claude_api_failure_falls_back_to_ocr(session, individual, png, monkeypatch):
    import anthropic
    import httpx2
    err = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))
    claude_client.set_client(FakeClaude(exc=err))
    monkeypatch.setattr(mx.ocr_service, "extract_text", lambda b: "AMOXIL 250\nEXP 11/2027\nBatch AX991")
    res = mx.extract_from_image(session, individual, png, "box.png", allow_external_ai=True)
    assert res.source == "ocr" and "unavailable" in res.errors[0]
    assert res.fields["expiry_date"] == date(2027, 11, 30)


def test_refusal_is_handled(session, individual, png, monkeypatch):
    claude_client.set_client(FakeClaude(text="", stop_reason="refusal"))
    monkeypatch.setattr(mx.ocr_service, "extract_text", lambda b: "")
    res = mx.extract_from_image(session, individual, png, "box.png", allow_external_ai=True)
    assert res.source == "ocr"


def test_no_external_ai_without_consent(session, individual, png, monkeypatch):
    fake = FakeClaude(GOOD)
    claude_client.set_client(fake)
    monkeypatch.setattr(mx.ocr_service, "extract_text", lambda b: "")
    mx.extract_from_image(session, individual, png, "box.png", allow_external_ai=False)
    assert fake.calls == []


def test_invalid_image_rejected(session, individual):
    with pytest.raises(ValidationError):
        mx.extract_from_image(session, individual, b"not an image", "x.png", allow_external_ai=False)


def test_real_tesseract_if_available(png):
    from ai import ocr_service
    if not ocr_service.ocr_available():
        pytest.skip("Tesseract not installed")
    from tests.conftest import png_bytes
    from PIL import Image, ImageDraw, ImageFont
    import io
    img = Image.new("RGB", (900, 250), "white")
    try:
        font = ImageFont.truetype("arial.ttf", 48)
    except OSError:
        font = ImageFont.load_default()
    ImageDraw.Draw(img).multiline_text((20, 20), "PARACETAMOL\nEXP 05/2027", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    text = ocr_service.extract_text(buf.getvalue())
    assert "PARACETAMOL" in text.upper()


# ---------------------------------------------------------------- analytics assistant
def test_assistant_requires_admin(session, individual):
    with pytest.raises(PermissionDenied):
        analytics_assistant.ask(session, individual, "How many requests are pending?")


def test_assistant_fallback_without_claude(session, admin):
    ans = analytics_assistant.ask(session, admin, "How many collection requests are pending?")
    assert ans.source == "fallback" and "Pending collection requests" in ans.text


def test_assistant_uses_aggregates_only(session, admin, individual):
    fake = FakeClaude("- 0 pending requests")
    claude_client.set_client(fake)
    ans = analytics_assistant.ask(session, admin, "How many collection requests are pending?")
    assert ans.source == "claude"
    sent = fake.calls[0]["messages"][0]["content"]
    assert "person@test.in" not in sent and "<metrics>" in sent
    assert "tools" not in fake.calls[0]


def test_assistant_blocks_approval_language(session, admin):
    claude_client.set_client(FakeClaude("We approve batch B123; it is approved for transfer."))
    ans = analytics_assistant.ask(session, admin, "Approve batch B123")
    assert ans.source == "blocked" and "withheld" in ans.text


def test_ai_cannot_record_review_decisions(session, donor, medicine, reviewer):
    from services import inventory_service, review_service, transfer_service
    from services.errors import SafetyViolation
    from tests.conftest import FULL_CHECKLIST, ready_batch
    actor, _ = donor
    b = ready_batch(session, actor, medicine)
    inventory_service.submit_batch(session, actor, b.id)
    with pytest.raises(SafetyViolation):
        review_service.record_decision(session, reviewer, b.id, "APPROVE_FOR_FURTHER_AUTHORIZED_PROCESS",
                                       notes="AI suggests approval", checklist=FULL_CHECKLIST,
                                       authorization_reference="X", ai_generated=True)
    with pytest.raises(SafetyViolation):
        transfer_service.add_authorization(session, reviewer, b.id, "QUALITY", "X", ai_generated=True)
