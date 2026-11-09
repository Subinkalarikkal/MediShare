"""Upload validation and image pre-processing."""
from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from utils.constants import ALLOWED_DOCUMENT_TYPES, ALLOWED_IMAGE_TYPES
from utils.validators import ValidationError

_MAGIC = {
    b"\x89PNG\r\n\x1a\n": ("png", "image/png"),
    b"\xff\xd8\xff": ("jpg", "image/jpeg"),
    b"%PDF-": ("pdf", "application/pdf"),
}

MAX_PIXELS = 40_000_000
MIN_SIDE = 64


@dataclass
class ValidatedFile:
    data: bytes
    filename: str
    extension: str
    content_type: str


def sniff_type(data: bytes) -> tuple[str, str] | None:
    for magic, info in _MAGIC.items():
        if data.startswith(magic):
            return info
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ("webp", "image/webp")
    return None


def validate_upload(data: bytes, filename: str, *, max_mb: int, images_only: bool) -> ValidatedFile:
    """Validate by size, extension AND file signature (never trust the extension alone)."""
    if not data:
        raise ValidationError("The uploaded file is empty.")
    if len(data) > max_mb * 1024 * 1024:
        raise ValidationError(f"File exceeds the {max_mb} MB limit.")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    allowed = ALLOWED_IMAGE_TYPES if images_only else ALLOWED_DOCUMENT_TYPES
    if ext not in allowed:
        raise ValidationError(f"File type '.{ext}' is not allowed. Allowed: {', '.join(sorted(allowed))}.")
    sniffed = sniff_type(data)
    if sniffed is None:
        raise ValidationError("File content does not match an allowed file type.")
    real_ext, content_type = sniffed
    normal_ext = "jpg" if ext == "jpeg" else ext
    if real_ext != normal_ext:
        raise ValidationError("File extension does not match file content.")
    if content_type.startswith("image/"):
        validate_image_bytes(data)
    safe_name = "".join(c for c in filename if c.isalnum() or c in "._- ")[:200] or f"upload.{ext}"
    return ValidatedFile(data=data, filename=safe_name, extension=real_ext, content_type=content_type)


def validate_image_bytes(data: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ValidationError("The image could not be read or is corrupted.") from exc
    w, h = img.size
    if w * h > MAX_PIXELS:
        raise ValidationError("Image resolution is too large.")
    if min(w, h) < MIN_SIDE:
        raise ValidationError("Image is too small to read packaging details.")
    return img


def preprocess_for_ocr(data: bytes):
    """Return an OpenCV grayscale, denoised, thresholded image (numpy array)."""
    import cv2
    import numpy as np

    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValidationError("Image could not be decoded for OCR.")
    h, w = img.shape[:2]
    scale = 1600 / max(h, w) if max(h, w) < 1600 else 1.0
    if scale > 1.0:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.fastNlMeansDenoising(gray, h=10)
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11)


def to_jpeg_for_model(data: bytes, max_side: int = 1568) -> bytes:
    """Downscale/convert an image before sending it to a vision model (EXIF stripped)."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((max_side, max_side))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=88)
    return out.getvalue()
