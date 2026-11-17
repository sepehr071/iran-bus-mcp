"""Safar724 bus data: cities, trips on a route and a trip's seat map (all public GETs of service.safar724.com)."""

from __future__ import annotations

import time
from typing import Any

from .http import API, ApiError, fetch

_cities: tuple[float, list[dict[str, Any]]] | None = None
CITIES_TTL = 6 * 3600


async def cities() -> list[dict[str, Any]]:
    """All cities the site sells to (about 1000): id, code, English name, Persian name, province."""
    global _cities
    if _cities is None or time.monotonic() - _cities[0] > CITIES_TTL:
        _cities = (time.monotonic(), await fetch(f"{API}/Route/GetCities"))
    return _cities[1]


async def route(date: str, origin_code: str, destination_code: str) -> dict[str, Any]:
    """Trips of one route on a Jalali date (YYYY-MM-DD). Buses only; prices are in Rial."""
    try:
        return await fetch(
            f"{API}/bus/route",
            {"date": date, "origin": origin_code, "destination": destination_code, "vehicleType": "Bus"},
        )
    except ApiError as e:
        if e.status == 400:  # DatesDoNotMatch: the date is in the past or too far ahead
            raise ApiError("safar724 has no trips for that date (past or too far ahead).", 400) from e
        raise


async def service(trip_id: int, destination_code: str) -> dict[str, Any]:
    """Seat map and refund rules of one trip (read only; selecting a seat is a separate, unused call)."""
    return await fetch(f"{API}/bus/serviceDetails", {"id": trip_id, "destinationCode": destination_code})
