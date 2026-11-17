"""Bus tools. Each takes `provider` and returns one block per provider, so more ticket sites can join the same tools."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .registry import tool
from .safar724 import bus as sf

# shortcut: payaneh.ir is not here. Its /api needs a per-request signature header built by the site's own JS, which this
# server does not forge; add it as a second provider if the site opens a plain public API.
PROVIDER = Annotated[Literal["all", "safar724"], Field(description="all, or one ticket site.")]
ORIGIN = Annotated[
    str, Field(description="Origin city, Persian or English, e.g. تهران, Tehran, or a bus_cities code.", max_length=40)
]
DEST = Annotated[str, Field(description="Destination city, Persian or English, e.g. اصفهان, Isfahan.", max_length=40)]
DATE = Annotated[
    str,
    Field(
        description="Travel day: Jalali 1405/08/28 or Gregorian 2026-11-19, or today / tomorrow (Tehran time).",
        max_length=12,
    ),
]
SHAPE_ERRORS = (ToolError, KeyError, IndexError, TypeError, ValueError)
TEHRAN = timezone(timedelta(hours=3, minutes=30))
_G_DAYS = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)


def jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    """Gregorian to Jalali date (jalaali-js algorithm)."""
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 + gd + _G_DAYS[gm - 1]
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        return jy, 1 + days // 31, 1 + days % 31
    return jy, 7 + (days - 186) // 30, 1 + (days - 186) % 30


def parse_date(text: str) -> str:
    """Jalali YYYY-MM-DD from a Jalali or Gregorian date, 'today' or 'tomorrow'."""
    t = text.strip().lower()
    if t in ("today", "tomorrow"):
        d = datetime.now(TEHRAN) + timedelta(days=t == "tomorrow")
        return "{:04d}-{:02d}-{:02d}".format(*jalali(d.year, d.month, d.day))
    t = t.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", t)
    if not m:
        raise ToolError(f"Date '{text}' not understood. Use 1405/08/28, 2026-11-19, today or tomorrow.")
    y, mo, d = map(int, m.groups())
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        raise ToolError(f"Date '{text}' is not a real date.")
    if y > 1700:
        y, mo, d = jalali(y, mo, d)
    return f"{y:04d}-{mo:02d}-{d:02d}"


def _norm(s: str) -> str:
    # the site spells it esfahan; isfahan is the common English form
    return re.sub(r"[\s\-_]+", "", s.translate(str.maketrans("يك", "یک"))).lower().replace("isfahan", "esfahan")


def _city(cities: list[dict[str, Any]], q: str) -> dict[str, Any]:
    """One city by code, exact name or a unique partial name; otherwise a ToolError listing the candidates."""
    n = _norm(q)
    exact = [c for c in cities if n in (c["code"], _norm(c["name"]), _norm(c["persianName"]))]
    hits = exact or [c for c in cities if n and (n in _norm(c["name"]) or n in _norm(c["persianName"]))]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise ToolError(f"No city matches '{q}'. Try bus_cities.")
    names = ", ".join(f"{c['persianName']} ({c['name']}, {c['provincePersianName']})" for c in hits[:8])
    raise ToolError(f"'{q}' matches several cities: {names}. Use the exact name or code.")


async def _each(provider: str, **jobs: Callable[[], Awaitable[Any]]) -> dict[str, Any]:
    """Run the chosen providers at once. A failing provider becomes {"error": ...}; all failing raises."""
    jobs = {n: f for n, f in jobs.items() if provider in (n, "all")}
    results = await asyncio.gather(*(f() for f in jobs.values()), return_exceptions=True)
    out: dict[str, Any] = {}
    for name, r in zip(jobs, results, strict=True):
        if isinstance(r, SHAPE_ERRORS):
            out[name] = {"error": str(r) if isinstance(r, ToolError) else f"unexpected answer shape ({r!r})"}
        elif isinstance(r, BaseException):
            raise r
        else:
            out[name] = r
    if all("error" in v for v in out.values()):
        raise ToolError("; ".join(f"{n}: {v['error']}" for n, v in out.items()))
    return out


def _is_vip(text: str) -> bool:
    return "vip" in text.lower() or "وی آی پی" in text


def _trip(i: dict[str, Any]) -> dict[str, Any]:
    """One trip line. Prices come in Rial; shown in Toman."""
    text = " ".join((i.get("busType") or "").split())
    return {
        "id": i["id"],
        "time": i["departureTime"],
        "company": i["companyPersianName"],
        "bus_type": text,
        "vip": _is_vip(text),
        "price_toman": i["price"] // 10,
        "seats_free": i["availableSeatCount"],
        "from_terminal": i["originTerminalPersianName"],
        "to_terminal": i["destinationTerminalPersianName"],
        "via": i.get("midwayCity") or None,
    }


async def _safar724_route(origin: str, destination: str, date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    cities = await sf.cities()
    o, d = _city(cities, origin), _city(cities, destination)
    r = await sf.route(date, o["code"], d["code"])
    return r, {"from": o["persianName"], "to": d["persianName"], "date": r["date"]}


async def _safar724_trips(origin: str, destination: str, date: str) -> dict[str, Any]:
    r, head = await _safar724_route(origin, destination, date)
    trips = [_trip(i) for i in r["items"] if i["status"] == "Available"]
    return {**head, "trips": trips}


def _sorted(trips: list[dict[str, Any]], by: str) -> list[dict[str, Any]]:
    return sorted(
        trips,
        key=(lambda t: (t["price_toman"], t["time"])) if by == "price" else lambda t: (t["time"], t["price_toman"]),
    )


@tool("Find bus cities")
async def bus_cities(
    query: Annotated[str, Field(description="Part of a city name, Persian or English, e.g. اصف, shir.", max_length=40)],
    provider: PROVIDER = "all",
) -> dict[str, Any]:
    """Look up cities (name, code, province) that tickets can be bought for; use it to spell a city exactly."""

    async def sf_cities() -> list[dict[str, Any]]:
        n = _norm(query)
        hits = [c for c in await sf.cities() if n in _norm(c["name"]) or n in _norm(c["persianName"])]
        return [
            {"name": c["persianName"], "english": c["name"], "code": c["code"], "province": c["provincePersianName"]}
            for c in hits[:20]
        ]

    return await _each(provider, safar724=sf_cities)


@tool("Search bus trips")
async def bus_search(
    origin: ORIGIN,
    destination: DEST,
    date: DATE,
    provider: PROVIDER = "all",
    sort: Annotated[Literal["time", "price"], Field(description="Order of the trips.")] = "time",
    vip_only: Annotated[bool, Field(description="Only trips whose bus type says VIP.")] = False,
    limit: Annotated[int, Field(description="Trips per provider.", ge=1, le=300)] = 30,
) -> dict[str, Any]:
    """List bus trips between two cities on one day: time, company, bus type, price in Toman, free seats, terminals.

    One block per provider with the trip count and the price range. Trips with no free seat are left out. The trip id feeds bus_trip.
    """
    day = parse_date(date)

    async def sf_search() -> dict[str, Any]:
        r = await _safar724_trips(origin, destination, day)
        trips = [t for t in r["trips"] if t["vip"] or not vip_only]
        prices = [t["price_toman"] for t in trips]
        return {
            **{k: r[k] for k in ("from", "to", "date")},
            "count": len(trips),
            "price_range_toman": [min(prices), max(prices)] if prices else None,
            "trips": _sorted(trips, sort)[:limit],
        }

    return await _each(provider, safar724=sf_search)


@tool("Cheapest bus tickets")
async def bus_cheapest(
    origin: ORIGIN,
    destination: DEST,
    date: DATE,
    provider: PROVIDER = "all",
    vip_only: Annotated[bool, Field(description="Only trips whose bus type says VIP.")] = False,
    limit: Annotated[int, Field(description="How many trips.", ge=1, le=20)] = 5,
) -> dict[str, Any]:
    """Cheapest bus trips between two cities on one day, merged across providers and sorted by price in Toman.

    Each trip carries its `provider`. A provider that failed is listed under `errors`.
    """
    day = parse_date(date)
    blocks = await _each(provider, safar724=lambda: _safar724_trips(origin, destination, day))
    trips, errors = [], {}
    for name, b in blocks.items():
        if "error" in b:
            errors[name] = b["error"]
            continue
        trips += [{"provider": name, **t} for t in b["trips"] if t["vip"] or not vip_only]
    head = next(b for b in blocks.values() if "error" not in b)
    out: dict[str, Any] = {
        "from": head["from"],
        "to": head["to"],
        "date": head["date"],
        "trips": _sorted(trips, "price")[:limit],
    }
    if errors:
        out["errors"] = errors
    return out


@tool("Bus terminals on a route")
async def bus_terminals(origin: ORIGIN, destination: DEST, date: DATE, provider: PROVIDER = "all") -> dict[str, Any]:
    """List the departure and arrival terminals that have trips on a route and the companies running them."""
    day = parse_date(date)

    async def sf_terminals() -> dict[str, Any]:
        r, head = await _safar724_route(origin, destination, day)
        f = r.get("filterOptions") or {}
        t = f.get("terminals") or {}
        return {
            "from": head["from"],
            "to": head["to"],
            "origin_terminals": [x["terminalPersianName"] for x in t.get("originTerminals", [])],
            "destination_terminals": [x["terminalPersianName"] for x in t.get("destinationTerminals", [])],
            "companies": [{"name": c["companyPersianName"], "trips": c["count"]} for c in f.get("companies", [])],
        }

    return await _each(provider, safar724=sf_terminals)


@tool("Bus trip seats and refund rules")
async def bus_trip(
    trip_id: Annotated[int, Field(description="Trip id from bus_search or bus_cheapest.", ge=1)],
    destination: DEST,
    provider: Annotated[Literal["safar724"], Field(description="The provider the trip id came from.")] = "safar724",
) -> dict[str, Any]:
    """Seat map summary and refund penalties of one trip: free seat numbers, seats taken by men or women."""

    async def sf_trip() -> dict[str, Any]:
        d = _city(await sf.cities(), destination)
        r = await sf.service(trip_id, d["code"])
        seats = [s for row in r["seats"] for s in row if s]
        free = [s["number"] for s in seats if s["status"] == "Available"]
        return {
            "trip_id": r["id"],
            "status": r["status"],
            "date": r["date"],
            "seats_total": len(seats),
            "seats_free": r["availableSeatCount"],
            "free_seat_numbers": free,
            "taken_by_men": sum(s["status"] == "Gentleman" for s in seats),
            "taken_by_women": sum(s["status"] == "Ladies" for s in seats),
            "refund_rules": [
                {"when": x["message"], "penalty_percent": x["percent"]} for x in r.get("refundRules") or []
            ],
        }

    return await _each(provider, safar724=sf_trip)
