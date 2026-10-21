import json

import anyio
from mcp.client import Client

from fleet.keeper import DAY, Settings, State
from fleet.server import create_server, summarise_keeper_state as keeper_status

NOW = 1_800_000_000.0


def settings(tmp_path):
    return Settings(sites=["amazon.com", "amazon.de"], state_path=str(tmp_path / "keeper.json"), probe_seconds=7200,
                    max_logins=2, login_spacing_seconds=60, op_item="item", op_vault="vault")


def given(tmp_path, **sites):
    with State(str(tmp_path / "keeper.json")).edit() as data:
        data["sites"].update(sites)


def ok(result="signed_in", age=600):
    return {"logins": [], "last_probe": NOW - age, "last_result": result, "last_ok": NOW - age}


def test_ok_when_every_site_is_signed_in(tmp_path):
    given(tmp_path, **{"amazon.com": ok(), "amazon.de": ok("signed_in_again")})
    status = keeper_status(settings(tmp_path), now=NOW)
    assert status["ok"] is True and status["code"] is None
    assert [s["ok"] for s in status["sites"]] == [True, True]


def test_a_held_site_fails_and_is_named(tmp_path):
    held = {**ok(), "held": {"since": NOW, "reason": "AmazonOrdersAuthError: Amazon asked for input"}}
    given(tmp_path, **{"amazon.com": ok(), "amazon.de": held})
    status = keeper_status(settings(tmp_path), now=NOW)
    assert status["ok"] is False and status["code"] == "held_amazon_de"
    assert status["sites"][1]["held"].startswith("AmazonOrdersAuthError")


def test_one_probe_error_is_tolerated_but_a_stale_session_is_not(tmp_path):
    flaky = {**ok(age=3 * 3600), "last_probe": NOW - 60, "last_result": "probe_error:ConnectionError"}
    given(tmp_path, **{"amazon.com": ok(), "amazon.de": flaky})
    assert keeper_status(settings(tmp_path), now=NOW)["ok"] is True
    given(tmp_path, **{"amazon.de": {**flaky, "last_ok": NOW - DAY}})
    assert keeper_status(settings(tmp_path), now=NOW)["code"] == "stale_amazon_de"


def test_signed_out_and_unchecked_sites_fail(tmp_path):
    given(tmp_path, **{"amazon.com": ok("signed_out_budget")})
    status = keeper_status(settings(tmp_path), now=NOW)
    assert status["code"] == "signed_out_amazon_com"
    assert status["sites"][1]["last_result"] is None and status["sites"][1]["ok"] is False


def test_server_offers_keeper_status_alongside_upstream_tools(tmp_path):
    given(tmp_path, **{"amazon.com": ok(age=0), "amazon.de": ok(age=0)})

    async def call():
        async with Client(create_server(settings(tmp_path))) as client:
            names = [tool.name for tool in (await client.list_tools()).tools]
            result = await client.call_tool("keeper_status", {})
            return names, result

    names, result = anyio.run(call)
    assert "keeper_status" in names and "get_order_history" in names and "check_session" in names
    assert not result.is_error
    assert json.loads(result.content[0].text)["sites"][0]["site"] == "amazon.com"
