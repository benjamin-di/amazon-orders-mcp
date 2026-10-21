"""Sign-in keeper: keeps each Amazon site of the fleet's amazon-orders-mcp signed in.

The server never signs in (it only reuses saved sessions); this process does, beside it, sharing its config
directory and cookie jars. Every ``AMAZON_KEEPER_PROBE_SECONDS`` it checks each site's saved session the way the
server does (``SavedSession.run(_check_signed_in, site)``). A site Amazon no longer accepts is signed in again with
amazon-orders' automated authentication. The credentials are read from 1Password (``op item get``, with the
keeper's own service-account token) only for that sign-in, held in memory and never written or logged.

At most ``AMAZON_KEEPER_MAX_LOGINS`` sign-ins per site in 24 hours. A sign-in that fails holds the site: the keeper
won't try it again until someone has looked (``python -m fleet.keeper clear SITE``), because repeated failed
sign-ins are what Amazon flags. ``python -m fleet.keeper status`` prints the state. Anything Amazon asks that
amazon-orders can't answer on its own (an emailed code, an approval in the app) fails the sign-in, so it holds too.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, List, Optional

DAY = 24 * 60 * 60
DEFAULT_SITES = ("amazon.com", "amazon.de", "amazon.co.uk", "amazon.es")
SIGNED_IN = ("signed_in", "signed_in_again")
# Keys that pick the default site and its jar: set in the environment they'd outrank every site's own config
FORBIDDEN_ENV = ("AMAZON_DOMAIN", "AMAZON_COOKIE_JAR_PATH", "AMAZON_USERNAME", "AMAZON_PASSWORD",
                 "AMAZON_OTP_SECRET_KEY")

log = logging.getLogger("amazon-keeper")


class SignedOut(Exception):
    """Amazon doesn't accept the site's saved session (or there is none)."""


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str
    otp_secret_key: str

    def __repr__(self) -> str:  # never print the values
        return "Credentials(<redacted>)"


@dataclass(frozen=True)
class Settings:
    sites: List[str]
    state_path: str
    probe_seconds: int
    max_logins: int
    login_spacing_seconds: int
    op_item: str
    op_vault: str

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "Settings":
        env = dict(os.environ if env is None else env)
        sites = env.get("AMAZON_KEEPER_SITES", " ".join(DEFAULT_SITES)).replace(",", " ").split()
        return cls(
            sites=sites,
            state_path=env.get("AMAZON_KEEPER_STATE", os.path.join(env.get("HOME", "/state"), "keeper.json")),
            probe_seconds=int(env.get("AMAZON_KEEPER_PROBE_SECONDS", "7200")),
            max_logins=int(env.get("AMAZON_KEEPER_MAX_LOGINS", "2")),
            login_spacing_seconds=int(env.get("AMAZON_KEEPER_LOGIN_SPACING_SECONDS", "60")),
            op_item=env.get("AMAZON_KEEPER_OP_ITEM", ""),
            op_vault=env.get("AMAZON_KEEPER_OP_VAULT", "MCP-Browser Logins"),
        )


# --- state ------------------------------------------------------------------------------------------------------

class State:
    """``keeper.json``: per site, recent sign-ins, a hold, and the last probe. Every change is written atomically
    under a lock, so ``clear`` from ``docker exec`` and the loop can't lose each other's changes."""

    def __init__(self, path: str) -> None:
        self.path = path

    @contextlib.contextmanager
    def edit(self) -> Iterator[Dict[str, Any]]:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path + ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = self._read()
            yield data
            self._write(data)

    def read(self) -> Dict[str, Any]:
        return self._read()

    def _read(self) -> Dict[str, Any]:
        try:
            with open(self.path) as f:
                data = json.load(f)
        except FileNotFoundError:
            data = {}
        data.setdefault("sites", {})
        return data

    def _write(self, data: Dict[str, Any]) -> None:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path) or ".", prefix=".keeper-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2, sort_keys=True)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.remove(tmp)
            raise


# --- the keeper ----------------------------------------------------------------------------------------------------

class Keeper:
    """The decisions, with Amazon and 1Password behind injected callables (see :func:`build`)."""

    def __init__(self,
                 settings: Settings,
                 probe: Callable[[str], None],
                 login: Callable[[str, Credentials], None],
                 credentials: Callable[[], Credentials],
                 clock: Callable[[], float] = time.time) -> None:
        self.settings = settings
        self.state = State(settings.state_path)
        self._probe = probe
        self._login = login
        self._credentials = credentials
        self._clock = clock

    def check(self, site: str) -> str:
        """Probe one site and sign it in again if needed. Returns what happened (also logged and recorded)."""
        now = self._clock()
        with self.state.edit() as data:
            entry = data["sites"].setdefault(site, {"logins": []})
            entry["logins"] = [t for t in entry.get("logins", []) if now - t < DAY]
            held = entry.get("held")
        try:
            self._probe(site)
            outcome = "signed_in"
        except SignedOut as e:
            outcome = self._sign_in(site, held, str(e))
        except Exception as e:  # network, Amazon 5xx, a challenge on a read: says nothing about the session
            outcome = f"probe_error:{type(e).__name__}"
            log.warning(json.dumps({"site": site, "event": "probe_error", "error": type(e).__name__,
                                    "detail": str(e)[:200]}))
        with self.state.edit() as data:
            entry = data["sites"].setdefault(site, {"logins": []})
            entry["last_probe"] = now
            entry["last_result"] = outcome
            if outcome in SIGNED_IN:
                entry["last_ok"] = now
        log.info(json.dumps({"site": site, "event": "checked", "result": outcome}))
        return outcome

    def _sign_in(self, site: str, held: Optional[Dict[str, Any]], reason: str) -> str:
        if held:
            return "signed_out_held"
        now = self._clock()
        data = self.state.read()
        entry = data["sites"].get(site, {})
        recent = [t for t in entry.get("logins", []) if now - t < DAY]
        if len(recent) >= self.settings.max_logins:
            return "signed_out_budget"
        last_any = max((t for s in data["sites"].values() for t in s.get("logins", [])), default=0)
        if now - last_any < self.settings.login_spacing_seconds:
            return "signed_out_spacing"
        try:
            # Read first: a failure here (1Password, the item) never reached Amazon, so it costs no sign-in
            credentials = self._credentials()
        except Exception as e:
            with self.state.edit() as data:
                data["sites"].setdefault(site, {"logins": []})["held"] = {
                    "since": now, "reason": f"credentials: {type(e).__name__}: {str(e)[:300]}"}
            log.error(json.dumps({"site": site, "event": "credentials_failed_held", "error": type(e).__name__,
                                  "detail": str(e)[:300]}))
            return "credentials_failed_held"
        with self.state.edit() as data:
            data["sites"].setdefault(site, {"logins": []})["logins"].append(now)
        log.info(json.dumps({"site": site, "event": "signing_in", "reason": reason[:200]}))
        try:
            self._login(site, credentials)
            self._probe(site)
        except Exception as e:
            with self.state.edit() as data:
                data["sites"][site]["held"] = {"since": now, "reason": f"{type(e).__name__}: {str(e)[:300]}"}
            log.error(json.dumps({"site": site, "event": "sign_in_failed_held", "error": type(e).__name__,
                                  "detail": str(e)[:300]}))
            return "sign_in_failed_held"
        return "signed_in_again"

    def clear(self, site: str) -> None:
        with self.state.edit() as data:
            entry = data["sites"].setdefault(site, {"logins": []})
            entry.pop("held", None)
        log.info(json.dumps({"site": site, "event": "hold_cleared"}))

    def run_forever(self, sleep: Callable[[float], None] = time.sleep) -> None:
        while True:
            self.check_all(sleep)
            sleep(self.settings.probe_seconds)

    def check_all(self, sleep: Callable[[float], None] = time.sleep) -> List[str]:
        """Check every site, 30 s apart, or after a sign-in the sign-in spacing apart, so that several sites signed
        out at once (e.g. a fresh deploy) are all signed in on the same round."""
        outcomes: List[str] = []
        for site in self.settings.sites:
            if outcomes:
                sleep(self.settings.login_spacing_seconds if outcomes[-1] == "signed_in_again" else 30)
            outcomes.append(self.check(site))
        return outcomes


# --- Amazon and 1Password ------------------------------------------------------------------------------------------

def read_credentials(item: str, vault: str, run: Callable[..., Any] = subprocess.run) -> Credentials:
    """Read the Amazon login (username, password, one-time password secret) from a 1Password LOGIN item."""
    if not item:
        raise RuntimeError("AMAZON_KEEPER_OP_ITEM is not set")
    result = run(["op", "item", "get", item, "--vault", vault, "--format", "json", "--reveal"],
                 capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"op item get failed: {(result.stderr or '').strip()[:200]}")
    fields = json.loads(result.stdout).get("fields", [])
    by_purpose = {f.get("purpose"): f.get("value") for f in fields if f.get("purpose")}
    otp = next((f.get("value") for f in fields if f.get("type") == "OTP" and f.get("value")), None)
    if not by_purpose.get("USERNAME") or not by_purpose.get("PASSWORD") or not otp:
        raise RuntimeError("the 1Password item needs a username, a password and a one-time password")
    secret = otp
    if otp.startswith("otpauth://"):
        secret = (urllib.parse.parse_qs(urllib.parse.urlsplit(otp).query).get("secret") or [""])[0]
    if not secret:
        raise RuntimeError("the one-time password has no secret")
    return Credentials(by_purpose["USERNAME"], by_purpose["PASSWORD"], secret)


def build(settings: Settings) -> Keeper:
    """The keeper wired to amazon-orders(-mcp) and 1Password."""
    from amazonorders.exception import AmazonOrdersAuthError, AmazonOrdersError
    from amazonorders.session import AmazonSession, IODefault
    from amazonorders_mcp.server import _check_signed_in
    from amazonorders_mcp.session import SavedSession

    class KeeperIO(IODefault):
        def echo(self, msg: str, **kwargs: Any) -> None:
            log.info(json.dumps({"event": "amazon_orders", "detail": str(msg)[:300]}))

        def prompt(self, msg: str, type: Optional[Any] = None, **kwargs: Any) -> Any:
            raise AmazonOrdersAuthError(f"Amazon asked for input (\"{msg}\"), which the keeper can't give.")

    def probe(site: str) -> None:
        try:
            SavedSession().run(_check_signed_in, site=site)
        except AmazonOrdersAuthError as e:
            raise SignedOut(str(e)) from e
        except AmazonOrdersError:
            raise

    def login(site: str, credentials: Credentials) -> None:
        # The same config the server builds for the site: its own cookie jar beside the default one
        config = SavedSession()._site_config(site)
        session = AmazonSession(username=credentials.username, password=credentials.password,
                                otp_secret_key=credentials.otp_secret_key, io=KeeperIO(), config=config)
        session.logout()  # Amazon no longer accepts the saved cookies: start from a clean session, as login() does
        session.login()
        if not session.is_authenticated:
            raise AmazonOrdersAuthError("the sign-in didn't complete")

    return Keeper(settings, probe, login, lambda: read_credentials(settings.op_item, settings.op_vault))


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    parser = argparse.ArgumentParser(prog="python -m fleet.keeper", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="probe every site forever, signing in again when needed")
    once = sub.add_parser("check", help="probe (and if needed sign in) one site once")
    once.add_argument("site")
    sub.add_parser("status", help="print the keeper state")
    clear = sub.add_parser("clear", help="clear a site's hold after a failed sign-in")
    clear.add_argument("site")
    args = parser.parse_args(argv)
    settings = Settings.from_env()

    if args.command == "status":
        print(json.dumps(State(settings.state_path).read(), indent=2, sort_keys=True))
        return 0
    if args.command == "clear":
        Keeper(settings, _unused, _unused, _unused).clear(args.site)
        return 0

    present = [key for key in FORBIDDEN_ENV if os.environ.get(key)]
    if present:
        parser.error(f"unset {', '.join(present)}: the default site comes from config.yml, and credentials only "
                     "from 1Password at sign-in")
    keeper = build(settings)
    if args.command == "check":
        print(keeper.check(args.site))
        return 0
    keeper.run_forever()
    return 0


def _unused(*args: Any) -> Any:
    raise AssertionError("not used")


if __name__ == "__main__":
    sys.exit(main())
