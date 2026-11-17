import pytest

from iran_bus_mcp.merged import jalali, parse_date

from .conftest import fixture

pytestmark = pytest.mark.anyio


@pytest.fixture
def site(api):
    api["/cs/api/Route/GetCities"] = fixture("cities.json")
    api["/cs/api/bus/route"] = fixture("route.json")
    api["/cs/api/bus/serviceDetails"] = fixture("trip.json")
    return api


def test_dates():
    assert jalali(2026, 11, 19) == (1405, 8, 28)
    assert jalali(2026, 3, 21) == (1405, 1, 1)
    assert parse_date("2026-11-19") == "1405-08-28"
    assert parse_date("1405/8/28") == "1405-08-28"
    assert parse_date("۱۴۰۵/۰۸/۲۸") == "1405-08-28"
    with pytest.raises(Exception, match="not understood"):
        parse_date("next week")


async def test_search(client, site):
    r = (
        await client.call_tool("bus_search", {"origin": "تهران", "destination": "isfahan", "date": "2026-11-19"})
    ).structured_content
    b = r["safar724"]
    assert (b["from"], b["to"], b["date"]) == ("تهران", "اصفهان", "1405-08-28")
    q = site.calls[-1].url.params
    assert (q["date"], q["origin"], q["destination"], q["vehicleType"]) == ("1405-08-28", "11320000", "21310000", "Bus")
    t = b["trips"][0]
    assert t["price_toman"] == 712000 and t["seats_free"] == 14 and t["time"] == "00:30" and t["vip"]
    assert t["company"] == "رویال سفر ایرانیان" and t["from_terminal"] == "بیهقی (آرژانتین)"
    assert b["price_range_toman"] == [712000, 5000000] and b["count"] == 4


async def test_cheapest_sorted_with_provider(client, site):
    r = (
        await client.call_tool(
            "bus_cheapest", {"origin": "tehran", "destination": "esfahan", "date": "1405/08/28", "limit": 2}
        )
    ).structured_content
    assert [t["price_toman"] for t in r["trips"]] == [712000, 712000]
    assert all(t["provider"] == "safar724" for t in r["trips"])


async def test_terminals_and_trip(client, site):
    r = (
        await client.call_tool("bus_terminals", {"origin": "tehran", "destination": "esfahan", "date": "today"})
    ).structured_content["safar724"]
    assert "بیهقی (آرژانتین)" in r["origin_terminals"] and r["companies"][0]["trips"] == 14
    t = (await client.call_tool("bus_trip", {"trip_id": 40473676, "destination": "esfahan"})).structured_content[
        "safar724"
    ]
    assert site.calls[-1].url.params["destinationCode"] == "21310000"
    assert t["seats_total"] == 25 and t["seats_free"] == 14 and 7 in t["free_seat_numbers"]
    assert t["refund_rules"][0]["penalty_percent"] == 10.0


async def test_cities_and_ambiguity(client, site):
    r = (await client.call_tool("bus_cities", {"query": "اصفهان"})).structured_content["safar724"]
    assert r[0]["code"] == "21310000"
    res = await client.call_tool("bus_search", {"origin": "zzz", "destination": "tehran", "date": "today"})
    assert res.is_error
