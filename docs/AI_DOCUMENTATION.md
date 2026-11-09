# AI Documentation

AI in MediShare assists with **OCR, information extraction, operational prioritisation and administrative
reporting only**. It never prescribes, recommends doses or substitutions, determines clinical suitability,
approves redistribution, overrides reviewers or makes legal/regulatory decisions.

## Claude integration (`ai/claude_client.py`)
* Official `anthropic` Python SDK; model `claude-opus-5-5` (configurable via `CLAUDE_MODEL`), effort `low`.
* Server-side refusal fallback enabled (`betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`).
* Typed error handling (rate limit, auth, status, connection) and `refusal` stop reasons map to
  `ClaudeUnavailable`; callers fall back to OCR / deterministic answers and tell the user.
* Disabled automatically when `ANTHROPIC_API_KEY` is missing or `AI_EXTERNAL_PROCESSING_ENABLED=false`.

## Medicine information extraction (`ai/medicine_extraction.py`)
1. Validate image (type by magic bytes, size, decodability, resolution) → store privately.
2. OCR: OpenCV pre-processing (upscale, grayscale, denoise, adaptive threshold) + Tesseract (EasyOCR if installed).
3. If the user consented: send a downscaled JPEG + OCR text to Claude with a **strict JSON schema**
   (`output_config.format`) and a transcription-only system prompt.
4. Validate: JSON parse → Pydantic schema → strip forbidden keys (dosage, approval, suitability …) → normalise
   batch numbers and packaging dates (`EXP 05/2027` → 2027‑05‑31; month-only expiry = last day of month,
   month-only manufacture = first day) → consistency checks (manufacture ≤ expiry).
5. Display candidates with warnings; the user must edit/confirm; the record is stored in
   `ai_extraction_records` and linked to the registration on confirmation.

Output (spec format): `medicine_name, generic_name, manufacturer, batch_number, manufacturing_date, expiry_date,
confidence{}, requires_manual_review: true`. **Confidence values are the model's own labels, shown as such, and
are not treated as calibrated probabilities.** `requires_manual_review` is always true.

Fallback without Claude: regex extraction from OCR text (EXP/MFG/B.No/Lot/Mfd. by patterns).

## Expiry-risk engine (`ai/expiry_risk_engine.py`, algorithm v1.0.0)
`Days remaining = expiry − today`. Categories: > 180 NORMAL · 91–180 MONITOR · 31–90 ATTENTION · 1–30 URGENT ·
≤ 0 EXPIRED.

`Risk = 0.50·T + 0.20·Q + 0.20·D + 0.10·C`, each 0–100:
* **T** = 100 if expired, else 100·(1 − days/365), floored at 0
* **Q** = 100·quantity / 1000 (capped)
* **D** = 100·days waiting without progress / 30 (capped)
* **C** = household: active requests ÷ verified daily capacity in the district (100 if no centre);
  institutional: review queue ÷ (verified reviewers × 15)

Levels: 0–29 LOW · 30–49 MODERATE · 50–74 HIGH · 75–100 CRITICAL. Weights, references and thresholds are system
settings (validated; weights must sum to 1). Each run stores score, components, date, version and a full input
snapshot (including the configuration) in `risk_assessments`, creates/updates `expiry_alerts`, notifies owners,
resolves stale alerts and **quarantines expired institutional batches** (directing them to disposal).
This is an operational priority score, not a medicine-safety measure.

## Forecasting (`ai/forecasting.py`)
Ordinary least-squares linear trend on monthly registration counts (needs ≥ 3 months), clipped at 0, labelled
as an estimate for capacity planning.

## Analytics assistant (`ai/analytics_assistant.py`)
Question → permission check (`ai.assistant` + `analytics.view`) → fixed aggregated queries
(`analytics_service.assistant_snapshot`, no personal data) → Claude (no tools, no SQL) → response validation that
withholds approval/authorization/clinical language → answer with the data sections used. Every query is audited.
Without Claude, the matching database figures are shown.

## Tests
`tests/test_ai_extraction.py` covers invalid OCR output, missing fields, bad date formats, low confidence,
API failure, refusal, malformed JSON, forbidden fields, consent, real Tesseract OCR, assistant permissions,
de-identification and blocking of approval language. `tests/test_expiry_risk.py` covers categories, levels,
the formula, weights validation, persistence, alerts and forecasting.
