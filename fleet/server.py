"""The fleet's server entry point: upstream's amazon-orders-mcp, plus ``keeper_status`` for monitoring.

``python -m fleet.server`` takes upstream's command-line arguments and runs the same server with one more tool,
``keeper_status``: what the sign-in keeper (fleet/keeper.py) last found for every site. It reads the keeper's state
file only, so a monitor calling it every few minutes makes no Amazon request. It reports ``ok: false`` with a
``code`` (``held_<site>``, ``signed_out_<site>``, ``stale_<site>``, ``not_checked_<site>``) when any site isn't
known to be signed in.
"""

from __future__ import annotations

import datetime
import os
import time
from typing import Any, Dict, List, Optional

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from amazonorders_mcp import server as upstream
from fleet.keeper import SIGNED_IN, Settings, State

STATUS = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def summarise_keeper_state(settings: Settings, now: Optional[float] = None) -> Dict[str, Any]:
    """
    Summarise the keeper's state for every site: ``ok`` only when each was confirmed signed in recently (within
    2.5 probe intervals, so one failed probe doesn't fail it) and none is held or signed out.
    """
    now = time.time() if now is None else now
    data = State(settings.state_path).read()
    sites: List[Dict[str, Any]] = []
    problems: List[str] = []
    for site in settings.sites:
        entry = data["sites"].get(site, {})
        last_ok = entry.get("last_ok")
        result = entry.get("last_result")
        if entry.get("held"):
            problem: Optional[str] = "held"
        elif result is None:
            problem = "not_checked"
        elif result not in SIGNED_IN and not result.startswith("probe_error"):
            problem = "signed_out"
        elif last_ok is None or now - last_ok > 2.5 * settings.probe_seconds:
            problem = "stale"
        else:
            problem = None
        if problem:
            problems.append(f"{problem}_{site.replace('.', '_')}")
        sites.append({"site": site, "ok": problem is None, "last_result": result,
                      "last_signed_in": _iso(last_ok), "last_checked": _iso(entry.get("last_probe")),
                      "held": (entry.get("held") or {}).get("reason"),
                      "sign_ins_last_24h": len(entry.get("logins", []))})
    return {"ok": not problems, "code": problems[0] if problems else None, "sites": sites}


def _iso(timestamp: Optional[float]) -> Optional[str]:
    if timestamp is None:
        return None
    return datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc).isoformat(timespec="seconds")


def create_server(settings: Optional[Settings] = None) -> MCPServer:
    """Upstream's server with ``keeper_status`` added."""
    settings = Settings.from_env() if settings is None else settings
    server = upstream.create_server()

    @server.tool(annotations=STATUS,
                 description="Operations check for this deployment: whether the sign-in keeper has every Amazon "
                             "site signed in (from its last check of each, every 2 hours). No Amazon request. Use "
                             "`check_session` to answer the user.")
    async def keeper_status() -> Dict[str, Any]:
        return summarise_keeper_state(settings)

    return server


def main(args: Optional[List[str]] = None) -> None:
    parsed_args = upstream._parse_args(args)
    server = create_server()
    if parsed_args.transport == "stdio":
        server.run()
        return
    app = upstream.create_http_app(server, host=parsed_args.host, json_response=parsed_args.json_response,
                                   token=parsed_args.token)
    uvicorn.run(app, host=parsed_args.host, port=parsed_args.port, log_level=server.settings.log_level.lower())


if __name__ == "__main__":
    os.environ.setdefault("AMAZON_KEEPER_STATE", os.path.join(os.environ.get("HOME", "/state"), "keeper.json"))
    main()
