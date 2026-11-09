"""OCR with OpenCV pre-processing and Tesseract (EasyOCR used if installed)."""
from __future__ import annotations

import logging
import os
import shutil

from utils.image_utils import preprocess_for_ocr

log = logging.getLogger(__name__)

_WINDOWS_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def _configure_tesseract():
    import pytesseract

    cmd = os.getenv("TESSERACT_CMD") or shutil.which("tesseract")
    if not cmd and os.path.exists(_WINDOWS_TESSERACT):
        cmd = _WINDOWS_TESSERACT
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    return pytesseract, cmd


def ocr_available() -> bool:
    try:
        _, cmd = _configure_tesseract()
        return bool(cmd)
    except Exception:
        return False


def extract_text(image_bytes: bytes) -> str:
    """Return OCR text, or an empty string when no OCR engine is available."""
    try:
        import easyocr  # optional dependency

        reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        return "\n".join(r[1] for r in reader.readtext(image_bytes))
    except ImportError:
        pass
    except Exception as exc:  # pragma: no cover
        log.warning("EasyOCR failed: %s", exc)
    try:
        pytesseract, cmd = _configure_tesseract()
        if not cmd:
            return ""
        processed = preprocess_for_ocr(image_bytes)
        return pytesseract.image_to_string(processed, config="--oem 3 --psm 6").strip()
    except Exception as exc:
        log.warning("Tesseract OCR failed: %s", exc)
        return ""
