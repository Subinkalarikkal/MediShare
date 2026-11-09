"""Geographic helpers."""
from __future__ import annotations

import math

# Approximate district centroids (Kerala) used when a user has no coordinates.
DISTRICT_CENTROIDS: dict[str, tuple[float, float]] = {
    "Alappuzha": (9.4981, 76.3388), "Ernakulam": (9.9816, 76.2999),
    "Idukki": (9.8494, 76.9720), "Kannur": (11.8745, 75.3704),
    "Kasaragod": (12.4996, 74.9869), "Kollam": (8.8932, 76.6141),
    "Kottayam": (9.5916, 76.5222), "Kozhikode": (11.2588, 75.7804),
    "Malappuram": (11.0510, 76.0711), "Palakkad": (10.7867, 76.6548),
    "Pathanamthitta": (9.2648, 76.7870), "Thiruvananthapuram": (8.5241, 76.9366),
    "Thrissur": (10.5276, 76.2144), "Wayanad": (11.6854, 76.1320),
}
KERALA_CENTER = (10.35, 76.35)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def district_centroid(district: str | None) -> tuple[float, float] | None:
    if not district:
        return None
    return DISTRICT_CENTROIDS.get(district.strip().title())
