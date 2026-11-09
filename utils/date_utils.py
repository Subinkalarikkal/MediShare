"""Date helpers. All persisted timestamps are naive UTC."""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timezone

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_name) if m})


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def today() -> date:
    return utcnow().date()


def days_until(target: date, reference: date | None = None) -> int:
    return (target - (reference or today())).days


def parse_flexible_date(value: str | date | None, *, end_of_month: bool = True) -> date | None:
    """Parse dates as printed on medicine packaging.

    Supports ISO (2026-05-31), DD/MM/YYYY, MM/YYYY, MM-YY, 'MAY 2026',
    'May-26'. Month-only expiry dates resolve to the last day of the month
    (packaging convention) unless end_of_month=False (e.g. manufacture date).
    Returns None if the value cannot be parsed unambiguously.
    """
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip().upper().replace(".", "/")
    if not text or text in {"NULL", "NONE", "N/A", "NA"}:
        return None

    def _month_date(year: int, month: int) -> date | None:
        if not 1 <= month <= 12:
            return None
        if year < 100:
            year += 2000
        if not 1990 <= year <= 2100:
            return None
        day = calendar.monthrange(year, month)[1] if end_of_month else 1
        return date(year, month, day)

    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text)
    if m:
        try:
            return date(int(m[3]), int(m[2]), int(m[1]))
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", text)
    if m:
        return _month_date(int(m[1]), int(m[2]))
    m = re.fullmatch(r"(\d{1,2})[/-](\d{2}|\d{4})", text)
    if m:
        return _month_date(int(m[2]), int(m[1]))
    m = re.fullmatch(r"([A-Z]{3,9})[\s/-]*(\d{2}|\d{4})", text)
    if m and m[1].lower() in _MONTHS:
        return _month_date(int(m[2]), _MONTHS[m[1].lower()])
    return None


def month_key(d: date | datetime) -> str:
    return f"{d.year:04d}-{d.month:02d}"
