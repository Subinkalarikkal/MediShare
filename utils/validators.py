"""Input validation helpers shared by services."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from email_validator import EmailNotValidError, validate_email

from utils.date_utils import today


class ValidationError(ValueError):
    """Raised when user-supplied data fails validation."""

    def __init__(self, errors: list[str] | str):
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        super().__init__("; ".join(self.errors))


PIN_RE = re.compile(r"^[1-9][0-9]{5}$")  # Indian PIN code
PHONE_RE = re.compile(r"^\+?[0-9][0-9\s-]{7,15}$")
BATCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/\-_. ]{0,99}$")


def clean_text(value: str | None, max_len: int = 500) -> str | None:
    if value is None:
        return None
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value)).strip()
    return value[:max_len] if value else None


def require(value, label: str, errors: list[str]) -> None:
    if value is None or (isinstance(value, str) and not value.strip()):
        errors.append(f"{label} is required.")


def check_email(value: str | None, errors: list[str], label: str = "Email") -> str | None:
    if not value:
        errors.append(f"{label} is required.")
        return None
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        errors.append(f"{label} is not a valid email address.")
        return None


def normalize_email(value: str) -> str:
    return value.strip().lower()


def check_pin(value: str | None, errors: list[str], required: bool = True) -> str | None:
    if not value:
        if required:
            errors.append("PIN code is required.")
        return None
    value = value.strip()
    if not PIN_RE.match(value):
        errors.append("PIN code must be a valid 6-digit Indian PIN code.")
        return None
    return value


def check_phone(value: str | None, errors: list[str], required: bool = True) -> str | None:
    if not value:
        if required:
            errors.append("Phone number is required.")
        return None
    value = value.strip()
    if not PHONE_RE.match(value):
        errors.append("Phone number is not valid.")
        return None
    return value


def check_quantity(value, errors: list[str], label: str = "Quantity",
                   max_value: float = 1_000_000) -> Decimal | None:
    try:
        q = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        errors.append(f"{label} must be a number.")
        return None
    if q.is_nan() or q <= 0:
        errors.append(f"{label} must be greater than zero.")
        return None
    if q > Decimal(str(max_value)):
        errors.append(f"{label} exceeds the maximum allowed ({max_value:,.0f}).")
        return None
    return q.quantize(Decimal("0.01"))


def check_dates(mfg: date | None, expiry: date | None, errors: list[str],
                expiry_required: bool = True) -> None:
    if expiry is None:
        if expiry_required:
            errors.append("Expiry date is required.")
        return
    if not isinstance(expiry, date):
        errors.append("Expiry date is not a valid date.")
        return
    if expiry.year < 1990 or expiry.year > 2100:
        errors.append("Expiry date is outside the accepted range.")
    if mfg is not None:
        if mfg > today():
            errors.append("Manufacturing date cannot be in the future.")
        if mfg > expiry:
            errors.append("Manufacturing date must be on or before the expiry date.")


def check_batch(value: str | None, errors: list[str], required: bool = False) -> str | None:
    if not value:
        if required:
            errors.append("Batch number is required.")
        return None
    value = value.strip().upper()
    if not BATCH_RE.match(value):
        errors.append("Batch number contains invalid characters.")
        return None
    return value


def check_choice(value: str | None, choices, errors: list[str], label: str) -> str | None:
    if value not in set(choices):
        errors.append(f"{label} must be one of: {', '.join(sorted(choices))}.")
        return None
    return value


def raise_if(errors: list[str]) -> None:
    if errors:
        raise ValidationError(errors)
