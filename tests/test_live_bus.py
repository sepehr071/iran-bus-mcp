import pytest

from iran_bus_mcp.merged import parse_date

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_live_search_cheapest_trip(client):
    args = {"origin": "تهران", "destination": "اصفهان", "date": "tomorrow"}
    b = (await client.call_tool("bus_search", args)).structured_content["safar724"]
    assert b["count"] > 0 and b["trips"][0]["price_toman"] > 10000 and b["trips"][0]["company"]
    c = (await client.call_tool("bus_cheapest", {**args, "limit": 3})).structured_content
    assert c["trips"][0]["price_toman"] == b["price_range_toman"][0]
    t = (
        await client.call_tool("bus_trip", {"trip_id": b["trips"][0]["id"], "destination": "اصفهان"})
    ).structured_content["safar724"]
    assert t["seats_free"] == b["trips"][0]["seats_free"]
    assert parse_date("tomorrow") == b["date"]


async def test_live_terminals_cities(client):
    r = (
        await client.call_tool("bus_terminals", {"origin": "تهران", "destination": "مشهد", "date": "tomorrow"})
    ).structured_content
    assert r["safar724"]["origin_terminals"]
    r = (await client.call_tool("bus_cities", {"query": "shiraz"})).structured_content
    assert r["safar724"][0]["code"]
